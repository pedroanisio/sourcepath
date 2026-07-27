"""LLM output validation for the L4 enrichment layer.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT

LLMs statistically produce errors: omissions, hallucinations,
partial completions, schema violations, and silent failures.
These are properties of the model class, not exceptional conditions.

Any caller of this function that skips output validation is
introducing an architectural omission — not a code bug downstream.

Verification is mandatory. Treat all LLM output as untrusted input.

----------------------------------------------------------------------

What this module fixes
======================

Before it existed, the entire handling of model output in this plugin was
``text.strip()``. Whatever the model returned went verbatim into
``inventory.ttl`` and ``enrichments.jsonl``: a refusal ("I'm sorry, I can't
help with that") was stored as a file's purpose; four kilobytes of markdown
were stored as the "exactly one declarative sentence" the prompt asked for;
a fenced code block was stored as prose. The SHACL shapes could not catch any
of it — they constrain the *provenance* predicates (``min_length`` on
``*Model``, a hex ``pattern`` on ``*PromptSha``) but apply no constraint at
all to the annotation text, so any string conformed.

That is the exact shape CLAUDE.md names a design defect rather than a runtime
bug, and it sat in the one component in the tree that consumes LLM output.

What this module deliberately does not do
=========================================

It cannot verify that a summary is *true*. Nothing here reads the source file
and confirms the claim. Truth verification is out of reach for a syntactic
gate, and pretending otherwise would be a worse failure than the gap it
replaces: a caller could then treat validated output as fact.

The contract is narrower and honest: **reject output that is structurally
incapable of being a good annotation**, and disclose every rejection. Output
that passes is still unverified LLM text and is still labelled as such
everywhere it surfaces.

Tolerances
==========

Bounds enforced here are deliberately looser than the prompts' wording. The
``file_summary`` prompt asks for "under 30 words"; a 32-word summary is a
style miss, not a correctness risk, and rejecting it would discard usable
data. The bounds exist to catch the *dangerous* failure modes — refusals,
runaway generations, empty output, fence/markup leakage, prompt echo — so
they sit well outside the prompt's stated limits and are asserted as ceilings
rather than as the contract itself.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Hard ceiling for any single annotation. The longest legitimate kind
# (concept_description, "three to five sentences") lands far below this;
# anything above is a runaway generation, not a paragraph.
MAX_ANNOTATION_CHARS = 2000

# Per-kind sentence bounds. Ceilings, not the prompt contract — see module
# docstring. `None` means "no lower bound beyond non-empty".
SENTENCE_BOUNDS: dict[str, tuple[int, int]] = {
    "file_summary": (1, 3),
    "concept_description": (1, 10),
    "schema_purpose": (1, 8),
}

# `file_summary` asks for one sentence under 30 words. 60 is the runaway
# ceiling: double the ask, so ordinary overshoot survives and a model that
# starts narrating does not.
MAX_FILE_SUMMARY_WORDS = 60

# Sentinels the prompts explicitly instruct the model to emit. They bypass
# the shape checks because they *are* the contract for their case.
SENTINELS = {
    "file_summary": {"empty file"},
    "concept_description": {"insufficient context to characterize this concept"},
    "schema_purpose": set(),
}

# Refusal / meta-commentary. A model that declines still returns 200 OK with
# prose, so this is the only place the decline can be detected.
_REFUSAL_PATTERNS = [
    r"\bi'?m sorry\b",
    r"\bi am sorry\b",
    r"\bi apologi[sz]e\b",
    r"\bi (?:can ?not|can't|cannot)\b",
    r"\bi'?m (?:unable|not able)\b",
    r"\bi am (?:unable|not able)\b",
    r"\bas an ai\b",
    r"\bas a language model\b",
    r"\bi don'?t have (?:access|the ability)\b",
    r"\bunable to (?:assist|help|comply)\b",
]
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)

# Prompt echo: the model repeating the scaffolding instead of answering.
_ECHO_RE = re.compile(r"^\s*(?:SYSTEM|USER|ASSISTANT)\s*:", re.IGNORECASE | re.MULTILINE)

# A fenced block that survived unwrapping means the model emitted code or a
# multi-part answer where prose was requested.
_FENCE_RE = re.compile(r"```")

_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one annotation.

    ``text`` carries the normalized value and is meaningful only when
    ``ok``; ``reason`` is a stable machine-readable slug naming the rule that
    rejected the output, suitable for a degradation entry.
    """

    ok: bool
    text: str = ""
    reason: str = ""


def _unwrap_fence(text: str) -> str:
    """Strip one wrapping ``` fence, a common instruction-following miss.

    Only a *fully* wrapping fence is removed. A fence in the middle of the
    output is left in place so the fence check below rejects it — that shape
    is a code answer, not a formatting slip.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()


def _count_sentences(text: str) -> int:
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return max(1, len(parts))


def validate(kind: str, raw: str) -> ValidationResult:
    """Validate one LLM annotation. Never raises; returns a verdict.

    Pure and deterministic: the same text always yields the same verdict, so
    applying it to cached output preserves the warm-cache byte-determinism
    guarantee that ``verify_llm_enrich_determinism`` pins.
    """
    if raw is None or not isinstance(raw, str):
        return ValidationResult(False, reason="not_a_string")

    text = _unwrap_fence(raw).strip()

    if not text:
        return ValidationResult(False, reason="empty")

    if text.lower() in SENTINELS.get(kind, set()):
        return ValidationResult(True, text=text.lower())

    if len(text) > MAX_ANNOTATION_CHARS:
        return ValidationResult(False, reason="too_long")

    # Control characters (other than ordinary whitespace) indicate binary or
    # truncated transport, never prose.
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in text):
        return ValidationResult(False, reason="control_characters")

    if _REFUSAL_RE.search(text):
        return ValidationResult(False, reason="refusal")

    if _ECHO_RE.search(text):
        return ValidationResult(False, reason="prompt_echo")

    if _FENCE_RE.search(text):
        return ValidationResult(False, reason="code_fence")

    low, high = SENTENCE_BOUNDS.get(kind, (1, 20))
    sentences = _count_sentences(text)
    if not (low <= sentences <= high):
        return ValidationResult(False, reason="sentence_count")

    if kind == "file_summary" and len(text.split()) > MAX_FILE_SUMMARY_WORDS:
        return ValidationResult(False, reason="too_many_words")

    return ValidationResult(True, text=text)

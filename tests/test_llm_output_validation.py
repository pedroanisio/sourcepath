"""L4 LLM output validation — PALS's Law enforcement.

CLAUDE.md states that a pipeline consuming LLM output without verifying it
carries an *architectural* defect rather than a downstream bug. Until
`plugins/llm_enrich/validation.py` existed, this plugin — the only component
in the tree that consumes LLM output — handled model responses with
`text.strip()` and nothing else. A refusal, a fenced code block, or four
kilobytes of markdown all went verbatim into `inventory.ttl` and
`enrichments.jsonl`, and the SHACL shapes could not catch any of it: they
constrain the provenance predicates but place no constraint on the
annotation text, so every string conformed.

This suite pins the gate that closed that hole, and the disclosure that
accompanies every rejection — a dropped annotation must never be silent.

Run from the repo root:  python -m pytest tests/test_llm_output_validation.py
"""
from __future__ import annotations

import pytest

from plugins.llm_enrich.validation import (
    MAX_ANNOTATION_CHARS,
    MAX_FILE_SUMMARY_WORDS,
    validate,
)


def _ctx():
    """Minimal PipelineCtx — only `scratch` matters for disclosure tests."""
    from codebase_mapper.shared_kernel.extensions import PipelineCtx

    return PipelineCtx(
        repo=None, commit="", records=[], blob_by_path={},
        mode_by_path={}, paths_set=set(), read_path=lambda _p: b"",
    )


# --------------------------------------------------------------------------
# Accepts legitimate output
# --------------------------------------------------------------------------

def test_accepts_a_well_formed_file_summary():
    result = validate("file_summary", "Parses inventory graphs into typed records.")
    assert result.ok
    assert result.text == "Parses inventory graphs into typed records."


def test_accepts_a_multi_sentence_concept_description():
    text = ("Represents a unit of behavior in the mapper. It appears across "
            "the emission modules. Callers treat it as an opaque handle.")
    result = validate("concept_description", text)
    assert result.ok


def test_strips_surrounding_whitespace():
    result = validate("file_summary", "  \n Emits SHACL shapes.  \n ")
    assert result.ok
    assert result.text == "Emits SHACL shapes."


def test_word_count_overshoot_is_tolerated():
    """The prompt asks for under 30 words; 32 is a style miss, not a defect.

    Bounds here are runaway ceilings, not the prompt contract — rejecting
    slightly long but usable summaries would discard good data.
    """
    text = " ".join(["word"] * 32) + "."
    assert validate("file_summary", text).ok


# --------------------------------------------------------------------------
# Rejects the dangerous failure modes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I'm sorry, I can't help with that.",
    "I cannot analyze this file.",
    "As an AI language model, I am unable to determine the purpose.",
    "I apologize, but the content is unclear.",
])
def test_rejects_refusals(text):
    """A declining model still returns HTTP 200 with prose.

    Without this check the refusal is stored as the file's purpose and
    surfaces in the UI and MCP output as an authored summary.
    """
    result = validate("file_summary", text)
    assert not result.ok
    assert result.reason == "refusal"


def test_rejects_empty_output():
    assert validate("file_summary", "").reason == "empty"
    assert validate("file_summary", "   \n  ").reason == "empty"


def test_rejects_runaway_generation():
    result = validate("concept_description", "word " * (MAX_ANNOTATION_CHARS))
    assert not result.ok
    assert result.reason == "too_long"


def test_rejects_narrating_file_summary():
    text = " ".join(["word"] * (MAX_FILE_SUMMARY_WORDS + 5)) + "."
    result = validate("file_summary", text)
    assert not result.ok
    # Either bound may trip first; both mean "the model started narrating".
    assert result.reason in {"too_many_words", "sentence_count"}


def test_rejects_prompt_echo():
    result = validate("file_summary", "SYSTEM: You summarize source files.")
    assert not result.ok
    assert result.reason == "prompt_echo"


def test_rejects_embedded_code_fence():
    result = validate("file_summary", "Here is the code:\n```python\nx = 1\n```")
    assert not result.ok
    assert result.reason == "code_fence"


def test_rejects_control_characters():
    result = validate("file_summary", "Parses\x00 the graph.")
    assert not result.ok
    assert result.reason == "control_characters"


def test_rejects_non_string():
    assert validate("file_summary", None).reason == "not_a_string"
    assert validate("file_summary", 42).reason == "not_a_string"


# --------------------------------------------------------------------------
# Normalization and sentinels
# --------------------------------------------------------------------------

def test_unwraps_a_fully_wrapping_fence():
    """A wrapping fence is an instruction-following slip, not a code answer."""
    result = validate("file_summary", "```\nParses the inventory graph.\n```")
    assert result.ok
    assert result.text == "Parses the inventory graph."


def test_accepts_the_empty_file_sentinel():
    """The prompt instructs this exact reply, so it is the contract."""
    result = validate("file_summary", "empty file")
    assert result.ok
    assert result.text == "empty file"


def test_accepts_the_insufficient_context_sentinel():
    result = validate(
        "concept_description",
        "insufficient context to characterize this concept",
    )
    assert result.ok


# --------------------------------------------------------------------------
# Determinism — warm-cache byte-identity depends on it
# --------------------------------------------------------------------------

def test_validation_is_pure_and_deterministic():
    """Same text, same verdict, every call.

    The gate runs on cache hits as well as fresh calls, so a non-deterministic
    verdict would break the warm-cache byte-identity that
    verify_llm_enrich_determinism pins.
    """
    samples = [
        "Parses inventory graphs into typed records.",
        "I'm sorry, I cannot help.",
        "```\nfenced\n```",
        "",
    ]
    for text in samples:
        first = validate("file_summary", text)
        for _ in range(5):
            again = validate("file_summary", text)
            assert (again.ok, again.text, again.reason) == \
                   (first.ok, first.text, first.reason)


# --------------------------------------------------------------------------
# Disclosure — a dropped annotation is never silent
# --------------------------------------------------------------------------

def test_enricher_discloses_rejected_output():
    """A rejection must reach ctx.scratch['degradations'], not just a log line.

    An empty degradations list is the project's healthy-run statement, so a
    drop that left no entry would read downstream as "the model produced
    nothing to say" instead of "output was produced and thrown away".
    """
    from plugins.llm_enrich.enricher import LlmEnricher

    enricher = LlmEnricher()
    ctx = _ctx()

    enricher._reject(ctx, "pkg/a.py", "refusal")
    enricher._reject(ctx, "pkg/b.py", "too_long")
    enricher._reject(ctx, "pkg/c.py", "refusal")

    entries = ctx.scratch["degradations"]
    assert len(entries) == 1, "one aggregated entry per component/kind"
    entry = entries[0]
    assert entry["component"] == "llm_enrich"
    assert entry["reason"] == "output_failed_validation"
    assert entry["skipped"] == 3
    assert entry["by_rule"] == {"refusal": 2, "too_long": 1}
    assert {e["path"] for e in entry["examples"]} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}


def test_rejection_does_not_disable_the_enricher():
    """Invalid output is not an outage — the next record must still run.

    Conflating the two would let one malformed response silently truncate
    enrichment for the rest of the repository.
    """
    from plugins.llm_enrich.enricher import LlmEnricher

    enricher = LlmEnricher()
    ctx = _ctx()
    enricher._reject(ctx, "pkg/a.py", "refusal")
    assert enricher._disabled is False

"""Prompt registry — versioned prompt files keyed by enrichment kind.

Each kind has exactly one *active* prompt at a time:

    plugins/llm_enrich/prompts/<kind>.v<N>.txt

The file's bytes are SHA-256'd at module import; the resulting hex
digest enters the cache key alongside (model, target_content_sha).
Editing the file changes the SHA, which invalidates every cache entry
built against the old version — by design. To keep an old prompt
working alongside a new one, *bump the version number*: copy
``kind.v1.txt`` to ``kind.v2.txt``, edit v2, and point the registry
at v2 by changing ``version=2`` in ``PROMPT_REGISTRY``.

File format (simple two-section text, not YAML/TOML — keeps the SHA
input human-auditable):

    SYSTEM:
    <system message, one or more lines>

    USER:
    <user message with {placeholder} tokens>

Whitespace between sections is preserved verbatim in the SHA input but
trimmed when rendering. Placeholders are filled by ``render()``; an
unknown placeholder raises ``KeyError`` at render time (loud failure
on a mis-named field is correct — silent fallback would mask bugs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cache import hash_text


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class PromptVersionMismatch(RuntimeError):
    """Raised when the on-disk prompt file's SHA disagrees with the
    cached SHA registered for its (kind, version). Strict by design —
    silent prompt drift would silently invalidate the entire cache."""


@dataclass(frozen=True)
class PromptTemplate:
    """A single versioned prompt.

    ``kind`` matches the enrichment kind (``file_summary``,
    ``concept_description``, ``schema_purpose``). ``version`` is the
    integer in the filename. ``filename`` is auto-derived from those
    two. ``system`` and ``user`` are the parsed template strings (with
    ``{placeholder}`` tokens preserved). ``sha256`` is computed over
    the file's *raw bytes* at import; it's what lands in the cache key.
    """
    kind: str
    version: int
    system: str
    user: str
    sha256: str
    filename: str

    @property
    def path(self) -> Path:
        return PROMPTS_DIR / self.filename

    def render(self, **fields: Any) -> tuple[str, str]:
        """Return (system, user) with ``{placeholder}`` tokens replaced.

        Missing or unknown fields raise ``KeyError`` at format time —
        no silent fallback. Callers are responsible for passing
        exactly the placeholders the template expects (one per
        template; documented per kind below).
        """
        return self.system.format(**fields), self.user.format(**fields)


# --- parser ----------------------------------------------------------

def _parse_prompt_text(text: str) -> tuple[str, str]:
    """Split a ``SYSTEM:\\n... USER:\\n...`` file into (system, user).

    Lines are trimmed of trailing whitespace at the section boundaries
    so the rendered output doesn't carry a stray blank line. The raw
    bytes (including those trailing-whitespace lines) still drive the
    SHA — the trim only affects rendering."""
    head, _, rest = text.partition("SYSTEM:")
    if not rest:
        raise ValueError("prompt missing SYSTEM: section")
    system_part, sep, user_part = rest.partition("USER:")
    if not sep:
        raise ValueError("prompt missing USER: section")
    return system_part.strip(), user_part.strip()


def _load(kind: str, version: int) -> PromptTemplate:
    filename = f"{kind}.v{version}.txt"
    p = PROMPTS_DIR / filename
    raw = p.read_bytes()
    system, user = _parse_prompt_text(raw.decode("utf-8"))
    return PromptTemplate(
        kind=kind, version=version,
        system=system, user=user,
        sha256=hash_text(raw),
        filename=filename,
    )


# --- registry --------------------------------------------------------
#
# Maps enrichment kind → active PromptTemplate. Adding a new kind is a
# one-line edit + a new file under prompts/. Bumping a version is a
# one-character edit here (1 → 2) + a new vN file.
#
# Each kind's expected placeholders are documented inline. Mis-naming a
# placeholder in either the template or the caller will raise
# KeyError at render time — strict by design.

PROMPT_REGISTRY: dict[str, PromptTemplate] = {
    # file_summary placeholders: {path, language, content}
    "file_summary": _load("file_summary", 1),
    # concept_description placeholders: {name, kind, frequency,
    #                                     alt_labels, cooccurring, files}
    "concept_description": _load("concept_description", 1),
    # schema_purpose placeholders: {path, filename, content}
    "schema_purpose": _load("schema_purpose", 1),
}


def list_kinds() -> list[str]:
    """Sorted list of registered enrichment kinds."""
    return sorted(PROMPT_REGISTRY)


def get(kind: str) -> PromptTemplate:
    """Lookup helper; raises KeyError for unknown kinds (deliberately —
    a misspelled kind name should fail loudly)."""
    return PROMPT_REGISTRY[kind]


# --- self-check ------------------------------------------------------
#
# Re-hash every active prompt at module import. If the on-disk SHA
# disagrees with what was loaded (impossible under normal flow, since
# we just loaded it), raise loudly. The real value of this is in the
# verifier (verify_llm_enrich_prompts.py) which calls this with
# tampered fixtures.

def verify_registry() -> None:
    """Re-read every registered prompt file and assert its SHA matches
    the entry in PROMPT_REGISTRY. Used by the verifier; safe to call
    at any time."""
    for kind, tmpl in PROMPT_REGISTRY.items():
        raw = tmpl.path.read_bytes()
        actual = hash_text(raw)
        if actual != tmpl.sha256:
            raise PromptVersionMismatch(
                f"prompt drift detected for {kind!r}: "
                f"registry has {tmpl.sha256!r} but {tmpl.filename} "
                f"on disk hashes to {actual!r}. "
                f"Did you edit a prompt file without bumping the version?"
            )

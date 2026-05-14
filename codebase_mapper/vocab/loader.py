"""codebase_mapper.vocab.loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


# The three semantic kinds a curated concept can carry. Anything outside
# this set is rejected at load time so typos in the YAML fail loudly.
ConceptKind = Literal[
    "domain-primitive",
    "structural-primitive",
    "relational-primitive",
]
_CONCEPT_KINDS: frozenset[str] = frozenset(("domain-primitive",
                                            "structural-primitive",
                                            "relational-primitive"))


VOCAB_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class VocabTerm:
    """A single curated concept.

    `name` is the canonical snake_case identifier emitted as the
    `skos:prefLabel`. `aliases` collapse to the canonical name during
    L3 splitting. `broader` (when present) names a `skos:Collection`
    tail under cbml3:collection/<broader>.
    """
    name: str
    kind: ConceptKind
    aliases: tuple[str, ...] = ()
    broader: str | None = None


@dataclass(frozen=True)
class Vocabulary:
    """A loaded, validated vocabulary.

    `terms` is canonical_name -> VocabTerm. `by_alias` resolves any known
    alias (including the canonical name itself) to its canonical form;
    look-ups should always go through `resolve()`.
    """
    version: int
    terms: dict[str, VocabTerm] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)

    def resolve(self, token: str) -> VocabTerm | None:
        """Return the curated VocabTerm for `token`, or None.

        Match is case-insensitive on the canonical/alias keys; the input
        is left as-is for the caller's accounting.
        """
        canon = self.by_alias.get(token.lower())
        if canon is None:
            return None
        return self.terms[canon]

    def __len__(self) -> int:
        return len(self.terms)


def builtin_vocabulary_path() -> Path:
    """Filesystem path to the bundled `software_primitives.yaml`."""
    return Path(__file__).resolve().parent / "software_primitives.yaml"


def builtin_vocabulary() -> Vocabulary:
    """Load the vocabulary shipped with cbm (Stage 2 fills the YAML)."""
    return load_vocabulary(builtin_vocabulary_path())


def load_vocabulary(path: Path) -> Vocabulary:
    """Parse, validate, and return a Vocabulary from a YAML file.

    The YAML shape is:

        version: 1
        kinds:
          domain-primitive:    [behavior, intent, ...]
          structural-primitive: [module, class, ...]
          relational-primitive: [...]
        aliases:
          behavior: [behaviour, behaviors, behaviours]
        broader:
          domain-primitive: intent_first_ontology

    Empty kinds/aliases/broader sections are permitted (Stage 1 ships
    an empty file). Unknown kinds and dangling aliases raise ValueError.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"vocab {path}: top-level must be a mapping, got "
                         f"{type(raw).__name__}")

    version = raw.get("version", VOCAB_SCHEMA_VERSION)
    if version != VOCAB_SCHEMA_VERSION:
        raise ValueError(f"vocab {path}: unsupported version {version!r}, "
                         f"expected {VOCAB_SCHEMA_VERSION}")

    kinds_section = raw.get("kinds") or {}
    aliases_section = raw.get("aliases") or {}
    broader_section = raw.get("broader") or {}

    unknown_kinds = set(kinds_section) - _CONCEPT_KINDS
    if unknown_kinds:
        raise ValueError(f"vocab {path}: unknown concept kind(s): "
                         f"{sorted(unknown_kinds)}")

    unknown_broader = set(broader_section) - _CONCEPT_KINDS
    if unknown_broader:
        raise ValueError(f"vocab {path}: `broader` keyed by unknown "
                         f"kind(s): {sorted(unknown_broader)}")

    # Build canonical terms first; aliases attach in a second pass so we
    # can reject aliases that point at unknown canonical names.
    terms: dict[str, VocabTerm] = {}
    for kind, names in kinds_section.items():
        if names is None:
            continue
        for name in names:
            canon = _normalize(name)
            if canon in terms:
                raise ValueError(f"vocab {path}: duplicate term {canon!r}")
            terms[canon] = VocabTerm(
                name=canon,
                kind=kind,  # validated above
                aliases=(),
                broader=broader_section.get(kind),
            )

    by_alias: dict[str, str] = {canon: canon for canon in terms}
    for canon, alts in aliases_section.items():
        canon_norm = _normalize(canon)
        if canon_norm not in terms:
            raise ValueError(f"vocab {path}: alias entry for unknown "
                             f"term {canon_norm!r}")
        alt_tuple: list[str] = []
        for alt in (alts or []):
            alt_norm = _normalize(alt)
            existing = by_alias.get(alt_norm)
            if existing is not None and existing != canon_norm:
                raise ValueError(f"vocab {path}: alias {alt_norm!r} "
                                 f"already maps to {existing!r}")
            by_alias[alt_norm] = canon_norm
            alt_tuple.append(alt_norm)
        # Re-emit the term with its aliases populated. VocabTerm is
        # frozen, so we replace the dict entry rather than mutate.
        prev = terms[canon_norm]
        terms[canon_norm] = VocabTerm(
            name=prev.name, kind=prev.kind,
            aliases=tuple(alt_tuple), broader=prev.broader,
        )

    return Vocabulary(version=version, terms=terms, by_alias=by_alias)


def _normalize(token: str) -> str:
    """Canonical form used for both term names and alias keys."""
    return token.strip().lower()

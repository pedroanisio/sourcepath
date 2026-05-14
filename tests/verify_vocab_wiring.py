#!/usr/bin/env python3
"""verify_vocab_wiring.py — Stage 4 acceptance test.

Exercises end-to-end behavior of the vocabulary wiring:

  1. canonicalize() collapses British -> American via the vocab.
  2. canonicalize() promotes stopwords (`func`) to canonical (`function`)
     when the vocab carries them — pre-vocab path would have dropped.
  3. canonicalize() falls back to the plural-strip path for tokens the
     vocab doesn't carry.
  4. ConceptAggregator(USE_BUILTIN) wired through register_all() tags
     curated atomic concepts with `kind` and `broader`.
  5. ConceptAggregator(None) suppresses typing entirely (back-compat).
  6. ctx.scratch["host:concept_vocab_disabled"] overrides the
     constructor at runtime (the kill-switch path used by --no-builtin-vocab
     from non-CLI callers).
  7. ctx.indices["host:concept_vocab"] override takes precedence over
     the constructor (used by --concept-vocab).
  8. The resolved vocab is stashed on ctx.scratch so the graph writer's
     L2 chunk-anchoring uses the same alias table as the aggregator.
  9. ConceptsArtifact emits `kind` and `broader` into concepts.json
     when present, and omits them when not (back-compat for older
     readers).

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from codebase_mapper.vocab import Vocabulary, builtin_vocabulary, load_vocabulary
from plugins.concept_graph import USE_BUILTIN
from plugins.concept_graph.artifact import ConceptsArtifact, _concept_payload
from plugins.concept_graph.concepts import (
    ConceptAggregator,
    canonicalize,
)


@dataclass
class _StubCtx:
    """Minimal stand-in for PipelineCtx."""
    indices: dict = field(default_factory=dict)
    scratch: dict = field(default_factory=dict)


def _raw_terms(*identifiers: str) -> dict:
    """Build a raw_terms map for a single fake file from raw identifiers."""
    from plugins.concept_graph.splitter import split_identifier
    entries = []
    for raw in identifiers:
        toks = split_identifier(raw)
        entries.append({
            "source": "symbol", "owner": raw, "raw": raw, "tokens": toks,
        })
    return {"fake.py": entries}


def test_canonicalize_british_to_american() -> None:
    v = builtin_vocabulary()
    assert canonicalize("behaviour", v) == "behavior"
    assert canonicalize("Behaviour", v) == "behavior"
    # Without vocab, the only thing that happens is plural strip; the
    # British spelling survives unchanged.
    assert canonicalize("behaviour", None) == "behaviour"


def test_canonicalize_vocab_beats_stopword() -> None:
    v = builtin_vocabulary()
    # `func` is in STOPWORDS, so the pre-vocab path drops it.
    assert canonicalize("func", None) is None
    # The vocab carries it as an alias for `function`, which must win.
    assert canonicalize("func", v) == "function"


def test_canonicalize_uncurated_falls_through() -> None:
    v = builtin_vocabulary()
    # An English noun that isn't in the vocab; plural-strip behavior
    # must be preserved.
    assert canonicalize("language", v) == "language"
    assert canonicalize("languages", v) == "language"
    # And tokens that the vocab doesn't carry behave identically with
    # and without the vocab.
    assert canonicalize("widget", v) == canonicalize("widget", None) == "widget"


def test_aggregator_tags_curated_concepts() -> None:
    ctx = _StubCtx(scratch={"raw_terms": _raw_terms(
        "UserBehavior", "AuthContract", "LoadUsers",
    )})
    agg = ConceptAggregator(USE_BUILTIN)
    idx = agg.run(ctx)
    concepts = idx["concepts"]
    # behavior and contract are curated; they must carry kind+broader.
    assert concepts["behavior"]["kind"] == "domain-primitive"
    assert concepts["behavior"]["broader"] == "intent_first_ontology"
    assert concepts["contract"]["kind"] == "domain-primitive"
    # `user`, `auth`, `load` are not in the vocab; no typing.
    for uncurated in ("user", "auth", "load"):
        assert "kind" not in concepts.get(uncurated, {}), (
            f"{uncurated} unexpectedly tagged: {concepts.get(uncurated)}"
        )


def test_aggregator_none_disables_typing() -> None:
    ctx = _StubCtx(scratch={"raw_terms": _raw_terms("UserBehavior")})
    agg = ConceptAggregator(None)
    idx = agg.run(ctx)
    # No concept should carry kind even though 'behavior' is normally curated.
    for c in idx["concepts"].values():
        assert "kind" not in c, f"unexpected typing: {c}"


def test_ctx_disabled_overrides_constructor() -> None:
    ctx = _StubCtx(
        scratch={
            "raw_terms": _raw_terms("UserBehavior"),
            "host:concept_vocab_disabled": True,
        },
    )
    # Even with USE_BUILTIN configured, the kill-switch wins.
    agg = ConceptAggregator(USE_BUILTIN)
    idx = agg.run(ctx)
    for c in idx["concepts"].values():
        assert "kind" not in c, f"kill-switch ignored: {c}"


def test_ctx_override_beats_constructor() -> None:
    # Custom vocab with a single curated term: "widget" tagged
    # `structural-primitive`. This proves the index override wins
    # over both the builtin default and the constructor's USE_BUILTIN.
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  structural-primitive: [widget]\n"
        "broader:\n"
        "  structural-primitive: custom_collection\n"
    )
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", delete=False, mode="w", encoding="utf-8",
    ) as fp:
        fp.write(yaml_text)
        path = Path(fp.name)
    custom = load_vocabulary(path)

    ctx = _StubCtx(
        scratch={"raw_terms": _raw_terms("WidgetFactory", "UserBehavior")},
        indices={"host:concept_vocab": custom},
    )
    agg = ConceptAggregator(USE_BUILTIN)
    idx = agg.run(ctx)
    concepts = idx["concepts"]
    # Custom term tagged via the override.
    assert concepts["widget"]["kind"] == "structural-primitive"
    assert concepts["widget"]["broader"] == "custom_collection"
    # Builtin term NOT tagged — the builtin was bypassed.
    assert "kind" not in concepts.get("behavior", {}), (
        f"builtin leaked through override: {concepts.get('behavior')}"
    )


def test_resolved_vocab_stashed_on_scratch() -> None:
    ctx = _StubCtx(scratch={"raw_terms": _raw_terms("UserBehavior")})
    agg = ConceptAggregator(USE_BUILTIN)
    agg.run(ctx)
    stashed = ctx.scratch.get("l3:resolved_vocab")
    assert isinstance(stashed, Vocabulary), (
        f"expected Vocabulary on scratch, got {type(stashed).__name__}"
    )
    assert "behavior" in stashed.terms


def test_artifact_includes_kind_when_present() -> None:
    # _concept_payload is the projection used by ConceptsArtifact.emit;
    # exercising it directly avoids the cost of a full pipeline run.
    with_kind = _concept_payload({
        "label": "behavior", "alt_labels": [], "components": [],
        "frequency": 3, "file_count": 2, "embedding_row": None,
        "kind": "domain-primitive", "broader": "intent_first_ontology",
    })
    assert with_kind["kind"] == "domain-primitive"
    assert with_kind["broader"] == "intent_first_ontology"

    # Without kind, the keys are absent (back-compat for older readers).
    without_kind = _concept_payload({
        "label": "widget", "alt_labels": [], "components": [],
        "frequency": 1, "file_count": 1, "embedding_row": None,
    })
    assert "kind" not in without_kind
    assert "broader" not in without_kind


def test_register_all_with_none_yields_untyped_bundle() -> None:
    """End-to-end: a fresh aggregator(None) emits no kind in its idx."""
    ctx = _StubCtx(scratch={"raw_terms": _raw_terms(
        "UserBehavior", "AuthContract",
    )})
    # Build by hand (mirror register_all but skip host registration —
    # the verifier just wants the idx shape).
    idx = ConceptAggregator(None).run(ctx)
    for c in idx["concepts"].values():
        assert "kind" not in c
        assert "broader" not in c
    # And the resolved-vocab stash should reflect the disabled state.
    assert ctx.scratch.get("l3:resolved_vocab") is None


def main() -> int:
    tests = [
        test_canonicalize_british_to_american,
        test_canonicalize_vocab_beats_stopword,
        test_canonicalize_uncurated_falls_through,
        test_aggregator_tags_curated_concepts,
        test_aggregator_none_disables_typing,
        test_ctx_disabled_overrides_constructor,
        test_ctx_override_beats_constructor,
        test_resolved_vocab_stashed_on_scratch,
        test_artifact_includes_kind_when_present,
        test_register_all_with_none_yields_untyped_bundle,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    if failures:
        print(f"\n{failures}/{len(tests)} test(s) failed")
        return 1
    print(f"\n{len(tests)} test(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

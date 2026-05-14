"""Stage 5: end-to-end tests for the curated-vocab surface in MCP tools.

These tests require the live bundle to have been emitted with Stage 4
in effect (i.e., the bundled software_primitives.yaml was active during
the run, so `concepts.json` carries `kind` / `broader` per curated term).
The auto-discovery fixture in conftest.py picks up `_tmp/code-mapper`
when present.

Verifies:
  1. concept_detail surfaces `kind` and `broader` on curated terms.
  2. concept_detail omits them on uncurated terms.
  3. concept_neighborhood echoes the input `kind` filter.
  4. concept_neighborhood with kind=domain-primitive returns only
     domain primitives.
  5. concept_neighborhood without filter still attaches per-neighbor
     `kind` / `broader` when known.
"""
from __future__ import annotations

import pytest

from frontend.mcp_server import dispatch
from frontend.mcp_server.handlers import ToolError


_KIND_LITERALS = frozenset((
    "domain-primitive", "structural-primitive", "relational-primitive",
))


def _pick_typed_concept(live_bundle) -> str | None:
    """Return any concept name whose record carries a `kind`."""
    for name, meta in live_bundle.concepts.get("concepts", {}).items():
        if "kind" in meta:
            return name
    return None


def _pick_untyped_concept(live_bundle) -> str | None:
    """Return any concept name whose record lacks `kind`."""
    for name, meta in live_bundle.concepts.get("concepts", {}).items():
        if "kind" not in meta:
            return name
    return None


def test_concept_detail_surfaces_kind_and_broader(live_bundle, bundle_name):
    name = _pick_typed_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no curated-vocab concepts "
                    "(rebuild with Stage 4+ to enable this test)")
    p = dispatch("concept_detail", {"bundle": bundle_name, "name": name})
    c = p["concept"]
    assert c.get("kind") in _KIND_LITERALS, (
        f"expected curated kind, got {c.get('kind')!r}"
    )
    assert isinstance(c.get("broader"), str) and c["broader"], (
        f"expected non-empty broader, got {c.get('broader')!r}"
    )


def test_concept_detail_omits_typing_for_uncurated(live_bundle, bundle_name):
    name = _pick_untyped_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no uncurated concepts to test against")
    p = dispatch("concept_detail", {"bundle": bundle_name, "name": name})
    c = p["concept"]
    assert "kind" not in c, f"unexpected kind on uncurated concept: {c}"
    assert "broader" not in c, f"unexpected broader on uncurated concept: {c}"


def test_concept_neighborhood_echoes_kind_filter(live_bundle, bundle_name):
    name = _pick_typed_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no curated-vocab concepts")
    p = dispatch("concept_neighborhood", {
        "bundle": bundle_name, "name": name,
        "kind": "domain-primitive", "limit": 50,
    })
    assert p.get("kind_filter") == "domain-primitive"


def test_concept_neighborhood_kind_filter_constrains_results(
    live_bundle, bundle_name,
):
    """Every neighbor returned with a kind filter must carry that kind."""
    name = _pick_typed_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no curated-vocab concepts")
    p = dispatch("concept_neighborhood", {
        "bundle": bundle_name, "name": name,
        "kind": "domain-primitive", "limit": 100, "depth": 2,
    })
    # All returned neighbors must be domain-primitive; the filter is
    # the only thing that decides emission.
    bad = [n for n in p["neighbors"] if n.get("kind") != "domain-primitive"]
    assert not bad, f"filter leaked uncurated/mistyped neighbors: {bad[:3]}"


def test_concept_neighborhood_unfiltered_attaches_kind_per_neighbor(
    live_bundle, bundle_name,
):
    """Without a filter, every neighbor whose underlying concept is
    typed should still carry that typing in the row."""
    name = _pick_typed_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no curated-vocab concepts")
    p = dispatch("concept_neighborhood", {
        "bundle": bundle_name, "name": name,
        "depth": 2, "limit": 100,
    })
    # The unfiltered response must not echo a kind_filter.
    assert "kind_filter" not in p
    # At least one neighbor in a real bundle should carry typing,
    # otherwise the round-trip isn't being exercised.
    typed = [n for n in p["neighbors"] if "kind" in n]
    assert typed, "no typed neighbors surfaced — wiring may be broken"
    # Every typed neighbor's kind must be a legal literal.
    for n in typed:
        assert n["kind"] in _KIND_LITERALS, n


def test_concept_neighborhood_unknown_kind_rejected_by_schema(
    live_bundle, bundle_name,
):
    """The input schema's enum should reject a junk kind value before
    the handler ever runs. validate_in raises jsonschema.ValidationError
    (the protocol layer maps that to a JSON-RPC `invalid params` error)."""
    from jsonschema import ValidationError
    name = _pick_typed_concept(live_bundle)
    if name is None:
        pytest.skip("bundle has no curated-vocab concepts")
    with pytest.raises(ValidationError) as exc:
        dispatch("concept_neighborhood", {
            "bundle": bundle_name, "name": name, "kind": "not-a-real-kind",
        })
    # The error must mention the offending value or the `kind` path.
    msg = str(exc.value)
    assert "not-a-real-kind" in msg or "kind" in msg

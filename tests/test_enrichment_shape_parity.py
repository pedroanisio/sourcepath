"""LlmEnrichment shape parity across its Python restatements (drift-risk H5).

The enrichment record shape is authored once — `plugins/llm_enrich/artifact.py`
writes enrichments.jsonl — and restated by hand in the MCP surface:
`handlers.py::_llm_payload` projects a row to `{text, provenance{...}}` and
`schemas.py::_LLM_ENRICHMENT` advertises that shape. The coupling was
documented (docs/llm-enrich.md) but unenforced: adding or renaming a field
required synchronized edits in every layer, and a missed edit silently
dropped the field in that layer.

Pinned here:

- the artifact writer's record keys (via the real `_iter_records` path, not
  a hand-written copy);
- `_llm_payload` reads only keys the writer produces, and its output
  validates against `_LLM_ENRICHMENT`;
- `_LLM_ENRICHMENT` declares exactly the payload's shape, closed at both
  levels (additionalProperties: false), every property required.

The fourth restatement — api.ts::LlmEnrichment — is pinned by
tests/verify_api_field_parity.py.

Run from the repo root:  uv run python -m pytest tests/test_enrichment_shape_parity.py
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("jsonschema")
from jsonschema import validate  # noqa: E402

from plugins.llm_enrich.artifact import _iter_records  # noqa: E402
from frontend.mcp_server.handlers import _llm_payload  # noqa: E402
from frontend.mcp_server.schemas import _LLM_ENRICHMENT  # noqa: E402

_SOURCE_REC = {
    "text": "Summarizes the module.",
    "model": "qwen2.5-coder:7b",
    "prompt_sha": "a" * 64,
    "target_sha": "b" * 64,
    "generated_at": "2026-07-10T00:00:00Z",
}


def _written_record() -> dict:
    """One record as the REAL artifact writer emits it (no hand copy)."""
    ctx = SimpleNamespace(scratch={"llm:file_summary": {"app.py": _SOURCE_REC}})
    rows = _iter_records(ctx)
    assert len(rows) == 1
    return rows[0]


def test_artifact_record_carries_the_documented_keys():
    assert set(_written_record()) == {
        "target", "kind", "text", "model",
        "prompt_sha", "target_sha", "generated_at",
    }


def test_llm_payload_reads_only_writer_produced_keys():
    record = _written_record()
    payload = _llm_payload(record)
    assert set(payload) == {"text", "provenance"}
    # every projected value must originate from a writer-produced key
    assert payload["text"] == record["text"]
    for key, value in payload["provenance"].items():
        assert key in record, f"_llm_payload reads {key!r}, writer never emits it"
        assert value == record[key]


def test_payload_validates_against_advertised_schema():
    validate(_llm_payload(_written_record()), _LLM_ENRICHMENT)


def test_schema_is_closed_and_exactly_the_payload_shape():
    payload = _llm_payload(_written_record())
    assert _LLM_ENRICHMENT["additionalProperties"] is False
    assert set(_LLM_ENRICHMENT["properties"]) == set(payload)
    assert set(_LLM_ENRICHMENT["required"]) == set(payload)
    prov = _LLM_ENRICHMENT["properties"]["provenance"]
    assert prov["additionalProperties"] is False
    assert set(prov["properties"]) == set(payload["provenance"])
    assert set(prov["required"]) == set(payload["provenance"])

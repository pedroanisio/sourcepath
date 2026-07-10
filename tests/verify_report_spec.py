#!/usr/bin/env python3
"""verify_report_spec.py — regression guard for the declarative reporting contract.

docs/reporting/report-spec.schema.json is the machine-checkable half of the
reporting view model (docs/reporting/reporting-view-model.md). Until an
executor exists, the contract itself is the shipped artifact — so it gets the
same drift protection as any other emitted schema:

  1. the schema is a valid JSON Schema draft 2020-12 document (check_schema);
  2. both shipped examples validate (ReportSpec + ResultEnvelope fragment);
  3. representative invalid specs are rejected — closed catalog, UUID shape,
     missing provenance, unknown top-level keys, renderer/shape mismatch,
     LLM narrative without model attribution, text_cards without provenance;
  4. the 30-component catalog in reporting-view-model.md and the schema's
     component_id enum are the same set, numbered 1..30 (doc <-> contract
     drift guard);
  5. the epistemic knobs stay pinned: disclaimer_mode offers exactly
     {default_notice, evidence_basis_banner} and defaults to the banner;
     evidence_tier stays the closed {fact, derived, unverified} set.

Run from the repo root:  python tests/verify_report_spec.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTING = ROOT / "docs" / "reporting"
SCHEMA_PATH = REPORTING / "report-spec.schema.json"
DOC_PATH = REPORTING / "reporting-view-model.md"
SPEC_EXAMPLE = REPORTING / "examples" / "due-diligence-view.report-spec.json"
ENVELOPE_EXAMPLE = (
    REPORTING / "examples" / "language-distribution.result-envelope.json"
)

# **N. `component-id`** — the catalog entry form used by the view-model doc.
_CATALOG_ENTRY = re.compile(r"^\*\*(\d+)\.\s+`([a-z0-9-]+)`\*\*", re.MULTILINE)

_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "ok" if ok else "FAIL"
    print(f"== {label} == {status}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def main(argv: list[str] | None = None) -> int:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ModuleNotFoundError:
        print("jsonschema is required: run via `uv run python "
              "tests/verify_report_spec.py` (it is in the project lock).",
              file=sys.stderr)
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    # --- 1. the schema itself is valid ------------------------------------
    try:
        Draft202012Validator.check_schema(schema)
        check("schema is a valid draft 2020-12 document", True)
    except SchemaError as exc:
        check("schema is a valid draft 2020-12 document", False, str(exc))
        return _finish()

    spec_validator = Draft202012Validator(schema)
    # Envelope instances validate against the fragment; a bare {"$ref": ...}
    # wrapper keeps the ReportSpec top-level keywords out of the evaluation.
    envelope_validator = Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": "#/$defs/ResultEnvelope",
        }
    )

    def errors(validator, instance) -> list[str]:
        return [e.message for e in validator.iter_errors(instance)]

    # --- 2. shipped examples validate --------------------------------------
    spec = json.loads(SPEC_EXAMPLE.read_text(encoding="utf-8"))
    errs = errors(spec_validator, spec)
    check(f"example validates: {SPEC_EXAMPLE.name}", not errs, "; ".join(errs[:3]))

    envelope = json.loads(ENVELOPE_EXAMPLE.read_text(encoding="utf-8"))
    errs = errors(envelope_validator, envelope)
    check(f"example validates: {ENVELOPE_EXAMPLE.name}", not errs,
          "; ".join(errs[:3]))

    # --- 3. representative invalid instances are rejected ------------------
    def rejected(label: str, mutate) -> None:
        bad = copy.deepcopy(spec)
        mutate(bad)
        check(f"rejects {label}", bool(errors(spec_validator, bad)))

    rejected("component_id outside the closed catalog",
             lambda s: s["blocks"][0].__setitem__("component_id", "bogus-component"))
    rejected("malformed report_id (not a lowercase UUID)",
             lambda s: s.__setitem__("report_id", "not-a-uuid"))
    rejected("missing authoring provenance",
             lambda s: s.pop("provenance"))
    rejected("unknown top-level property",
             lambda s: s.__setitem__("surprise", True))
    rejected("renderer incompatible with the component's result shape",
             lambda s: s["blocks"][0].update(
                 component_id="repo-overview", params={}, renderer="sankey"))
    rejected("LLM narrative without model attribution",
             lambda s: s["blocks"][0].__setitem__(
                 "narrative", {"text": "x", "authored_by": "llm"}))

    bad_env = copy.deepcopy(envelope)
    bad_env["evidence_tier"] = "unverified"
    bad_env["data"] = {"shape": "text_cards",
                       "cards": [{"target_id": "a", "title": "t",
                                  "body_text": "b"}]}
    check("rejects text_cards without model/prompt_sha/generated_at provenance",
          bool(errors(envelope_validator, bad_env)))

    # --- 4. doc catalog <-> schema enum drift guard ------------------------
    doc = DOC_PATH.read_text(encoding="utf-8")
    entries = _CATALOG_ENTRY.findall(doc)
    doc_numbers = [int(n) for n, _ in entries]
    doc_ids = [cid for _, cid in entries]
    enum_ids = schema["$defs"]["ReportBlock"]["properties"]["component_id"]["enum"]

    check("doc catalog is numbered 1..30 with no gaps",
          doc_numbers == list(range(1, 31)),
          f"got {doc_numbers[:5]}... ({len(doc_numbers)} entries)")
    check("doc catalog ids are unique", len(set(doc_ids)) == len(doc_ids))
    missing = set(enum_ids) - set(doc_ids)
    extra = set(doc_ids) - set(enum_ids)
    check("doc catalog == schema component_id enum", not missing and not extra,
          f"schema-only: {sorted(missing)}; doc-only: {sorted(extra)}")
    check("oneOf carries one branch per catalog component",
          len(schema["$defs"]["ReportBlock"]["oneOf"]) == len(enum_ids))

    # --- 5. epistemic knobs stay pinned -------------------------------------
    dm = schema["properties"]["disclaimer_mode"]
    check("disclaimer_mode enum is exactly {default_notice, evidence_basis_banner}",
          sorted(dm["enum"]) == ["default_notice", "evidence_basis_banner"])
    check("disclaimer_mode defaults to evidence_basis_banner",
          dm.get("default") == "evidence_basis_banner")
    check("evidence_tier stays the closed {fact, derived, unverified} set",
          sorted(schema["$defs"]["EvidenceTier"]["enum"])
          == ["derived", "fact", "unverified"])

    return _finish()


def _finish() -> int:
    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed: {', '.join(_FAILURES)}",
              file=sys.stderr)
        return 1
    print("\nall report-spec contract checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

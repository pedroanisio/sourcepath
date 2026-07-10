#!/usr/bin/env python3
"""verify_api_field_parity.py — backend Pydantic <-> api.ts field parity.

drift-risk-map C1: `frontend/ui/src/api.ts` hand-mirrors the backend's
response models field-by-field. The previous guard (`verify_drift_p1` finding
#9) checked tool-name *presence* only, so renaming a field on either side
passed every test while the UI rendered `undefined` in production.

This verifier does the deferred field-level check without a codegen pipeline:

  1. parse every `export interface` in api.ts (brace-aware, including nested
     inline object literals like `FileDetail.file` and `Array<{...}>` rows);
  2. import the live FastAPI app and read each Pydantic model's declared
     fields;
  3. assert exact field-name equality per mapped pair, in both directions.
     TS-only fields must be on the documented allowlist (the L4 decoration
     fields, added via extra="allow" and declared optional in TS);
  4. the `LlmEnrichment` payload shape is pinned against its single builder
     (`frontend/mcp_server/handlers.py::_llm_payload`);
  5. a parse floor guards the TS parser against silently matching nothing.

Run from the repo root:  uv run python tests/verify_api_field_parity.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "frontend" / "backend"
API_TS = REPO_ROOT / "frontend" / "ui" / "src" / "api.ts"
sys.path.insert(0, str(BACKEND_DIR))

# TS interface (or nested path) -> backend model attribute name on app.py.
TOP_LEVEL = {
    "Summary": "SummaryResp",
    "GraphNode": "GraphNode",
    "GraphEdge": "GraphEdge",
    "GraphResp": "GraphResp",
    "ChunkRow": "ChunkResp",
    "ChunkListResp": "ChunkListResp",
    "BundleInfo": "BundleInfo",
    "BundleListResp": "BundleListResp",
    "FileImpact": "ImpactResp",
    "FileDetail": "FileDetailResp",
    "ConceptDetail": "ConceptDetailResp",
    "ChunkDetail": "ChunkDetailResp",
}
NESTED = {
    "FileDetail.file": "_FileBlock",
    "FileDetail.chunks": "_FileChunk",
    "ConceptDetail.concept": "_ConceptInfo",
    "ConceptDetail.cooccurring": "_CooccurringConcept",
    "ConceptDetail.chunks": "_ConceptChunk",
    "ChunkDetail.chunk": "_ChunkBlock",
}

# L4 decoration fields: the backend adds them by dict mutation under
# extra="allow" (MCP layer today; REST decoration would reuse the same
# sidecar), so they are TS-declared but not Pydantic-declared — by design.
TS_ONLY_ALLOWED = {
    "FileDetail": {"llm_summary", "llm_schema_purpose"},
    "ConceptDetail": {"llm_description"},
}

MIN_INTERFACES = 12

# No ^ anchor: pattern.match(body, i) already anchors at i, and fields must
# also match mid-line after a `;` (single-line inline literals).
_FIELD_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:")
_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"== {label} == {'ok' if ok else 'FAIL'}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _balanced(text: str, open_idx: int) -> int:
    """Index just past the brace that closes text[open_idx] == '{'."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unbalanced braces in api.ts")


def _fields_of(body: str, path: str, out: dict[str, set[str]]) -> None:
    """Collect field names at depth 0 of an interface body; recurse into
    nested inline object literals (both `f: {…}` and `f: Array<{…}>`)."""
    out.setdefault(path, set())
    i = 0
    while i < len(body):
        # next field name at current depth
        m = _FIELD_NAME.match(body, i)
        nl = body.find("\n", i)
        if m:
            name = m.group(1)
            out[path].add(name)
            # type runs to the ';' at depth 0 — walk it, diving into '{'
            j = m.end()
            while j < len(body):
                ch = body[j]
                if ch == "{":
                    end = _balanced(body, j)
                    _fields_of(body[j + 1:end - 1], f"{path}.{name}", out)
                    j = end
                    continue
                if ch == ";":
                    break
                j += 1
            i = j + 1
        else:
            i = (nl + 1) if nl != -1 else len(body)


def parse_api_ts(text: str) -> dict[str, set[str]]:
    text = _strip_comments(text)
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"export\s+interface\s+([A-Za-z0-9_]+)\s*\{", text):
        end = _balanced(text, m.end() - 1)
        _fields_of(text[m.end():end - 1], m.group(1), out)
    return out


def main() -> int:
    import app as backend  # frontend/backend/app.py

    ts = parse_api_ts(API_TS.read_text(encoding="utf-8"))
    top_level_count = len([k for k in ts if "." not in k])
    check(f"api.ts parse floor ({top_level_count} interfaces, "
          f"need >= {MIN_INTERFACES})", top_level_count >= MIN_INTERFACES)

    for ts_name, model_name in {**TOP_LEVEL, **NESTED}.items():
        model = getattr(backend, model_name, None)
        if model is None:
            check(f"{ts_name} <-> {model_name}", False,
                  f"backend model {model_name} not found on app.py")
            continue
        if ts_name not in ts:
            check(f"{ts_name} <-> {model_name}", False,
                  f"interface/path {ts_name} not found in api.ts")
            continue
        py_fields = set(model.model_fields.keys())
        allowed = TS_ONLY_ALLOWED.get(ts_name, set())
        ts_fields = ts[ts_name] - allowed
        ts_only = sorted(ts_fields - py_fields)
        py_only = sorted(py_fields - ts_fields)
        check(f"{ts_name} <-> {model_name}: field parity",
              not ts_only and not py_only,
              f"ts-only={ts_only} py-only={py_only}")

    # L4 decoration allowlist must stay real: each allowed field is actually
    # produced somewhere (today: the MCP surface) — no stale allowlist.
    surface_src = (REPO_ROOT / "frontend" / "mcp_server" / "handlers.py"
                   ).read_text(encoding="utf-8")
    for iface, fields in TS_ONLY_ALLOWED.items():
        for f in sorted(fields):
            check(f"allowlisted {iface}.{f} is produced by a backend surface",
                  f'"{f}"' in surface_src)

    # The LlmEnrichment payload shape has exactly one builder: _llm_payload.
    llm_fields = ts.get("LlmEnrichment", set())
    prov_fields = ts.get("LlmEnrichment.provenance", set())
    check("LlmEnrichment declares {text, provenance}",
          llm_fields == {"text", "provenance"}, f"got {sorted(llm_fields)}")
    for f in sorted({"model", "prompt_sha", "target_sha", "generated_at"}):
        check(f"LlmEnrichment.provenance.{f} built by _llm_payload",
              f in prov_fields and f'"{f}"' in surface_src)

    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall API field-parity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

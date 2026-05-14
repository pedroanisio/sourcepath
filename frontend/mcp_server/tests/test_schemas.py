"""Phase 1 contract tests for the MCP server schemas.

Exit criterion (from the phase plan):
    every tool name in the design appears in both dicts;
    jsonschema.Draft202012Validator.check_schema(s) passes for all

We also exercise representative valid/invalid payloads to catch
schema bugs (e.g. forgetting ``additionalProperties: false``) and to
document the contract for handler authors.
"""
from __future__ import annotations

import re

import pytest
from jsonschema import Draft202012Validator, ValidationError

from frontend.mcp_server.schemas import (
    DESCRIPTIONS,
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    RESOURCE_URI_TEMPLATES,
    TOOL_NAMES,
    validate_in,
    validate_out,
)


# --------------------------------------------------------------------------
# Well-formedness — every schema parses as Draft 2020-12
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", sorted(INPUT_SCHEMAS))
def test_input_schema_is_valid_draft_2020_12(tool):
    Draft202012Validator.check_schema(INPUT_SCHEMAS[tool])


@pytest.mark.parametrize("tool", sorted(OUTPUT_SCHEMAS))
def test_output_schema_is_valid_draft_2020_12(tool):
    Draft202012Validator.check_schema(OUTPUT_SCHEMAS[tool])


# --------------------------------------------------------------------------
# Completeness — every tool has both halves and a description
# --------------------------------------------------------------------------

def test_every_tool_has_both_halves():
    assert set(INPUT_SCHEMAS) == set(OUTPUT_SCHEMAS), (
        "tools listed in INPUT_SCHEMAS but missing from OUTPUT_SCHEMAS: "
        f"{set(INPUT_SCHEMAS) - set(OUTPUT_SCHEMAS)}; "
        "and vice versa: "
        f"{set(OUTPUT_SCHEMAS) - set(INPUT_SCHEMAS)}"
    )


def test_every_tool_has_a_description():
    missing = set(INPUT_SCHEMAS) - set(DESCRIPTIONS)
    assert not missing, f"tools missing a model-selection description: {sorted(missing)}"


def test_tool_names_are_sorted_and_unique():
    assert TOOL_NAMES == tuple(sorted(set(TOOL_NAMES)))


@pytest.mark.parametrize("name", sorted(INPUT_SCHEMAS))
def test_tool_name_charset_matches_mcp_spec(name):
    # MCP spec: ASCII letters, digits, _, -, and . — no spaces or other chars
    assert re.fullmatch(r"[A-Za-z0-9._-]+", name), name


# --------------------------------------------------------------------------
# Strictness — every input/output object rejects unknown properties
# --------------------------------------------------------------------------

def _walk_objects(schema):
    """Yield every object-typed (sub)schema."""
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if t == "object" or ("properties" in schema and t in (None, "object")):
        yield schema
    for value in schema.values():
        if isinstance(value, dict):
            yield from _walk_objects(value)
        elif isinstance(value, list):
            for item in value:
                yield from _walk_objects(item)


def _ap_is_explicit(obj):
    """An object's ``additionalProperties`` is explicit (intentional)
    when it's set to False, True, or a value schema. Missing/None means
    JSON Schema's lax default — which we forbid except by deliberate opt-in.
    """
    ap = obj.get("additionalProperties", None)
    return ap is False or ap is True or isinstance(ap, dict)


@pytest.mark.parametrize("tool", sorted(INPUT_SCHEMAS))
def test_input_objects_forbid_unknown_properties(tool):
    for obj in _walk_objects(INPUT_SCHEMAS[tool]):
        assert _ap_is_explicit(obj), (
            f"input schema for {tool} has an object with implicit "
            f"additionalProperties (must be explicit): {obj}"
        )
        # Inputs in particular should never allow arbitrary keys silently.
        assert obj.get("additionalProperties") is not True, (
            f"input schema for {tool} explicitly allows arbitrary args: {obj}"
        )


@pytest.mark.parametrize("tool", sorted(OUTPUT_SCHEMAS))
def test_output_objects_have_explicit_additional_properties_policy(tool):
    for obj in _walk_objects(OUTPUT_SCHEMAS[tool]):
        assert _ap_is_explicit(obj), (
            f"output schema for {tool} has an object with implicit "
            f"additionalProperties (must be explicit): {obj}"
        )


# --------------------------------------------------------------------------
# Representative valid / invalid payloads
# --------------------------------------------------------------------------

VALID_INPUTS: dict[str, dict] = {
    "orient_bundle": {},
    "bundle_summary": {},
    "repository_summary": {"central_files_limit": 5},
    "list_bundles": {},
    "select_bundle": {"bundle": "alpha"},
    "list_files": {"language": "python", "limit": 10},
    "file_detail": {"path": "src/app.py"},
    "file_impact": {"path": "src/app.py", "depth": 3},
    "imports_of": {"path": "src/app.py"},
    "imported_by": {"path": "src/app.py"},
    "chunk_detail": {"idx": 42},
    "chunk_blob": {"sha": "a" * 64},
    "list_chunks": {"q": "schema", "limit": 25},
    "semantic_neighbors": {"q": "authentication flow", "k": 10},
    "concept_detail": {"name": "schema"},
    "concept_neighborhood": {"name": "schema", "depth": 2, "limit": 30},
    "sparql": {"query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1"},
    "items_by_attribute": {"pattern": "#[test]", "kind": "function"},
}

# (tool, bad_payload, expected error fragment)
INVALID_INPUTS: list[tuple[str, dict, str]] = [
    ("select_bundle", {}, "bundle"),  # missing required
    ("select_bundle", {"bundle": "a/b"}, "pattern"),  # slash rejected
    ("select_bundle", {"bundle": ""}, "minLength"),
    ("file_detail", {}, "path"),  # required missing
    ("file_impact", {"path": "x.py", "depth": 10}, "maximum"),
    ("chunk_detail", {"idx": -1}, "minimum"),
    ("chunk_blob", {"sha": "abc"}, "pattern"),
    ("chunk_blob", {"sha": "g" * 64}, "pattern"),
    ("concept_neighborhood", {"name": "x", "depth": 4}, "maximum"),
    ("semantic_neighbors", {"q": "ok", "k": 0}, "minimum"),
    ("semantic_neighbors", {"q": "ok", "k": 5, "garbage": True}, "Additional"),
    ("list_files", {"sort": "alphabetical"}, "enum"),
]


@pytest.mark.parametrize("tool,args", sorted(VALID_INPUTS.items()))
def test_valid_inputs_pass(tool, args):
    validate_in(tool, args)


@pytest.mark.parametrize("tool,args,fragment", INVALID_INPUTS)
def test_invalid_inputs_raise(tool, args, fragment):
    with pytest.raises(ValidationError) as excinfo:
        validate_in(tool, args)
    assert fragment.lower() in str(excinfo.value).lower(), (
        f"expected error mentioning {fragment!r}; got: {excinfo.value}"
    )


# --------------------------------------------------------------------------
# Representative output payloads conform to outputSchema
# --------------------------------------------------------------------------

VALID_OUTPUTS: dict[str, dict] = {
    "orient_bundle": {
        "bundle": {"name": "alpha", "path": "/data/alpha"},
        "schema_hint": {
            "namespaces": {"cbm": "https://codebase-mapper.example.org/cbm#"},
            "layers": [
                {"name": "L1", "purpose": "host", "key_predicates": ["cbm:imports"]}
            ],
        },
        "suggested_first_calls": [
            {"tool": "bundle_summary", "args": {}, "why": "counts + lang breakdown"}
        ],
    },
    "bundle_summary": {
        "counts": {"files": 100},
        "files_by_language": {"python": 60},
        "files_by_type": {"source_code": 80},
        "n_chunks": 200,
        "n_concepts": 50,
        "output_dir": "/data/alpha",
    },
    "repository_summary": {
        "bundle": {"name": "alpha", "path": "/data/alpha"},
        "total_files": 100,
        "total_chunks": 200,
        "total_concepts": 50,
        "files_by_language": {"python": 60},
        "files_by_type": {"source_code": 80},
        "central_files": [{
            "path": "src/app.py",
            "import_degree": 7,
            "imports_out": 3,
            "imports_in": 4,
            "language": "python",
            "type": "source_code",
            "size": 1024,
            # Optional L4 field (Step 7).
            "llm_summary": "Defines the application entry point.",
        }],
        "entry_points": [{
            "path": "src/__main__.py",
            "kind": "python_main",
            "language": "python",
        }],
        "key_concepts": [{
            "name": "schema",
            "frequency": 5,
            "file_count": 2,
            "kind": "structural-primitive",
            "broader": "code_structure",
            "llm_description": "Schema concept describing entity contracts.",
        }],
        "dependency_summary": {
            "internal_imports": 12,
            "external_imports": 5,
            "declares_dependency": 2,
            "pins_dependency": 2,
        },
        "test_coverage_hint": {
            "test_files": 20,
            "source_files": 80,
            "ratio": 0.25,
            "tests_edges": 15,
        },
    },
    "list_bundles": {
        "bundles": [{"name": "alpha", "path": "/data/alpha", "files": 10}],
        "selected": "alpha",
        "bundles_root": "/data",
    },
    "select_bundle": {"selected": "alpha"},
    "list_files": {
        "files": [{"path": "src/app.py", "language": "python"}],
        "total": 1,
    },
    "file_detail": {
        "file": {"path": "src/app.py"},
        "imports_out": ["src/lib.py"],
        "imports_in": [],
        "chunks": [{"idx": 0}],
        "concepts": ["schema"],
    },
    "file_impact": {
        "file": "src/app.py",
        "depth": 2,
        "direct_dependencies": ["src/lib.py"],
        "direct_dependents": [],
        "transitive_dependencies": ["src/lib.py", "src/util.py"],
        "transitive_dependents": [],
    },
    "imports_of": {"file": "src/app.py", "imports": ["src/lib.py"]},
    "imported_by": {"file": "src/lib.py", "imported_by": ["src/app.py"]},
    "chunk_detail": {
        "chunk": {"idx": 0, "symbol": "main", "file": "src/app.py"},
        "concepts": ["schema"],
        "blob_preview": "def main(): ...",
    },
    "chunk_blob": {"sha256": "a" * 64, "text": "..."},
    "list_chunks": {"chunks": [{"idx": 0}], "total": 1, "mode": "lexical"},
    "semantic_neighbors": {
        "chunks": [{"idx": 0, "score": 0.91}],
        "total": 1,
        "backend": "sentence-transformers/all-MiniLM-L6-v2",
        "mode": "semantic",
    },
    "concept_detail": {
        "concept": {"label": "schema", "frequency": 5, "file_count": 2},
        "files": ["src/app.py"],
        "cooccurring": [{"name": "auth", "weight": 3}],
        "chunks": [{"idx": 0}],
        "components": [],
    },
    "concept_neighborhood": {
        "root": "schema",
        "neighbors": [{"name": "auth", "weight": 3, "depth": 1}],
    },
    "sparql": {
        "columns": ["s", "p", "o"],
        "rows": [{"s": "http://x", "p": "http://x/p", "o": "v"}],
        "row_count": 1,
        "truncated": False,
        "query_form": "SELECT",
        "ask_result": None,
    },
    "items_by_attribute": {
        "items": [{
            "path": "src/lib.rs",
            "kind": "function",
            "name": "test_thing",
            "parent": None,
            "line_start": 10,
            "line_end": 20,
            "is_pub": False,
            "is_async": False,
            "attributes": ["#[test]"],
        }],
        "total": 1,
    },
}


@pytest.mark.parametrize("tool,payload", sorted(VALID_OUTPUTS.items()))
def test_valid_outputs_conform(tool, payload):
    validate_out(tool, payload)


def test_every_tool_has_a_sample_output():
    """Tests for output schemas are only useful with real payloads. Catch
    the case where a new tool is added but no sample exists yet."""
    missing = set(OUTPUT_SCHEMAS) - set(VALID_OUTPUTS)
    assert not missing, f"missing sample outputs for: {sorted(missing)}"


def test_every_tool_has_a_sample_input():
    missing = set(INPUT_SCHEMAS) - set(VALID_INPUTS)
    assert not missing, f"missing sample inputs for: {sorted(missing)}"


# --------------------------------------------------------------------------
# Resource URI templates
# --------------------------------------------------------------------------

def test_resource_uri_templates_use_cbm_scheme():
    for key, spec in RESOURCE_URI_TEMPLATES.items():
        uri = spec["uri"]
        assert uri.startswith("cbm://"), f"{key} URI doesn't use cbm:// scheme: {uri}"


def test_resource_uri_template_placeholders_are_named():
    # Every {placeholder} must use a-z0-9_ — no positional or unnamed slots.
    placeholder_re = re.compile(r"\{([^{}]*)\}")
    for key, spec in RESOURCE_URI_TEMPLATES.items():
        for match in placeholder_re.findall(spec["uri"]):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", match), (
                f"{key} URI placeholder {match!r} should be lowercase_underscore"
            )


def test_subscribable_resources_only_include_manifest():
    """Only the manifest is subscribable in v1 (per design)."""
    subs = {
        k for k, spec in RESOURCE_URI_TEMPLATES.items() if spec.get("subscribable")
    }
    assert subs == {"bundle_manifest"}, subs


# --------------------------------------------------------------------------
# Cross-checks: read-only-ness, no surprising tool names
# --------------------------------------------------------------------------

WRITE_VERBS = ("create_", "delete_", "update_", "send_", "deploy_", "write_", "edit_")


@pytest.mark.parametrize("name", sorted(INPUT_SCHEMAS))
def test_no_write_verbs_in_tool_names(name):
    """v1 is strictly read-only. Catch accidental write tools by name."""
    assert not any(name.startswith(v) for v in WRITE_VERBS), name

"""JSON Schemas for the MCP server's tool surface (Phase 1).

This module is the contract — handlers (Phase 2) and the protocol layer
(Phase 3) both consume it. Each tool has:

* an entry in ``INPUT_SCHEMAS`` describing accepted arguments
* an entry in ``OUTPUT_SCHEMAS`` describing the ``structuredContent`` payload
* a one-line ``DESCRIPTIONS`` blurb suitable for the MCP tool description

Resources are addressed by URI templates in ``RESOURCE_URI_TEMPLATES``.

Design notes:

* All schemas use JSON Schema Draft 2020-12.
* Strict mode by default: ``additionalProperties: false`` on every object,
  bounded ints, enum constraints where applicable.
* Patterns are intentionally permissive at the schema layer because JSON
  Schema regex is uneven across validators; deep validation (path
  traversal, bundle-name shape) lives in ``validators.py`` and is applied
  *in addition* to the schema check.
* All tools are read-only.
"""
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# Common building blocks
# --------------------------------------------------------------------------

# A "loose" string that the handler will run through the deeper validator
# (rejects ``..``, leading dots, absolute paths). The schema layer just
# imposes a length cap so a 10MB string can't reach the handler.
_BUNDLE_NAME: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z0-9._-]+$",
}

_FILE_PATH: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 4096,
    "description": (
        "Bundle-relative file path (POSIX separators). Path traversal "
        "(``..``) and absolute paths are rejected by the handler."
    ),
}

_CONCEPT_NAME: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
}

_SHA256: dict[str, Any] = {
    "type": "string",
    "pattern": r"^[a-f0-9]{64}$",
}

_INT_GE_0: dict[str, Any] = {"type": "integer", "minimum": 0}

# Object that's used in many output schemas — a chunk row condensed for
# list endpoints.
_CHUNK_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["idx"],
    "properties": {
        "idx": {"type": "integer", "minimum": 0},
        "uri": {"type": "string"},
        "symbol": {"type": ["string", "null"]},
        "kind": {"type": ["string", "null"]},
        "file": {"type": ["string", "null"]},
        "beginLine": {"type": ["integer", "null"]},
        "endLine": {"type": ["integer", "null"]},
        "embeddingRow": {"type": ["integer", "null"]},
        "contentSha256": {"type": ["string", "null"]},
        "score": {"type": ["number", "null"]},
    },
}

_FILE_RECORD: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "uri": {"type": "string"},
        "language": {"type": ["string", "null"]},
        "type": {"type": ["string", "null"]},
        "size": {"type": ["integer", "null"]},
        "contentSha256": {"type": ["string", "null"]},
    },
}

_BUNDLE_INFO: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "path"],
    "properties": {
        "name": {"type": "string"},
        "path": {"type": "string"},
        "repo_name": {"type": ["string", "null"]},
        "commit_sha": {"type": ["string", "null"]},
        "generated_at": {"type": ["string", "null"]},
        "tool_version": {"type": ["string", "null"]},
        "files": {"type": ["integer", "null"]},
    },
}


def _bundle_arg() -> dict[str, Any]:
    """Optional ``bundle`` argument re-used by every tool."""
    return {
        "bundle": {
            **_BUNDLE_NAME,
            "description": (
                "Optional bundle name. When omitted, the session's selected "
                "bundle (set via ``select_bundle``) or the server's default "
                "is used."
            ),
        }
    }


# --------------------------------------------------------------------------
# Tool descriptions (model-selection hints)
# --------------------------------------------------------------------------

DESCRIPTIONS: dict[str, str] = {
    "orient_bundle": (
        "FIRST CALL after connecting. Returns the active bundle's metadata, "
        "a one-screen layer/namespace cheat sheet, and a suggested first set "
        "of tool calls. Use this to learn the shape of the data before "
        "running other queries."
    ),
    "bundle_summary": (
        "Active bundle's run manifest: file counts, language/type histogram, "
        "embeddings backend, SHACL conformance. Cheap; safe to call any time."
    ),
    "repository_summary": (
        "One-shot executive read of the active bundle: language/type histograms, "
        "the most-connected files by import degree, detected entry points, top "
        "concepts by frequency (with controlled-vocab kind when present), "
        "dependency-edge counts, and a test-coverage hint. Use as the first "
        "deep call after ``select_bundle`` when an agent wants the gist of the "
        "repo in a single response — replaces a chain of ``bundle_summary`` + "
        "``list_files`` + ``concept_detail`` calls."
    ),
    "list_bundles": (
        "List every bundle available under the server's bundles root. Use to "
        "decide which bundle to ``select_bundle``."
    ),
    "select_bundle": (
        "Set the session's active bundle. Subsequent tool calls without an "
        "explicit ``bundle`` argument target this one."
    ),
    "list_files": (
        "Browse files filtered by language, type, or directory prefix. "
        "Paginated and ranked by import-degree by default. Use to explore "
        "modules without loading the full graph."
    ),
    "file_detail": (
        "Inspect a single file: metadata, imports (both directions), tests "
        "that exercise it, chunks, and concepts it lexicalizes. Use when you "
        "have a known path and want everything about it."
    ),
    "file_impact": (
        "Transitive dependency closure for a file up to ``depth``: every "
        "file it (in)directly imports + every file that (in)directly imports "
        "it, plus the related tests. Use to scope the blast radius of a "
        "change."
    ),
    "imports_of": (
        "Direct outgoing imports from a file (one hop). Use when you only "
        "need the immediate dependencies; for deeper, use ``file_impact``."
    ),
    "imported_by": (
        "Direct incoming imports (one hop). Use to find immediate callers; "
        "for deeper, use ``file_impact``."
    ),
    "chunk_detail": (
        "Inspect a chunk by its bundle-stable ``idx``: source preview "
        "(file-level chunks only), parent file, concepts."
    ),
    "chunk_blob": (
        "Read raw blob text by SHA-256 (up to 20 KB). Bundle's blob store; "
        "only file-level blobs are materialized in current bundles."
    ),
    "list_chunks": (
        "Browse chunks with optional lexical filter. Paginated. Prefer "
        "``semantic_neighbors`` when you have a natural-language query and "
        "an sbert-backed bundle."
    ),
    "semantic_neighbors": (
        "Top-k semantic neighbors for a query string. Uses sbert vectors "
        "when the bundle's backend supports it; falls back to lexical match "
        "on symbol/path otherwise. Response's ``mode`` field tells you "
        "which."
    ),
    "concept_detail": (
        "Inspect a SKOS concept: frequency, alt-labels, components, top-k "
        "cooccurring concepts, files and chunks that lexicalize it. "
        "Concepts from the curated vocabulary also report ``kind`` "
        "(``domain-primitive`` | ``structural-primitive`` | "
        "``relational-primitive``) and the ``broader`` collection name."
    ),
    "concept_neighborhood": (
        "k-hop cooccurrence expansion from a concept. Use to explore the "
        "domain vocabulary around a concept; bounded ``depth`` ≤ 3. "
        "Pass ``kind`` to restrict the returned neighbors to a single "
        "curated-vocab category (e.g. \"show me every domain-primitive "
        "cooccurring with `behavior`\"). Bundles built without the "
        "controlled vocabulary contain no kinds, so this filter "
        "matches nothing on those bundles."
    ),
    "sparql": (
        "ADVANCED. Run a read-only SPARQL query (SELECT or ASK) against the "
        "bundle's RDF graph. DANGER: gated by CBM_ENABLE_SPARQL=1 — disabled "
        "by default. Hard limits: 10s walltime, 1000 rows, 10000 chars; "
        "mutating keywords (INSERT/DELETE/UPDATE/DROP/CLEAR/CREATE/LOAD/"
        "COPY/MOVE/ADD) rejected. Prefer specialized tools (file_detail, "
        "concept_neighborhood, semantic_neighbors) — they're cheaper and "
        "safer."
    ),
}

# --------------------------------------------------------------------------
# Input schemas
# --------------------------------------------------------------------------

INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "orient_bundle": {
        "type": "object",
        "additionalProperties": False,
        "properties": _bundle_arg(),
    },
    "bundle_summary": {
        "type": "object",
        "additionalProperties": False,
        "properties": _bundle_arg(),
    },
    "repository_summary": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_bundle_arg(),
            "central_files_limit": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 10,
            },
            "entry_points_limit": {
                "type": "integer", "minimum": 1, "maximum": 50, "default": 10,
            },
            "key_concepts_limit": {
                "type": "integer", "minimum": 1, "maximum": 100, "default": 20,
            },
        },
    },
    "list_bundles": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "select_bundle": {
        "type": "object",
        "additionalProperties": False,
        "required": ["bundle"],
        "properties": {"bundle": _BUNDLE_NAME},
    },
    "list_files": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_bundle_arg(),
            "language": {"type": "string", "minLength": 1, "maxLength": 50},
            "type": {"type": "string", "minLength": 1, "maxLength": 50},
            "prefix": {**_FILE_PATH, "description": "Bundle-relative directory prefix."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "offset": {**_INT_GE_0, "default": 0},
            "sort": {
                "type": "string",
                "enum": ["import_degree", "path", "size"],
                "default": "import_degree",
            },
        },
    },
    "file_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {**_bundle_arg(), "path": _FILE_PATH},
    },
    "file_impact": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {
            **_bundle_arg(),
            "path": _FILE_PATH,
            "depth": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
    },
    "imports_of": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {**_bundle_arg(), "path": _FILE_PATH},
    },
    "imported_by": {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {**_bundle_arg(), "path": _FILE_PATH},
    },
    "chunk_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["idx"],
        "properties": {**_bundle_arg(), "idx": _INT_GE_0},
    },
    "chunk_blob": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sha"],
        "properties": {**_bundle_arg(), "sha": _SHA256},
    },
    "list_chunks": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **_bundle_arg(),
            "q": {"type": "string", "maxLength": 2048},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "offset": {**_INT_GE_0, "default": 0},
        },
    },
    "semantic_neighbors": {
        "type": "object",
        "additionalProperties": False,
        "required": ["q"],
        "properties": {
            **_bundle_arg(),
            "q": {"type": "string", "minLength": 1, "maxLength": 2048},
            "k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
    },
    "concept_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            **_bundle_arg(),
            "name": _CONCEPT_NAME,
            "cooccur_k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "chunk_k": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "file_k": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
    },
    "concept_neighborhood": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            **_bundle_arg(),
            "name": _CONCEPT_NAME,
            "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "min_weight": {"type": "integer", "minimum": 1, "default": 2},
            "kind": {
                "type": "string",
                "enum": [
                    "domain-primitive",
                    "structural-primitive",
                    "relational-primitive",
                ],
                "description": (
                    "Optional curated-vocab filter. Only neighbors whose "
                    "concept record carries this `kind` are returned. "
                    "Traversal still walks every cooccurrence edge so "
                    "kinded neighbors past an unkinded hop remain "
                    "reachable. Bundles built without the controlled "
                    "vocabulary contain no kinds, so this filter "
                    "matches nothing on those bundles."
                ),
            },
        },
    },
    "sparql": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            **_bundle_arg(),
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 10000,
                "description": (
                    "SPARQL 1.1 query. Only SELECT and ASK forms are "
                    "accepted; CONSTRUCT/DESCRIBE/UPDATE are rejected."
                ),
            },
        },
    },
}


# --------------------------------------------------------------------------
# Output schemas (structuredContent payloads)
# --------------------------------------------------------------------------

OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "orient_bundle": {
        "type": "object",
        "additionalProperties": False,
        "required": ["bundle", "schema_hint", "suggested_first_calls"],
        "properties": {
            "bundle": _BUNDLE_INFO,
            "schema_hint": {
                "type": "object",
                "additionalProperties": False,
                "required": ["namespaces", "layers"],
                "properties": {
                    "namespaces": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "layers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "purpose"],
                            "properties": {
                                "name": {"type": "string"},
                                "purpose": {"type": "string"},
                                "key_predicates": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "suggested_first_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tool", "why"],
                    "properties": {
                        "tool": {"type": "string"},
                        # `args` is intentionally opaque — the shape varies
                        # per `tool`; the handler validates it against the
                        # nested tool's INPUT_SCHEMA before recommending.
                        "args": {"type": "object", "additionalProperties": True},
                        "why": {"type": "string"},
                    },
                },
            },
        },
    },
    "bundle_summary": {
        "type": "object",
        "additionalProperties": False,
        "required": ["counts", "files_by_language", "files_by_type", "output_dir"],
        "properties": {
            "repo_name": {"type": ["string", "null"]},
            "commit_sha": {"type": ["string", "null"]},
            "generated_at": {"type": ["string", "null"]},
            "tool_version": {"type": ["string", "null"]},
            "counts": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "files_by_language": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "files_by_type": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "embeddings_backend": {"type": ["string", "null"]},
            "embeddings_dimension": {"type": ["integer", "null"]},
            "n_chunks": {"type": "integer", "minimum": 0},
            "n_concepts": {"type": "integer", "minimum": 0},
            "shacl_conforms": {"type": ["boolean", "null"]},
            "output_dir": {"type": "string"},
        },
    },
    "list_bundles": {
        "type": "object",
        "additionalProperties": False,
        "required": ["bundles", "bundles_root"],
        "properties": {
            "bundles": {"type": "array", "items": _BUNDLE_INFO},
            "selected": {"type": ["string", "null"]},
            "bundles_root": {"type": "string"},
        },
    },
    "repository_summary": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "bundle", "total_files", "total_chunks", "total_concepts",
            "files_by_language", "files_by_type",
            "central_files", "entry_points", "key_concepts",
            "dependency_summary", "test_coverage_hint",
        ],
        "properties": {
            "bundle": _BUNDLE_INFO,
            "total_files": _INT_GE_0,
            "total_chunks": _INT_GE_0,
            "total_concepts": _INT_GE_0,
            "shacl_conforms": {"type": ["boolean", "null"]},
            "files_by_language": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "files_by_type": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
            "central_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "import_degree"],
                    "properties": {
                        "path": {"type": "string"},
                        "import_degree": _INT_GE_0,
                        "imports_out": _INT_GE_0,
                        "imports_in": _INT_GE_0,
                        "language": {"type": ["string", "null"]},
                        "type": {"type": ["string", "null"]},
                        "size": {"type": ["integer", "null"]},
                    },
                },
            },
            "entry_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "kind"],
                    "properties": {
                        "path": {"type": "string"},
                        "kind": {"type": "string"},
                        "language": {"type": ["string", "null"]},
                    },
                },
            },
            "key_concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "frequency"],
                    "properties": {
                        "name": {"type": "string"},
                        "frequency": _INT_GE_0,
                        "file_count": {"type": ["integer", "null"]},
                        "kind": {"type": ["string", "null"]},
                        "broader": {"type": ["string", "null"]},
                    },
                },
            },
            "dependency_summary": {
                "type": "object",
                "additionalProperties": False,
                "required": ["internal_imports", "external_imports"],
                "properties": {
                    "internal_imports": _INT_GE_0,
                    "external_imports": _INT_GE_0,
                    "declares_dependency": _INT_GE_0,
                    "pins_dependency": _INT_GE_0,
                },
            },
            "test_coverage_hint": {
                "type": "object",
                "additionalProperties": False,
                "required": ["test_files", "source_files"],
                "properties": {
                    "test_files": _INT_GE_0,
                    "source_files": _INT_GE_0,
                    "ratio": {"type": ["number", "null"]},
                    "tests_edges": _INT_GE_0,
                    # Source files containing inline #[test] functions
                    # (the Rust #[cfg(test)] mod tests pattern). Omitted
                    # when not present in the bundle (pre-Stage-3 bundles).
                    "rust_files_with_inline_tests": {
                        "type": ["integer", "null"], "minimum": 0,
                    },
                },
            },
        },
    },
    "select_bundle": {
        "type": "object",
        "additionalProperties": False,
        "required": ["selected"],
        "properties": {"selected": {"type": "string"}},
    },
    "list_files": {
        "type": "object",
        "additionalProperties": False,
        "required": ["files", "total"],
        "properties": {
            "files": {"type": "array", "items": _FILE_RECORD},
            "total": {"type": "integer", "minimum": 0},
            "truncated": {"type": "boolean"},
        },
    },
    "file_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["file", "imports_out", "imports_in", "chunks", "concepts"],
        "properties": {
            "file": _FILE_RECORD,
            "imports_out": {"type": "array", "items": {"type": "string"}},
            "imports_in": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "tested_subjects": {"type": "array", "items": {"type": "string"}},
            "chunks": {"type": "array", "items": _CHUNK_ROW},
            "concepts": {"type": "array", "items": {"type": "string"}},
        },
    },
    "file_impact": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "file",
            "depth",
            "direct_dependencies",
            "direct_dependents",
            "transitive_dependencies",
            "transitive_dependents",
        ],
        "properties": {
            "file": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1},
            "direct_dependencies": {"type": "array", "items": {"type": "string"}},
            "direct_dependents": {"type": "array", "items": {"type": "string"}},
            "transitive_dependencies": {"type": "array", "items": {"type": "string"}},
            "transitive_dependents": {"type": "array", "items": {"type": "string"}},
            "related_tests": {"type": "array", "items": {"type": "string"}},
            "tested_subjects": {"type": "array", "items": {"type": "string"}},
            "concepts": {"type": "array", "items": {"type": "string"}},
            "chunks": {"type": "array", "items": _CHUNK_ROW},
            "truncated": {"type": "boolean"},
        },
    },
    "imports_of": {
        "type": "object",
        "additionalProperties": False,
        "required": ["file", "imports"],
        "properties": {
            "file": {"type": "string"},
            "imports": {"type": "array", "items": {"type": "string"}},
        },
    },
    "imported_by": {
        "type": "object",
        "additionalProperties": False,
        "required": ["file", "imported_by"],
        "properties": {
            "file": {"type": "string"},
            "imported_by": {"type": "array", "items": {"type": "string"}},
        },
    },
    "chunk_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["chunk", "concepts"],
        "properties": {
            "chunk": _CHUNK_ROW,
            "concepts": {"type": "array", "items": {"type": "string"}},
            "blob_preview": {"type": ["string", "null"]},
        },
    },
    "chunk_blob": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sha256", "text"],
        "properties": {
            "sha256": _SHA256,
            "text": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
    },
    "list_chunks": {
        "type": "object",
        "additionalProperties": False,
        "required": ["chunks", "total", "mode"],
        "properties": {
            "chunks": {"type": "array", "items": _CHUNK_ROW},
            "total": {"type": "integer", "minimum": 0},
            "backend": {"type": ["string", "null"]},
            "mode": {"type": "string", "enum": ["semantic", "lexical"]},
        },
    },
    "semantic_neighbors": {
        "type": "object",
        "additionalProperties": False,
        "required": ["chunks", "total", "mode"],
        "properties": {
            "chunks": {"type": "array", "items": _CHUNK_ROW},
            "total": {"type": "integer", "minimum": 0},
            "backend": {"type": ["string", "null"]},
            "mode": {"type": "string", "enum": ["semantic", "lexical"]},
        },
    },
    "concept_detail": {
        "type": "object",
        "additionalProperties": False,
        "required": ["concept", "files", "cooccurring", "chunks", "components"],
        "properties": {
            "concept": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label"],
                "properties": {
                    "label": {"type": "string"},
                    "alt_labels": {"type": "array", "items": {"type": "string"}},
                    "components": {"type": "array", "items": {"type": "string"}},
                    "frequency": {"type": "integer", "minimum": 0},
                    "file_count": {"type": "integer", "minimum": 0},
                    "embedding_row": {"type": ["integer", "null"]},
                    # Stage 5: curated-vocab typing. Present only when the
                    # concept matched a term in the bundled vocabulary.
                    "kind": {
                        "type": "string",
                        "enum": [
                            "domain-primitive",
                            "structural-primitive",
                            "relational-primitive",
                        ],
                    },
                    "broader": {"type": "string"},
                },
            },
            "files": {"type": "array", "items": {"type": "string"}},
            "cooccurring": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "weight"],
                    "properties": {
                        "name": {"type": "string"},
                        "weight": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "chunks": {"type": "array", "items": _CHUNK_ROW},
            "components": {"type": "array", "items": {"type": "string"}},
            "file_count_total": {"type": "integer", "minimum": 0},
            "chunk_count_total": {"type": "integer", "minimum": 0},
        },
    },
    "concept_neighborhood": {
        "type": "object",
        "additionalProperties": False,
        "required": ["root", "neighbors"],
        "properties": {
            "root": {"type": "string"},
            "neighbors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "weight", "depth"],
                    "properties": {
                        "name": {"type": "string"},
                        "weight": {"type": "integer", "minimum": 1},
                        "depth": {"type": "integer", "minimum": 1},
                        "via": {"type": "array", "items": {"type": "string"}},
                        # Stage 5: curated-vocab typing; present per
                        # neighbor when the underlying concept matched
                        # a term in the bundled vocabulary.
                        "kind": {
                            "type": "string",
                            "enum": [
                                "domain-primitive",
                                "structural-primitive",
                                "relational-primitive",
                            ],
                        },
                        "broader": {"type": "string"},
                    },
                },
            },
            "truncated": {"type": "boolean"},
            # Echo of the input `kind` filter when one was supplied, so
            # clients can confirm the filter took effect. Absent when no
            # filter was requested.
            "kind_filter": {
                "type": "string",
                "enum": [
                    "domain-primitive",
                    "structural-primitive",
                    "relational-primitive",
                ],
            },
        },
    },
    "sparql": {
        "type": "object",
        "additionalProperties": False,
        "required": ["columns", "rows", "row_count", "truncated", "query_form"],
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": {"type": ["string", "null"]},
                },
            },
            "row_count": {"type": "integer", "minimum": 0},
            "truncated": {"type": "boolean"},
            "query_form": {"type": "string", "enum": ["SELECT", "ASK"]},
            "ask_result": {"type": ["boolean", "null"]},
        },
    },
}


# --------------------------------------------------------------------------
# Resource URI templates (Phase 4 plugs into these)
# --------------------------------------------------------------------------

RESOURCE_URI_TEMPLATES: dict[str, dict[str, Any]] = {
    "bundles_index": {
        "uri": "cbm://bundles",
        "name": "Bundles index",
        "description": "List of all bundles available under the server's root.",
        "mimeType": "application/json",
    },
    "bundle_manifest": {
        "uri": "cbm://bundle/{bundle}/manifest",
        "name": "Bundle run manifest",
        "description": "run_manifest.json verbatim. Subscribable: changes are pushed on re-runs.",
        "mimeType": "application/json",
        "subscribable": True,
    },
    "bundle_summary": {
        "uri": "cbm://bundle/{bundle}/summary",
        "name": "Bundle summary",
        "description": "Counts + language/type histograms (same shape as bundle_summary tool).",
        "mimeType": "application/json",
    },
    "bundle_shacl": {
        "uri": "cbm://bundle/{bundle}/shapes.shacl.ttl",
        "name": "Bundle SHACL shapes",
        "description": "SHACL shapes the bundle conforms to. Useful when the agent wants to validate a hypothesis.",
        "mimeType": "text/turtle",
    },
    "bundle_ontology": {
        "uri": "cbm://bundle/{bundle}/ontology-mapping.ttl",
        "name": "Bundle ontology mapping",
        "description": "Namespace aliases (cbm, cbml2, cbml3, skos, nif…). Read first if you don't know the ontology.",
        "mimeType": "text/turtle",
    },
    "file": {
        "uri": "cbm://bundle/{bundle}/file/{path}",
        "name": "File detail",
        "description": "Single file record. Same shape as the file_detail tool.",
        "mimeType": "application/json",
    },
    "chunk": {
        "uri": "cbm://bundle/{bundle}/chunk/{idx}",
        "name": "Chunk detail",
        "description": "Single chunk record. Same shape as the chunk_detail tool.",
        "mimeType": "application/json",
    },
    "concept": {
        "uri": "cbm://bundle/{bundle}/concept/{name}",
        "name": "Concept detail",
        "description": "Single concept record. Same shape as the concept_detail tool.",
        "mimeType": "application/json",
    },
}


# --------------------------------------------------------------------------
# Validator helpers
# --------------------------------------------------------------------------

TOOL_NAMES: tuple[str, ...] = tuple(sorted(INPUT_SCHEMAS.keys()))


def validate_in(tool: str, args: dict[str, Any]) -> None:
    """Validate tool-call arguments against ``INPUT_SCHEMAS[tool]``.

    Raises ``jsonschema.ValidationError`` on a contract violation,
    ``KeyError`` on an unknown tool. The handler should also run deeper
    domain-level validators (e.g. ``_validate_bundle_name``, path-traversal
    checks) after this passes.
    """
    from jsonschema import Draft202012Validator

    schema = INPUT_SCHEMAS[tool]
    Draft202012Validator(schema).validate(args)


def validate_out(tool: str, payload: dict[str, Any]) -> None:
    """Validate a structuredContent payload against ``OUTPUT_SCHEMAS[tool]``.

    Used both in tests (asserting handler output conforms) and in the
    server's response pipeline as a defence-in-depth check.
    """
    from jsonschema import Draft202012Validator

    schema = OUTPUT_SCHEMAS[tool]
    Draft202012Validator(schema).validate(payload)

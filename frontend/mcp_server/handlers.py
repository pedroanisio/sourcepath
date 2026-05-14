"""Pure-Python handlers for every MCP tool (Phase 2).

Each handler takes a validated args dict, returns a payload dict that
conforms to ``OUTPUT_SCHEMAS[tool]``. Transport-agnostic: Phase 3 wires
these to MCP's ``tools/call``; the same functions are exercised directly
by ``tests/test_handlers.py`` and can be reused in a CLI debugger.

Bundle resolution:
  args["bundle"]  →  passed through
  else session-default (caller threads via ``bundle_default`` kw)
  else server default (CBM_OUTPUT_DIR / first listed)

All handlers re-validate inputs server-side (defence in depth) even
though the protocol layer runs ``validate_in`` first.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# Make the backend importable when this package is loaded standalone
# (e.g. by pytest collecting from repo root).
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402  (after sys.path tweak)
import app as backend_app  # noqa: E402  (after sys.path tweak)

from .schemas import (  # noqa: E402
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    validate_in,
    validate_out,
)
from .validators import (  # noqa: E402
    INTERNAL,
    INVALID_ARGUMENT,
    NOT_FOUND,
    ToolError,
    truncate_text,
    validate_bundle_name,
    validate_relative_path,
    validate_sha256,
)

# Per-spec, chunk-detail blob previews are aggressive (2 KB) so a tool call
# never blows the context window. chunk_blob can return larger because the
# agent explicitly asked for it.
BLOB_PREVIEW_BYTES = 2048
BLOB_FULL_BYTES = 20_000

# --------------------------------------------------------------------------
# Bundle resolution shim
# --------------------------------------------------------------------------


def _get_bundle(name: str | None):
    """Resolve and load a bundle, mapping FastAPI's HTTPException to ToolError."""
    try:
        return backend_app.get_bundle(name)
    except HTTPException as e:
        if e.status_code == 404:
            raise ToolError(NOT_FOUND, str(e.detail)) from e
        if e.status_code == 400:
            raise ToolError(INVALID_ARGUMENT, str(e.detail)) from e
        raise ToolError(INTERNAL, str(e.detail)) from e


def _pick_bundle_name(args: dict[str, Any], bundle_default: str | None) -> str | None:
    name = args.get("bundle") or bundle_default
    if name is not None:
        validate_bundle_name(name)
    return name


# --------------------------------------------------------------------------
# Registry + decorator
# --------------------------------------------------------------------------

HandlerFn = Callable[[dict[str, Any], str | None], dict[str, Any]]
HANDLERS: dict[str, HandlerFn] = {}


def tool(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a handler. Wraps it with input + output schema validation."""
    if name not in INPUT_SCHEMAS:
        raise KeyError(f"tool {name!r} has no INPUT_SCHEMAS entry")
    if name not in OUTPUT_SCHEMAS:
        raise KeyError(f"tool {name!r} has no OUTPUT_SCHEMAS entry")

    def decorator(fn: HandlerFn) -> HandlerFn:
        def wrapped(args: dict[str, Any], bundle_default: str | None = None) -> dict[str, Any]:
            validate_in(name, args)
            payload = fn(args, bundle_default)
            validate_out(name, payload)
            return payload

        wrapped.__name__ = fn.__name__
        wrapped.__doc__ = fn.__doc__
        HANDLERS[name] = wrapped
        return wrapped

    return decorator


def dispatch(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    bundle_default: str | None = None,
) -> dict[str, Any]:
    """Single entry point Phase 3 (and tests) call. Raises ToolError on
    domain failures, jsonschema.ValidationError on contract violations.
    """
    if tool_name not in HANDLERS:
        raise ToolError(NOT_FOUND, f"unknown tool: {tool_name!r}")
    return HANDLERS[tool_name](args or {}, bundle_default)


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _file_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Strip internal-only fields (uri kept; nothing else to strip today)."""
    return {
        "path": rec["path"],
        "uri": rec.get("uri"),
        "language": rec.get("language"),
        "type": rec.get("type"),
        "size": rec.get("size"),
        "contentSha256": rec.get("contentSha256"),
    }


def _chunk_row(rec: dict[str, Any], *, score: float | None = None) -> dict[str, Any]:
    out = {
        "idx": rec["idx"],
        "uri": rec.get("uri"),
        "symbol": rec.get("symbol"),
        "kind": rec.get("kind"),
        "file": rec.get("file"),
        "beginLine": rec.get("beginLine"),
        "endLine": rec.get("endLine"),
        "embeddingRow": rec.get("embeddingRow"),
        "contentSha256": rec.get("contentSha256"),
    }
    if score is not None:
        out["score"] = score
    return out


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@tool("orient_bundle")
def _orient_bundle(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    b = _get_bundle(name)
    bundle_info = {
        "name": b.output_dir.name,
        "path": str(b.output_dir),
        "repo_name": b.manifest.get("repo_name"),
        "commit_sha": b.manifest.get("commit_sha"),
        "generated_at": b.manifest.get("generated_at"),
        "tool_version": b.manifest.get("tool_version"),
        "files": (b.manifest.get("counts") or {}).get("files"),
    }
    namespaces = {
        "cbm":   "https://codebase-mapper.example.org/cbm#",
        "cbml2": "https://codebase-mapper.example.org/cbml2#",
        "cbml3": "https://codebase-mapper.example.org/cbml3#",
        "skos":  "http://www.w3.org/2004/02/skos/core#",
        "nif":   "http://persistence.uni-leipzig.org/nlp2rdf/ontologies/nif-core#",
    }
    layers = [
        {
            "name": "L1 host",
            "purpose": "Files, languages, types, imports, dependency manifests, AST summaries.",
            "key_predicates": ["cbm:path", "cbm:imports", "cbm:hasPhase", "cbm:tests"],
        },
        {
            "name": "L2 chunks_embeddings",
            "purpose": "Per-function/class/file chunks with NIF spans and embedding vectors.",
            "key_predicates": ["cbml2:inFile", "cbml2:beginIndex", "cbml2:endIndex", "cbml2:embeddingRow"],
        },
        {
            "name": "L3 concept_graph",
            "purpose": "SKOS concepts from identifier splitting; cooccurrence as skos:related.",
            "key_predicates": ["cbml3:lexicalizes", "cbml3:composedOf", "skos:related", "skos:prefLabel"],
        },
    ]
    suggested = [
        {"tool": "bundle_summary", "args": {}, "why": "Counts, language/type breakdown, embeddings backend."},
        {"tool": "list_files", "args": {"sort": "import_degree", "limit": 20},
         "why": "20 most-connected files = a fast map of the codebase's spine."},
        {"tool": "concept_neighborhood",
         "args": {"name": "<a concept name from list_files's hits>", "depth": 1},
         "why": "Expand the domain vocabulary around a representative concept."},
    ]
    return {
        "bundle": bundle_info,
        "schema_hint": {"namespaces": namespaces, "layers": layers},
        "suggested_first_calls": suggested,
    }


@tool("bundle_summary")
def _bundle_summary(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    b = _get_bundle(name)
    m = b.manifest
    backend_meta = b.embeddings_meta.get("backend") or {}
    return {
        "repo_name": m.get("repo_name"),
        "commit_sha": m.get("commit_sha"),
        "generated_at": m.get("generated_at"),
        "tool_version": m.get("tool_version"),
        "counts": m.get("counts", {}),
        "files_by_language": m.get("files_by_language", {}),
        "files_by_type": m.get("files_by_type", {}),
        "embeddings_backend": backend_meta.get("name"),
        "embeddings_dimension": b.embeddings_meta.get("dimension"),
        "n_chunks": b.embeddings_meta.get("n_chunks", 0),
        "n_concepts": len(b.concepts.get("concepts", {})),
        "shacl_conforms": (m.get("shacl_self_check") or {}).get("conforms"),
        "output_dir": str(b.output_dir),
    }


_ENTRY_POINT_BASENAMES: dict[str, str] = {
    "__main__.py": "python_main",
    "main.py": "python_main",
    "cli.py": "python_cli",
    "app.py": "python_app",
    "server.py": "python_app",
    "main.rs": "rust_main",
    "main.go": "go_main",
    "index.js": "js_index",
    "index.ts": "js_index",
    "index.jsx": "js_index",
    "index.tsx": "js_index",
    "index.mjs": "js_index",
}


def _entry_point_kind(path: str, file_type: str | None) -> str | None:
    """Heuristic classifier. Returns an entry-point kind tag or None.

    Tags use the convention ``<language>_<role>``: ``python_main``,
    ``python_cli``, ``python_app``, ``rust_main``, ``rust_bin``,
    ``go_main``, ``js_index``.
    """
    from pathlib import PurePosixPath

    p = PurePosixPath(path)
    name = p.name
    parts = p.parts

    kind = _ENTRY_POINT_BASENAMES.get(name)
    if kind is not None:
        # Filter heuristic candidates that aren't source (e.g. test app.py)
        if file_type == "source_code":
            return kind
        # __main__.py is reliably an entry point regardless of type
        if name == "__main__.py":
            return kind
        return None

    # Rust bin/ convention — files under .../bin/*.rs are alternative binaries.
    if "bin" in parts and p.suffix == ".rs":
        return "rust_bin"
    # Generic bin/ entrypoint scripts (no suffix, in a bin/ dir).
    if "bin" in parts and not p.suffix and file_type == "source_code":
        return "shell_bin"
    return None


@tool("repository_summary")
def _repository_summary(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    """One-shot executive read: combines bundle metadata + central files +
    entry points + top concepts + dependency edge counts + test ratio.

    Pure graph queries — no LLM, no extraction beyond what's already in the
    emitted bundle. Deterministic for a given bundle state.
    """
    name = _pick_bundle_name(args, default)
    b = _get_bundle(name)
    m = b.manifest
    counts = m.get("counts") or {}
    files_by_type = m.get("files_by_type") or {}

    central_limit = int(args.get("central_files_limit", 10))
    entry_limit = int(args.get("entry_points_limit", 10))
    concept_limit = int(args.get("key_concepts_limit", 20))

    bundle_info = {
        "name": b.output_dir.name,
        "path": str(b.output_dir),
        "repo_name": m.get("repo_name"),
        "commit_sha": m.get("commit_sha"),
        "generated_at": m.get("generated_at"),
        "tool_version": m.get("tool_version"),
        "files": counts.get("files"),
    }

    # Central files — ranked by combined in+out import degree.
    central: list[dict[str, Any]] = []
    for rec in b.files:
        path = rec["path"]
        out_deg = len(b.imports_out.get(path, []))
        in_deg = len(b.imports_in.get(path, []))
        deg = out_deg + in_deg
        if deg == 0:
            continue
        central.append({
            "path": path,
            "import_degree": deg,
            "imports_out": out_deg,
            "imports_in": in_deg,
            "language": rec.get("language"),
            "type": rec.get("type"),
            "size": rec.get("size"),
        })
    central.sort(key=lambda r: (-r["import_degree"], r["path"]))
    central = central[:central_limit]

    # Entry points — heuristic; ranked by path for stable output.
    entry: list[dict[str, Any]] = []
    for rec in b.files:
        kind = _entry_point_kind(rec["path"], rec.get("type"))
        if kind is None:
            continue
        entry.append({
            "path": rec["path"],
            "kind": kind,
            "language": rec.get("language"),
        })
    entry.sort(key=lambda r: r["path"])
    entry = entry[:entry_limit]

    # Key concepts — ranked by frequency descending. Surface curated-vocab
    # typing (kind, broader) when present.
    concepts_dict = b.concepts.get("concepts", {}) or {}
    ranked = sorted(
        concepts_dict.items(),
        key=lambda kv: (-int(kv[1].get("frequency", 0)), kv[0]),
    )
    key_concepts: list[dict[str, Any]] = []
    for cname, crec in ranked[:concept_limit]:
        item: dict[str, Any] = {
            "name": cname,
            "frequency": int(crec.get("frequency", 0)),
            "file_count": crec.get("file_count"),
            "kind": crec.get("kind"),
            "broader": crec.get("broader"),
        }
        key_concepts.append(item)

    dep_summary = {
        "internal_imports": int(counts.get("import_edges", 0)),
        "external_imports": int(counts.get("import_external_edges", 0)),
        "declares_dependency": int(counts.get("declares_dependency_edges", 0)),
        "pins_dependency": int(counts.get("pins_dependency_edges", 0)),
    }

    src_files = int(files_by_type.get("source_code", 0))
    test_files = int(files_by_type.get("test_code", 0))
    test_hint = {
        "test_files": test_files,
        "source_files": src_files,
        "ratio": (test_files / src_files) if src_files > 0 else None,
        "tests_edges": int(counts.get("tests_edges", 0)),
        # Stage-3 addition. Older bundles omit the key; we surface it
        # as `null` so consumers can distinguish "no inline tests" (0)
        # from "this bundle doesn't carry the count" (null).
        "rust_files_with_inline_tests": (
            int(counts["rust_files_with_inline_tests"])
            if "rust_files_with_inline_tests" in counts
            else None
        ),
    }

    # Stage 4: top-N Rust attribute → count distribution. Computed
    # from the sidecar payload loaded at bundle open. Pre-Stage-4
    # bundles surface as None (no sidecar present); a Stage-4 bundle
    # with no Rust code surfaces as an empty list.
    rust_attr_dist: list[dict[str, Any]] | None
    rust_items = getattr(b, "rust_items", None)
    if rust_items is None:
        rust_attr_dist = None
    else:
        attr_counter: dict[str, int] = {}
        for item in rust_items:
            for attr in item.get("attributes") or []:
                attr_counter[attr] = attr_counter.get(attr, 0) + 1
        ranked = sorted(
            attr_counter.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[:20]
        rust_attr_dist = [{"attribute": a, "count": c} for a, c in ranked]

    payload: dict[str, Any] = {
        "bundle": bundle_info,
        "total_files": int(counts.get("files", 0)),
        "total_chunks": int(b.embeddings_meta.get("n_chunks", 0)),
        "total_concepts": len(concepts_dict),
        "shacl_conforms": (m.get("shacl_self_check") or {}).get("conforms"),
        "files_by_language": m.get("files_by_language", {}),
        "files_by_type": files_by_type,
        "central_files": central,
        "entry_points": entry,
        "key_concepts": key_concepts,
        "dependency_summary": dep_summary,
        "test_coverage_hint": test_hint,
    }
    if rust_attr_dist is not None:
        payload["rust_attribute_distribution"] = rust_attr_dist
    return payload


@tool("list_bundles")
def _list_bundles(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    return {
        "bundles": backend_app.list_bundles(),
        "selected": default or backend_app._default_bundle_name(),
        "bundles_root": str(backend_app._bundles_root()),
    }


@tool("select_bundle")
def _select_bundle(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = args["bundle"]
    validate_bundle_name(name)
    # Resolve to confirm it exists; raises NOT_FOUND if not.
    _get_bundle(name)
    return {"selected": name}


@tool("list_files")
def _list_files(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    b = _get_bundle(name)
    language = args.get("language")
    file_type = args.get("type")
    prefix = args.get("prefix")
    sort = args.get("sort", "import_degree")
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    if prefix:
        prefix = validate_relative_path(prefix)

    rows = b.files
    if language:
        rows = [r for r in rows if r.get("language") == language]
    if file_type:
        rows = [r for r in rows if r.get("type") == file_type]
    if prefix:
        rows = [r for r in rows if r["path"] == prefix or r["path"].startswith(prefix + "/")]

    if sort == "path":
        rows = sorted(rows, key=lambda r: r["path"])
    elif sort == "size":
        rows = sorted(rows, key=lambda r: r.get("size") or 0, reverse=True)
    else:
        def deg(r):
            p = r["path"]
            return len(b.imports_out.get(p, [])) + len(b.imports_in.get(p, []))
        rows = sorted(rows, key=deg, reverse=True)

    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "files": [_file_record(r) for r in page],
        "total": total,
        "truncated": total > offset + len(page),
    }


@tool("file_detail")
def _file_detail(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    path = validate_relative_path(args["path"])
    b = _get_bundle(name)
    rec = b.file_by_path.get(path)
    if not rec:
        raise ToolError(NOT_FOUND, f"file not found: {path}")
    chunk_idxs = b.chunks_by_file.get(path, [])
    chunks = [
        _chunk_row(b.chunks[i]) for i in chunk_idxs
    ]
    concepts = list((b.concepts.get("per_path_concepts") or {}).get(path, []))
    return {
        "file": _file_record(rec),
        "imports_out": sorted(b.imports_out.get(path, [])),
        "imports_in": sorted(b.imports_in.get(path, [])),
        "tests": sorted(b.tests_for_subject.get(path, [])),
        "tested_subjects": sorted(b.subjects_for_test.get(path, [])),
        "chunks": chunks,
        "concepts": concepts,
    }


def _walk_paths(start, adjacency, depth, limit):
    """BFS over a path -> [paths] adjacency. Returns (paths, truncated)."""
    seen = {start}
    frontier = [start]
    out: list[str] = []
    for _ in range(depth):
        nxt: list[str] = []
        for src in frontier:
            for dst in sorted(adjacency.get(src, [])):
                if dst in seen:
                    continue
                seen.add(dst)
                out.append(dst)
                nxt.append(dst)
                if len(out) >= limit:
                    return out, True
        frontier = nxt
        if not frontier:
            break
    return out, False


@tool("file_impact")
def _file_impact(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    path = validate_relative_path(args["path"])
    depth = int(args.get("depth", 2))
    limit = int(args.get("limit", 100))
    b = _get_bundle(name)
    if path not in b.file_by_path:
        raise ToolError(NOT_FOUND, f"file not found: {path}")

    deps, dep_trunc = _walk_paths(path, b.imports_out, depth, limit)
    dependents, rev_trunc = _walk_paths(path, b.imports_in, depth, limit)

    related_tests = set(b.tests_for_subject.get(path, []))
    tested_subjects = set(b.subjects_for_test.get(path, []))
    for impacted in dependents:
        related_tests.update(b.tests_for_subject.get(impacted, []))

    chunk_idxs = b.chunks_by_file.get(path, [])[:25]
    chunks = [_chunk_row(b.chunks[i]) for i in chunk_idxs]
    concepts = list((b.concepts.get("per_path_concepts") or {}).get(path, []))

    return {
        "file": path,
        "depth": depth,
        "direct_dependencies": sorted(b.imports_out.get(path, [])),
        "direct_dependents": sorted(b.imports_in.get(path, [])),
        "transitive_dependencies": deps,
        "transitive_dependents": dependents,
        "related_tests": sorted(related_tests),
        "tested_subjects": sorted(tested_subjects),
        "concepts": concepts,
        "chunks": chunks,
        "truncated": dep_trunc or rev_trunc,
    }


@tool("imports_of")
def _imports_of(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    path = validate_relative_path(args["path"])
    b = _get_bundle(name)
    if path not in b.file_by_path:
        raise ToolError(NOT_FOUND, f"file not found: {path}")
    return {"file": path, "imports": sorted(b.imports_out.get(path, []))}


@tool("imported_by")
def _imported_by(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    path = validate_relative_path(args["path"])
    b = _get_bundle(name)
    if path not in b.file_by_path:
        raise ToolError(NOT_FOUND, f"file not found: {path}")
    return {"file": path, "imported_by": sorted(b.imports_in.get(path, []))}


@tool("chunk_detail")
def _chunk_detail(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    idx = int(args["idx"])
    b = _get_bundle(name)
    if idx < 0 or idx >= len(b.chunks):
        raise ToolError(NOT_FOUND, f"chunk idx out of range: {idx}")
    rec = b.chunks[idx]
    sha = rec.get("contentSha256")
    blob_preview: str | None = None
    if sha and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
        p = b.output_dir / "blobs" / sha
        if p.exists():
            try:
                raw = p.read_text(errors="replace")
            except OSError:
                raw = None
            if raw is not None:
                blob_preview, _ = truncate_text(raw, BLOB_PREVIEW_BYTES)
    return {
        "chunk": _chunk_row(rec),
        "concepts": list(b.chunk_concepts.get(idx, [])),
        "blob_preview": blob_preview,
    }


@tool("chunk_blob")
def _chunk_blob(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    sha = args["sha"]
    validate_sha256(sha)
    b = _get_bundle(name)
    p = b.output_dir / "blobs" / sha
    if not p.exists():
        raise ToolError(NOT_FOUND, f"blob not found: {sha}")
    try:
        raw = p.read_text(errors="replace")
    except OSError as e:  # pragma: no cover — defensive
        raise ToolError(INTERNAL, f"blob read failed: {e}") from e
    text, truncated = truncate_text(raw, BLOB_FULL_BYTES)
    return {"sha256": sha, "text": text or "", "truncated": truncated}


@tool("list_chunks")
def _list_chunks(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    q = (args.get("q") or "").lower()
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    b = _get_bundle(name)
    rows = b.chunks
    if q:
        rows = [
            r for r in rows
            if (r.get("symbol") or "").lower().find(q) >= 0
            or (r.get("file") or "").lower().find(q) >= 0
        ]
    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "chunks": [_chunk_row(r) for r in page],
        "total": total,
        "backend": (b.embeddings_meta.get("backend") or {}).get("name"),
        "mode": "lexical",
    }


@tool("semantic_neighbors")
def _semantic_neighbors(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    q = args["q"]
    k = int(args.get("k", 20))
    b = _get_bundle(name)
    backend_name = (b.embeddings_meta.get("backend") or {}).get("name") or ""
    is_sbert = any(
        s in backend_name.lower() for s in ("sentence-transformer", "sbert", "minilm")
    )

    if not is_sbert or b.chunk_vectors is None:
        ql = q.lower()
        scored = [
            (r, 1.0 if (r.get("symbol") or "").lower().find(ql) >= 0
             else (0.5 if (r.get("file") or "").lower().find(ql) >= 0 else 0.0))
            for r in b.chunks
        ]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        out = scored[:k]
        return {
            "chunks": [_chunk_row(r, score=score) for r, score in out],
            "total": len(scored),
            "backend": backend_name or None,
            "mode": "lexical",
        }

    # Semantic path: embed query, cosine top-k.
    import numpy as np  # local import keeps the module light when sbert isn't used

    model = backend_app._get_model("sentence-transformers/all-MiniLM-L6-v2")
    q_vec = model.encode([q], normalize_embeddings=True)[0].astype("float32")
    sims = b.chunk_vectors @ q_vec
    top_idx = np.argsort(-sims)[:k]
    by_row: dict[int, dict[str, Any]] = {
        r["embeddingRow"]: r for r in b.chunks if r.get("embeddingRow") is not None
    }
    out_chunks: list[dict[str, Any]] = []
    for i in top_idx:
        row = int(i)
        r = by_row.get(row)
        if r is None:
            continue
        out_chunks.append(_chunk_row(r, score=float(sims[row])))
    return {
        "chunks": out_chunks,
        "total": len(out_chunks),
        "backend": backend_name,
        "mode": "semantic",
    }


@tool("concept_detail")
def _concept_detail(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    concept_name = args["name"]
    cooccur_k = int(args.get("cooccur_k", 20))
    chunk_k = int(args.get("chunk_k", 50))
    file_k = int(args.get("file_k", 100))
    b = _get_bundle(name)
    c = b.concepts.get("concepts", {}).get(concept_name)
    if not c:
        raise ToolError(NOT_FOUND, f"concept not found: {concept_name}")
    files: list[str] = []
    total_files = 0
    for path, names in (b.concepts.get("per_path_concepts") or {}).items():
        if concept_name in names:
            total_files += 1
            if len(files) < file_k:
                files.append(path)
    cooc = [
        {"name": n, "weight": int(w)} for n, w in b.cooccur.get(concept_name, [])[:cooccur_k]
    ]
    chunk_idxs = b.concept_chunks.get(concept_name, [])[:chunk_k]
    chunks = [_chunk_row(b.chunks[i]) for i in chunk_idxs]
    concept_payload: dict[str, Any] = {
        "label": c.get("label", concept_name),
        "alt_labels": list(c.get("alt_labels", [])),
        "components": list(c.get("components", [])),
        "frequency": int(c.get("frequency", 0)),
        "file_count": int(c.get("file_count", 0)),
        "embedding_row": c.get("embedding_row"),
    }
    # Stage 5: surface curated-vocab typing when the bundle carries it.
    # Pre-vocab bundles simply lack these keys; older clients ignore them.
    if "kind" in c:
        concept_payload["kind"] = c["kind"]
    if "broader" in c:
        concept_payload["broader"] = c["broader"]
    return {
        "concept": concept_payload,
        "files": files,
        "cooccurring": cooc,
        "chunks": chunks,
        "components": list(c.get("components", [])),
        "file_count_total": total_files,
        "chunk_count_total": len(b.concept_chunks.get(concept_name, [])),
    }


@tool("sparql")
def _sparql(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    from . import sparql as _sparql_mod  # imported lazily to keep module load cheap

    name = _pick_bundle_name(args, default)
    return _sparql_mod.run_sparql(args["query"], bundle_default=name)


@tool("concept_neighborhood")
def _concept_neighborhood(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    name = _pick_bundle_name(args, default)
    concept_name = args["name"]
    depth = int(args.get("depth", 1))
    limit = int(args.get("limit", 20))
    min_weight = int(args.get("min_weight", 2))
    # Stage 5: optional curated-vocab filter. When provided, only
    # neighbors whose concept record carries this `kind` are returned.
    # Traversal still walks every cooccurrence edge so a kinded
    # neighbor at depth 2 isn't hidden behind an unkinded depth-1 node.
    kind_filter = args.get("kind")
    b = _get_bundle(name)
    all_concepts = b.concepts.get("concepts", {})
    if concept_name not in all_concepts:
        raise ToolError(NOT_FOUND, f"concept not found: {concept_name}")

    visited = {concept_name}
    frontier: list[tuple[str, int, list[str]]] = [(concept_name, 0, [])]
    neighbors: list[dict[str, Any]] = []
    truncated = False

    while frontier and not truncated:
        next_frontier: list[tuple[str, int, list[str]]] = []
        for cur, cur_depth, via in frontier:
            if cur_depth >= depth:
                continue
            for n, w in b.cooccur.get(cur, []):
                if n in visited or int(w) < min_weight:
                    continue
                visited.add(n)
                n_meta = all_concepts.get(n, {})
                n_kind = n_meta.get("kind")
                # Surface kind/broader on each neighbor (when known)
                # whether or not a filter is in play — same shape either
                # way is friendlier for client renderers.
                row: dict[str, Any] = {
                    "name": n,
                    "weight": int(w),
                    "depth": cur_depth + 1,
                    "via": via + [cur] if via else ([cur] if cur != concept_name else []),
                }
                if n_kind is not None:
                    row["kind"] = n_kind
                if "broader" in n_meta:
                    row["broader"] = n_meta["broader"]
                # Always extend the frontier so deeper kinded neighbors
                # remain reachable. Apply the filter only when deciding
                # to emit the current row.
                next_frontier.append((n, cur_depth + 1, via + [cur] if cur != concept_name else [cur]))
                if kind_filter is not None and n_kind != kind_filter:
                    continue
                neighbors.append(row)
                if len(neighbors) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        frontier = next_frontier

    out: dict[str, Any] = {
        "root": concept_name,
        "neighbors": neighbors,
        "truncated": truncated,
    }
    if kind_filter is not None:
        out["kind_filter"] = kind_filter
    return out


@tool("items_by_attribute")
def _items_by_attribute(args: dict[str, Any], default: str | None) -> dict[str, Any]:
    """Stage 4: filter Rust items (from the rust_items.jsonl sidecar)
    by attribute substring + optional kind. Returns at most ``limit``
    items starting at ``offset``; the full filtered count is in
    ``total``, with ``truncated`` flagging that more results exist."""
    name = _pick_bundle_name(args, default)
    pattern = args["pattern"]
    kind = args.get("kind")
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    b = _get_bundle(name)

    matches: list[dict[str, Any]] = []
    for item in getattr(b, "rust_items", None) or []:
        if kind is not None and item.get("kind") != kind:
            continue
        attrs = item.get("attributes") or []
        if not any(pattern in a for a in attrs):
            continue
        matches.append({
            "path": item["path"],
            "kind": item["kind"],
            "name": item["name"],
            "parent": item.get("parent"),
            "line_start": item.get("line_start"),
            "line_end": item.get("line_end"),
            "is_pub": bool(item.get("is_pub", False)),
            "is_async": bool(item.get("is_async", False)),
            "attributes": list(attrs),
        })

    total = len(matches)
    page = matches[offset:offset + limit]
    return {
        "items": page,
        "total": total,
        "truncated": total > offset + len(page),
    }

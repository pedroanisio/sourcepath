"""FastAPI backend for visualizing codebase-mapper output bundles.

Bundle resolution (in order of precedence):
  - ``?bundle=NAME`` query param on any endpoint — picks ``CBM_BUNDLES_ROOT/NAME``
    (falls back to CBM_OUTPUT_DIR when its basename matches).
  - ``CBM_OUTPUT_DIR`` env var — single-bundle default for back-compat.
  - First bundle found under ``CBM_BUNDLES_ROOT`` (default ``_tmp``).

Run:
    CBM_OUTPUT_DIR=_tmp/usl-ng-core-map uvicorn frontend.backend.app:app --port 8000
or, with multiple bundles available:
    CBM_BUNDLES_ROOT=_tmp uvicorn frontend.backend.app:app --port 8000
"""
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

CBM = Namespace("https://codebase-mapper.example.org/cbm#")
CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")
CBML3 = Namespace("https://codebase-mapper.example.org/cbml3#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
CBMI_NS = "https://codebase-mapper.example.org/cbm/instance#"


@dataclass
class Bundle:
    output_dir: Path
    manifest: dict[str, Any]
    concepts: dict[str, Any]
    embeddings_meta: dict[str, Any]
    chunk_vectors: np.ndarray | None
    chunk_ids: list[str]
    concept_vectors: np.ndarray | None
    concept_ids: list[str]
    files: list[dict[str, Any]]  # path, language, type, size, contentSha256
    file_by_path: dict[str, dict[str, Any]]
    imports: list[tuple[str, str]]  # (src_path, dst_path)
    imports_out: dict[str, list[str]]
    imports_in: dict[str, list[str]]
    tests: list[tuple[str, str]]  # (test_path, subject_path)
    tests_for_subject: dict[str, list[str]]
    subjects_for_test: dict[str, list[str]]
    chunks: list[dict[str, Any]]  # adds: idx
    chunks_by_uri: dict[str, int]  # uri -> idx
    chunks_by_file: dict[str, list[int]]  # file path -> [idx,...]
    chunk_concepts: dict[int, list[str]]  # idx -> [concept_name,...]
    concept_chunks: dict[str, list[int]]  # concept_name -> [idx,...]
    cooccur: dict[str, list[tuple[str, int]]]  # concept -> [(neighbor, weight), ...] sorted desc
    # Symbol-level xrefs (L3 symbol_xrefs plugin). When the bundle was
    # produced without symbol_xrefs registered, xrefs is empty and the
    # *_by_* indices are empty dicts — endpoints just return empty lists.
    xrefs: list[dict[str, Any]] = field(default_factory=list)
    xrefs_by_src_idx: dict[int, list[int]] = field(default_factory=dict)
    xrefs_by_dst_idx: dict[int, list[int]] = field(default_factory=dict)
    # Stage 4: Rust items carrying at least one attribute. Pre-Stage-4
    # bundles or bundles with no Rust code have empty defaults.
    rust_items: list[dict[str, Any]] = field(default_factory=list)
    rust_items_by_file: dict[str, list[int]] = field(default_factory=dict)
    # L4 enrichment layer (cbml4: predicates). Three buckets keyed by
    # the enrichment target. Pre-L4 bundles and bundles emitted without
    # the L4 scope opted in carry empty defaults — endpoints just
    # return no llm_* fields.
    enrichment_file_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrichment_concept_description: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrichment_schema_purpose: dict[str, dict[str, Any]] = field(default_factory=dict)


def _resolve_file_type_uri(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def load_bundle(output_dir: Path) -> Bundle:
    if not output_dir.exists():
        raise FileNotFoundError(f"output dir not found: {output_dir}")

    manifest = json.loads((output_dir / "run_manifest.json").read_text())
    # concepts.json and embeddings_meta.json come from the L3 / L2 plugins
    # respectively; a host-only bundle (e.g. produced by `python -m
    # codebase_mapper`) won't have them. Treat them as optional.
    concepts_path = output_dir / "concepts.json"
    concepts = json.loads(concepts_path.read_text()) if concepts_path.exists() else {}
    emb_meta_path = output_dir / "embeddings_meta.json"
    embeddings_meta = (
        json.loads(emb_meta_path.read_text()) if emb_meta_path.exists() else {}
    )

    chunk_npz_path = output_dir / "embeddings.npz"
    chunk_vectors = None
    chunk_ids: list[str] = []
    if chunk_npz_path.exists():
        # ids are stored as a numpy object array of Python strings; allow_pickle is required
        npz = np.load(chunk_npz_path, allow_pickle=True)
        chunk_vectors = np.asarray(npz[embeddings_meta.get("vectors_field", "vectors")])
        chunk_ids = [s.decode() if isinstance(s, bytes) else str(s)
                     for s in npz[embeddings_meta.get("ids_field", "ids")]]

    concept_npz_path = output_dir / "concepts_embeddings.npz"
    concept_vectors = None
    concept_ids: list[str] = list(concepts.get("concept_embedding_ids") or [])
    if concept_npz_path.exists():
        npz = np.load(concept_npz_path, allow_pickle=True)
        concept_vectors = np.asarray(npz["vectors"] if "vectors" in npz.files else npz[npz.files[0]])

    g = Graph()
    g.parse(output_dir / "inventory.ttl", format="turtle")

    files: list[dict[str, Any]] = []
    file_by_uri: dict[str, dict[str, Any]] = {}
    for f in g.subjects(RDF.type, CBM.File):
        path = str(g.value(f, CBM.path))
        language = None
        for hp in g.objects(f, CBM.hasPhase):
            language = str(hp).rsplit("#", 1)[-1] if "#" in str(hp) else None
        # file_type comes from cbm:type with a URI value
        ftype_uri = None
        for t in g.objects(f, CBM.type):
            su = str(t)
            if "cbm/type" in su:
                ftype_uri = _resolve_file_type_uri(su)
                break
        size_b = g.value(f, CBM.sizeBytes)
        sha = g.value(f, CBM.contentSha256)
        # language proper lives in cbm:language predicate if present, else guess by extension
        lang_lit = g.value(f, CBM.language)
        rec = {
            "uri": str(f),
            "path": path,
            "language": str(lang_lit) if lang_lit else None,
            "type": ftype_uri,
            "size": int(size_b) if size_b is not None else None,
            "contentSha256": str(sha) if sha is not None else None,
        }
        files.append(rec)
        file_by_uri[str(f)] = rec

    files.sort(key=lambda r: r["path"])

    imports: list[tuple[str, str]] = []
    imports_out: dict[str, list[str]] = {}
    imports_in: dict[str, list[str]] = {}
    for s, o in g.subject_objects(CBM.imports):
        s_path = file_by_uri.get(str(s), {}).get("path")
        o_path = file_by_uri.get(str(o), {}).get("path")
        if s_path and o_path:
            imports.append((s_path, o_path))
            imports_out.setdefault(s_path, []).append(o_path)
            imports_in.setdefault(o_path, []).append(s_path)

    tests: list[tuple[str, str]] = []
    tests_for_subject: dict[str, list[str]] = {}
    subjects_for_test: dict[str, list[str]] = {}
    for s, o in g.subject_objects(CBM.tests):
        test_path = file_by_uri.get(str(s), {}).get("path")
        subject_path = file_by_uri.get(str(o), {}).get("path")
        if test_path and subject_path:
            tests.append((test_path, subject_path))
            tests_for_subject.setdefault(subject_path, []).append(test_path)
            subjects_for_test.setdefault(test_path, []).append(subject_path)

    chunks: list[dict[str, Any]] = []
    chunk_uri_to_idx: dict[str, int] = {}
    for c in g.subjects(RDF.type, CBML2.Chunk):
        sym = g.value(c, CBML2.symbol)
        kind = g.value(c, CBML2.kind)
        in_file = g.value(c, CBML2.inFile)
        begin_line = g.value(c, CBML2.beginLine)
        end_line = g.value(c, CBML2.endLine)
        emb_row = g.value(c, CBML2.embeddingRow)
        sha = g.value(c, CBML2.contentSha256)
        chunks.append({
            "uri": str(c),
            "symbol": str(sym) if sym is not None else None,
            "kind": str(kind) if kind is not None else None,
            "file": file_by_uri.get(str(in_file), {}).get("path") if in_file else None,
            "beginLine": int(begin_line) if begin_line is not None else None,
            "endLine": int(end_line) if end_line is not None else None,
            "embeddingRow": int(emb_row) if emb_row is not None else None,
            "contentSha256": str(sha) if sha is not None else None,
        })
    chunks.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0))
    chunks_by_file: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        c["idx"] = i
        chunk_uri_to_idx[c["uri"]] = i
        if c["file"]:
            chunks_by_file.setdefault(c["file"], []).append(i)

    # chunk -> concepts via cbml3:lexicalizes
    chunk_concepts: dict[int, list[str]] = {}
    concept_chunks: dict[str, list[int]] = {}
    for s, o in g.subject_objects(CBML3.lexicalizes):
        idx = chunk_uri_to_idx.get(str(s))
        if idx is None:
            continue
        name = _concept_name_from_uri(str(o))
        if not name:
            continue
        chunk_concepts.setdefault(idx, []).append(name)
        concept_chunks.setdefault(name, []).append(idx)

    # cooccurrence neighbor index (descending weight)
    cooccur: dict[str, list[tuple[str, int]]] = {}
    for entry in concepts.get("cooccurrence", []) or []:
        if len(entry) != 3:
            continue
        a, b_, w = entry
        cooccur.setdefault(a, []).append((b_, int(w)))
        cooccur.setdefault(b_, []).append((a, int(w)))
    for k in cooccur:
        cooccur[k].sort(key=lambda t: t[1], reverse=True)

    file_by_path = {r["path"]: r for r in files}

    xrefs, xrefs_by_src_idx, xrefs_by_dst_idx = _load_xrefs(
        output_dir / "xrefs.jsonl", chunk_uri_to_idx,
    )

    rust_items, rust_items_by_file = _load_rust_items(
        output_dir / "rust_items.jsonl"
    )

    enrich_fs, enrich_cd, enrich_sp = _load_enrichments(
        output_dir / "enrichments.jsonl"
    )

    return Bundle(
        output_dir=output_dir,
        manifest=manifest,
        concepts=concepts,
        embeddings_meta=embeddings_meta,
        chunk_vectors=chunk_vectors,
        chunk_ids=chunk_ids,
        concept_vectors=concept_vectors,
        concept_ids=concept_ids,
        files=files,
        file_by_path=file_by_path,
        imports=imports,
        imports_out=imports_out,
        imports_in=imports_in,
        tests=tests,
        tests_for_subject=tests_for_subject,
        subjects_for_test=subjects_for_test,
        chunks=chunks,
        chunks_by_uri=chunk_uri_to_idx,
        chunks_by_file=chunks_by_file,
        chunk_concepts=chunk_concepts,
        concept_chunks=concept_chunks,
        cooccur=cooccur,
        xrefs=xrefs,
        xrefs_by_src_idx=xrefs_by_src_idx,
        xrefs_by_dst_idx=xrefs_by_dst_idx,
        rust_items=rust_items,
        rust_items_by_file=rust_items_by_file,
        enrichment_file_summary=enrich_fs,
        enrichment_concept_description=enrich_cd,
        enrichment_schema_purpose=enrich_sp,
    )


def _load_rust_items(
    sidecar_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    """Parse rust_items.jsonl into a flat list + per-file index.

    Stage 4 sidecar. Each line is one Rust item with at least one
    attribute, schema fixed by ``emit_bundle._emit_rust_items_sidecar``.
    Returns ``([], {})`` when the sidecar is absent — pre-Stage-4
    bundles have no rust_items.
    """
    items: list[dict[str, Any]] = []
    by_file: dict[str, list[int]] = {}
    if not sidecar_path.exists():
        return items, by_file
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = row.get("path")
        if not path:
            continue
        idx = len(items)
        items.append(row)
        by_file.setdefault(path, []).append(idx)
    return items, by_file


def _chunk_id_to_uri(chunk_id: str) -> str:
    """Mirror plugins.symbol_xrefs.graph_writer.chunk_iri.

    The xrefs sidecar uses raw chunk_id strings; the inventory keys
    chunks by their URI. We re-quote the chunk_id and look up via
    chunks_by_uri so the backend doesn't need to keep two parallel maps.
    """
    return f"{CBMI_NS}chunk/{urllib.parse.quote(chunk_id, safe='')}"


def _load_enrichments(
    sidecar_path: Path,
) -> tuple[dict[str, dict[str, Any]],
           dict[str, dict[str, Any]],
           dict[str, dict[str, Any]]]:
    """Partition enrichments.jsonl rows into per-kind dicts.

    Returns ``(file_summary, concept_description, schema_purpose)``
    where each is keyed by the row's ``target`` field. Missing file,
    empty file, or malformed rows are tolerated — the backend's L4
    surface is opt-in and silently absent on pre-L4 bundles.

    Each value is the full sidecar row: ``{target, kind, text, model,
    prompt_sha, target_sha, generated_at}``. The MCP handlers project
    that to a friendlier client shape; the backend just preserves it.
    """
    fs: dict[str, dict[str, Any]] = {}
    cd: dict[str, dict[str, Any]] = {}
    sp: dict[str, dict[str, Any]] = {}
    if not sidecar_path.exists():
        return fs, cd, sp
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = row.get("kind")
        target = row.get("target")
        if not kind or not target:
            continue
        if kind == "file_summary":
            fs[target] = row
        elif kind == "concept_description":
            cd[target] = row
        elif kind == "schema_purpose":
            sp[target] = row
        # Unknown kinds are silently dropped — forward-compat with
        # future enrichment kinds the backend doesn't know about yet.
    return fs, cd, sp


def _load_xrefs(
    sidecar_path: Path, chunks_by_uri: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[int, list[int]], dict[int, list[int]]]:
    """Parse xrefs.jsonl into idx-keyed indices.

    Each loaded edge is:
        {"src_idx": int, "dst_idx": int,
         "kind": str, "resolution": str, "resolver": str}
    Edges whose endpoints don't resolve to a chunk in the inventory are
    silently dropped — the inventory is the source of truth; if the
    sidecar and inventory drift the user shouldn't see ghost edges.
    """
    xrefs: list[dict[str, Any]] = []
    by_src: dict[int, list[int]] = {}
    by_dst: dict[int, list[int]] = {}
    if not sidecar_path.exists():
        return xrefs, by_src, by_dst
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        src_idx = chunks_by_uri.get(_chunk_id_to_uri(raw["src_chunk_id"]))
        dst_idx = chunks_by_uri.get(_chunk_id_to_uri(raw["dst_chunk_id"]))
        if src_idx is None or dst_idx is None:
            continue
        edge_idx = len(xrefs)
        xrefs.append({
            "src_idx": src_idx,
            "dst_idx": dst_idx,
            "kind": raw["kind"],
            "resolution": raw["resolution"],
            "resolver": raw["resolver"],
        })
        by_src.setdefault(src_idx, []).append(edge_idx)
        by_dst.setdefault(dst_idx, []).append(edge_idx)
    return xrefs, by_src, by_dst


def _xref_row(b: "Bundle", peer_idx: int, edge: dict[str, Any]) -> dict[str, Any]:
    """Render one xref endpoint as a row for /api/chunk and /api/file responses.

    ``peer_idx`` is the chunk at the *other end* of the edge (the dst for
    callers, the src for callees). Edge provenance (kind/resolution/resolver)
    is carried verbatim so the UI can dim heuristic results.
    """
    c = b.chunks[peer_idx]
    return {
        "idx": peer_idx,
        "symbol": c.get("symbol"),
        "kind": c.get("kind"),
        "file": c.get("file"),
        "beginLine": c.get("beginLine"),
        "endLine": c.get("endLine"),
        "xref_kind": edge["kind"],
        "resolution": edge["resolution"],
        "resolver": edge["resolver"],
    }


def _concept_name_from_uri(uri: str) -> str | None:
    """Extract 'foo' from '...#concept/foo' or '.../concept/foo'."""
    if "#concept/" in uri:
        return uri.rsplit("#concept/", 1)[-1]
    if "/concept/" in uri:
        return uri.rsplit("/concept/", 1)[-1]
    return None


def _bundles_root() -> Path:
    return Path(os.environ.get("CBM_BUNDLES_ROOT", "_tmp")).resolve()


def _validate_bundle_name(name: str) -> None:
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid bundle name: {name!r}")


def _bundle_info(path: Path) -> dict[str, Any] | None:
    """Return summary metadata if the dir contains a valid run_manifest.json."""
    manifest_path = path / "run_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        m = json.loads(manifest_path.read_text())
    except Exception:
        return None
    return {
        "name": path.name,
        "path": str(path),
        "repo_name": m.get("repo_name"),
        "commit_sha": m.get("commit_sha"),
        "generated_at": m.get("generated_at"),
        "tool_version": m.get("tool_version"),
        "files": (m.get("counts") or {}).get("files"),
    }


def list_bundles() -> list[dict[str, Any]]:
    """List discoverable bundles: anything under CBM_BUNDLES_ROOT with a
    run_manifest.json, plus the CBM_OUTPUT_DIR override if it lives outside
    the root.
    """
    seen: set[Path] = set()
    out: list[dict[str, Any]] = []

    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        info = _bundle_info(p)
        if info:
            out.append(info)
            seen.add(p)

    root = _bundles_root()
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            cp = child.resolve()
            if cp in seen:
                continue
            info = _bundle_info(child)
            if info:
                out.append(info)
                seen.add(cp)
    return out


def _default_bundle_name() -> str | None:
    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        # Only honor CBM_OUTPUT_DIR if it's actually a bundle. Otherwise the
        # basename leaks into the picker and the frontend later asks for it.
        if _bundle_info(p) is not None:
            return p.name
    items = list_bundles()
    return items[0]["name"] if items else None


def _resolve_bundle_path(name: str | None) -> Path:
    """Resolve a bundle name to an on-disk directory path.

    Raises HTTPException(404) when the requested bundle isn't found OR
    when the requested path lacks a run_manifest.json (i.e. isn't a valid
    bundle — guards against CBM_OUTPUT_DIR misconfigured to a parent dir).
    """
    if name:
        _validate_bundle_name(name)
        env_out = os.environ.get("CBM_OUTPUT_DIR")
        if env_out:
            p = Path(env_out).resolve()
            if p.name == name and _bundle_info(p) is not None:
                return p
        p = (_bundles_root() / name).resolve()
        if not p.is_dir() or _bundle_info(p) is None:
            raise HTTPException(
                status_code=404,
                detail=f"bundle '{name}' not found or missing run_manifest.json",
            )
        return p

    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        if _bundle_info(p) is not None:
            return p
    items = list_bundles()
    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"no bundles found in {_bundles_root()}; set CBM_OUTPUT_DIR "
                   "or place bundles under CBM_BUNDLES_ROOT",
        )
    return Path(items[0]["path"]).resolve()


@lru_cache(maxsize=4)
def _load_bundle_cached(path_str: str) -> Bundle:
    return load_bundle(Path(path_str))


def get_bundle(name: str | None = None) -> Bundle:
    path = _resolve_bundle_path(name)
    return _load_bundle_cached(str(path))


def _clear_bundle_cache() -> None:
    _load_bundle_cached.cache_clear()


get_bundle.cache_clear = _clear_bundle_cache  # type: ignore[attr-defined]


# -- Pydantic response models -------------------------------------------------

class SummaryResp(BaseModel):
    repo_name: str | None = None
    commit_sha: str | None = None
    generated_at: str | None = None
    tool_version: str | None = None
    counts: dict[str, int]
    files_by_language: dict[str, int]
    files_by_type: dict[str, int]
    embeddings_backend: str | None = None
    embeddings_dimension: int | None = None
    n_chunks: int = 0
    n_concepts: int = 0
    shacl_conforms: bool | None = None
    output_dir: str


class GraphNode(BaseModel):
    id: str
    label: str
    group: str | None = None
    weight: float | None = None
    meta: dict[str, Any] | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float | None = None


class GraphResp(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
    total_nodes_available: int | None = None


class ChunkResp(BaseModel):
    idx: int | None = None
    symbol: str | None
    kind: str | None
    file: str | None
    beginLine: int | None
    endLine: int | None
    embeddingRow: int | None
    score: float | None = None


class ChunkListResp(BaseModel):
    chunks: list[ChunkResp]
    total: int
    backend: str | None = None
    mode: str  # "semantic" | "lexical"


class BundleInfo(BaseModel):
    name: str
    path: str
    repo_name: str | None = None
    commit_sha: str | None = None
    generated_at: str | None = None
    tool_version: str | None = None
    files: int | None = None


class BundleListResp(BaseModel):
    bundles: list[BundleInfo]
    selected: str | None = None
    bundles_root: str


class ImpactResp(BaseModel):
    file: str
    depth: int
    direct_dependencies: list[str]
    direct_dependents: list[str]
    transitive_dependencies: list[str]
    transitive_dependents: list[str]
    related_tests: list[str]
    tested_subjects: list[str]
    concepts: list[str]
    chunks: list[ChunkResp]
    # Symbol-level transitive impact via cbmxr:Edge walks. Seeds are every
    # chunk in the file; ``symbol_callees`` walks outgoing call edges (this
    # file's chunks → who they reach), ``symbol_callers`` walks incoming
    # call edges (who reaches into this file's chunks). Lists exclude the
    # seed chunks themselves. Empty for bundles without xrefs.jsonl.
    symbol_callers: list[ChunkResp] = []
    symbol_callees: list[ChunkResp] = []
    truncated: bool = False


# -- FastAPI app --------------------------------------------------------------

app = FastAPI(title="codebase-mapper visualizer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Optionally expose the MCP server over streamable HTTP. Skipped unless
# CBM_MCP_TOKEN is set — no anonymous remote access by default.
if os.environ.get("CBM_MCP_TOKEN"):
    try:
        from frontend.mcp_server.http_transport import mount_mcp  # noqa: E402
        mount_mcp(app)
    except Exception:  # pragma: no cover — mount must never break the REST app
        import logging
        logging.getLogger("cbm").exception("failed to mount MCP HTTP transport")


@app.get("/api/bundles", response_model=BundleListResp)
def bundles() -> BundleListResp:
    items = list_bundles()
    return BundleListResp(
        bundles=[BundleInfo(**it) for it in items],
        selected=_default_bundle_name(),
        bundles_root=str(_bundles_root()),
    )


@app.get("/api/summary", response_model=SummaryResp)
def summary(bundle: str | None = Query(default=None)) -> SummaryResp:
    b = get_bundle(bundle)
    m = b.manifest
    return SummaryResp(
        repo_name=m.get("repo_name"),
        commit_sha=m.get("commit_sha"),
        generated_at=m.get("generated_at"),
        tool_version=m.get("tool_version"),
        counts=m.get("counts", {}),
        files_by_language=m.get("files_by_language", {}),
        files_by_type=m.get("files_by_type", {}),
        embeddings_backend=(b.embeddings_meta.get("backend") or {}).get("name"),
        embeddings_dimension=b.embeddings_meta.get("dimension"),
        n_chunks=b.embeddings_meta.get("n_chunks", 0),
        n_concepts=len(b.concepts.get("concepts", {})),
        shacl_conforms=(m.get("shacl_self_check") or {}).get("conforms"),
        output_dir=str(b.output_dir),
    )


@app.get("/api/file-graph", response_model=GraphResp)
def file_graph(
    limit: int = Query(default=400, ge=1, le=5000),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    b = get_bundle(bundle)
    # rank files by degree (in + out import edges) so the limit picks the most-connected core
    deg: dict[str, int] = {}
    for a, b_ in b.imports:
        deg[a] = deg.get(a, 0) + 1
        deg[b_] = deg.get(b_, 0) + 1
    ranked = sorted(b.files, key=lambda r: deg.get(r["path"], 0), reverse=True)
    selected = ranked[:limit]
    selected_paths = {r["path"] for r in selected}
    nodes = [
        GraphNode(
            id=r["path"],
            label=r["path"].rsplit("/", 1)[-1],
            group=r["language"] or r["type"] or "unknown",
            weight=float(deg.get(r["path"], 0)),
            meta={"path": r["path"], "type": r["type"], "language": r["language"], "size": r["size"]},
        )
        for r in selected
    ]
    edges = [
        GraphEdge(source=a, target=b_)
        for a, b_ in b.imports
        if a in selected_paths and b_ in selected_paths
    ]
    return GraphResp(
        nodes=nodes,
        edges=edges,
        truncated=len(b.files) > len(selected),
        total_nodes_available=len(b.files),
    )


@app.get("/api/symbol-graph", response_model=GraphResp)
def symbol_graph(
    limit: int = Query(default=400, ge=1, le=5000),
    kind: str = Query(default="calls",
                       description="Edge kind filter. 'all' includes every kind."),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    """Symbol-level call graph — nodes are chunks, edges are cbmxr:Edge.

    Same shape as /api/file-graph so the frontend reuses CytoscapeGraph
    unchanged. Nodes are ranked by call-degree (in + out edges of the
    chosen kind) so the limit picks the most-connected core.

    Node ``id`` is the chunk idx as a string — the frontend's
    onNodeClick navigates to /chunk/{id}.
    """
    b = get_bundle(bundle)

    selected_edges = [
        e for e in b.xrefs if kind == "all" or e["kind"] == kind
    ]
    deg: dict[int, int] = {}
    for e in selected_edges:
        deg[e["src_idx"]] = deg.get(e["src_idx"], 0) + 1
        deg[e["dst_idx"]] = deg.get(e["dst_idx"], 0) + 1

    # Stable order: degree desc, idx asc — keeps two runs reproducible.
    ranked_idxs = sorted(deg.keys(), key=lambda i: (-deg[i], i))
    selected_idxs = ranked_idxs[:limit]
    selected_set = set(selected_idxs)

    nodes = [
        GraphNode(
            id=str(i),
            label=b.chunks[i].get("symbol") or "—",
            group=b.chunks[i].get("kind") or "unknown",
            weight=float(deg.get(i, 0)),
            meta={
                "idx": i,
                "file": b.chunks[i].get("file"),
                "kind": b.chunks[i].get("kind"),
                "beginLine": b.chunks[i].get("beginLine"),
                "endLine": b.chunks[i].get("endLine"),
            },
        )
        for i in selected_idxs
    ]
    edges = [
        GraphEdge(source=str(e["src_idx"]), target=str(e["dst_idx"]))
        for e in selected_edges
        if e["src_idx"] in selected_set and e["dst_idx"] in selected_set
    ]
    return GraphResp(
        nodes=nodes,
        edges=edges,
        truncated=len(deg) > len(selected_idxs),
        total_nodes_available=len(deg),
    )


@app.get("/api/concept-graph", response_model=GraphResp)
def concept_graph(
    limit: int = Query(default=150, ge=1, le=2000),
    min_edge: int = Query(default=3, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    b = get_bundle(bundle)
    concepts = b.concepts.get("concepts", {})
    # top-N by frequency
    ranked = sorted(concepts.items(), key=lambda kv: kv[1].get("frequency", 0), reverse=True)
    selected = ranked[:limit]
    selected_set = {k for k, _ in selected}
    nodes = [
        GraphNode(
            id=k,
            label=v.get("label") or k,
            weight=float(v.get("frequency", 0)),
            meta={
                "alt_labels": v.get("alt_labels", []),
                "frequency": v.get("frequency", 0),
                "file_count": v.get("file_count", 0),
                "components": v.get("components", []),
            },
        )
        for k, v in selected
    ]
    edges = []
    for entry in b.concepts.get("cooccurrence", []):
        if len(entry) != 3:
            continue
        a, b_, w = entry
        if w < min_edge:
            continue
        if a in selected_set and b_ in selected_set:
            edges.append(GraphEdge(source=a, target=b_, weight=float(w)))
    return GraphResp(
        nodes=nodes,
        edges=edges,
        truncated=len(concepts) > len(selected),
        total_nodes_available=len(concepts),
    )


@app.get("/api/chunks", response_model=ChunkListResp)
def chunks(
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    bundle: str | None = Query(default=None),
) -> ChunkListResp:
    b = get_bundle(bundle)
    rows = b.chunks
    if q:
        ql = q.lower()
        rows = [r for r in rows if (r["symbol"] or "").lower().find(ql) >= 0
                or (r["file"] or "").lower().find(ql) >= 0]
    total = len(rows)
    rows = rows[offset:offset + limit]
    return ChunkListResp(
        chunks=[ChunkResp(**{k: r.get(k) for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")}) for r in rows],
        total=total,
        backend=(b.embeddings_meta.get("backend") or {}).get("name"),
        mode="lexical",
    )


class SearchReq(BaseModel):
    q: str
    k: int = 20


@app.post("/api/chunks/search", response_model=ChunkListResp)
def chunk_search(
    req: SearchReq,
    bundle: str | None = Query(default=None),
) -> ChunkListResp:
    b = get_bundle(bundle)
    backend_name = (b.embeddings_meta.get("backend") or {}).get("name") or ""
    is_sbert = "sentence-transformer" in backend_name.lower() or "sbert" in backend_name.lower() or "minilm" in backend_name.lower()
    if not is_sbert or b.chunk_vectors is None:
        # fall back to lexical
        ql = req.q.lower()
        scored = [
            (r, 1.0 if (r["symbol"] or "").lower().find(ql) >= 0
             else (0.5 if (r["file"] or "").lower().find(ql) >= 0 else 0.0))
            for r in b.chunks
        ]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        out = scored[:req.k]
        return ChunkListResp(
            chunks=[ChunkResp(**{k: r.get(k) for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")}, score=score) for r, score in out],
            total=len(scored),
            backend=backend_name,
            mode="lexical",
        )

    # semantic: embed query, cosine top-k
    from sentence_transformers import SentenceTransformer  # type: ignore
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    model = _get_model(model_name)
    q_vec = model.encode([req.q], normalize_embeddings=True)[0].astype("float32")
    sims = b.chunk_vectors @ q_vec  # rows are L2-normalized
    top_idx = np.argsort(-sims)[:req.k]
    chunk_by_row: dict[int, dict[str, Any]] = {r["embeddingRow"]: r for r in b.chunks if r["embeddingRow"] is not None}
    out_chunks: list[ChunkResp] = []
    for i in top_idx:
        row = int(i)
        r = chunk_by_row.get(row)
        if not r:
            continue
        out_chunks.append(ChunkResp(
            **{k: r.get(k) for k in ("symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")},
            score=float(sims[row]),
        ))
    return ChunkListResp(
        chunks=out_chunks,
        total=len(out_chunks),
        backend=backend_name,
        mode="semantic",
    )


@lru_cache(maxsize=1)
def _get_model(name: str):
    from sentence_transformers import SentenceTransformer  # type: ignore
    return SentenceTransformer(name)


@app.get("/api/chunk-blob/{sha}")
def chunk_blob(
    sha: str,
    bundle: str | None = Query(default=None),
) -> dict[str, str]:
    b = get_bundle(bundle)
    if not all(c in "0123456789abcdef" for c in sha) or len(sha) != 64:
        raise HTTPException(status_code=400, detail="invalid sha")
    p = b.output_dir / "blobs" / sha
    if not p.exists():
        raise HTTPException(status_code=404, detail="blob not found")
    try:
        text = p.read_text(errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"sha256": sha, "text": text[:20000]}


@app.get("/api/concept/{name}")
def concept_detail(
    name: str,
    cooccur_k: int = Query(default=30, ge=1, le=500),
    chunk_k: int = Query(default=50, ge=1, le=500),
    file_k: int = Query(default=100, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> dict[str, Any]:
    b = get_bundle(bundle)
    c = b.concepts.get("concepts", {}).get(name)
    if not c:
        raise HTTPException(status_code=404, detail="concept not found")
    files: list[str] = []
    for path, names in (b.concepts.get("per_path_concepts") or {}).items():
        if name in names:
            files.append(path)
            if len(files) >= file_k:
                break
    cooc = [{"name": n, "weight": w} for n, w in b.cooccur.get(name, [])[:cooccur_k]]
    chunk_idxs = b.concept_chunks.get(name, [])[:chunk_k]
    chunks = [
        {k: b.chunks[i].get(k) for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine")}
        for i in chunk_idxs
    ]
    return {
        "concept": c,
        "files": files,
        "cooccurring": cooc,
        "chunks": chunks,
        "components": c.get("components", []),
        "file_count_total": len([
            p for p, ns in (b.concepts.get("per_path_concepts") or {}).items() if name in ns
        ]),
        "chunk_count_total": len(b.concept_chunks.get(name, [])),
    }


@app.get("/api/file/{path:path}")
def file_detail(
    path: str,
    bundle: str | None = Query(default=None),
) -> dict[str, Any]:
    b = get_bundle(bundle)
    rec = b.file_by_path.get(path)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    chunk_idxs = b.chunks_by_file.get(path, [])
    chunks = [
        {k: b.chunks[i].get(k) for k in ("idx", "symbol", "kind", "beginLine", "endLine", "embeddingRow")}
        for i in chunk_idxs
    ]
    concepts = list((b.concepts.get("per_path_concepts") or {}).get(path, []))

    # Symbol-level xrefs aggregated over every chunk in the file. Dedup
    # by peer-chunk idx — if two chunks in this file both call X, the
    # user sees one row for X (the first edge's provenance wins).
    xrefs_out: list[dict[str, Any]] = []
    xrefs_in: list[dict[str, Any]] = []
    seen_out: set[int] = set()
    seen_in: set[int] = set()
    for ci in chunk_idxs:
        for e_idx in b.xrefs_by_src_idx.get(ci, []):
            edge = b.xrefs[e_idx]
            if edge["dst_idx"] in seen_out:
                continue
            seen_out.add(edge["dst_idx"])
            xrefs_out.append(_xref_row(b, edge["dst_idx"], edge))
        for e_idx in b.xrefs_by_dst_idx.get(ci, []):
            edge = b.xrefs[e_idx]
            if edge["src_idx"] in seen_in:
                continue
            seen_in.add(edge["src_idx"])
            xrefs_in.append(_xref_row(b, edge["src_idx"], edge))
    xrefs_out.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    xrefs_in.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))

    return {
        "file": rec,
        "imports_out": sorted(b.imports_out.get(path, [])),
        "imports_in": sorted(b.imports_in.get(path, [])),
        "chunks": chunks,
        "concepts": concepts,
        "xrefs_out": xrefs_out,
        "xrefs_in": xrefs_in,
    }


def _walk_paths(
    start: str,
    adjacency: dict[str, list[str]],
    depth: int,
    limit: int,
) -> tuple[list[str], bool]:
    """Breadth-first walk from a file path through a path adjacency index."""
    seen = {start}
    frontier = [start]
    out: list[str] = []
    truncated = False
    for _ in range(depth):
        next_frontier: list[str] = []
        for src in frontier:
            for dst in sorted(adjacency.get(src, [])):
                if dst in seen:
                    continue
                seen.add(dst)
                out.append(dst)
                next_frontier.append(dst)
                if len(out) >= limit:
                    return out, True
        frontier = next_frontier
        if not frontier:
            break
    return out, truncated


def _walk_xref_chunks(
    seeds: list[int],
    adjacency: dict[int, list[int]],
    edges: list[dict[str, Any]],
    peer_key: str,
    depth: int,
    limit: int,
) -> tuple[list[int], bool]:
    """BFS over the xref graph from a set of seed chunk indices.

    ``adjacency`` maps a chunk_idx to the edge_indices touching it
    (``xrefs_by_src_idx`` for downstream walks, ``xrefs_by_dst_idx`` for
    upstream). ``peer_key`` selects the other end of each edge:
    ``"dst_idx"`` when walking downstream, ``"src_idx"`` upstream.

    Seeds are marked seen so they don't appear in the result. Returns the
    reached chunk_idxs in discovery order plus a truncation flag.
    """
    seen = set(seeds)
    frontier = list(seeds)
    out: list[int] = []
    for _ in range(depth):
        next_frontier: list[int] = []
        for node in frontier:
            for e_idx in adjacency.get(node, []):
                peer = edges[e_idx][peer_key]
                if peer in seen:
                    continue
                seen.add(peer)
                out.append(peer)
                next_frontier.append(peer)
                if len(out) >= limit:
                    return out, True
        frontier = next_frontier
        if not frontier:
            break
    return out, False


@app.get("/api/impact/{path:path}", response_model=ImpactResp)
def impact(
    path: str,
    depth: int = Query(default=2, ge=1, le=5),
    limit: int = Query(default=100, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> ImpactResp:
    b = get_bundle(bundle)
    if path not in b.file_by_path:
        raise HTTPException(status_code=404, detail="file not found")

    dependencies, dep_truncated = _walk_paths(path, b.imports_out, depth, limit)
    dependents, rev_truncated = _walk_paths(path, b.imports_in, depth, limit)
    chunk_idxs = b.chunks_by_file.get(path, [])[:25]
    chunks = [
        ChunkResp(**{
            k: b.chunks[i].get(k)
            for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")
        })
        for i in chunk_idxs
    ]

    related_tests = set(b.tests_for_subject.get(path, []))
    tested_subjects = set(b.subjects_for_test.get(path, []))
    for impacted in dependents:
        related_tests.update(b.tests_for_subject.get(impacted, []))

    # Symbol-level transitive walk. Seed = every chunk in the file. The
    # outgoing walk follows xrefs_by_src_idx (this file's chunks → who they
    # reach); the incoming walk follows xrefs_by_dst_idx (who reaches into
    # this file). Seeds are excluded from the results.
    file_chunk_seeds = list(b.chunks_by_file.get(path, []))
    callee_idxs, callees_trunc = _walk_xref_chunks(
        file_chunk_seeds, b.xrefs_by_src_idx, b.xrefs, "dst_idx", depth, limit,
    )
    caller_idxs, callers_trunc = _walk_xref_chunks(
        file_chunk_seeds, b.xrefs_by_dst_idx, b.xrefs, "src_idx", depth, limit,
    )

    def _as_chunk_resp(i: int) -> ChunkResp:
        return ChunkResp(**{
            k: b.chunks[i].get(k)
            for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")
        })

    symbol_callees = [_as_chunk_resp(i) for i in callee_idxs]
    symbol_callers = [_as_chunk_resp(i) for i in caller_idxs]
    symbol_callees.sort(key=lambda r: (r.file or "", r.beginLine or 0, r.symbol or ""))
    symbol_callers.sort(key=lambda r: (r.file or "", r.beginLine or 0, r.symbol or ""))

    return ImpactResp(
        file=path,
        depth=depth,
        direct_dependencies=sorted(b.imports_out.get(path, [])),
        direct_dependents=sorted(b.imports_in.get(path, [])),
        transitive_dependencies=dependencies,
        transitive_dependents=dependents,
        related_tests=sorted(related_tests),
        tested_subjects=sorted(tested_subjects),
        concepts=list((b.concepts.get("per_path_concepts") or {}).get(path, [])),
        chunks=chunks,
        symbol_callers=symbol_callers,
        symbol_callees=symbol_callees,
        truncated=dep_truncated or rev_truncated or callees_trunc or callers_trunc,
    )


@app.get("/api/chunk/{idx}")
def chunk_detail(
    idx: int,
    bundle: str | None = Query(default=None),
) -> dict[str, Any]:
    b = get_bundle(bundle)
    if idx < 0 or idx >= len(b.chunks):
        raise HTTPException(status_code=404, detail="chunk idx out of range")
    rec = b.chunks[idx]
    concepts = list(b.chunk_concepts.get(idx, []))
    blob_preview: str | None = None
    sha = rec.get("contentSha256")
    if sha and len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha):
        p = b.output_dir / "blobs" / sha
        if p.exists():
            try:
                blob_preview = p.read_text(errors="replace")[:8000]
            except Exception:
                blob_preview = None

    # Symbol-level xrefs: callers = edges where this chunk is the dst;
    # callees = edges where this chunk is the src.
    callers = [
        _xref_row(b, b.xrefs[e]["src_idx"], b.xrefs[e])
        for e in b.xrefs_by_dst_idx.get(idx, [])
    ]
    callees = [
        _xref_row(b, b.xrefs[e]["dst_idx"], b.xrefs[e])
        for e in b.xrefs_by_src_idx.get(idx, [])
    ]
    callers.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    callees.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))

    return {
        "chunk": rec,
        "concepts": concepts,
        "blob_preview": blob_preview,
        "callers": callers,
        "callees": callees,
    }


@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

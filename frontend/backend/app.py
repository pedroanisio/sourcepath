"""FastAPI backend for visualizing a codebase-mapper output bundle.

Run:
    uvicorn frontend.backend.app:app --reload --port 8000 \
        --factory  # if using make_app(output_dir=...)
or simpler:
    CBM_OUTPUT_DIR=_tmp/usl-ng-core-map uvicorn frontend.backend.app:app --port 8000
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
    imports: list[tuple[str, str]]  # (src_path, dst_path)
    chunks: list[dict[str, Any]]  # symbol, kind, file, beginLine, endLine, contentSha256


def _resolve_file_type_uri(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def load_bundle(output_dir: Path) -> Bundle:
    if not output_dir.exists():
        raise FileNotFoundError(f"output dir not found: {output_dir}")

    manifest = json.loads((output_dir / "run_manifest.json").read_text())
    concepts = json.loads((output_dir / "concepts.json").read_text())
    embeddings_meta = json.loads((output_dir / "embeddings_meta.json").read_text())

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
    for s, o in g.subject_objects(CBM.imports):
        s_path = file_by_uri.get(str(s), {}).get("path")
        o_path = file_by_uri.get(str(o), {}).get("path")
        if s_path and o_path:
            imports.append((s_path, o_path))

    chunks: list[dict[str, Any]] = []
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
        imports=imports,
        chunks=chunks,
    )


@lru_cache(maxsize=1)
def get_bundle() -> Bundle:
    out = os.environ.get("CBM_OUTPUT_DIR", "_tmp/usl-ng-core-map")
    return load_bundle(Path(out).resolve())


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


# -- FastAPI app --------------------------------------------------------------

app = FastAPI(title="codebase-mapper visualizer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/summary", response_model=SummaryResp)
def summary() -> SummaryResp:
    b = get_bundle()
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
def file_graph(limit: int = Query(default=400, ge=1, le=5000)) -> GraphResp:
    b = get_bundle()
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


@app.get("/api/concept-graph", response_model=GraphResp)
def concept_graph(
    limit: int = Query(default=150, ge=1, le=2000),
    min_edge: int = Query(default=3, ge=1, le=1000),
) -> GraphResp:
    b = get_bundle()
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
) -> ChunkListResp:
    b = get_bundle()
    rows = b.chunks
    if q:
        ql = q.lower()
        rows = [r for r in rows if (r["symbol"] or "").lower().find(ql) >= 0
                or (r["file"] or "").lower().find(ql) >= 0]
    total = len(rows)
    rows = rows[offset:offset + limit]
    return ChunkListResp(
        chunks=[ChunkResp(**{k: r.get(k) for k in ("symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")}) for r in rows],
        total=total,
        backend=(b.embeddings_meta.get("backend") or {}).get("name"),
        mode="lexical",
    )


class SearchReq(BaseModel):
    q: str
    k: int = 20


@app.post("/api/chunks/search", response_model=ChunkListResp)
def chunk_search(req: SearchReq) -> ChunkListResp:
    b = get_bundle()
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
            chunks=[ChunkResp(**{k: r.get(k) for k in ("symbol", "kind", "file", "beginLine", "endLine", "embeddingRow")}, score=score) for r, score in out],
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
def chunk_blob(sha: str) -> dict[str, str]:
    b = get_bundle()
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
def concept_detail(name: str) -> dict[str, Any]:
    b = get_bundle()
    c = b.concepts.get("concepts", {}).get(name)
    if not c:
        raise HTTPException(status_code=404, detail="concept not found")
    # files that lexicalize this concept
    files: list[str] = []
    for path, names in (b.concepts.get("per_path_concepts") or {}).items():
        if name in names:
            files.append(path)
            if len(files) > 200:
                break
    return {"concept": c, "files": files}


@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

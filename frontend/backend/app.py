"""FastAPI backend for visualizing codebase-mapper output bundles."""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

try:  # Support both `frontend.backend.app` and test-time `import app`.
    from .serving.application.bundle_data import (
        Bundle,
        _bundle_info,
        _bundles_root,
        _clear_bundle_cache,
        _concept_name_from_uri,
        _default_bundle_name,
        _load_bundle_cached,
        _resolve_bundle_path,
        _resolve_file_type_uri,
        _validate_bundle_name,
        get_bundle,
        list_bundles,
        load_bundle,
    )
    from .serving.application.bundles import list_bundles_response
    from .serving.application.chunks import (
        _get_model,
        get_chunk_blob_response,
        get_chunk_detail_response,
        list_chunks_response,
        search_chunks_response,
    )
    from .serving.application.concepts import get_concept_detail_response
    from .serving.application.files import get_file_detail_response
    from .serving.application.graphs import (
        build_concept_graph_response,
        build_file_graph_response,
        build_symbol_graph_response,
    )
    from .serving.application.health import health_response
    from .serving.application.impact import get_impact_response
    from .serving.application.summary import build_summary_response
except ImportError:  # pragma: no cover - exercised by backend tests importing app as a script module.
    from serving.application.bundle_data import (
        Bundle,
        _bundle_info,
        _bundles_root,
        _clear_bundle_cache,
        _concept_name_from_uri,
        _default_bundle_name,
        _load_bundle_cached,
        _resolve_bundle_path,
        _resolve_file_type_uri,
        _validate_bundle_name,
        get_bundle,
        list_bundles,
        load_bundle,
    )
    from serving.application.bundles import list_bundles_response
    from serving.application.chunks import (
        _get_model,
        get_chunk_blob_response,
        get_chunk_detail_response,
        list_chunks_response,
        search_chunks_response,
    )
    from serving.application.concepts import get_concept_detail_response
    from serving.application.files import get_file_detail_response
    from serving.application.graphs import (
        build_concept_graph_response,
        build_file_graph_response,
        build_symbol_graph_response,
    )
    from serving.application.health import health_response
    from serving.application.impact import get_impact_response
    from serving.application.summary import build_summary_response
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
    model_config = ConfigDict(extra="allow")
    idx: int | None = None
    symbol: str | None
    kind: str | None
    file: str | None
    beginLine: int | None
    endLine: int | None
    embeddingRow: int | None
    score: float | None = None
    xref_kind: str | None = None
    resolution: str | None = None
    resolver: str | None = None


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


# -- Detail endpoints: response models mirror frontend/ui/src/api.ts ---------

# `extra="allow"` is deliberate: L4 enrichment plugins decorate these
# responses by mutating the returned dict (e.g. adding `llm_summary`),
# and forbidding unknown fields would lock cbm out of forward-compatible
# enrichment. The model still validates *known* fields strictly, which
# is the regression-relevant property.

class _ConceptInfo(BaseModel):
    """Inner `concept` block — mirrors api.ts::ConceptDetail.concept."""
    model_config = ConfigDict(extra="allow")
    label: str
    alt_labels: list[str] = []
    components: list[str] = []
    frequency: int
    file_count: int
    embedding_row: int | None = None
    # Curated-vocab fields. Present only on concepts that matched a term
    # in the bundled vocabulary; pre-vocab bundles return them as absent.
    kind: str | None = None
    broader: str | None = None


class _CooccurringConcept(BaseModel):
    name: str
    weight: int | float


class _ConceptChunk(BaseModel):
    """Lean chunk shape returned by /api/concept/{name}."""
    model_config = ConfigDict(extra="allow")
    idx: int | None = None
    symbol: str | None = None
    kind: str | None = None
    file: str | None = None
    beginLine: int | None = None
    endLine: int | None = None


class ConceptDetailResp(BaseModel):
    """Mirrors api.ts::ConceptDetail. extra="allow" so L4 plugins may add
    `llm_description` (and any future enrichment field) without breaking
    serialization."""
    model_config = ConfigDict(extra="allow")
    concept: _ConceptInfo
    files: list[str]
    cooccurring: list[_CooccurringConcept]
    chunks: list[_ConceptChunk]
    components: list[str]
    file_count_total: int
    chunk_count_total: int


class _FileBlock(BaseModel):
    """Inner `file` block returned by /api/file/{path}. Mirrors
    api.ts::FileDetail.file but admits the full FileRecord shape via
    extra='allow' — the underlying record carries more fields than the
    UI consumes (uri, contentSha256, etc.)."""
    model_config = ConfigDict(extra="allow")
    path: str
    language: str | None = None
    type: str | None = None
    size: int | None = None
    contentSha256: str | None = None


class _FileChunk(BaseModel):
    model_config = ConfigDict(extra="allow")
    idx: int
    symbol: str | None = None
    kind: str | None = None
    beginLine: int | None = None
    endLine: int | None = None
    embeddingRow: int | None = None


class FileDetailResp(BaseModel):
    """Mirrors api.ts::FileDetail. extra="allow" for forward-compatible
    L4 fields (`llm_summary`, `llm_schema_purpose`, …)."""
    model_config = ConfigDict(extra="allow")
    file: _FileBlock
    imports_out: list[str]
    imports_in: list[str]
    chunks: list[_FileChunk]
    concepts: list[str]
    # ChunkResp shape suffices for xref rows — the backend reuses it via
    # `_xref_row`. Optional because pre-Phase-9 bundles emit no xrefs.
    xrefs_out: list[ChunkResp] = []
    xrefs_in: list[ChunkResp] = []


# -- FastAPI app --------------------------------------------------------------

from codebase_mapper.shared_kernel.constants import TOOL_VERSION as _CBM_TOOL_VERSION
app = FastAPI(title="codebase-mapper visualizer", version=_CBM_TOOL_VERSION)
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
    return BundleListResp(**list_bundles_response())


@app.get("/api/summary", response_model=SummaryResp)
def summary(bundle: str | None = Query(default=None)) -> SummaryResp:
    return SummaryResp(**build_summary_response(bundle))


@app.get("/api/file-graph", response_model=GraphResp)
def file_graph(
    limit: int = Query(default=400, ge=1, le=5000),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    return GraphResp(**build_file_graph_response(limit, bundle))


@app.get("/api/symbol-graph", response_model=GraphResp)
def symbol_graph(
    limit: int = Query(default=400, ge=1, le=5000),
    kind: str = Query(default="calls",
                       description="Edge kind filter. 'all' includes every kind."),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    return GraphResp(**build_symbol_graph_response(limit, kind, bundle))


@app.get("/api/concept-graph", response_model=GraphResp)
def concept_graph(
    limit: int = Query(default=150, ge=1, le=2000),
    min_edge: int = Query(default=3, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> GraphResp:
    return GraphResp(**build_concept_graph_response(limit, min_edge, bundle))


@app.get("/api/chunks", response_model=ChunkListResp)
def chunks(
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    bundle: str | None = Query(default=None),
) -> ChunkListResp:
    return ChunkListResp(**list_chunks_response(q, limit, offset, bundle))


class SearchReq(BaseModel):
    q: str
    k: int = 20


@app.post("/api/chunks/search", response_model=ChunkListResp)
def chunk_search(req: SearchReq, bundle: str | None = Query(default=None)) -> ChunkListResp:
    return ChunkListResp(**search_chunks_response(req.q, req.k, bundle))


@app.get("/api/chunk-blob/{sha}")
def chunk_blob(sha: str, bundle: str | None = Query(default=None)) -> dict[str, str]:
    return get_chunk_blob_response(sha, bundle)


@app.get("/api/concept/{name}", response_model=ConceptDetailResp)
def concept_detail(
    name: str,
    cooccur_k: int = Query(default=30, ge=1, le=500),
    chunk_k: int = Query(default=50, ge=1, le=500),
    file_k: int = Query(default=100, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> dict[str, Any]:
    return get_concept_detail_response(name, cooccur_k, chunk_k, file_k, bundle)


@app.get("/api/file/{path:path}", response_model=FileDetailResp)
def file_detail(path: str, bundle: str | None = Query(default=None)) -> dict[str, Any]:
    return get_file_detail_response(path, bundle)


@app.get("/api/impact/{path:path}", response_model=ImpactResp)
def impact(
    path: str,
    depth: int = Query(default=2, ge=1, le=5),
    limit: int = Query(default=100, ge=1, le=1000),
    bundle: str | None = Query(default=None),
) -> ImpactResp:
    return ImpactResp(**get_impact_response(path, depth, limit, bundle))


@app.get("/api/chunk/{idx}")
def chunk_detail(idx: int, bundle: str | None = Query(default=None)) -> dict[str, Any]:
    return get_chunk_detail_response(idx, bundle)


@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return health_response()

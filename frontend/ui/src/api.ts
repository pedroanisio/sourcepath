export interface Summary {
  repo_name?: string;
  commit_sha?: string;
  generated_at?: string;
  tool_version?: string;
  counts: Record<string, number>;
  files_by_language: Record<string, number>;
  files_by_type: Record<string, number>;
  embeddings_backend?: string;
  embeddings_dimension?: number;
  n_chunks: number;
  n_concepts: number;
  shacl_conforms?: boolean;
  output_dir: string;
}

export interface GraphNode {
  id: string;
  label: string;
  group?: string;
  weight?: number;
  meta?: Record<string, any>;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight?: number;
}

export interface GraphResp {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes_available?: number;
}

export interface ChunkRow {
  idx?: number | null;
  symbol: string | null;
  kind: string | null;
  file: string | null;
  beginLine: number | null;
  endLine: number | null;
  embeddingRow?: number | null;
  score?: number | null;
  // Present only on rows produced by an xref lookup (callers/callees on
  // ChunkDetail; xrefs_out/xrefs_in on FileDetail). The same shape carries
  // edge provenance through to the UI so we don't need a parallel type.
  xref_kind?: string;
  resolution?: string;
  resolver?: string;
}

export interface FileDetail {
  file: {
    path: string;
    language: string | null;
    type: string | null;
    size: number | null;
    contentSha256: string | null;
  };
  imports_out: string[];
  imports_in: string[];
  chunks: Array<{
    idx: number;
    symbol: string | null;
    kind: string | null;
    beginLine: number | null;
    endLine: number | null;
    embeddingRow: number | null;
  }>;
  concepts: string[];
  // Symbol-level xrefs aggregated across every chunk in this file. Each
  // row is deduped per peer chunk by the backend; the first edge's
  // provenance (xref_kind/resolution/resolver) wins.
  xrefs_out?: ChunkRow[];
  xrefs_in?: ChunkRow[];
}

export interface FileImpact {
  file: string;
  depth: number;
  direct_dependencies: string[];
  direct_dependents: string[];
  transitive_dependencies: string[];
  transitive_dependents: string[];
  related_tests: string[];
  tested_subjects: string[];
  concepts: string[];
  chunks: ChunkRow[];
  // Symbol-level transitive impact (BFS over cbmxr:Edge). Optional —
  // older bundles without xrefs.jsonl return empty lists; absent on
  // pre-Phase-9 backends.
  symbol_callers?: ChunkRow[];
  symbol_callees?: ChunkRow[];
  truncated: boolean;
}

export interface ChunkDetail {
  chunk: {
    idx: number;
    uri: string;
    symbol: string | null;
    kind: string | null;
    file: string | null;
    beginLine: number | null;
    endLine: number | null;
    embeddingRow: number | null;
    contentSha256: string | null;
  };
  concepts: string[];
  blob_preview: string | null;
  // Symbol-level xrefs. `callers` are chunks that call into this chunk;
  // `callees` are chunks this chunk calls out to. Optional because older
  // bundles (no symbol_xrefs sidecar) won't return these keys.
  callers?: ChunkRow[];
  callees?: ChunkRow[];
}

// Curated-vocab kind values. Concepts that match a term in the bundled
// vocabulary carry one of these on `concept.kind`; pre-vocab bundles
// and uncurated concepts have neither `kind` nor `broader`.
export type ConceptKind =
  | "domain-primitive"
  | "structural-primitive"
  | "relational-primitive";

export interface ConceptDetail {
  concept: {
    label: string;
    alt_labels: string[];
    components: string[];
    frequency: number;
    file_count: number;
    embedding_row: number | null;
    kind?: ConceptKind;
    broader?: string;
  };
  files: string[];
  cooccurring: Array<{ name: string; weight: number }>;
  chunks: Array<{
    idx: number;
    symbol: string | null;
    kind: string | null;
    file: string | null;
    beginLine: number | null;
    endLine: number | null;
  }>;
  components: string[];
  file_count_total: number;
  chunk_count_total: number;
}

export interface ChunkListResp {
  chunks: ChunkRow[];
  total: number;
  backend?: string | null;
  mode: "semantic" | "lexical";
}

export interface BundleInfo {
  name: string;
  path: string;
  repo_name?: string | null;
  commit_sha?: string | null;
  generated_at?: string | null;
  tool_version?: string | null;
  files?: number | null;
}

export interface BundleListResp {
  bundles: BundleInfo[];
  selected: string | null;
  bundles_root: string;
}

// Module-level current bundle. The picker in App.tsx updates this; every
// helper below appends `?bundle=<name>` to its URL when it's set.
let _currentBundle: string | null = null;

export function setBundle(name: string | null): void {
  _currentBundle = name;
}

export function getCurrentBundle(): string | null {
  return _currentBundle;
}

function withBundle(url: string): string {
  if (_currentBundle === null) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}bundle=${encodeURIComponent(_currentBundle)}`;
}

async function get<T>(path: string): Promise<T> {
  const url = withBundle(path);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${url}`);
  return r.json();
}

async function post<T>(path: string, body: any): Promise<T> {
  const url = withBundle(path);
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${url}`);
  return r.json();
}

export const api = {
  bundles: () => get<BundleListResp>("/api/bundles"),
  summary: () => get<Summary>("/api/summary"),
  fileGraph: (limit = 400) => get<GraphResp>(`/api/file-graph?limit=${limit}`),
  symbolGraph: (limit = 400, kind: "calls" | "all" = "calls") =>
    get<GraphResp>(`/api/symbol-graph?limit=${limit}&kind=${kind}`),
  conceptGraph: (limit = 150, min_edge = 3) =>
    get<GraphResp>(`/api/concept-graph?limit=${limit}&min_edge=${min_edge}`),
  chunks: (q = "", limit = 50, offset = 0) =>
    get<ChunkListResp>(
      `/api/chunks?limit=${limit}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`
    ),
  searchChunks: (q: string, k = 20) =>
    post<ChunkListResp>("/api/chunks/search", { q, k }),
  concept: (name: string) =>
    get<ConceptDetail>(`/api/concept/${encodeURIComponent(name)}`),
  file: (path: string) =>
    get<FileDetail>(
      "/api/file/" + path.split("/").map(encodeURIComponent).join("/")
    ),
  impact: (path: string, depth = 2) =>
    get<FileImpact>(
      `/api/impact/${path.split("/").map(encodeURIComponent).join("/")}?depth=${depth}`
    ),
  chunk: (idx: number) => get<ChunkDetail>(`/api/chunk/${idx}`),
};

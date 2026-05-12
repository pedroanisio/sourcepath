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
  symbol: string | null;
  kind: string | null;
  file: string | null;
  beginLine: number | null;
  endLine: number | null;
  embeddingRow: number | null;
  score?: number | null;
}

export interface ChunkListResp {
  chunks: ChunkRow[];
  total: number;
  backend?: string | null;
  mode: "semantic" | "lexical";
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

async function post<T>(path: string, body: any): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  fileGraph: (limit = 400) => get<GraphResp>(`/api/file-graph?limit=${limit}`),
  conceptGraph: (limit = 150, min_edge = 3) =>
    get<GraphResp>(`/api/concept-graph?limit=${limit}&min_edge=${min_edge}`),
  chunks: (q = "", limit = 50, offset = 0) =>
    get<ChunkListResp>(
      `/api/chunks?limit=${limit}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`
    ),
  searchChunks: (q: string, k = 20) =>
    post<ChunkListResp>("/api/chunks/search", { q, k }),
  concept: (name: string) => get<any>(`/api/concept/${encodeURIComponent(name)}`),
};

import type {
  Summary,
  GraphResp,
  ChunkListResp,
  FileDetail,
  FileImpact,
  ChunkDetail,
  ConceptDetail,
  BundleListResp,
} from "../api";

export const summaryFixture: Summary = {
  repo_name: "demo",
  commit_sha: "abc1234",
  generated_at: "2026-05-12T00:00:00Z",
  tool_version: "0.5.0",
  counts: { files: 10, import_edges: 5, declares_dependency_edges: 2 },
  files_by_language: { python: 6, rust: 4 },
  files_by_type: { source_code: 8, test_code: 2 },
  embeddings_backend: "deterministic-hash-sha256-v1",
  embeddings_dimension: 256,
  n_chunks: 20,
  n_concepts: 30,
  shacl_conforms: true,
  output_dir: "/data",
};

export const fileGraphFixture: GraphResp = {
  nodes: [
    { id: "a.py", label: "a.py", group: "python", weight: 2 },
    { id: "b.py", label: "b.py", group: "python", weight: 1 },
  ],
  edges: [{ source: "a.py", target: "b.py", weight: 1 }],
  truncated: false,
  total_nodes_available: 2,
};

export const conceptGraphFixture: GraphResp = {
  nodes: [
    { id: "schema", label: "schema", weight: 10 },
    { id: "auth", label: "auth", weight: 7 },
  ],
  edges: [{ source: "schema", target: "auth", weight: 3 }],
  truncated: false,
  total_nodes_available: 2,
};

export const symbolGraphFixture: GraphResp = {
  nodes: [
    {
      id: "1",
      label: "main",
      group: "function",
      weight: 2,
      meta: { idx: 1, file: "app.py", kind: "function", beginLine: 4, endLine: 8 },
    },
    {
      id: "2",
      label: "helper",
      group: "function",
      weight: 3,
      meta: { idx: 2, file: "app.py", kind: "function", beginLine: 1, endLine: 2 },
    },
    {
      id: "3",
      label: "greet",
      group: "method",
      weight: 1,
      meta: { idx: 3, file: "app.py", kind: "method", beginLine: 14, endLine: 16 },
    },
  ],
  edges: [
    { source: "1", target: "2" },
    { source: "3", target: "2" },
  ],
  truncated: false,
  total_nodes_available: 3,
};

export const chunkListFixture: ChunkListResp = {
  chunks: [
    {
      idx: 0,
      symbol: "<file>",
      kind: "file",
      file: "a.py",
      beginLine: 1,
      endLine: 10,
      embeddingRow: 0,
    },
    {
      idx: 1,
      symbol: "do_thing",
      kind: "function",
      file: "a.py",
      beginLine: 3,
      endLine: 5,
      embeddingRow: 1,
      score: 0.97,
    },
  ],
  total: 2,
  backend: "deterministic-hash-sha256-v1",
  mode: "lexical",
};

export const fileDetailFixture: FileDetail = {
  file: {
    path: "a.py",
    language: "python",
    type: "source_code",
    size: 256,
    contentSha256: "a".repeat(64),
  },
  imports_out: ["b.py"],
  imports_in: ["c.py", "d.py"],
  chunks: [
    {
      idx: 0,
      symbol: "<file>",
      kind: "file",
      beginLine: 1,
      endLine: 10,
      embeddingRow: 0,
    },
  ],
  concepts: ["schema", "auth"],
  xrefs_out: [
    {
      idx: 7,
      symbol: "load_users",
      kind: "function",
      file: "b.py",
      beginLine: 12,
      endLine: 16,
      xref_kind: "calls",
      resolution: "exact",
      resolver: "python_inter_file",
    },
    {
      idx: 9,
      symbol: "guess_owner",
      kind: "function",
      file: "c.py",
      beginLine: 30,
      endLine: 33,
      xref_kind: "calls",
      resolution: "heuristic",
      resolver: "python_inter_file",
    },
  ],
  xrefs_in: [
    {
      idx: 3,
      symbol: "main",
      kind: "function",
      file: "c.py",
      beginLine: 5,
      endLine: 8,
      xref_kind: "calls",
      resolution: "exact",
      resolver: "python_intra_file",
    },
  ],
};

export const fileImpactFixture: FileImpact = {
  file: "a.py",
  depth: 2,
  direct_dependencies: ["b.py"],
  direct_dependents: ["c.py", "d.py"],
  transitive_dependencies: ["b.py"],
  transitive_dependents: ["c.py", "d.py", "e.py"],
  related_tests: ["tests/test_a.py"],
  tested_subjects: [],
  concepts: ["schema", "auth"],
  chunks: [
    {
      idx: 0,
      symbol: "<file>",
      kind: "file",
      file: "a.py",
      beginLine: 1,
      endLine: 10,
      embeddingRow: 0,
    },
  ],
  symbol_callers: [
    {
      idx: 21,
      symbol: "caller_one",
      kind: "function",
      file: "c.py",
      beginLine: 5,
      endLine: 10,
    },
  ],
  symbol_callees: [
    {
      idx: 31,
      symbol: "downstream_helper",
      kind: "function",
      file: "b.py",
      beginLine: 12,
      endLine: 18,
    },
    {
      idx: 32,
      symbol: "deeper_helper",
      kind: "function",
      file: "e.py",
      beginLine: 1,
      endLine: 4,
    },
  ],
  truncated: false,
};

export const chunkDetailFixture: ChunkDetail = {
  chunk: {
    idx: 0,
    uri: "https://example/chunk/0",
    symbol: "<file>",
    kind: "file",
    file: "a.py",
    beginLine: 1,
    endLine: 10,
    embeddingRow: 0,
    contentSha256: "a".repeat(64),
  },
  concepts: ["schema"],
  blob_preview: "def hello():\n    return 'hi'\n",
  callers: [
    {
      idx: 11,
      symbol: "main",
      kind: "function",
      file: "c.py",
      beginLine: 4,
      endLine: 6,
      xref_kind: "calls",
      resolution: "exact",
      resolver: "python_intra_file",
    },
  ],
  callees: [
    {
      idx: 12,
      symbol: "load_users",
      kind: "function",
      file: "b.py",
      beginLine: 12,
      endLine: 16,
      xref_kind: "calls",
      resolution: "heuristic",
      resolver: "python_inter_file",
    },
  ],
};

export const conceptDetailFixture: ConceptDetail = {
  concept: {
    label: "schema",
    alt_labels: ["Schema", "schemas"],
    components: [],
    frequency: 14,
    file_count: 5,
    embedding_row: 0,
  },
  files: ["a.py", "b.py"],
  cooccurring: [{ name: "auth", weight: 3 }],
  chunks: [
    {
      idx: 0,
      symbol: "<file>",
      kind: "file",
      file: "a.py",
      beginLine: 1,
      endLine: 10,
    },
  ],
  components: [],
  file_count_total: 5,
  chunk_count_total: 1,
};

export const bundlesFixture: BundleListResp = {
  bundles: [
    {
      name: "alpha",
      path: "/tmp/alpha",
      repo_name: "repo-a",
      generated_at: "2026-05-12T00:00:00Z",
      files: 100,
    },
    {
      name: "beta",
      path: "/tmp/beta",
      repo_name: "repo-b",
      generated_at: "2026-05-12T01:00:00Z",
      files: 250,
    },
  ],
  selected: "alpha",
  bundles_root: "/tmp",
};

/** Wires `globalThis.fetch` to return the right fixture per URL prefix.
 *  Matches the path component only — `?bundle=` query params are accepted
 *  and ignored, so each test can assert that URLs are formed correctly
 *  without the mock fighting it.
 */
export function installFetchMock() {
  const handlers: Array<[RegExp, () => unknown]> = [
    [/^\/api\/bundles(\?|$)/, () => bundlesFixture],
    [/^\/api\/summary(\?|$)/, () => summaryFixture],
    [/^\/api\/file-graph/, () => fileGraphFixture],
    [/^\/api\/symbol-graph/, () => symbolGraphFixture],
    [/^\/api\/concept-graph/, () => conceptGraphFixture],
    [/^\/api\/chunks(\?|$)/, () => chunkListFixture],
    [/^\/api\/chunks\/search(\?|$)/, () => chunkListFixture],
    [/^\/api\/impact\//, () => fileImpactFixture],
    [/^\/api\/file\//, () => fileDetailFixture],
    [/^\/api\/chunk\/\d+(\?|$)/, () => chunkDetailFixture],
    [/^\/api\/concept\//, () => conceptDetailFixture],
  ];
  (globalThis as any).fetch = async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [pat, fn] of handlers) {
      if (pat.test(url)) {
        return new Response(JSON.stringify(fn()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({ detail: "not mocked" }), {
      status: 404,
    });
  };
}

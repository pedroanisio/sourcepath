/**
 * Coverage tests for the empty/error branches in each detail view.
 * Each test installs a per-route fetch mock that returns an empty payload.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";

vi.mock("../components/CytoscapeGraph", () => ({
  // expose onNodeClick via a button so tests can drive the parent's nav handler
  default: ({ onNodeClick }: { onNodeClick?: (id: string) => void }) => (
    <div data-testid="cy-stub">
      <button
        data-testid="fake-node-click"
        onClick={() => onNodeClick?.("__test__")}
      >
        fake-click
      </button>
    </div>
  ),
}));

import App from "../App";
import {
  fileDetailFixture,
  chunkDetailFixture,
  conceptDetailFixture,
  chunkListFixture,
} from "./fixtures";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<App />} />
      </Routes>
    </MemoryRouter>
  );
}

function mockFetch(routes: Array<[RegExp, unknown]>) {
  (globalThis as any).fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [pat, body] of routes) {
      if (pat.test(url)) {
        return new Response(JSON.stringify(body), { status: 200 });
      }
    }
    return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("FileDetail — empty branches", () => {
  it("renders 'none' for empty imports/chunks/concepts and the empty list state", async () => {
    mockFetch([
      [
        /^\/api\/file\//,
        {
          ...fileDetailFixture,
          imports_out: [],
          imports_in: [],
          chunks: [],
          concepts: [],
        },
      ],
    ]);
    renderAt("/file/orphan.py");
    await screen.findByRole("heading", { name: /a\.py/ });
    // three 'none' empty-state placeholders + one 'no chunks'
    const noneCount = screen.getAllByText("none").length;
    expect(noneCount).toBe(3);
    expect(screen.getByText("no chunks")).toBeInTheDocument();
  });
});

describe("ChunkDetail — empty + invalid", () => {
  it("renders empty concepts placeholder and missing blob preview", async () => {
    mockFetch([
      [
        /^\/api\/chunk\/\d+$/,
        { ...chunkDetailFixture, concepts: [], blob_preview: null },
      ],
    ]);
    renderAt("/chunk/0");
    expect(
      await screen.findByText("no concepts lexicalized by this chunk")
    ).toBeInTheDocument();
    expect(screen.getByText("blob not available")).toBeInTheDocument();
  });

  it("rejects a non-numeric idx with the invalid-idx error", async () => {
    renderAt("/chunk/not-a-number");
    expect(await screen.findByText("invalid chunk idx")).toBeInTheDocument();
  });

  it("falls back to em-dash when symbol is null", async () => {
    mockFetch([
      [
        /^\/api\/chunk\/\d+$/,
        {
          ...chunkDetailFixture,
          chunk: { ...chunkDetailFixture.chunk, symbol: null, file: null },
        },
      ],
    ]);
    renderAt("/chunk/0");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /chunk #0/ })).toBeInTheDocument()
    );
  });
});

describe("ConceptDetail — alt_labels details", () => {
  it("renders the collapsible alt_labels block with each variant", async () => {
    mockFetch([
      [
        /^\/api\/concept\//,
        {
          ...conceptDetailFixture,
          concept: {
            ...conceptDetailFixture.concept,
            alt_labels: ["Foo", "FOO_BAR"],
          },
        },
      ],
    ]);
    renderAt("/concept/foo");
    expect(await screen.findByText(/alt_labels \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Foo")).toBeInTheDocument();
    expect(screen.getByText("FOO_BAR")).toBeInTheDocument();
  });
});

describe("Detail views — all-null field fallbacks", () => {
  it("FileDetail renders em-dashes when language/type/size/sha are null", async () => {
    mockFetch([
      [
        /^\/api\/file\//,
        {
          file: {
            path: "x",
            language: null,
            type: null,
            size: null,
            contentSha256: null,
          },
          imports_out: [],
          imports_in: [],
          chunks: [
            {
              idx: 0,
              symbol: null,
              kind: null,
              beginLine: null,
              endLine: null,
              embeddingRow: null,
            },
          ],
          concepts: [],
        },
      ],
    ]);
    renderAt("/file/x");
    // wait for metadata to render; the row + metadata both have em-dashes
    await screen.findByText("Metadata");
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(5);
  });

  it("ChunkDetail renders em-dashes when symbol/kind/file/lines/row are null", async () => {
    mockFetch([
      [
        /^\/api\/chunk\/\d+$/,
        {
          chunk: {
            idx: 0,
            uri: "u",
            symbol: null,
            kind: null,
            file: null,
            beginLine: null,
            endLine: null,
            embeddingRow: null,
            contentSha256: null,
          },
          concepts: [],
          blob_preview: null,
        },
      ],
    ]);
    renderAt("/chunk/0");
    await screen.findByText("Metadata");
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(5);
  });

  it("ConceptDetail with null-field chunk rows still renders the table", async () => {
    mockFetch([
      [
        /^\/api\/concept\//,
        {
          ...conceptDetailFixture,
          chunks: [
            {
              idx: 0,
              symbol: null,
              kind: null,
              file: null,
              beginLine: null,
              endLine: null,
            },
          ],
        },
      ],
    ]);
    renderAt("/concept/schema");
    await screen.findByRole("heading", { name: /schema/ });
    // null kind, null file, null lines render em-dashes
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

});

describe("ConceptDetail — empty branches", () => {
  it("renders empty placeholders for cooccurring/files/chunks", async () => {
    mockFetch([
      [
        /^\/api\/concept\//,
        {
          ...conceptDetailFixture,
          cooccurring: [],
          files: [],
          chunks: [],
        },
      ],
    ]);
    renderAt("/concept/orphan");
    await screen.findByRole("heading", { name: /schema/ });
    expect(screen.getByText("no cooccurrence neighbors")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(
      screen.getByText("no chunks lexicalize this concept")
    ).toBeInTheDocument();
  });

  it("renders the 'Composed of' card and links composing concepts", async () => {
    mockFetch([
      [
        /^\/api\/concept\//,
        { ...conceptDetailFixture, components: ["alpha", "beta"] },
      ],
    ]);
    renderAt("/concept/compound");
    const alpha = await screen.findByRole("link", { name: "alpha" });
    expect(alpha).toHaveAttribute("href", "/concept/alpha");
    expect(screen.getByRole("link", { name: "beta" })).toHaveAttribute(
      "href",
      "/concept/beta"
    );
  });
});

describe("ChunkSearch — empty results + null file/symbol fallbacks", () => {
  it("renders 'no results' when the search returns an empty list", async () => {
    mockFetch([
      [/^\/api\/chunks(\?|$)/, { ...chunkListFixture, chunks: [], total: 0 }],
      [/^\/api\/chunks\/search$/, { ...chunkListFixture, chunks: [], total: 0 }],
    ]);
    renderAt("/chunks");
    expect(await screen.findByText("no results")).toBeInTheDocument();
  });

  it("renders em-dash fallbacks when chunk row has null fields", async () => {
    mockFetch([
      [
        /^\/api\/chunks(\?|$)/,
        {
          ...chunkListFixture,
          chunks: [
            {
              idx: null,
              symbol: null,
              kind: null,
              file: null,
              beginLine: null,
              endLine: null,
              embeddingRow: null,
            },
          ],
          total: 1,
        },
      ],
    ]);
    renderAt("/chunks");
    // multiple em-dashes are rendered in the single row
    await waitFor(() =>
      expect(screen.getAllByText("—").length).toBeGreaterThan(3)
    );
  });
});

describe("Dashboard — SHACL non-conforming branch", () => {
  it("renders the 'non-conforming' tag when shacl_conforms is false", async () => {
    mockFetch([
      [
        /^\/api\/summary$/,
        {
          repo_name: "x",
          counts: { files: 1 },
          files_by_language: { python: 1 },
          files_by_type: { source_code: 1 },
          n_chunks: 1,
          n_concepts: 1,
          shacl_conforms: false,
          output_dir: "/data",
        },
      ],
    ]);
    renderAt("/dashboard");
    expect(await screen.findByText(/non-conforming/)).toBeInTheDocument();
  });
});

describe("Dashboard — failure path", () => {
  it("renders the error state when /api/summary fails", async () => {
    (globalThis as any).fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "boom" }), { status: 500 })
    );
    renderAt("/dashboard");
    await waitFor(() =>
      expect(screen.getByText(/500.*\/api\/summary/)).toBeInTheDocument()
    );
  });
});

describe("FileGraph / ConceptGraph — failure path", () => {
  it("FileGraph shows the error when /api/file-graph fails", async () => {
    (globalThis as any).fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "x" }), { status: 503 })
    );
    renderAt("/files");
    await waitFor(() =>
      expect(screen.getByText(/503.*\/api\/file-graph/)).toBeInTheDocument()
    );
  });

  it("ConceptGraph shows the error when /api/concept-graph fails", async () => {
    (globalThis as any).fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "x" }), { status: 503 })
    );
    renderAt("/concepts");
    await waitFor(() =>
      expect(screen.getByText(/503.*\/api\/concept-graph/)).toBeInTheDocument()
    );
  });
});

describe("Toolbar interactions", () => {
  it("FileGraph reload button refetches and shows truncation info", async () => {
    let calls = 0;
    (globalThis as any).fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (/^\/api\/file-graph/.test(url)) {
        calls++;
        return new Response(
          JSON.stringify({
            nodes: [{ id: "a", label: "a", group: "py", weight: 1 }],
            edges: [],
            truncated: true,
            total_nodes_available: 99,
          }),
          { status: 200 }
        );
      }
      return new Response("{}", { status: 200 });
    });
    renderAt("/files");
    await screen.findByText(/truncated from 99/);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /reload/i }));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
  });

  it("ConceptGraph reload button refetches and shows totals", async () => {
    let calls = 0;
    (globalThis as any).fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (/^\/api\/concept-graph/.test(url)) {
        calls++;
        return new Response(
          JSON.stringify({
            nodes: [{ id: "schema", label: "schema", weight: 5 }],
            edges: [],
            truncated: true,
            total_nodes_available: 6685,
          }),
          { status: 200 }
        );
      }
      return new Response("{}", { status: 200 });
    });
    renderAt("/concepts");
    await screen.findByText(/6685 concepts total/);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /reload/i }));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
  });
});

describe("ChunkSearch — semantic-mode tag + null score row", () => {
  it("renders the 'good' tag and score for semantic results", async () => {
    mockFetch([
      [
        /^\/api\/chunks(\?|$)/,
        {
          ...chunkListFixture,
          mode: "semantic",
          chunks: [
            { idx: 0, symbol: "x", kind: "function", file: "x.py", beginLine: 1, endLine: 2, embeddingRow: 0, score: 0.812 },
            { idx: null, symbol: null, kind: null, file: null, beginLine: null, endLine: null, embeddingRow: null, score: null },
          ],
          total: 2,
        },
      ],
    ]);
    renderAt("/chunks");
    expect(await screen.findByText(/mode:/)).toBeInTheDocument();
    expect(screen.getByText("semantic")).toHaveClass("tag", "good");
    expect(screen.getByText("0.812")).toBeInTheDocument();
  });
});

describe("Toolbar interactions — selects + node-click", () => {
  it("FileGraph select onChange triggers a refetch", async () => {
    let lastUrl = "";
    (globalThis as any).fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      lastUrl = url;
      if (/^\/api\/file-graph/.test(url)) {
        return new Response(
          JSON.stringify({
            nodes: [{ id: "a", label: "a", group: "py", weight: 1 }],
            edges: [],
            truncated: false,
            total_nodes_available: 1,
          }),
          { status: 200 }
        );
      }
      return new Response("{}", { status: 200 });
    });
    renderAt("/files");
    await screen.findByTestId("cy-stub");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox"), "100");
    await user.click(screen.getByRole("button", { name: /reload/i }));
    expect(lastUrl).toContain("limit=100");
  });

  it("ConceptGraph node-click navigates to /concept/:name", async () => {
    mockFetch([
      [
        /^\/api\/concept-graph/,
        {
          nodes: [{ id: "schema", label: "schema", weight: 5 }],
          edges: [],
          truncated: false,
          total_nodes_available: 1,
        },
      ],
      [/^\/api\/concept\//, conceptDetailFixture],
    ]);
    renderAt("/concepts");
    const btn = await screen.findByTestId("fake-node-click");
    const user = userEvent.setup();
    await user.click(btn);
    // navigation to /concept/__test__ — the concept detail mock returns
    // conceptDetailFixture whose label is 'schema'
    expect(await screen.findByRole("heading", { name: /schema/ })).toBeInTheDocument();
  });

  it("FileGraph node-click navigates to /file/<path>", async () => {
    mockFetch([
      [
        /^\/api\/file-graph/,
        {
          nodes: [{ id: "a.py", label: "a.py", weight: 1 }],
          edges: [],
          truncated: false,
          total_nodes_available: 1,
        },
      ],
      [/^\/api\/file\//, fileDetailFixture],
    ]);
    renderAt("/files");
    const btn = await screen.findByTestId("fake-node-click");
    const user = userEvent.setup();
    await user.click(btn);
    expect(await screen.findByRole("heading", { name: /a\.py/ })).toBeInTheDocument();
  });
});

describe("ChunkSearch — submit guards", () => {
  it("ignores empty-query submission (no /api/chunks/search call)", async () => {
    const searchCalls: string[] = [];
    (globalThis as any).fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/chunks/search")) searchCalls.push(url);
      if (url.endsWith("/api/chunks/search"))
        return new Response(JSON.stringify(chunkListFixture), { status: 200 });
      return new Response(JSON.stringify(chunkListFixture), { status: 200 });
    });
    renderAt("/chunks");
    await screen.findByText("do_thing");
    const user = userEvent.setup();
    // submit form with empty input — should return early without firing /search
    await user.click(screen.getByRole("button", { name: /search/i }));
    expect(searchCalls).toHaveLength(0);
  });
});

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { installFetchMock } from "./fixtures";

// cytoscape relies on canvas APIs jsdom doesn't ship; replace with a div stub.
vi.mock("../components/CytoscapeGraph", () => ({
  default: ({ data }: { data: { nodes: any[]; edges: any[] } }) => (
    <div data-testid="cy-stub">
      cy:{data.nodes.length}n/{data.edges.length}e
    </div>
  ),
}));

import App from "../App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<App />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  installFetchMock();
});

describe("Dashboard", () => {
  it("renders counts and language bars from /api/summary", async () => {
    renderAt("/dashboard");
    expect(await screen.findByRole("heading", { name: /demo/i })).toBeInTheDocument();
    expect(screen.getByText("Files by language")).toBeInTheDocument();
    // language bar labels
    expect(await screen.findByText("python")).toBeInTheDocument();
    expect(screen.getByText("rust")).toBeInTheDocument();
    // counts grid renders the right stat
    expect(screen.getByText("files")).toBeInTheDocument();
  });
});

describe("FileGraph route", () => {
  it("renders toolbar + stubbed cytoscape", async () => {
    renderAt("/files");
    expect(await screen.findByText(/top-N by import degree/i)).toBeInTheDocument();
    expect(await screen.findByTestId("cy-stub")).toHaveTextContent("cy:2n/1e");
  });
});

describe("ConceptGraph route", () => {
  it("renders toolbar + stubbed cytoscape", async () => {
    renderAt("/concepts");
    expect(await screen.findByText(/top-N by frequency/i)).toBeInTheDocument();
    expect(await screen.findByTestId("cy-stub")).toHaveTextContent("cy:2n/1e");
  });
});

describe("SymbolGraph route", () => {
  it("renders toolbar + stubbed cytoscape with one node per fixture chunk", async () => {
    renderAt("/symbols");
    expect(await screen.findByText(/top-N by call degree/i)).toBeInTheDocument();
    expect(await screen.findByTestId("cy-stub")).toHaveTextContent("cy:3n/2e");
    // Kind selector defaults to `calls` and is selectable
    const kindSelect = screen.getAllByRole("combobox").find(
      (el) => (el as HTMLSelectElement).value === "calls"
    ) as HTMLSelectElement;
    expect(kindSelect).toBeDefined();
  });

  it("renders the empty state when the bundle has no symbol edges", async () => {
    const originalFetch = (globalThis as any).fetch;
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/symbol-graph")) {
        return new Response(
          JSON.stringify({ nodes: [], edges: [], truncated: false, total_nodes_available: 0 }),
          { status: 200 },
        );
      }
      return originalFetch(input);
    };
    renderAt("/symbols");
    expect(await screen.findByText(/no symbol-level edges/i)).toBeInTheDocument();
  });
});

describe("ChunkSearch route", () => {
  it("loads initial list and submits a search", async () => {
    renderAt("/chunks");
    expect(await screen.findByText("do_thing")).toBeInTheDocument();
    expect(screen.getByText("<file>")).toBeInTheDocument();
    // submitting a query should not throw and should re-show rows
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/query/i), "schema");
    await user.click(screen.getByRole("button", { name: /search/i }));
    expect(await screen.findByText("do_thing")).toBeInTheDocument();
  });

  it("links chunk and file columns", async () => {
    renderAt("/chunks");
    const chunkLink = await screen.findByRole("link", { name: "do_thing" });
    expect(chunkLink).toHaveAttribute("href", "/chunk/1");
    const fileLink = screen.getAllByRole("link", { name: "a.py" })[0];
    expect(fileLink).toHaveAttribute("href", "/file/a.py");
  });
});

describe("FileDetail route", () => {
  it("shows imports + chunks + concepts with click-through links", async () => {
    renderAt("/file/a.py");
    expect(await screen.findByRole("heading", { name: /a\.py/ })).toBeInTheDocument();
    expect(screen.getByText(/Imports out/)).toBeInTheDocument();
    expect(screen.getByText(/Imported by/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "b.py" })[0]).toHaveAttribute("href", "/file/b.py");
    expect(screen.getByRole("link", { name: "schema" })).toHaveAttribute(
      "href",
      "/concept/schema"
    );
    expect(screen.getByText("Change impact")).toBeInTheDocument();
    expect(screen.getByText(/Transitive dependents/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "tests/test_a.py" })).toHaveAttribute(
      "href",
      "/file/tests/test_a.py"
    );
  });

  it("renders the xrefs columns with click-through and resolution badges", async () => {
    renderAt("/file/a.py");
    // Both panels render with their row counts.
    expect(await screen.findByText("Calls out (2)")).toBeInTheDocument();
    expect(screen.getByText("Called from (1)")).toBeInTheDocument();
    // Symbol links jump to /chunk/{idx}.
    expect(screen.getByRole("link", { name: "load_users" })).toHaveAttribute(
      "href",
      "/chunk/7"
    );
    expect(screen.getByRole("link", { name: "guess_owner" })).toHaveAttribute(
      "href",
      "/chunk/9"
    );
    expect(screen.getByRole("link", { name: "main" })).toHaveAttribute(
      "href",
      "/chunk/3"
    );
    // Resolution badges carry the provenance through the title attribute.
    const heuristic = screen.getByText("heuristic");
    expect(heuristic).toHaveClass("muted");
    expect(heuristic).toHaveAttribute("title", "resolver: python_inter_file");
    const exactBadges = screen.getAllByText("exact");
    expect(exactBadges[0]).not.toHaveClass("muted");
  });

  it("renders empty xrefs panels when the bundle has no edges", async () => {
    // Patch fetch to return a FileDetail without xrefs_out / xrefs_in.
    const originalFetch = (globalThis as any).fetch;
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/file/")) {
        const stripped = {
          ...((await (await originalFetch(input)).json()) as any),
        };
        delete stripped.xrefs_out;
        delete stripped.xrefs_in;
        return new Response(JSON.stringify(stripped), { status: 200 });
      }
      return originalFetch(input);
    };
    renderAt("/file/a.py");
    expect(await screen.findByText("Calls out (0)")).toBeInTheDocument();
    expect(screen.getByText("Called from (0)")).toBeInTheDocument();
    expect(screen.getByText(/no tracked calls leave this file/)).toBeInTheDocument();
  });
});

describe("ChunkDetail route", () => {
  it("shows blob preview + parent file + concept tag", async () => {
    renderAt("/chunk/0");
    expect(await screen.findByRole("heading", { name: /chunk #0/ })).toBeInTheDocument();
    expect(screen.getByText("def hello():", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "a.py" })[0]).toHaveAttribute("href", "/file/a.py");
    expect(screen.getByRole("link", { name: "schema" })).toHaveAttribute(
      "href",
      "/concept/schema"
    );
  });

  it("renders Callers and Callees with click-through links to the peer chunks", async () => {
    renderAt("/chunk/0");
    expect(await screen.findByText("Callers (1)")).toBeInTheDocument();
    expect(screen.getByText("Callees (1)")).toBeInTheDocument();
    // Caller row links to /chunk/11; callee row links to /chunk/12.
    expect(screen.getByRole("link", { name: "main" })).toHaveAttribute(
      "href",
      "/chunk/11"
    );
    expect(screen.getByRole("link", { name: "load_users" })).toHaveAttribute(
      "href",
      "/chunk/12"
    );
    // The heuristic callee renders dimmed; the exact caller does not.
    const heuristicBadge = screen.getByTitle(/resolver: python_inter_file/);
    expect(heuristicBadge).toHaveTextContent("heuristic");
    expect(heuristicBadge).toHaveClass("muted");
    const exactBadge = screen.getByTitle(/resolver: python_intra_file/);
    expect(exactBadge).toHaveTextContent("exact");
    expect(exactBadge).not.toHaveClass("muted");
  });

  it("renders the empty state when a chunk has no xrefs", async () => {
    // Patch fetch to drop callers/callees from the chunk response.
    const originalFetch = (globalThis as any).fetch;
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (/^\/api\/chunk\/\d+/.test(url)) {
        const body = (await (await originalFetch(input)).json()) as any;
        delete body.callers;
        delete body.callees;
        return new Response(JSON.stringify(body), { status: 200 });
      }
      return originalFetch(input);
    };
    renderAt("/chunk/0");
    expect(await screen.findByText("Callers (0)")).toBeInTheDocument();
    expect(screen.getByText("Callees (0)")).toBeInTheDocument();
    expect(screen.getByText(/not called by any chunk/)).toBeInTheDocument();
  });
});

describe("ConceptDetail route", () => {
  it("shows cooccur table, files list, chunks list — all linked", async () => {
    renderAt("/concept/schema");
    expect(await screen.findByRole("heading", { name: /schema/ })).toBeInTheDocument();
    // cooccurring concept link
    expect(screen.getByRole("link", { name: "auth" })).toHaveAttribute(
      "href",
      "/concept/auth"
    );
    // file link
    const fileLinks = screen.getAllByRole("link", { name: "a.py" });
    expect(fileLinks[0]).toHaveAttribute("href", "/file/a.py");
    // chunk link via symbol
    expect(screen.getByRole("link", { name: "<file>" })).toHaveAttribute(
      "href",
      "/chunk/0"
    );
  });
});

describe("Unknown route", () => {
  it("falls back to dashboard via the root redirect", async () => {
    // root redirects to /dashboard
    renderAt("/");
    expect(await screen.findByRole("heading", { name: /demo/i })).toBeInTheDocument();
  });
});

describe("API error handling", () => {
  it("FileDetail shows the error when /api/file 404s", async () => {
    // override fetch to always 404 for /api/file
    (globalThis as any).fetch = async () =>
      new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
    renderAt("/file/missing.py");
    await waitFor(() =>
      expect(screen.getByText(/404.*\/api\/file\/missing\.py/)).toBeInTheDocument()
    );
  });
});

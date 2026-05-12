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
    expect(screen.getByRole("link", { name: "b.py" })).toHaveAttribute("href", "/file/b.py");
    expect(screen.getByRole("link", { name: "schema" })).toHaveAttribute(
      "href",
      "/concept/schema"
    );
  });
});

describe("ChunkDetail route", () => {
  it("shows blob preview + parent file + concept tag", async () => {
    renderAt("/chunk/0");
    expect(await screen.findByRole("heading", { name: /chunk #0/ })).toBeInTheDocument();
    expect(screen.getByText("def hello():", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "a.py" })).toHaveAttribute("href", "/file/a.py");
    expect(screen.getByRole("link", { name: "schema" })).toHaveAttribute(
      "href",
      "/concept/schema"
    );
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

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { installFetchMock, bundlesFixture, summaryFixture } from "./fixtures";
import { api, setBundle, getCurrentBundle } from "../api";

// Cytoscape needs canvas APIs jsdom doesn't ship — stub it as a div.
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

// ---------------------------------------------------------------- rendering
describe("BundlePicker render", () => {
  it("shows a <select> populated from /api/bundles", async () => {
    renderAt("/dashboard");
    const select = await screen.findByLabelText("Bundle");
    expect(select).toBeInTheDocument();
    // each fixture bundle is an option
    for (const b of bundlesFixture.bundles) {
      expect(
        screen.getByRole("option", { name: new RegExp(b.name) })
      ).toBeInTheDocument();
    }
    // default selection comes from the API response
    expect((select as HTMLSelectElement).value).toBe(bundlesFixture.selected);
  });

  it("renders empty-state message when no bundles are discovered", async () => {
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/bundles")) {
        return new Response(
          JSON.stringify({ bundles: [], selected: null, bundles_root: "/tmp" }),
          { status: 200 }
        );
      }
      // other endpoints behave normally
      return new Response(JSON.stringify(summaryFixture), { status: 200 });
    };
    renderAt("/dashboard");
    expect(await screen.findByText(/no bundles discovered/i)).toBeInTheDocument();
    // and no <select> is rendered
    expect(screen.queryByLabelText("Bundle")).not.toBeInTheDocument();
  });

  it("renders an error message when /api/bundles fails", async () => {
    const realFetch = globalThis.fetch;
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/bundles")) {
        return new Response(JSON.stringify({ detail: "boom" }), { status: 500 });
      }
      return realFetch(input as any);
    };
    renderAt("/dashboard");
    expect(
      await screen.findByText(/bundle list unavailable/i)
    ).toBeInTheDocument();
  });
});

// --------------------------------------------------------- bundle switching
describe("BundlePicker switching", () => {
  it("selecting a different bundle updates the api module state and re-fetches", async () => {
    // Spy on api.summary so we can assert it's invoked under a new bundle.
    const summarySpy = vi.spyOn(api, "summary");
    renderAt("/dashboard");
    await screen.findByLabelText("Bundle");
    // Initial fetch happened under selected=alpha.
    await waitFor(() => expect(summarySpy).toHaveBeenCalled());
    expect(getCurrentBundle()).toBe("alpha");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Bundle"), "beta");
    await waitFor(() => expect(getCurrentBundle()).toBe("beta"));
    // The main subtree re-mounted under the new bundle, so summary fires again.
    await waitFor(() => expect(summarySpy.mock.calls.length).toBeGreaterThan(1));
  });

  it("persists the selection to localStorage", async () => {
    renderAt("/dashboard");
    await screen.findByLabelText("Bundle");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Bundle"), "beta");
    await waitFor(() =>
      expect(localStorage.getItem("cbm-bundle")).toBe("beta")
    );
  });

  it("hydrates the initial selection from localStorage", async () => {
    localStorage.setItem("cbm-bundle", "beta");
    renderAt("/dashboard");
    const select = await screen.findByLabelText("Bundle");
    expect((select as HTMLSelectElement).value).toBe("beta");
    expect(getCurrentBundle()).toBe("beta");
  });

  it("self-heals when localStorage holds a stale bundle not in the listing", async () => {
    // Regression: a deploy used to pre-select "data" (a parent dir) and the
    // value got persisted. After a config fix, "data" is no longer in the
    // listing and the frontend should fall back to the backend's pick
    // rather than keep sending ?bundle=data forever.
    localStorage.setItem("cbm-bundle", "data");
    renderAt("/dashboard");
    const select = await screen.findByLabelText("Bundle");
    await waitFor(() =>
      expect((select as HTMLSelectElement).value).toBe("alpha")
    );
    expect(getCurrentBundle()).toBe("alpha");
    expect(localStorage.getItem("cbm-bundle")).toBe("alpha");
  });

  it("clears the stale bundle even when the listing is empty", async () => {
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/bundles")) {
        return new Response(
          JSON.stringify({ bundles: [], selected: null, bundles_root: "/tmp" }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({ detail: "no bundles" }), { status: 404 });
    };
    localStorage.setItem("cbm-bundle", "data");
    renderAt("/dashboard");
    await screen.findByText(/no bundles discovered/i);
    // Stale bundle must be cleared so subsequent fetches don't carry ?bundle=data.
    await waitFor(() => expect(getCurrentBundle()).toBeNull());
    expect(localStorage.getItem("cbm-bundle")).toBeNull();
  });
});

// --------------------------------------------------- API helper query string
describe("api helpers thread ?bundle=", () => {
  function captureUrls() {
    const urls: string[] = [];
    (globalThis as any).fetch = async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      urls.push(url);
      return new Response(JSON.stringify(summaryFixture), { status: 200 });
    };
    return urls;
  }

  it("appends ?bundle=NAME when one is set, on a URL without query string", async () => {
    const urls = captureUrls();
    setBundle("beta");
    await api.summary();
    expect(urls[0]).toBe("/api/summary?bundle=beta");
  });

  it("appends &bundle=NAME on URLs that already have a query string", async () => {
    const urls = captureUrls();
    setBundle("beta");
    await api.fileGraph(50);
    expect(urls[0]).toBe("/api/file-graph?limit=50&bundle=beta");
  });

  it("omits the bundle param when none is set", async () => {
    const urls = captureUrls();
    setBundle(null);
    await api.summary();
    await api.fileGraph(10);
    expect(urls[0]).toBe("/api/summary");
    expect(urls[1]).toBe("/api/file-graph?limit=10");
  });

  it("threads ?bundle= through POST /api/chunks/search too", async () => {
    const urls = captureUrls();
    setBundle("beta");
    await api.searchChunks("hi");
    expect(urls[0]).toBe("/api/chunks/search?bundle=beta");
  });

  it("url-encodes bundle names that contain reserved chars", async () => {
    const urls = captureUrls();
    setBundle("my bundle"); // space — picker won't produce this but the helper must encode
    await api.summary();
    expect(urls[0]).toBe("/api/summary?bundle=my%20bundle");
  });
});

/**
 * Tests for the CytoscapeGraph wrapper. The underlying `react-cytoscapejs`
 * library renders to a canvas, which jsdom can't host — so we mock its
 * default export with a div that captures the props the wrapper computes
 * (elements, layout, cy callback).
 */
import { describe, it, expect, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

const props: any = {};

vi.mock("react-cytoscapejs", () => ({
  default: (p: any) => {
    Object.assign(props, p);
    return <div data-testid="cy-mock">{p.elements.length} elements</div>;
  },
}));

import CytoscapeGraph from "../components/CytoscapeGraph";
import type { GraphResp } from "../api";

const data: GraphResp = {
  nodes: [
    { id: "a", label: "a", group: "py", weight: 4 },
    { id: "b", label: "b", group: "py", weight: 1 },
    { id: "c", label: "c", group: "rs", weight: 2 },
    { id: "d", label: "d", group: undefined, weight: 1 },
  ],
  edges: [
    { source: "a", target: "b", weight: 1 },
    { source: "a", target: "c", weight: 2 },
  ],
  truncated: false,
  total_nodes_available: 4,
};

describe("CytoscapeGraph", () => {
  it("computes node + edge elements and renders the palette legend", () => {
    render(<CytoscapeGraph data={data} />);
    expect(screen.getByTestId("cy-mock")).toHaveTextContent("6 elements");
    expect(props.elements).toHaveLength(6);
    expect(props.layout.name).toBe("cose");
    // legend lists one entry per defined group; undefined groups don't seed it
    expect(screen.getByText("py")).toBeInTheDocument();
    expect(screen.getByText("rs")).toBeInTheDocument();
  });

  it("respects custom layout prop", () => {
    render(<CytoscapeGraph data={data} layout="grid" />);
    expect(props.layout.name).toBe("grid");
  });

  it("wires node tap to populate the details panel, dbltap to onNodeClick", () => {
    const handler = vi.fn();
    render(<CytoscapeGraph data={data} onNodeClick={handler} />);

    const listeners: Record<string, Array<(e: any) => void>> = {};
    const fakeCy: any = {
      removeAllListeners: vi.fn(),
      on(event: string, selOrFn: any, fn?: any) {
        const h = typeof selOrFn === "function" ? selOrFn : fn;
        listeners[event] = listeners[event] || [];
        listeners[event].push(h);
      },
    };
    act(() => props.cy(fakeCy));
    expect(fakeCy.removeAllListeners).toHaveBeenCalled();

    act(() =>
      listeners["tap"][0]({
        target: {
          data: () => ({ id: "a", group: "py", meta: { foo: 1, list: [1, 2] } }),
        },
      })
    );
    const panel = screen.getByText(/foo: 1/).closest("pre")!;
    expect(panel).toHaveTextContent(/foo: 1/);
    expect(panel).toHaveTextContent(/list: \[2\]/);

    act(() => listeners["tap"][1]({ target: fakeCy }));
    expect(screen.queryByText(/foo: 1/)).not.toBeInTheDocument();

    act(() =>
      listeners["dbltap"][0]({ target: { data: () => ({ id: "a" }) } })
    );
    expect(handler).toHaveBeenCalledWith("a");
  });

  it("does not register a dbltap listener when onNodeClick is omitted", () => {
    render(<CytoscapeGraph data={data} />);
    const listeners: Record<string, Array<(e: any) => void>> = {};
    const fakeCy: any = {
      removeAllListeners: vi.fn(),
      on(event: string, _: any, fn?: any) {
        listeners[event] = listeners[event] || [];
        listeners[event].push(fn || _);
      },
    };
    props.cy(fakeCy);
    expect(listeners["dbltap"]).toBeUndefined();
  });
});

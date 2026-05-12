import CytoscapeComponent from "react-cytoscapejs";
import cytoscape from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphResp } from "../api";

const PALETTE = [
  "#58a6ff",
  "#3fb950",
  "#d29922",
  "#f85149",
  "#bc8cff",
  "#39c5cf",
  "#ff7b72",
  "#e3b341",
  "#7ee787",
  "#a371f7",
];

function colorFor(group: string | undefined, palette: Map<string, string>): string {
  if (!group) return "#8b949e";
  const cached = palette.get(group);
  if (cached) return cached;
  const c = PALETTE[palette.size % PALETTE.length];
  palette.set(group, c);
  return c;
}

type Props = {
  data: GraphResp;
  layout?: string;
  onNodeClick?: (id: string) => void;
};

export default function CytoscapeGraph({ data, layout = "cose", onNodeClick }: Props) {
  const cyRef = useRef<cytoscape.Core | null>(null);
  const palette = useMemo(() => new Map<string, string>(), [data]);
  const [details, setDetails] = useState<string | null>(null);
  const [legend, setLegend] = useState<Array<[string, string]>>([]);

  const elements = useMemo(() => {
    palette.clear();
    const maxWeight = data.nodes.reduce((m, n) => Math.max(m, n.weight ?? 1), 1);
    const nodes = data.nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.label,
        group: n.group ?? "—",
        color: colorFor(n.group, palette),
        size: 12 + Math.min(36, Math.sqrt((n.weight ?? 1) / maxWeight) * 36),
        meta: n.meta,
      },
    }));
    const edges = data.edges.map((e, i) => ({
      data: { id: `e${i}`, source: e.source, target: e.target, weight: e.weight ?? 1 },
    }));
    return [...nodes, ...edges];
  }, [data, palette]);

  useEffect(() => {
    setLegend(Array.from(palette.entries()));
  }, [elements, palette]);

  return (
    <div className="graph-host">
      <CytoscapeComponent
        elements={elements}
        style={{ width: "100%", height: "100%" }}
        layout={{
          name: layout,
          animate: false,
          // @ts-ignore — these are cose-specific
          nodeRepulsion: 4500,
          // @ts-ignore
          idealEdgeLength: 80,
          // @ts-ignore
          gravity: 0.4,
          padding: 24,
        }}
        cy={(cy: cytoscape.Core) => {
          cyRef.current = cy;
          cy.removeAllListeners();
          cy.on("tap", "node", (evt: cytoscape.EventObject) => {
            const d = evt.target.data();
            const meta = d.meta || {};
            const lines = [
              `id: ${d.id}`,
              `group: ${d.group}`,
              ...Object.entries(meta).map(
                ([k, v]) => `${k}: ${Array.isArray(v) ? `[${(v as any[]).length}]` : String(v)}`
              ),
            ];
            setDetails(lines.join("\n") + (onNodeClick ? "\n\n(double-click to open details)" : ""));
          });
          if (onNodeClick) {
            cy.on("dbltap", "node", (evt: cytoscape.EventObject) => {
              onNodeClick(evt.target.data().id);
            });
          }
          cy.on("tap", (evt: cytoscape.EventObject) => {
            if (evt.target === cy) setDetails(null);
          });
        }}
        stylesheet={[
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              label: "data(label)",
              "font-size": 8,
              color: "#e6edf3",
              "text-outline-color": "#0e1116",
              "text-outline-width": 2,
              width: "data(size)",
              height: "data(size)",
            },
          },
          {
            selector: "edge",
            style: {
              width: 1,
              "line-color": "#30363d",
              "curve-style": "bezier",
              "target-arrow-color": "#30363d",
              "target-arrow-shape": "triangle",
              "arrow-scale": 0.6,
              opacity: 0.6,
            },
          },
          {
            selector: "node:selected",
            style: {
              "border-width": 2,
              "border-color": "#58a6ff",
            },
          },
        ]}
      />
      {legend.length > 0 && (
        <div className="legend">
          {legend.map(([k, color]) => (
            <div key={k} className="legend-row">
              <span className="legend-swatch" style={{ background: color }} />
              <span>{k}</span>
            </div>
          ))}
        </div>
      )}
      {details && <pre className="details">{details}</pre>}
    </div>
  );
}

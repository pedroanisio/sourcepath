import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, GraphResp } from "../api";
import CytoscapeGraph from "../components/CytoscapeGraph";

export default function ConceptGraph() {
  const navigate = useNavigate();
  const [limit, setLimit] = useState(120);
  const [minEdge, setMinEdge] = useState(4);
  const [data, setData] = useState<GraphResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = () => {
    setLoading(true);
    setErr(null);
    api
      .conceptGraph(limit, minEdge)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <h2>Concept (SKOS) graph</h2>
      <div className="toolbar">
        <label>
          top-N by frequency
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            style={{ marginLeft: 6 }}
          >
            <option value={50}>50</option>
            <option value={120}>120</option>
            <option value={250}>250</option>
            <option value={500}>500</option>
          </select>
        </label>
        <label>
          min cooccur weight
          <select
            value={minEdge}
            onChange={(e) => setMinEdge(Number(e.target.value))}
            style={{ marginLeft: 6 }}
          >
            <option value={2}>2</option>
            <option value={4}>4</option>
            <option value={8}>8</option>
            <option value={16}>16</option>
            <option value={32}>32</option>
          </select>
        </label>
        <button onClick={reload} disabled={loading}>
          {loading ? "loading…" : "reload"}
        </button>
        {data && (
          <span className="info">
            {data.nodes.length} concepts · {data.edges.length} skos:related edges
            {data.truncated &&
              ` · ${data.total_nodes_available} concepts total`}
          </span>
        )}
      </div>
      {err && <div className="error">{err}</div>}
      {data && (
        <CytoscapeGraph
          data={data}
          layout="cose"
          onNodeClick={(id) => navigate(`/concept/${encodeURIComponent(id)}`)}
        />
      )}
    </>
  );
}

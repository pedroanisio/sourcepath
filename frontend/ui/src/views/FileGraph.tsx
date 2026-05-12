import { useEffect, useState } from "react";
import { api, GraphResp } from "../api";
import CytoscapeGraph from "../components/CytoscapeGraph";

export default function FileGraph() {
  const [limit, setLimit] = useState(300);
  const [data, setData] = useState<GraphResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = () => {
    setLoading(true);
    setErr(null);
    api
      .fileGraph(limit)
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
      <h2>File + import graph</h2>
      <div className="toolbar">
        <label>
          top-N by import degree
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            style={{ marginLeft: 6 }}
          >
            <option value={100}>100</option>
            <option value={300}>300</option>
            <option value={500}>500</option>
            <option value={1000}>1000</option>
            <option value={5000}>all (≤5000)</option>
          </select>
        </label>
        <button onClick={reload} disabled={loading}>
          {loading ? "loading…" : "reload"}
        </button>
        {data && (
          <span className="info">
            {data.nodes.length} nodes · {data.edges.length} edges
            {data.truncated &&
              ` · truncated from ${data.total_nodes_available}`}
          </span>
        )}
      </div>
      {err && <div className="error">{err}</div>}
      {data && <CytoscapeGraph data={data} layout="cose" />}
    </>
  );
}

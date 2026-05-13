import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, GraphResp } from "../api";
import CytoscapeGraph from "../components/CytoscapeGraph";
import { useBundleVersion } from "../bundle-context";

type EdgeKind = "calls" | "all";

export default function SymbolGraph() {
  const navigate = useNavigate();
  const [limit, setLimit] = useState(300);
  const [kind, setKind] = useState<EdgeKind>("calls");
  const [data, setData] = useState<GraphResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const bundleVersion = useBundleVersion();

  const reload = () => {
    setLoading(true);
    setErr(null);
    api
      .symbolGraph(limit, kind)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundleVersion]);

  return (
    <>
      <h2>Symbol call graph</h2>
      <div className="toolbar">
        <label>
          top-N by call degree
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
        <label>
          kind
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as EdgeKind)}
            style={{ marginLeft: 6 }}
          >
            <option value="calls">calls</option>
            <option value="all">all kinds</option>
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
      {data &&
        (data.nodes.length === 0 ? (
          <div className="empty">no symbol-level edges in this bundle</div>
        ) : (
          <CytoscapeGraph
            data={data}
            layout="cose"
            onNodeClick={(id) => navigate(`/chunk/${id}`)}
          />
        ))}
    </>
  );
}

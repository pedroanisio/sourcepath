import { FormEvent, useEffect, useState } from "react";
import { api, ChunkListResp } from "../api";

export default function ChunkSearch() {
  const [q, setQ] = useState("");
  const [k, setK] = useState(25);
  const [data, setData] = useState<ChunkListResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Show first page of chunks on load
  useEffect(() => {
    api
      .chunks("", 25, 0)
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setLoading(true);
    setErr(null);
    api
      .searchChunks(q, k)
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  return (
    <>
      <h2>Chunk explorer</h2>
      <form className="toolbar" onSubmit={submit}>
        <input
          type="text"
          placeholder="query — semantic if sbert backend, lexical otherwise"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: "1 1 360px" }}
        />
        <label>
          k
          <select value={k} onChange={(e) => setK(Number(e.target.value))} style={{ marginLeft: 6 }}>
            <option>10</option>
            <option>25</option>
            <option>50</option>
            <option>100</option>
          </select>
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "searching…" : "search"}
        </button>
        {data && (
          <span className="info">
            backend: {data.backend} · mode:{" "}
            <span className={`tag ${data.mode === "semantic" ? "good" : "warn"}`}>
              {data.mode}
            </span>{" "}
            · {data.chunks.length} of {data.total}
          </span>
        )}
      </form>
      {err && <div className="error">{err}</div>}
      <div className="card" style={{ padding: 0, overflow: "auto", maxHeight: "calc(100vh - 220px)" }}>
        <table className="rows">
          <thead>
            <tr>
              <th>score</th>
              <th>kind</th>
              <th>symbol</th>
              <th>file</th>
              <th>lines</th>
              <th>row</th>
            </tr>
          </thead>
          <tbody>
            {data?.chunks.map((c, i) => (
              <tr key={i}>
                <td>{c.score != null ? c.score.toFixed(3) : "—"}</td>
                <td>{c.kind ?? "—"}</td>
                <td>{c.symbol ?? "—"}</td>
                <td>{c.file ?? "—"}</td>
                <td>
                  {c.beginLine ?? "—"}–{c.endLine ?? "—"}
                </td>
                <td>{c.embeddingRow ?? "—"}</td>
              </tr>
            ))}
            {data && data.chunks.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  no results
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

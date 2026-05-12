import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, FileDetail as FD } from "../api";

function fileLink(path: string) {
  return `/file/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export default function FileDetail() {
  const params = useParams();
  const path = params["*"] || "";
  const [d, setD] = useState<FD | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    api.file(path).then(setD).catch((e) => setErr(String(e)));
  }, [path]);

  if (err) return <div className="error">{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  return (
    <>
      <h2>
        <span className="tag">file</span> {d.file.path}
      </h2>

      <div className="card">
        <h3>Metadata</h3>
        <dl className="kv">
          <dt>language</dt>
          <dd>{d.file.language ?? "—"}</dd>
          <dt>type</dt>
          <dd>{d.file.type ?? "—"}</dd>
          <dt>size</dt>
          <dd>{d.file.size?.toLocaleString() ?? "—"} bytes</dd>
          <dt>contentSha256</dt>
          <dd style={{ wordBreak: "break-all" }}>{d.file.contentSha256 ?? "—"}</dd>
        </dl>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="card">
          <h3>Imports out ({d.imports_out.length})</h3>
          {d.imports_out.length === 0 ? (
            <div className="empty">none</div>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {d.imports_out.map((p) => (
                <li key={p}>
                  <Link to={fileLink(p)}>{p}</Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <h3>Imported by ({d.imports_in.length})</h3>
          {d.imports_in.length === 0 ? (
            <div className="empty">none</div>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {d.imports_in.map((p) => (
                <li key={p}>
                  <Link to={fileLink(p)}>{p}</Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <h3 style={{ padding: "16px 16px 0" }}>Chunks ({d.chunks.length})</h3>
        {d.chunks.length === 0 ? (
          <div className="empty">no chunks</div>
        ) : (
          <table className="rows">
            <thead>
              <tr>
                <th>kind</th>
                <th>symbol</th>
                <th>lines</th>
                <th>row</th>
              </tr>
            </thead>
            <tbody>
              {d.chunks.map((c) => (
                <tr key={c.idx}>
                  <td>{c.kind ?? "—"}</td>
                  <td>
                    <Link to={`/chunk/${c.idx}`}>{c.symbol ?? "—"}</Link>
                  </td>
                  <td>
                    {c.beginLine ?? "—"}–{c.endLine ?? "—"}
                  </td>
                  <td>{c.embeddingRow ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Concepts lexicalized ({d.concepts.length})</h3>
        {d.concepts.length === 0 ? (
          <div className="empty">none</div>
        ) : (
          <div>
            {d.concepts.map((name) => (
              <Link key={name} to={`/concept/${encodeURIComponent(name)}`} className="tag" style={{ marginBottom: 4, display: "inline-block" }}>
                {name}
              </Link>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

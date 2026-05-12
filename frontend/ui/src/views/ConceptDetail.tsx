import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ConceptDetail as CCD } from "../api";

function fileLink(path: string) {
  return `/file/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export default function ConceptDetail() {
  const { name } = useParams();
  const [d, setD] = useState<CCD | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    if (!name) return;
    api.concept(name).then(setD).catch((e) => setErr(String(e)));
  }, [name]);

  if (err) return <div className="error">{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  const c = d.concept;
  return (
    <>
      <h2>
        <span className="tag">concept</span> {c.label}
      </h2>

      <div className="card">
        <h3>Metadata</h3>
        <dl className="kv">
          <dt>frequency</dt>
          <dd>{c.frequency.toLocaleString()}</dd>
          <dt>file_count</dt>
          <dd>{c.file_count.toLocaleString()} (showing first {d.files.length} of {d.file_count_total})</dd>
          <dt>chunks lexicalized</dt>
          <dd>
            {d.chunks.length} shown of {d.chunk_count_total} total
          </dd>
          <dt>embedding row</dt>
          <dd>{c.embedding_row ?? "—"}</dd>
        </dl>
        {c.alt_labels.length > 0 && (
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: "pointer", color: "var(--fg-dim)" }}>
              alt_labels ({c.alt_labels.length})
            </summary>
            <div style={{ marginTop: 6, lineHeight: 1.6 }}>
              {c.alt_labels.map((l) => (
                <span key={l} className="tag" style={{ marginBottom: 2 }}>
                  {l}
                </span>
              ))}
            </div>
          </details>
        )}
      </div>

      {d.components.length > 0 && (
        <div className="card">
          <h3>Composed of ({d.components.length})</h3>
          <div>
            {d.components.map((n) => (
              <Link key={n} to={`/concept/${encodeURIComponent(n)}`} className="tag" style={{ marginBottom: 4, display: "inline-block" }}>
                {n}
              </Link>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="card" style={{ padding: 0 }}>
          <h3 style={{ padding: "16px 16px 0" }}>
            Cooccurring concepts ({d.cooccurring.length})
          </h3>
          {d.cooccurring.length === 0 ? (
            <div className="empty">no cooccurrence neighbors</div>
          ) : (
            <table className="rows">
              <thead>
                <tr>
                  <th>concept</th>
                  <th>weight</th>
                </tr>
              </thead>
              <tbody>
                {d.cooccurring.map((co) => (
                  <tr key={co.name}>
                    <td>
                      <Link to={`/concept/${encodeURIComponent(co.name)}`}>{co.name}</Link>
                    </td>
                    <td>{co.weight}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card" style={{ padding: 0 }}>
          <h3 style={{ padding: "16px 16px 0" }}>Files ({d.files.length})</h3>
          {d.files.length === 0 ? (
            <div className="empty">none</div>
          ) : (
            <table className="rows">
              <thead>
                <tr>
                  <th>path</th>
                </tr>
              </thead>
              <tbody>
                {d.files.map((p) => (
                  <tr key={p}>
                    <td>
                      <Link to={fileLink(p)}>{p}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <h3 style={{ padding: "16px 16px 0" }}>Chunks ({d.chunks.length})</h3>
        {d.chunks.length === 0 ? (
          <div className="empty">no chunks lexicalize this concept</div>
        ) : (
          <table className="rows">
            <thead>
              <tr>
                <th>kind</th>
                <th>symbol</th>
                <th>file</th>
                <th>lines</th>
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
                    {c.file ? <Link to={fileLink(c.file)}>{c.file}</Link> : "—"}
                  </td>
                  <td>
                    {c.beginLine ?? "—"}–{c.endLine ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

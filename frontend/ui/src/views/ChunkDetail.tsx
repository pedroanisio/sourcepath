import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ChunkDetail as CD } from "../api";
import { useBundleVersion } from "../bundle-context";

function fileLink(path: string) {
  return `/file/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export default function ChunkDetail() {
  const { idx } = useParams();
  const idxNum = Number(idx);
  const [d, setD] = useState<CD | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const bundleVersion = useBundleVersion();

  useEffect(() => {
    if (!Number.isFinite(idxNum)) {
      setErr("invalid chunk idx");
      return;
    }
    setErr(null);
    api.chunk(idxNum).then(setD).catch((e) => setErr(String(e)));
  }, [idxNum, bundleVersion]);

  if (err) return <div className="error">{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;
  const c = d.chunk;

  return (
    <>
      <h2>
        <span className="tag">chunk #{c.idx}</span> {c.symbol ?? "—"}
      </h2>

      <div className="card">
        <h3>Metadata</h3>
        <dl className="kv">
          <dt>kind</dt>
          <dd>{c.kind ?? "—"}</dd>
          <dt>file</dt>
          <dd>{c.file ? <Link to={fileLink(c.file)}>{c.file}</Link> : "—"}</dd>
          <dt>lines</dt>
          <dd>
            {c.beginLine ?? "—"}–{c.endLine ?? "—"}
          </dd>
          <dt>embedding row</dt>
          <dd>{c.embeddingRow ?? "—"}</dd>
          <dt>contentSha256</dt>
          <dd style={{ wordBreak: "break-all" }}>{c.contentSha256 ?? "—"}</dd>
        </dl>
      </div>

      <div className="card">
        <h3>Concepts ({d.concepts.length})</h3>
        {d.concepts.length === 0 ? (
          <div className="empty">no concepts lexicalized by this chunk</div>
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

      <div className="card">
        <h3>Source (first 8 KB)</h3>
        {d.blob_preview ? (
          <pre
            style={{
              margin: 0,
              padding: 12,
              background: "var(--bg)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              maxHeight: "60vh",
              overflow: "auto",
              fontSize: 12,
              lineHeight: 1.45,
            }}
          >
            {d.blob_preview}
          </pre>
        ) : (
          <div className="empty">blob not available</div>
        )}
      </div>
    </>
  );
}

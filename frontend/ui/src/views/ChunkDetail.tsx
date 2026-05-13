import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ChunkDetail as CD, ChunkRow } from "../api";
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
  const callers = d.callers ?? [];
  const callees = d.callees ?? [];

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

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <XrefList title="Callers" rows={callers} emptyLabel="not called by any chunk" />
        <XrefList title="Callees" rows={callees} emptyLabel="this chunk calls nothing tracked" />
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

function XrefList({
  title,
  rows,
  emptyLabel,
}: {
  title: string;
  rows: ChunkRow[];
  emptyLabel: string;
}) {
  return (
    <div className="card" style={{ padding: 0 }}>
      <h3 style={{ padding: "16px 16px 0" }}>
        {title} ({rows.length})
      </h3>
      {rows.length === 0 ? (
        <div className="empty" style={{ padding: 16 }}>
          {emptyLabel}
        </div>
      ) : (
        <table className="rows">
          <thead>
            <tr>
              <th>symbol</th>
              <th>file</th>
              <th>lines</th>
              <th>via</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.idx}-${r.resolver ?? ""}`}>
                <td>
                  {r.idx != null ? (
                    <Link to={`/chunk/${r.idx}`}>{r.symbol ?? "—"}</Link>
                  ) : (
                    r.symbol ?? "—"
                  )}
                </td>
                <td>
                  {r.file ? <Link to={fileLink(r.file)}>{r.file}</Link> : "—"}
                </td>
                <td>
                  {r.beginLine ?? "—"}–{r.endLine ?? "—"}
                </td>
                <td>
                  <ResolutionBadge resolution={r.resolution} resolver={r.resolver} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ResolutionBadge({
  resolution,
  resolver,
}: {
  resolution?: string;
  resolver?: string;
}) {
  if (!resolution) return <>—</>;
  // `exact` is the default — render plain; non-exact gets the muted tone so
  // reviewers can spot heuristics at a glance.
  const cls = resolution === "exact" ? "tag" : "tag muted";
  return (
    <span className={cls} title={resolver ? `resolver: ${resolver}` : undefined}>
      {resolution}
    </span>
  );
}

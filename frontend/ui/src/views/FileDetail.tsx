import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ChunkRow, FileDetail as FD, FileImpact } from "../api";
import { useBundleVersion } from "../bundle-context";

function fileLink(path: string) {
  return `/file/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export default function FileDetail() {
  const params = useParams();
  const path = params["*"] || "";
  const [d, setD] = useState<FD | null>(null);
  const [impact, setImpact] = useState<FileImpact | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const bundleVersion = useBundleVersion();

  useEffect(() => {
    // No reset on dep change — keep the previous file's data visible until
    // the new response arrives. Avoids unmounting elements that
    // asynchronous tests are observing.
    setErr(null);
    setImpact(null);
    api.file(path).then(setD).catch((e) => setErr(String(e)));
    api.impact(path).then(setImpact).catch(() => setImpact(null));
  }, [path, bundleVersion]);

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

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <FileXrefList
          title="Calls out"
          rows={d.xrefs_out ?? []}
          emptyLabel="no tracked calls leave this file"
        />
        <FileXrefList
          title="Called from"
          rows={d.xrefs_in ?? []}
          emptyLabel="no tracked calls enter this file"
        />
      </div>

      {impact && (
        <div className="card">
          <h3>Change impact</h3>
          <div className="impact-grid">
            <ImpactList
              title={`Dependencies (${impact.direct_dependencies.length})`}
              items={impact.direct_dependencies}
            />
            <ImpactList
              title={`Dependents (${impact.direct_dependents.length})`}
              items={impact.direct_dependents}
            />
            <ImpactList
              title={`Transitive dependents, depth ${impact.depth} (${impact.transitive_dependents.length})`}
              items={impact.transitive_dependents}
            />
            <ImpactList
              title={`Related tests (${impact.related_tests.length})`}
              items={impact.related_tests}
            />
          </div>
          {impact.truncated && (
            <div className="impact-note">Results truncated by backend limit.</div>
          )}
        </div>
      )}

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

function FileXrefList({
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
                  {r.resolution ? (
                    <span
                      className={r.resolution === "exact" ? "tag" : "tag muted"}
                      title={r.resolver ? `resolver: ${r.resolver}` : undefined}
                    >
                      {r.resolution}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}


function ImpactList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="impact-list">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <div className="empty compact">none</div>
      ) : (
        <ul>
          {items.slice(0, 12).map((p) => (
            <li key={p}>
              <Link to={fileLink(p)}>{p}</Link>
            </li>
          ))}
        </ul>
      )}
      {items.length > 12 && (
        <div className="impact-note">+{items.length - 12} more</div>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";
import { api, Summary } from "../api";
import { useBundleVersion } from "../bundle-context";

function Bars({ data }: { data: Record<string, number> }) {
  const items = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = items[0]?.[1] || 1;
  return (
    <div className="bars">
      {items.map(([k, v]) => (
        <div key={k} className="bar-row">
          <div className="bar-label">{k}</div>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(v / max) * 100}%` }} />
          </div>
          <div className="bar-value">{v.toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [s, setS] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const bundleVersion = useBundleVersion();

  useEffect(() => {
    // Don't reset state here — on bundle change we want the old data to
    // stay visible until the new fetch returns, both for smoother UX and
    // to avoid unmounting elements that asynchronous tests are observing.
    api.summary().then(setS).catch((e) => setErr(String(e)));
  }, [bundleVersion]);

  if (err) return <div className="error">{err}</div>;
  if (!s) return <div className="empty">Loading…</div>;

  return (
    <>
      <h2>{s.repo_name || "(unnamed)"} · summary</h2>

      <div className="card">
        <h3>Run</h3>
        <dl className="kv">
          <dt>output_dir</dt>
          <dd>{s.output_dir}</dd>
          <dt>commit</dt>
          <dd>{s.commit_sha?.slice(0, 12) || "—"}</dd>
          <dt>generated_at</dt>
          <dd>{s.generated_at}</dd>
          <dt>tool_version</dt>
          <dd>{s.tool_version}</dd>
          <dt>embeddings backend</dt>
          <dd>
            {s.embeddings_backend} · dim {s.embeddings_dimension}
          </dd>
          <dt>SHACL</dt>
          <dd>
            {s.shacl_conforms ? (
              <span className="tag good">conforms</span>
            ) : (
              <span className="tag warn">non-conforming</span>
            )}
          </dd>
        </dl>
      </div>

      <div className="stats-grid">
        <Stat label="files" value={s.counts.files} />
        <Stat label="chunks" value={s.n_chunks} />
        <Stat label="concepts" value={s.n_concepts} />
        <Stat label="import edges" value={s.counts.import_edges} />
        <Stat label="ext. imports" value={s.counts.import_external_edges} />
        <Stat label="declared deps" value={s.counts.declares_dependency_edges} />
        <Stat label="pinned deps" value={s.counts.pins_dependency_edges} />
        <Stat label="test edges" value={s.counts.tests_edges} />
      </div>

      <div className="card">
        <h3>Files by language</h3>
        <Bars data={s.files_by_language} />
      </div>

      <div className="card">
        <h3>Files by type</h3>
        <Bars data={s.files_by_type} />
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{(value ?? 0).toLocaleString()}</div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./views/Dashboard";
import FileGraph from "./views/FileGraph";
import SymbolGraph from "./views/SymbolGraph";
import ConceptGraph from "./views/ConceptGraph";
import ChunkSearch from "./views/ChunkSearch";
import FileDetail from "./views/FileDetail";
import ChunkDetail from "./views/ChunkDetail";
import ConceptDetail from "./views/ConceptDetail";
import { api, setBundle, type BundleInfo } from "./api";
import { BundleVersionContext } from "./bundle-context";

const BUNDLE_STORAGE_KEY = "cbm-bundle";

export default function App() {
  // Hydrate the picker from localStorage so a refresh keeps the same bundle.
  const [bundle, setBundleState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(BUNDLE_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [bundles, setBundles] = useState<BundleInfo[]>([]);
  const [bundlesError, setBundlesError] = useState<string | null>(null);
  const [bundleVersion, setBundleVersion] = useState(0);

  // Push the current selection into the api module before any view fetches.
  // useState's initializer runs once on mount, so this also covers SSR-free
  // first-render to make sure the api helpers see the persisted value.
  if (typeof window !== "undefined") setBundle(bundle);

  // Fetch the bundle list once. If nothing is persisted yet, accept the
  // backend's selection so the first render shows real data.
  useEffect(() => {
    setBundle(bundle);
    api
      .bundles()
      .then((resp) => {
        // Defensive: a malformed response (e.g. a partial test mock) must
        // not crash the picker — fall back to an empty list.
        const list = Array.isArray(resp?.bundles) ? resp.bundles : [];
        setBundles(list);
        const valid = new Set(list.map((b) => b.name));
        if (bundle !== null && !valid.has(bundle)) {
          // Persisted bundle no longer exists (renamed/removed/wrong env).
          // Fall back to whatever the backend recommends — or null if the
          // listing is empty — so we stop sending ?bundle=<stale> forever.
          setBundleState(resp?.selected ?? null);
        } else if (bundle === null && resp?.selected) {
          setBundleState(resp.selected);
        }
      })
      .catch((e) => setBundlesError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist + propagate any subsequent change. Bump bundleVersion so views
  // that depend on it refetch — element references stay stable (no remount).
  useEffect(() => {
    setBundle(bundle);
    setBundleVersion((v) => v + 1);
    try {
      if (bundle !== null) localStorage.setItem(BUNDLE_STORAGE_KEY, bundle);
      else localStorage.removeItem(BUNDLE_STORAGE_KEY);
    } catch {
      // private-mode / quota — ignore
    }
  }, [bundle]);

  return (
    <BundleVersionContext.Provider value={bundleVersion}>
      <div className="app">
        <aside className="sidebar">
          <h1>codebase-mapper</h1>
          <BundlePicker
            bundles={bundles}
            selected={bundle}
            error={bundlesError}
            onSelect={setBundleState}
          />
          <nav>
            <NavLink to="/dashboard">Dashboard</NavLink>
            <NavLink to="/files">File graph</NavLink>
            <NavLink to="/symbols">Symbol graph</NavLink>
            <NavLink to="/concepts">Concept graph</NavLink>
            <NavLink to="/chunks">Chunk search</NavLink>
          </nav>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/files" element={<FileGraph />} />
            <Route path="/symbols" element={<SymbolGraph />} />
            <Route path="/concepts" element={<ConceptGraph />} />
            <Route path="/chunks" element={<ChunkSearch />} />
            <Route path="/file/*" element={<FileDetail />} />
            <Route path="/chunk/:idx" element={<ChunkDetail />} />
            <Route path="/concept/:name" element={<ConceptDetail />} />
          </Routes>
        </main>
      </div>
    </BundleVersionContext.Provider>
  );
}

function BundlePicker({
  bundles,
  selected,
  error,
  onSelect,
}: {
  bundles: BundleInfo[];
  selected: string | null;
  error: string | null;
  onSelect: (name: string | null) => void;
}) {
  if (error) {
    return (
      <div className="bundle-picker" role="alert">
        bundle list unavailable: {error}
      </div>
    );
  }
  if (bundles.length === 0) {
    return (
      <div className="bundle-picker">
        <span>No bundles discovered</span>
      </div>
    );
  }
  return (
    <div className="bundle-picker">
      <label htmlFor="bundle-select">Bundle</label>
      <select
        id="bundle-select"
        aria-label="Bundle"
        value={selected ?? ""}
        onChange={(e) => onSelect(e.target.value || null)}
      >
        {bundles.map((b) => (
          <option key={b.name} value={b.name}>
            {b.name}
            {b.files != null ? ` (${b.files})` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

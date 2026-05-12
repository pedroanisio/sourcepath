import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./views/Dashboard";
import FileGraph from "./views/FileGraph";
import ConceptGraph from "./views/ConceptGraph";
import ChunkSearch from "./views/ChunkSearch";

export default function App() {
  return (
    <div className="app">
      <aside className="sidebar">
        <h1>codebase-mapper</h1>
        <nav>
          <NavLink to="/dashboard">Dashboard</NavLink>
          <NavLink to="/files">File graph</NavLink>
          <NavLink to="/concepts">Concept graph</NavLink>
          <NavLink to="/chunks">Chunk search</NavLink>
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/files" element={<FileGraph />} />
          <Route path="/concepts" element={<ConceptGraph />} />
          <Route path="/chunks" element={<ChunkSearch />} />
        </Routes>
      </main>
    </div>
  );
}

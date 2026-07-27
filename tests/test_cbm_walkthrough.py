"""TDD spec — cbm_walkthrough, the narrated customer walkthrough page.

Absorbed from _to_process/ into scripts/ as a first-class cbm.py command.
Contracts:

  1. Dispatcher — ``walkthrough`` is a registered cbm.py command.
  2. Chunk-IRI parsing stays coupled to the real L2 id scheme: a chunk
     IRI built by the actual ``_chunk_id`` + ``chunk_iri`` helpers must
     parse back to its path/kind/symbol/lines.
  3. Keystone selection prefers a widely-imported file that carries its
     own symbols over a re-export hub (``__init__.py``).
  4. Blast radius is a depth- and cap-bounded BFS over cbm:imports.
  5. End-to-end on a real L1+L2+L3 bundle (hash backend): one HTML page
     with all five scenes, the standing "Evidence basis & confidence"
     banner, the PALS framing for LLM content, lexical fallback for the
     question panel, and zero external fetches (offline invariant).
  6. Without ``--out`` the page lands under CBM_REPORTS_DIR with the
     standardized ``<repo>__walkthrough__<ts>`` stem.

Run from the repo root:  python -m pytest tests/test_cbm_walkthrough.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("rdflib")

import cbm  # noqa: E402
import cbm_report as R  # noqa: E402
import cbm_walkthrough as W  # noqa: E402

from plugins.chunks_embeddings.embedder import _chunk_id  # noqa: E402
from plugins.chunks_embeddings.graph_writer import chunk_iri  # noqa: E402

_EXTERNAL_REF = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.IGNORECASE)


# --- Contract 1: dispatcher registration ----------------------------------

def test_walkthrough_is_a_dispatcher_command() -> None:
    assert "walkthrough" in cbm.COMMANDS
    module, _desc = cbm.COMMANDS["walkthrough"]
    assert module == "cbm_walkthrough"


# --- Contract 2: chunk-IRI parsing coupled to the live id scheme ----------

def test_parse_chunk_roundtrips_the_real_iri_scheme() -> None:
    chunk = {"kind": "function", "symbol": "validate_request",
             "parent_symbol": None, "line_start": 10, "line_end": 42,
             "byte_start": 120, "byte_end": 900}
    iri = chunk_iri(_chunk_id("src/core.py", chunk))
    parsed = W.parse_chunk(iri)
    assert parsed == {"path": "src/core.py", "symbol": "validate_request",
                      "kind": "function", "b": 10, "e": 42}


def test_parse_chunk_handles_parent_symbol_and_rejects_non_chunks() -> None:
    chunk = {"kind": "method", "symbol": "check", "parent_symbol": "Guard",
             "line_start": 5, "line_end": 9, "byte_start": 50, "byte_end": 99}
    parsed = W.parse_chunk(chunk_iri(_chunk_id("a.py", chunk)))
    assert parsed["symbol"] == "Guard.check"
    assert W.parse_chunk("https://example.org/cbm/instance#file/a.py") is None


# --- Contracts 3 & 4: keystone + blast radius on synthetic graphs ---------

def _analysis(indeg: dict, chunks_by_file: dict, adj=None) -> dict:
    return {"src": set(indeg), "indeg": Counter(indeg),
            "chunks_by_file": chunks_by_file,
            "adj_out": defaultdict(set, (adj or {}).get("out", {})),
            "adj_in": defaultdict(set, (adj or {}).get("in", {}))}


def _syms(n: int) -> list[dict]:
    return [{"kind": "function", "symbol": f"f{i}", "b": i, "e": i + 1}
            for i in range(n)]


def test_pick_keystone_prefers_substance_over_reexport_hub() -> None:
    A = _analysis(
        indeg={"pkg/__init__.py": 9, "core.py": 3, "leaf.py": 0},
        chunks_by_file={"pkg/__init__.py": _syms(0), "core.py": _syms(4),
                        "leaf.py": _syms(9)})
    # __init__ hub excluded; leaf.py scores 0 (no importers); core.py wins.
    assert W.pick_keystone(A, focus=None) == "core.py"


def test_pick_keystone_focus_overrides_when_present() -> None:
    A = _analysis(indeg={"core.py": 3, "other.py": 1},
                  chunks_by_file={"core.py": _syms(4), "other.py": _syms(3)})
    assert W.pick_keystone(A, focus="other.py") == "other.py"


def test_blast_radius_bounds_depth_and_cap() -> None:
    # chain: k -> a -> b -> c -> d  (depth 3 from k reaches a,b,c not d)
    out = {"k": {"a"}, "a": {"b"}, "b": {"c"}, "c": {"d"}}
    A = _analysis(indeg={}, chunks_by_file={},
                  adj={"out": out, "in": {}})
    deps, dependents = W.blast_radius(A, "k", depth=3)
    assert deps == {"a", "b", "c"}
    assert dependents == set()
    deps_capped, _ = W.blast_radius(A, "k", depth=3, cap=2)
    assert len(deps_capped) == 2


# --- Contracts 5 & 6: end-to-end on a real bundle --------------------------

@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("walkthrough")
    repo = base / "tinyrepo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "core.py").write_text(
        "def validate_request(payload):\n    return bool(payload)\n\n\n"
        "def parse_request(raw):\n    return {'raw': raw}\n\n\n"
        "class RequestGuard:\n    def check(self, payload):\n"
        "        return validate_request(payload)\n")
    (repo / "app.py").write_text(
        "import core\n\n\ndef handle_request(raw):\n"
        "    return core.validate_request(core.parse_request(raw))\n")
    (repo / "web.py").write_text(
        "import core\n\n\ndef route_request(raw):\n"
        "    return core.parse_request(raw)\n")
    (repo / "cli.py").write_text(
        "import core\n\n\ndef main():\n"
        "    return core.RequestGuard().check({'a': 1})\n")
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "test_app.py").write_text(
        "import app\n\n\ndef test_handle():\n"
        "    assert app.handle_request('x')\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], cwd=repo, check=True)

    out = base / "bundle"
    subprocess.run(
        [sys.executable, "scripts/run_l3.py", "--repo", str(repo),
         "--out", str(out), "--backend", "hash"],
        cwd=ROOT, check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def page(bundle, tmp_path_factory) -> str:
    stem = tmp_path_factory.mktemp("out") / "walk"
    rc = W.main(["--bundle", str(bundle), "--out", str(stem),
                 "--query", "validate an incoming request payload"])
    assert rc in (0, None)
    html = (stem.parent / "walk.html").read_text()
    assert html
    return html


def test_page_has_all_five_scenes(page) -> None:
    for title in ("Orientation", "keystone", "Blast radius",
                  "concept", "Ask it a question"):
        assert title in page, f"scene {title!r} missing"


def test_keystone_is_the_substantive_hub(page) -> None:
    assert "core.py" in page
    assert "validate_request" in page


def test_evidence_banner_and_pals_framing(page) -> None:
    assert "Evidence basis &amp; confidence" in page
    # No L4 layer in this bundle: the LLM receipt must be absent, and no
    # LLM text may be presented; the framing text still explains tiers.
    assert "LLM-authored" in page


def test_question_panel_falls_back_to_lexical_on_hash_backend(page) -> None:
    assert "lexical" in page
    assert "validate an incoming request payload" in page


# ---------------------------------------------------------------- ollama panel
@pytest.fixture(scope="module")
def ollama_bundle(bundle, tmp_path_factory):
    """A copy of the fixture bundle relabeled as an ``ollama:`` backend.
    Vectors are the hash backend's — irrelevant here, since what's under
    test is which code path the backend *name* selects."""
    import shutil

    import numpy as np

    work = tmp_path_factory.mktemp("ollama") / "bundle"
    shutil.copytree(bundle, work)
    dim = int(np.load(work / "embeddings.npz")["vectors"].shape[1])
    (work / "embeddings_meta.json").write_text(json.dumps(
        {"backend": {"name": "ollama:nomic-embed-text", "dimension": dim,
                     "normalized": True},
         "normalized": True}))
    return work, dim


def _semantic_with_encoder(ollama_bundle, monkeypatch, encoder):
    work, dim = ollama_bundle
    monkeypatch.setattr(W, "_encode_query_ollama", encoder)
    found = {k: str(work / k) for k in
             ("inventory.ttl", "embeddings.npz", "embeddings_meta.json",
              "concepts.json")
             if (work / k).exists()}
    A = W.analyze(R.load_graph(found, R.resolve_cache_dir(str(work), None)))
    return W.semantic(found, A, "validate an incoming request payload"), dim


def test_question_panel_uses_semantic_mode_on_ollama_backend(
        ollama_bundle, monkeypatch) -> None:
    """An ``ollama:<model>`` bundle must take the semantic path — before
    this, the panel tested for '/' in the model name and silently
    downgraded every Ollama bundle to lexical."""
    import numpy as np

    seen = {}
    _work, dim = ollama_bundle

    def _enc(model, query):
        seen["model"] = model
        v = np.ones(dim, dtype="float32")
        return v / np.linalg.norm(v)

    res, _ = _semantic_with_encoder(ollama_bundle, monkeypatch, _enc)
    assert seen["model"] == "nomic-embed-text"  # "ollama:" prefix stripped
    assert res["mode"] == "semantic"
    assert res["model"] == "ollama:nomic-embed-text"


def test_ollama_panel_degrades_to_lexical_when_server_fails(
        ollama_bundle, monkeypatch) -> None:
    def _boom(model, query):
        raise RuntimeError("connection refused")

    res, _ = _semantic_with_encoder(ollama_bundle, monkeypatch, _boom)
    assert res["mode"] == "lexical"


def test_ollama_panel_degrades_to_lexical_on_dimension_mismatch(
        ollama_bundle, monkeypatch) -> None:
    """A query vector of the wrong width would rank noise."""
    import numpy as np

    _work, dim = ollama_bundle
    res, _ = _semantic_with_encoder(
        ollama_bundle, monkeypatch,
        lambda m, q: np.ones(dim + 7, dtype="float32"))
    assert res["mode"] == "lexical"


def test_offline_invariant_no_external_fetches(page) -> None:
    assert not _EXTERNAL_REF.search(page)


def test_default_out_uses_reports_dir(bundle, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CBM_REPORTS_DIR", str(tmp_path))
    rc = W.main(["--bundle", str(bundle)])
    assert rc in (0, None)
    hits = list(tmp_path.glob("*__walkthrough__*.html"))
    assert len(hits) == 1

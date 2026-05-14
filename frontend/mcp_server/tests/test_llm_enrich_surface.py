"""Step 7: MCP surface for L4 enrichments — end-to-end test.

Builds a tiny bundle with cbml4: enrichments (requires live Ollama),
points the MCP backend at it, and asserts that ``file_detail``,
``concept_detail``, and ``repository_summary`` surface the L4 fields
the schemas declare.

These tests run inside the regular MCP test suite and skip cleanly
when Ollama is unreachable. They use a per-test temporary bundle
emitted on the fly — no dependency on a pre-built fixture, no
shared state with the session-scoped live_bundle fixture.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _ollama_reachable() -> bool:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from plugins.llm_enrich.client import OllamaClient
        return OllamaClient(timeout=3.0).ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama unreachable — L4 surface tests skipped",
)


def _build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    # Three files cross-importing each other, all referencing
    # 'behavior'/'contract' so L3's MIN_COOCCURRENCE=2 threshold
    # passes for those curated terms. Import edges give the central
    # files non-zero degree so repository_summary's central_files
    # array is populated.
    (target / "a.py").write_text(
        '"""Module A: Behavior + Contract."""\n'
        'from b import LoginIntent\n'
        'class UserBehavior:\n'
        '    def authenticate(self, t): return self.contract(t)\n'
        '    def contract(self, t): return bool(t)\n'
    )
    (target / "b.py").write_text(
        '"""Module B: Behavior + Intent."""\n'
        'from c import AdminBehavior\n'
        'class LoginIntent:\n'
        '    def behavior(self): return "login"\n'
        '    def contract(self): return True\n'
    )
    (target / "c.py").write_text(
        '"""Module C: Behavior + Contract."""\n'
        'class AdminBehavior:\n'
        '    def authenticate(self): pass\n'
        '    def contract(self): pass\n'
    )
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


@pytest.fixture(scope="module")
def enriched_bundle(tmp_path_factory):
    """Build a fresh bundle with L4 enrichments and yield (bundle_path,
    bundle_name) for the MCP handlers to consume."""
    sys.path.insert(0, str(REPO_ROOT))
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from codebase_mapper.inspection.repo_source import resolve_repo_source
    from plugins import chunks_embeddings, concept_graph, llm_enrich
    from plugins.llm_enrich.cache import Cache
    from plugins.llm_enrich.client import OllamaClient

    work = tmp_path_factory.mktemp("enriched")
    fixture = work / "fixture"
    _build_fixture(fixture)
    cache_dir = work / "cache"
    out = work / "bundle"

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(256))
    concept_graph.register_all()
    llm_enrich.register_all(
        client=OllamaClient(),
        cache=Cache(cache_dir=cache_dir),
        scopes=("files", "concepts"),
    )
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        emit("enriched", mapped, out.resolve(), emit_blobs_flag=False)
    return out, "bundle"  # backend uses the dir name as bundle_name


@pytest.fixture(scope="module", autouse=True)
def _wire_backend_to_enriched(enriched_bundle):
    """Point CBM_OUTPUT_DIR / CBM_BUNDLES_ROOT at our enriched bundle,
    clear the backend's lru_cache so it reloads from disk, and restore
    when the module's tests are done.

    This deliberately replaces the session-scoped `live_bundle` env
    only inside this module — the conftest's autouse `_env` fixture
    runs first; we override after."""
    import os
    out_dir, _name = enriched_bundle
    # The backend uses the directory's basename as the bundle name —
    # rename our temp dir to match what we want to dispatch to.
    bundle_path = out_dir.parent / "bundle"
    if not bundle_path.exists():
        out_dir.rename(bundle_path)
    saved_dir = os.environ.get("CBM_OUTPUT_DIR")
    saved_root = os.environ.get("CBM_BUNDLES_ROOT")
    os.environ["CBM_OUTPUT_DIR"] = str(bundle_path)
    os.environ["CBM_BUNDLES_ROOT"] = str(bundle_path.parent)

    import app as backend_app
    backend_app.get_bundle.cache_clear()

    yield

    backend_app.get_bundle.cache_clear()
    if saved_dir is not None:
        os.environ["CBM_OUTPUT_DIR"] = saved_dir
    else:
        os.environ.pop("CBM_OUTPUT_DIR", None)
    if saved_root is not None:
        os.environ["CBM_BUNDLES_ROOT"] = saved_root
    else:
        os.environ.pop("CBM_BUNDLES_ROOT", None)


def _dispatch(tool: str, args: dict | None = None):
    from frontend.mcp_server import dispatch
    return dispatch(tool, args or {})


def test_file_detail_returns_llm_summary():
    payload = _dispatch("file_detail",
                        {"bundle": "bundle", "path": "a.py"})
    assert "llm_summary" in payload, payload.keys()
    enr = payload["llm_summary"]
    assert isinstance(enr.get("text"), str) and enr["text"].strip()
    prov = enr["provenance"]
    assert prov["model"] == "qwen2.5-coder:7b"
    assert len(prov["prompt_sha"]) == 64
    assert len(prov["target_sha"]) == 64
    assert prov["generated_at"]


def test_concept_detail_returns_llm_description_when_typed():
    # 'behavior' is a curated-vocab term — should be enriched.
    payload = _dispatch("concept_detail",
                        {"bundle": "bundle", "name": "behavior"})
    assert "llm_description" in payload, payload.keys()
    enr = payload["llm_description"]
    assert isinstance(enr.get("text"), str) and enr["text"].strip()
    prov = enr["provenance"]
    assert prov["model"] == "qwen2.5-coder:7b"
    assert len(prov["prompt_sha"]) == 64


def test_concept_detail_omits_llm_description_for_untyped():
    """Uncurated concepts shouldn't have a description (the aggregator
    only enriches typed concepts)."""
    # Pick a non-curated concept name from the bundle.
    from frontend.mcp_server.handlers import _get_bundle
    b = _get_bundle("bundle")
    untyped = next(
        (n for n, m in b.concepts.get("concepts", {}).items()
         if "kind" not in m),
        None,
    )
    if not untyped:
        pytest.skip("no untyped concepts in this fixture")
    payload = _dispatch("concept_detail",
                        {"bundle": "bundle", "name": untyped})
    assert "llm_description" not in payload, (
        f"untyped concept {untyped!r} unexpectedly enriched: "
        f"{payload.get('llm_description')}"
    )


def test_repository_summary_central_files_carry_llm_summary():
    payload = _dispatch("repository_summary",
                        {"bundle": "bundle",
                         "central_files_limit": 10})
    enriched_central = [
        f for f in payload["central_files"] if "llm_summary" in f
    ]
    assert enriched_central, (
        f"no central_files entry carries llm_summary; "
        f"got: {payload['central_files']}"
    )
    for f in enriched_central:
        assert isinstance(f["llm_summary"], str)
        assert f["llm_summary"].strip()


def test_repository_summary_key_concepts_carry_llm_description():
    payload = _dispatch("repository_summary",
                        {"bundle": "bundle",
                         "key_concepts_limit": 30})
    enriched = [c for c in payload["key_concepts"]
                if "llm_description" in c]
    assert enriched, (
        f"no key_concepts entry carries llm_description; "
        f"got: {payload['key_concepts']}"
    )
    for c in enriched:
        # Every enriched concept should also be typed (only curated
        # concepts get descriptions).
        assert c.get("kind"), (
            f"concept {c['name']!r} has llm_description but no kind"
        )

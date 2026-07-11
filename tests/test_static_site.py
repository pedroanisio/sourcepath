"""generate_static_site.py — regression suite for the static bundle browser.

The generator promises (docstring + __file_meta__): pages are projected from
the backend's ``load_bundle`` (never re-derived), the output is fully offline,
and the three provenance tiers are visually distinguished. None of that was
pinned by a test. This suite builds a real minimal bundle by running the L1
mapper on a throwaway git repo (fast: a handful of files), generates the site,
and asserts:

- the build succeeds on an L1-only bundle (absent L2/L3/L4 layers degrade,
  they don't crash);
- the core pages exist and per-file pages are emitted for the mapped files;
- the offline invariant: no page references an external script, stylesheet,
  or image (``src=``/``href=`` pointing at http(s));
- provenance separation is rendered: tier badges (mechanical / inferred /
  llm) and the PALS's-LAW framing appear in the output.

Requires the project env: run via  uv run python -m pytest tests/test_static_site.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("rdflib")

import generate_static_site as G  # noqa: E402

_EXTERNAL_REF = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.IGNORECASE)


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("static-site")
    repo = base / "tinyrepo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "app.py").write_text(
        "from pkg import util\n\ndef main():\n    return util.helper()\n")
    (repo / "pkg" / "util.py").write_text("def helper():\n    return 42\n")
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "test_app.py").write_text(
        "import app\n\ndef test_main():\n    assert app.main() == 42\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"], cwd=repo, check=True)

    bundle = base / "bundle"
    subprocess.run(
        [sys.executable, "-m", "codebase_mapper",
         "--repo", str(repo), "--out", str(bundle)],
        cwd=ROOT, check=True, capture_output=True)

    out = base / "site"
    rc = G.main(["--bundle", str(bundle), "--output", str(out)])
    assert rc in (0, None)
    return out


def _pages(site: Path) -> list[Path]:
    return sorted(site.rglob("*.html"))


def test_core_pages_exist(site):
    for name in ("index.html", "architecture.html", "files.html",
                 "graph.html", "search.html", "concepts.html"):
        assert (site / name).is_file(), f"missing page: {name}"


def test_per_file_pages_cover_the_mapped_python_files(site):
    file_pages = list((site / "files").glob("*.html"))
    assert len(file_pages) >= 4  # app.py, test_app.py, pkg/util.py, pkg/__init__.py
    corpus = " ".join(p.read_text(encoding="utf-8") for p in file_pages)
    for path in ("app.py", "pkg/util.py", "test_app.py"):
        assert path in corpus


def test_l1_only_bundle_builds_without_l2_l3_l4(site):
    # The fixture bundle has no chunks/embeddings/concepts/enrichment;
    # reaching this assertion at all means absent layers degraded gracefully.
    assert (site / "index.html").stat().st_size > 0


def test_offline_invariant_no_external_fetches(site):
    for page in _pages(site):
        text = page.read_text(encoding="utf-8")
        m = _EXTERNAL_REF.search(text)
        assert m is None, f"{page.name} references an external URL: {m.group(0)!r}"


def test_provenance_tiers_are_rendered(site):
    index = (site / "index.html").read_text(encoding="utf-8")
    for badge in ("badge-mechanical", "badge-inferred", "badge-llm"):
        assert badge in index


def test_pals_framing_is_present(site):
    corpus = " ".join(p.read_text(encoding="utf-8")
                      for p in _pages(site) if p.parent == site)
    assert "PALS" in corpus
    assert "unverified" in corpus.lower()


def test_l1_bundle_site_has_no_map_page(site):
    """The Cartogram needs an L3 bundle; on an L1-only bundle the site must
    skip the Map page (disclosed in the build log), never link a dead nav
    entry."""
    assert not (site / "map.html").exists()
    assert 'map.html' not in (site / "index.html").read_text(encoding="utf-8")


@pytest.mark.skipif(__import__("shutil").which("node") is None,
                    reason="node not installed")
def test_l3_bundle_site_gets_interactive_map_page(tmp_path):
    """With an L3 bundle and Node available, the site gains the interactive
    Cartogram as map.html and every page's nav links it."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text("def helper():\n    return 42\n")
    (repo / "app.py").write_text(
        "from pkg import core\n\ndef main():\n    return core.helper()\n")
    (repo / "test_app.py").write_text(
        "import app\n\ndef test_m():\n    assert app.main() == 42\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "f"], cwd=repo, check=True)
    bundle = tmp_path / "bundle"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_l3.py"),
         "--repo", str(repo), "--out", str(bundle), "--backend", "hash"],
        cwd=ROOT, check=True, capture_output=True)

    out = tmp_path / "site"
    rc = G.main(["--bundle", str(bundle), "--output", str(out)])
    assert rc in (0, None)
    map_page = out / "map.html"
    assert map_page.is_file() and map_page.stat().st_size > 100_000
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="map.html"' in index and ">Map<" in index

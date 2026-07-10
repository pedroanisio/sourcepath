"""Env settings + standardized report naming — regression contract.

Pins the contracts introduced by ``codebase_mapper.shared_kernel.settings``:

- ``.env`` autoload: KEY=VALUE files load into ``os.environ`` WITHOUT
  overriding the real environment (deployment env always wins); discovery
  walks upward from the CWD; malformed lines fail loudly, not silently.
- Single source of truth for the bundles root: the ``"_tmp"`` default
  literal lives ONLY in settings.py — a grep guard fails if any other
  module re-hardcodes it next to CBM_BUNDLES_ROOT.
- ``CBM_REPORTS_DIR``: default ``reports/``, env-overridable, created on
  demand.
- Report naming: ``<source>__<kind>__<YYYYMMDDTHHMMSSZ>[.ext]`` — source
  and kind are slugged, the timestamp is UTC second-resolution, and
  ``default_report_path`` bumps a ``-N`` suffix instead of colliding.
- Wiring: cbm_report / cbm_dossier / cbm_report_rs derive their default
  output from the shared helper; ``cbm.py`` loads ``.env`` on every run;
  ``.env.example`` documents the new key (verify_drift_p1 enforces the
  full inventory).

Run from the repo root:  python -m pytest tests/test_env_settings.py
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from codebase_mapper.shared_kernel import settings  # noqa: E402


WHEN = datetime(2026, 7, 10, 3, 15, 0, tzinfo=timezone.utc)


@pytest.fixture()
def clean_env():
    """Snapshot/restore os.environ — the code under test mutates it."""
    saved = os.environ.copy()
    for key in ("CBM_BUNDLES_ROOT", "CBM_REPORTS_DIR"):
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------- load_env

def test_load_env_parses_comments_blanks_quotes_and_export(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        'DQUOTED="quoted value"\n'
        "SQUOTED='single'\n"
        "export EXPORTED=yes\n"
        "SPACED =  padded  \n"
    )
    applied = settings.load_env(env)
    assert applied == {
        "PLAIN": "value",
        "DQUOTED": "quoted value",
        "SQUOTED": "single",
        "EXPORTED": "yes",
        "SPACED": "padded",
    }
    assert os.environ["DQUOTED"] == "quoted value"


def test_load_env_skips_blank_placeholder_values(tmp_path, clean_env):
    """`KEY=` lines (an .env copied from .env.example) must not export empty
    strings — set-but-empty silently disables documented unset-fallbacks
    (CORS default origins, unshallow provenance, …)."""
    env = tmp_path / ".env"
    env.write_text("BLANK=\nQUOTED_BLANK=''\nREAL=x\n")
    applied = settings.load_env(env)
    assert applied == {"REAL": "x"}
    assert "BLANK" not in os.environ
    assert "QUOTED_BLANK" not in os.environ


def test_load_env_never_overrides_real_environment(tmp_path, clean_env):
    os.environ["PLAIN"] = "from-real-env"
    env = tmp_path / ".env"
    env.write_text("PLAIN=from-dotenv\n")
    applied = settings.load_env(env)
    assert "PLAIN" not in applied
    assert os.environ["PLAIN"] == "from-real-env"


def test_load_env_override_flag_wins(tmp_path, clean_env):
    os.environ["PLAIN"] = "from-real-env"
    env = tmp_path / ".env"
    env.write_text("PLAIN=from-dotenv\n")
    applied = settings.load_env(env, override=True)
    assert applied["PLAIN"] == "from-dotenv"
    assert os.environ["PLAIN"] == "from-dotenv"


def test_load_env_no_file_found_is_a_noop(tmp_path, clean_env, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert settings.load_env() == {}


def test_load_env_explicit_missing_path_raises(tmp_path, clean_env):
    with pytest.raises(FileNotFoundError):
        settings.load_env(tmp_path / "nope.env")


def test_load_env_malformed_line_raises_with_line_number(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("GOOD=1\nthis is not an assignment\n")
    with pytest.raises(ValueError, match="line 2"):
        settings.load_env(env)


def test_env_example_parses_clean(clean_env):
    """The committed inventory must always be loadable."""
    applied = settings.load_env(REPO_ROOT / ".env.example")
    assert "CBM_BUNDLES_ROOT" in applied


def test_find_env_file_walks_upward(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("X=1\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert settings.find_env_file() == tmp_path / ".env"


def test_find_env_file_none_when_absent(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()  # boundary — keeps the walk hermetic
    monkeypatch.chdir(tmp_path)
    assert settings.find_env_file() is None


def test_find_env_file_stops_at_repo_boundary(tmp_path, monkeypatch):
    """A .env in a PARENT of the repo is foreign config — never loaded."""
    (tmp_path / ".env").write_text("FOREIGN=1\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    assert settings.find_env_file() is None


# ----------------------------------------------------- bundles/reports dirs

def test_bundles_root_defaults_to_tmp(tmp_path, clean_env, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert settings.DEFAULT_BUNDLES_ROOT == "_tmp"
    assert settings.bundles_root() == (tmp_path / "_tmp").resolve()


def test_bundles_root_honours_env(tmp_path, clean_env):
    os.environ["CBM_BUNDLES_ROOT"] = str(tmp_path / "bundles")
    assert settings.bundles_root() == (tmp_path / "bundles").resolve()


def test_tmp_default_literal_lives_only_in_settings():
    """Regression guard: no module may re-hardcode the '_tmp' default
    next to CBM_BUNDLES_ROOT — settings.py is the single source."""
    pattern = re.compile(r'CBM_BUNDLES_ROOT["\']\s*,\s*["\']_tmp')
    offenders = []
    for root in ("codebase_mapper", "frontend", "scripts", "plugins"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path == REPO_ROOT / "codebase_mapper" / "shared_kernel" / "settings.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_reports_dir_default_and_ensure(tmp_path, clean_env, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert settings.DEFAULT_REPORTS_DIR == "reports"
    d = settings.reports_dir(ensure=True)
    assert d == (tmp_path / "reports").resolve()
    assert d.is_dir()


def test_reports_dir_honours_env(tmp_path, clean_env):
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "out" / "rpt")
    d = settings.reports_dir(ensure=True)
    assert d == (tmp_path / "out" / "rpt").resolve()
    assert d.is_dir()


def test_env_example_documents_reports_dir():
    text = (REPO_ROOT / ".env.example").read_text()
    keys = {l.split("=", 1)[0].strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#") and "=" in l}
    assert "CBM_REPORTS_DIR" in keys


# ------------------------------------------------------------ report naming

def test_report_stem_format(clean_env):
    assert settings.report_stem("graphite", "xray", when=WHEN) == \
        "graphite__xray__20260710T031500Z"


def test_report_stem_slugs_source_and_kind(clean_env):
    stem = settings.report_stem("Code Base (Mapper)!", "X-Ray Report", when=WHEN)
    assert stem == "code-base-mapper__x-ray-report__20260710T031500Z"


def test_report_stem_rejects_empty_and_naive(clean_env):
    with pytest.raises(ValueError):
        settings.report_stem("", "xray", when=WHEN)
    with pytest.raises(ValueError):
        settings.report_stem("src", "", when=WHEN)
    with pytest.raises(ValueError):
        settings.report_stem("src", "xray", when=datetime(2026, 7, 10))


def test_report_stem_field_separator_cannot_be_forged(clean_env):
    """'__' inside source/kind must not survive slugging — the separator
    stays parseable."""
    stem = settings.report_stem("a__b", "c__d", when=WHEN)
    assert stem.count("__") == 2


def test_default_report_path_places_under_reports_dir(tmp_path, clean_env):
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "reports")
    p = settings.default_report_path("graphite", "dossier", ext="pdf", when=WHEN)
    assert p == (tmp_path / "reports" / "graphite__dossier__20260710T031500Z.pdf").resolve()
    assert p.parent.is_dir()


def test_default_report_path_bumps_on_collision(tmp_path, clean_env):
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "reports")
    first = settings.default_report_path("zod", "xray", when=WHEN)
    # simulate the first run having produced artifacts off the stem
    first.parent.mkdir(parents=True, exist_ok=True)
    (first.parent / (first.name + ".html")).write_text("x")
    second = settings.default_report_path("zod", "xray", when=WHEN)
    assert second.name == "zod__xray__20260710T031500Z-2"
    assert first.name != second.name


# ----------------------------------------------------------------- wiring

def test_cbm_report_default_out_uses_standard_naming(tmp_path, clean_env):
    import cbm_report
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "reports")
    out = cbm_report.default_out("graphite", when=WHEN)
    assert out.endswith("graphite__xray__20260710T031500Z")
    assert str(tmp_path / "reports") in out


def test_cbm_dossier_default_out_uses_standard_naming(tmp_path, clean_env):
    pytest.importorskip("reportlab")  # cbm_dossier exits at import without it
    import cbm_dossier
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "reports")
    out = cbm_dossier.default_out("graphite", when=WHEN)
    assert out.endswith("graphite__dossier__20260710T031500Z.pdf")


def test_cbm_report_rs_injects_default_out(tmp_path, clean_env):
    import cbm_report_rs
    os.environ["CBM_REPORTS_DIR"] = str(tmp_path / "reports")
    argv = cbm_report_rs.inject_default_out(["_tmp/graphite"], when=WHEN)
    assert argv[0] == "_tmp/graphite"
    assert "-o" in argv
    out = argv[argv.index("-o") + 1]
    assert out.endswith("graphite__report__20260710T031500Z.pdf")


def test_cbm_report_rs_respects_explicit_out(clean_env):
    import cbm_report_rs
    argv = cbm_report_rs.inject_default_out(["_tmp/graphite", "-o", "mine.pdf"])
    assert argv == ["_tmp/graphite", "-o", "mine.pdf"]


def test_cbm_cli_loads_dotenv_on_every_run(tmp_path, clean_env, monkeypatch, capsys):
    import cbm
    env = tmp_path / ".env"
    env.write_text("CBM_REPORTS_DIR=from-dotenv-file\n")
    monkeypatch.chdir(tmp_path)
    rc = cbm.main([])  # usage path — still must load .env first
    assert rc == 2
    assert os.environ.get("CBM_REPORTS_DIR") == "from-dotenv-file"

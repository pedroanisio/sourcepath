"""Unified reporting CLI — one front door for the bundle tooling.

``python scripts/cbm.py <command>`` dispatches to the existing tools:
report (HTML/MD/JSON), dossier (A4 PDF), pdf (authored Markdown →
themed PDF), site (static bundle browser), repair (post-hoc bundle
fixes). Contracts pinned here:

- the usage text names every command with a one-line description;
- arguments after the command are forwarded verbatim and the
  subcommand's exit code is passed through;
- unknown commands fail with usage on stderr and exit 2;
- a command whose optional dependency is missing fails with an
  actionable install hint, not a traceback;
- ``<command> --help`` reaches the tool's own argparse help.

Run from the repo root:  python -m pytest tests/test_cbm_cli.py
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
import cbm  # noqa: E402

ALL_COMMANDS = ("report", "dossier", "pdf", "site", "repair")


def test_no_args_prints_usage_and_exits_2(capsys):
    rc = cbm.main([])
    assert rc == 2
    err = capsys.readouterr().err
    for command in ALL_COMMANDS:
        assert command in err


def test_help_flag_prints_usage_and_exits_0(capsys):
    rc = cbm.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for command in ALL_COMMANDS:
        assert command in out
    # descriptions render as prose, not as the raw (module, desc) tuple
    assert "Structural report (HTML / MD / JSON) from a bundle" in out
    assert "cbm_report" not in out
    assert "('" not in out


def test_unknown_command_exits_2_with_usage(capsys):
    rc = cbm.main(["frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "frobnicate" in err
    assert "report" in err


def test_arguments_forward_verbatim_and_rc_passes_through(monkeypatch):
    seen = {}

    def fake_main(argv=None):
        seen["argv"] = argv
        return 7

    monkeypatch.setattr(cbm, "_load_command",
                        lambda name: types.SimpleNamespace(main=fake_main))
    rc = cbm.main(["report", "--bundle", "/x", "--formats", "md"])
    assert rc == 7
    assert seen["argv"] == ["--bundle", "/x", "--formats", "md"]


def test_none_return_becomes_exit_0(monkeypatch):
    monkeypatch.setattr(
        cbm, "_load_command",
        lambda name: types.SimpleNamespace(main=lambda argv=None: None))
    assert cbm.main(["report"]) == 0


def test_missing_dependency_gives_actionable_error(monkeypatch, capsys):
    def broken_load(name):
        raise ImportError("No module named 'weasyprint'")

    monkeypatch.setattr(cbm, "_load_command", broken_load)
    rc = cbm.main(["pdf", "whatever.md"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "weasyprint" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("command,module", [
    ("report", "cbm_report"),
    ("dossier", "cbm_dossier"),
    ("repair", "cbm_repair"),
])
def test_subcommand_help_reaches_tool_parser(command, module, capsys):
    pytest.importorskip("rdflib")
    if command == "dossier":
        pytest.importorskip("reportlab")
    with pytest.raises(SystemExit) as exc:
        cbm.main([command, "--help"])
    assert exc.value.code == 0
    assert "--bundle" in capsys.readouterr().out

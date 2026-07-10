"""Smoke tests for scripts/cbm_dossier.py (A4 PDF dossier renderer).

The heavy path — typesetting a full bundle — needs a real output set and is
exercised manually (`python scripts/cbm_dossier.py --bundle <bundle>`). These
tests pin the parts that must hold on any machine, with or without the
designed TTF set installed:

- font registration falls back to built-in faces (with Paragraph family
  mappings) when the font directory is absent, instead of crashing;
- the style table and markup helpers the whole document is built from;
- graphic flowables tolerate degenerate inputs (empty build plans, empty
  decompositions, metro models with no lines) rather than dividing by zero.

Run from the repo root:  python -m pytest tests/test_cbm_dossier.py
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("reportlab")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
import cbm_dossier as D  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fonts_and_styles(tmp_path_factory):
    # An empty font dir forces the built-in fallback path — the one that runs
    # on machines without the designed TTF set.
    D.register_fonts(str(tmp_path_factory.mktemp("no-fonts")))
    D.make_styles()


def test_fallback_faces_are_registered_for_canvas_and_paragraph():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.lib.fonts import ps2tt

    for logical in D.FALLBACK_FACES:
        assert pdfmetrics.getFont(logical)
        # Paragraph markup resolves fonts through ps2tt; a missing family
        # mapping raises ValueError at Paragraph construction time. (The
        # returned family is not always the logical name itself — the Body
        # variants map back to the "body" family — so only resolution is
        # asserted, not the mapping's shape.)
        ps2tt(logical)


def test_markup_helpers():
    assert D.esc("<a & b>") == "&lt;a &amp; b&gt;"
    assert 'face="Mono"' in D.cspan("x")
    before = D.FIG["n"]
    D.figcap("a caption", "FACT")  # builds a Paragraph via the style table
    assert D.FIG["n"] == before + 1


def test_empty_index_items_emit_no_tag():
    """ReportLab's SimpleIndex crashes (IndexError) on an empty index
    entry at multiBuild time; blank anchors must be dropped, not typeset."""
    assert D.ixn("") == ""
    assert D.ixs("   ") == ""
    assert D.ixn(",,") == ""
    assert 'item="x"' in D.ixn("x")


def test_pin_name_handles_scoped_npm_packages():
    """Found live: zod's Register D indexed pins via split('@')[0], which
    is empty for scoped packages and crashed the dossier's index."""
    assert D.pin_name("@types/node@18.2.3") == "@types/node"
    assert D.pin_name("lodash@4.17.21") == "lodash"
    assert D.pin_name("plain-no-version") == "plain-no-version"


def test_flowables_tolerate_degenerate_inputs(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    canv = Canvas(str(tmp_path / "probe.pdf"))
    degenerate = [
        D.BarcodeRL({"seq": [], "phases": [], "total_creates": 0}),
        D.WaffleRL({"kinds": [], "part_conf": []}),
        D.MetroRL({"lines": [], "interchanges": [], "main_pkg": ""}),
        D.HBars([]),
    ]
    for fl in degenerate:
        fl.canv = canv
        fl.wrap(400, 400)
        fl.draw()  # must not raise

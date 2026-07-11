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


def test_glossary_defines_both_categories_before_any_chapter():
    """Phase-2 reading contract: no term of art lands on the reader before
    its definition. Two categories are mandatory — trade concepts
    (GLOSSARY_CONCEPTS) and house-styled terms (GLOSSARY_HOUSE) — and the
    terms page must render in front matter, before Chapter 01."""
    assert len(D.GLOSSARY_CONCEPTS) >= 10
    assert len(D.GLOSSARY_HOUSE) >= 10
    for term, meaning in [*D.GLOSSARY_CONCEPTS, *D.GLOSSARY_HOUSE]:
        assert term.strip(), "empty glossary term"
        assert len(meaning.strip()) >= 20, f"placeholder definition for {term!r}"

    # The terms the chapters lean on hardest must all be covered.
    covered = " ".join(
        t.lower() for t, _ in D.GLOSSARY_CONCEPTS + D.GLOSSARY_HOUSE)
    for needed in ("ast", "triple", "shacl", "embedding", "t-sne",
                   "heuristic", "receipt", "chokepoint", "interchange",
                   "district", "register", "bundle", "blob", "zero"):
        assert needed in covered, f"glossary misses {needed!r}"

    # Source ordering: the terms page is emitted before Chapter 01's story.
    src_path = os.path.join(os.path.dirname(D.__file__), "cbm_dossier.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert src.index('h3(st, "Terms used in this dossier")') \
        < src.index("CH 1 executive summary")


def test_glossary_entries_carry_tradition_or_local_marking():
    """Editorial contract, second pass: established terms must name the
    tradition/source their meaning comes from; house terms must present
    themselves as local coinages, and an overloaded ordinary word must be
    explicitly distinguished (the repo's own 'concept' most of all)."""
    concepts = dict(D.GLOSSARY_CONCEPTS)
    joined = " ".join(concepts.values())
    for tradition in ("W3C", "FIPS 180-4", "van der Maaten", "compiler",
                      "graph-theory", "Object Management Group"):
        assert tradition.lower() in joined.lower(), f"missing source: {tradition}"

    house = dict(D.GLOSSARY_HOUSE)
    local_marked = [m for m in house.values() if "local" in m.lower()]
    assert len(local_marked) >= 8, "house terms must present themselves as local"
    assert any("concept" in term.lower() and "as used here" in term.lower()
               for term in house), "the overloaded word 'concept' needs its own entry"


def test_narrative_tissue_and_orphan_guards():
    """Editorial reconstruction contract: chapters open with orientation
    prose, close with a transition into the next chapter, and no table or
    heading renders unconditionally over possibly-empty data."""
    src_path = os.path.join(os.path.dirname(D.__file__), "cbm_dossier.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()

    # Orientation: prose follows each substantive chapter call closely.
    for title in ("Inventory census", "The dependency map", "The metro",
                  "The districts", "Findings & recommendations"):
        idx = src.index(f'chapter(st, "{title}"')
        window = src[idx:idx + 1600]
        assert "p(st," in window, f"chapter {title!r} opens with no prose"

    # Transitions: prose precedes the next chapter's story block.
    for marker in ("# ================= CH 3 inventory",
                   "# ================= CH 4 graph layer",
                   "# ================= CH 5 test evidence",
                   "# ================= CH 6 metro",
                   "# ================= CH 8 concepts",
                   "# ================= CH 9 L4 receipts method"):
        idx = src.index(marker)
        assert "p(st," in src[idx - 600:idx], f"no transition before {marker}"

    # Orphan guards: data-dependent blocks must be conditional.
    for guard in ('if G["interchanges"]:', 'if G["chokepoints"]:',
                  "if pins_pairs:"):
        assert guard in src, f"missing empty-data guard: {guard}"


def test_palette_swatches_render_from_the_module_constants():
    """The design-system page renders the palette, not just hex strings —
    and the chips must derive from the same constants that ink the page,
    so swatch, label, and actual color can never drift apart."""
    sw = D.PaletteSwatches()
    w, h = sw.wrap(0, 0)
    assert w > 0 and h > 0
    names = [n for n, _ in sw.ITEMS]
    assert names == ["paper", "carbon ink", "warm grey", "pale", "faint",
                     "vermilion", "slate", "card"]
    by_name = dict(sw.ITEMS)
    assert by_name["paper"] is D.PAPER
    assert by_name["vermilion"] is D.VERM
    assert by_name["slate"] is D.SLATE


def test_hbars_share_labels():
    """The census charts show count AND share; the share is of the full
    population passed as `total`, never of the displayed top-N slice."""
    bars = D.HBars([("python", 197), ("sql", 45)], total=310)
    assert bars._val(197) == "197 · 64%"
    assert bars._val(2) == "2 · <1%"
    assert D.HBars([("a", 5)])._val(5) == "5"  # no total -> counts only


def test_cover_carries_commit_skew_caveat_and_glossary_scope_semantics():
    """Follow-ups from the external recount review: the cover must warn that
    figures describe one commit alone (the reviewer compared across
    commits), and the import-edge glossary entry must state the all-scopes
    counting semantics."""
    src_path = os.path.join(os.path.dirname(D.__file__), "cbm_dossier.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "describes this commit alone" in src
    concepts = dict(D.GLOSSARY_CONCEPTS)
    entry = concepts["import edge / in-degree / out-degree"]
    assert "wherever the statement appears" in entry


def test_map_chapters_point_to_the_interactive_cartogram():
    """The dossier's frozen maps must name their interactive companion
    (`cbm.py cartogram`) — connective tissue between the printed and the
    explorable views of the same measured facts."""
    src_path = os.path.join(os.path.dirname(D.__file__), "cbm_dossier.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    metro = src.index('chapter(st, "The metro"')
    districts = src.index('chapter(st, "The districts"')
    assert "cartogram" in src[metro:metro + 2500].lower()
    assert "cartogram" in src[districts:districts + 2500].lower()

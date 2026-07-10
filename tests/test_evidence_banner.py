"""One banner phrase across every report generator (gap: banner divergence).

CLAUDE.md Rule 5's standing operator override for commercial reports is the
"Evidence basis & confidence" banner. Before this suite existed, the Rust
crate, the authored-PDF pipeline, the template, and the reporting schema all
carried the literal phrase while the structural generators (cbm_report.py,
cbm_dossier.py) used only the FACT/DERIVED/UNVERIFIED taxonomy — three
vocabularies for one policy. cbm_report.EVIDENCE_BANNER_LABEL is now the
single Python source of the phrase; this suite pins:

- the phrase itself, and that the HTML/MD banners carry it plus all three
  evidence tiers;
- the call sites in emit_html / emit_md (deleting the banner fails here);
- the dossier's provenance page referencing the shared constant;
- cross-tool agreement: report_to_pdf's default label, the Rust crate's
  final page, the authoring template, and the report-spec schema's
  disclaimer_mode all use the same phrase.

Run from the repo root:  python -m pytest tests/test_evidence_banner.py
"""
from __future__ import annotations

import html as _html
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("rdflib")
import cbm_report as CR  # noqa: E402

LABEL = "Evidence basis & confidence"


def test_label_is_the_standing_override_phrase():
    assert CR.EVIDENCE_BANNER_LABEL == LABEL


def test_html_banner_carries_label_and_all_three_tiers():
    html = CR.evidence_banner_html()
    assert _html.escape(LABEL) in html  # rendered HTML escapes the ampersand
    for tier in ("FACT", "DERIVED", "UNVERIFIED"):
        assert tier in html


def test_md_banner_carries_label_and_all_three_tiers():
    md = CR.evidence_banner_md()
    assert f"**{LABEL}.**" in md
    for tier in ("FACT", "DERIVED", "UNVERIFIED"):
        assert tier in md


def test_banner_text_never_upgrades_llm_output():
    # The override may reframe the epistemics but must keep LLM output
    # disclosed as unverified (CLAUDE.md Rule 5 boundary).
    assert "unverified" in CR.EVIDENCE_BANNER_TEXT.lower()
    assert "untrusted" in CR.EVIDENCE_BANNER_TEXT.lower()


def test_emitters_render_the_banner():
    assert "evidence_banner_html()" in inspect.getsource(CR.emit_html)
    assert "evidence_banner_md()" in inspect.getsource(CR.emit_md)


def test_dossier_provenance_page_uses_the_shared_constant():
    # Source-level pin: importing cbm_dossier needs reportlab, which is an
    # optional extra; referencing the constant is what we require.
    src = (ROOT / "scripts" / "cbm_dossier.py").read_text(encoding="utf-8")
    assert "CR.EVIDENCE_BANNER_LABEL" in src


def test_authored_pdf_pipeline_defaults_to_the_same_label():
    pytest.importorskip("weasyprint")
    pytest.importorskip("markdown_it")
    pytest.importorskip("yaml")
    import report_to_pdf

    banner = report_to_pdf.build_disclaimer(
        {"disclaimer": {"notice": "n", "generated_by": "g", "date": "d"}})
    assert _html.escape(LABEL) in banner


def test_rust_crate_renders_the_same_phrase():
    pages = (ROOT / "tools" / "cbm-report" / "src" / "pdf" / "pages.rs"
             ).read_text(encoding="utf-8")
    assert LABEL in pages


def test_report_template_frontmatter_uses_the_same_label():
    template = (ROOT / "docs" / "reports" / "_report_template.md"
                ).read_text(encoding="utf-8")
    assert f'label: "{LABEL}"' in template


def test_report_spec_schema_offers_the_banner_mode():
    schema = json.loads(
        (ROOT / "docs" / "reporting" / "report-spec.schema.json"
         ).read_text(encoding="utf-8"))
    assert "evidence_basis_banner" in schema["properties"]["disclaimer_mode"]["enum"]

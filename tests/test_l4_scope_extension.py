"""E6 (error-free-mapping plan) — L4 file-summary language scope.

cpp was excluded from the summary allowlist while c was included — genuine
C++ projects (and pre-F20 kernel bundles) silently got no file summaries.

Run from the repo root:  python -m pytest tests/test_l4_scope_extension.py
"""
from __future__ import annotations

from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES


def test_cpp_and_c_family_summarizable():
    assert "cpp" in SUPPORTED_LANGUAGES
    assert "c" in SUPPORTED_LANGUAGES
    assert "objective-c" in SUPPORTED_LANGUAGES


def test_data_languages_stay_out_of_summary_scope():
    for lang in ("yaml", "json", "text", "restructuredtext"):
        assert lang not in SUPPORTED_LANGUAGES

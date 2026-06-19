"""Regression tests for minified-bundle classification.

Background: a vendored minified file (e.g. ``public/pdf.worker.min.mjs``, ~1.3 MB
of pdf.js) was classified as ``source_code`` because its ``.mjs`` suffix is a
known language extension. It was then fully chunked, embedded, and concept-
enriched — a single blob produced thousands of chunks that dominated the concept
graph (its internal symbol names ``namespace`` / ``stream`` / ``font`` / ``cff``
surfaced as top "concepts") and bloated the semantic index.

The byte-accurate-chunk fix (defects D1/D2/D3) made those chunks *correct and
injective* but did not make them *absent* — a minified blob still pollutes L2-L4.
The right fix is to classify minified web bundles as ``generated``, which the
chunker, embedder, and LLM enricher all skip by default.

These tests pin that classification and the downstream skip invariant.

Run from the repo root:  python3 -m pytest tests/test_minified_classification.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.classify import classify
from plugins.chunks_embeddings.chunker import SKIP_TYPES


_MINIFIED = b"!function(e){var t={};return t}(window);\n"


@pytest.mark.parametrize(
    "path",
    [
        "public/pdf.worker.min.mjs",
        "static/js/vendor.min.js",
        "assets/app.min.cjs",
        "public/styles.min.css",
        "dist/bundle.min.js",
        "wwwroot/lib/Chart.min.JS",  # case-insensitive
    ],
)
def test_minified_assets_classified_generated(path):
    assert classify(path, _MINIFIED) == "generated"


def test_minified_under_tests_dir_still_generated():
    """A minified blob is build output even under tests/ — it must not be
    rescued into test_code (mirrors the Dart-codegen precedence rule)."""
    assert classify("tests/fixtures/jquery.min.js", _MINIFIED) == "generated"


@pytest.mark.parametrize(
    "path",
    [
        "src/app.js",
        "src/main.mjs",
        "src/index.ts",
        "src/admin.cjs",
        "src/styles.css",
        # ".min" only matters as the penultimate dotted segment of the name.
        "src/min.js",
        "src/components/Minified.tsx",
    ],
)
def test_normal_sources_not_classified_generated(path):
    assert classify(path, b"export const x = 1;\n") != "generated"


def test_minified_classification_lands_in_chunker_skip_set():
    """End-to-end invariant: the type assigned to a minified file is one the
    chunker skips, so it produces no chunks (hence no embeddings, no concepts)."""
    assert classify("public/pdf.worker.min.mjs", _MINIFIED) in SKIP_TYPES

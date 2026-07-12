"""Data-language analyzers must disclose legitimate zero-symbol documents.

A JSON `{}` or an empty/scalar YAML document parses cleanly and yields no
items — that is a property of the document, not an extraction failure. The
coverage gate (codebase_mapper/inspection/coverage.py) counts a parsed file
with zero symbols and no ``zero_symbol_reason`` as SILENT — a disclosure
defect. c.py and lightweight.py already follow the convention; json/yaml
were added as first-class languages without it (golden-corpus gate red,
2026-07-11).

Run: uv run python -m pytest tests/test_data_language_zero_symbols.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.json import extract_json_ast_summary
from codebase_mapper.inspection.languages.yaml import extract_yaml_ast_summary


@pytest.mark.parametrize("content", [b"{}\n", b"[]\n", b"[1, 2]\n", b'"scalar"\n'])
def test_json_memberless_documents_disclose_reason(content):
    summary, errors = extract_json_ast_summary(content, "d.json")
    assert errors == []
    assert summary is not None
    assert summary["items"] == []
    assert summary.get("zero_symbol_reason"), (
        "memberless JSON must carry zero_symbol_reason (silent-zero gate)")


def test_json_with_members_has_no_reason():
    summary, _ = extract_json_ast_summary(b'{"a": 1}\n', "d.json")
    assert summary is not None
    assert summary["items"]
    assert "zero_symbol_reason" not in summary


@pytest.mark.parametrize("content", [b"", b"\n", b"# comment only\n", b"42\n"])
def test_yaml_memberless_documents_disclose_reason(content):
    summary, errors = extract_yaml_ast_summary(content, "a.yaml")
    assert summary is not None
    if summary["items"] == []:
        assert summary.get("zero_symbol_reason"), (
            "memberless YAML must carry zero_symbol_reason (silent-zero gate)")


def test_yaml_with_members_has_no_reason():
    summary, _ = extract_yaml_ast_summary(b"key: 1\n", "a.yaml")
    assert summary is not None
    assert summary["items"]
    assert "zero_symbol_reason" not in summary

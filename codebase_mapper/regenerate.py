"""codebase_mapper.regenerate — source from inventory.ttl + cbm:astSummary alone.

Companion to ``reconstruct``. Where reconstruct is byte-perfect via a blob
store, regenerate is lossy-by-design: it reads only the AST literal in
inventory.ttl and asks a per-language regenerator to emit source. The
output re-parses to the same AST as the original (semantic roundtrip)
but is not byte-identical (comments, blank lines, quote styles lost).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph
from rdflib.namespace import RDF

from .constants import CBM
from .languages.python import regenerate_python_source
from .languages.tsjs import regenerate_tsjs_source


# language string (matches FileRecord.language) -> regenerator callable.
# Callable takes the parsed ast_summary dict and returns source text.
# Python regenerates *semantically* (re-parses to the same AST); TS/JS
# regenerate *byte-identically* via the stored leaf-text CST.
_REGENERATORS: dict[str, Callable[[dict], str]] = {
    "python": regenerate_python_source,
    "typescript": regenerate_tsjs_source,
    "javascript": regenerate_tsjs_source,
}


def supported_languages() -> list[str]:
    return sorted(_REGENERATORS)


def regenerate(
    inventory_path: Path,
    out_dir: Path,
    report_path: Path | None = None,
) -> dict:
    """Materialize source files from inventory.ttl + cbm:astSummary alone.

    No blob store is read. Files whose language has no registered
    regenerator, whose AST summary is missing, or whose summary fails to
    parse/regenerate are skipped and reported. Returns a fidelity report
    and (optionally) writes it to ``report_path``.
    """
    g = Graph()
    g.parse(str(inventory_path), format="turtle")
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "files_total": 0,
        "files_regenerated": 0,
        "by_language": {},
        "ast_unsupported": [],
        "no_ast_summary": [],
        "ast_parse_errors": [],
        "regenerate_errors": [],
    }

    def _bump(lang: str, key: str) -> None:
        report["by_language"].setdefault(lang, {"ok": 0, "failed": 0})
        report["by_language"][lang][key] += 1

    for s in g.subjects(RDF.type, CBM.File):
        report["files_total"] += 1
        path_lits = list(g.objects(s, CBM.path))
        if not path_lits:
            continue
        path = str(path_lits[0])

        lang_lits = list(g.objects(s, CBM.language))
        lang = str(lang_lits[0]) if lang_lits else None

        if lang is None or lang not in _REGENERATORS:
            report["ast_unsupported"].append(
                {"path": path, "language": lang}
            )
            continue

        ast_lits = list(g.objects(s, CBM.astSummary))
        if not ast_lits:
            report["no_ast_summary"].append(path)
            _bump(lang, "failed")
            continue

        try:
            summary = json.loads(str(ast_lits[0]))
        except json.JSONDecodeError as e:
            report["ast_parse_errors"].append(
                {"path": path, "error": f"json: {e}"}
            )
            _bump(lang, "failed")
            continue

        regen_fn = _REGENERATORS[lang]
        try:
            source = regen_fn(summary)
        except Exception as e:
            report["regenerate_errors"].append(
                {"path": path, "error": f"{type(e).__name__}: {e}"}
            )
            _bump(lang, "failed")
            continue

        dst = out_dir / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(source, encoding="utf-8")
        report["files_regenerated"] += 1
        _bump(lang, "ok")

    report["ast_unsupported"].sort(key=lambda d: d["path"])
    report["no_ast_summary"].sort()
    report["ast_parse_errors"].sort(key=lambda d: d["path"])
    report["regenerate_errors"].sort(key=lambda d: d["path"])
    report["supported_languages"] = supported_languages()

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    return report

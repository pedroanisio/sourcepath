"""AST extraction coverage — a mechanically-derived honesty asset.

Answers, per language and in total: how many source files produced an
AST, how many failed to parse, how many produced *no* AST at all, and —
the column that matters most — how many parsed **cleanly** yet yielded
**zero symbols**.

Why the last column exists. The tree-sitter analyzers do not run a C
preprocessor, so macro-generated definitions (``SYSCALL_DEFINE*``,
``DEFINE_PER_CPU``, ``DECLARE_*``, tracepoint macros) are not
``function_definition`` / ``declaration`` nodes: they raise no parse
error and contribute no symbol. A coverage report that tracked only
parse errors would render green while silently under-capturing a
macro-heavy codebase like the Linux kernel. Publishing the
"clean-parse, zero-symbol" count turns that silent gap into a stated,
auditable limitation — a published limitation is credibility; a
discovered one is a takedown.

This module is pure (no I/O). ``codebase_mapper.emission`` calls
:func:`aggregate_coverage` and writes the result as the ``ast_coverage``
bundle asset.

Symbol counting note. Most tree-sitter analyzers expose a flat
``items`` list (C, C++, Go, Java, Kotlin, Swift, Dart, Ruby, Obj-C,
Clojure, Rust). Two express structure as a nested AST instead —
Python (``ast_json``) and TypeScript/JavaScript (``cst_json``). For the
nested-AST languages this metric returns ``None`` ("not counted here"),
never ``0``, so they are never mislabeled as silent-zero. The
silent-zero detector therefore targets exactly the node-extraction
languages where the macro/preprocessor gap applies.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

#: Key holding the flat symbol-item list in an ``ast_summary``.
SYMBOL_ITEMS_KEY = "items"
#: Key holding extracted imports (structure, but not a symbol definition).
IMPORTS_KEY = "imports"
#: Diagnostic string the analyzers append when the parse tree has errors.
PARSE_ERROR_MARKER = "parse_errors_present"
#: Companion diagnostic quantifying the damage: ``parse_error_nodes:<N>``.
PARSE_ERROR_NODES_PREFIX = "parse_error_nodes:"
#: Diagnostic prefixes that mean extraction did not complete at all.
_FAILURE_MARKERS = ("extract_failed", "extract_recursion_error",
                    "tree_sitter_unavailable")
#: Default cap on the inline silent-zero file preview in the manifest
#: fragment. The full list is written to the asset uncapped.
DEFAULT_MAX_LISTED = 200


def count_symbols(ast_summary: dict | None) -> int | None:
    """Number of extracted symbols, or ``None`` when not applicable.

    ``None`` means either "no AST" (``ast_summary is None``) or "this
    language expresses structure as a nested AST, not a flat item list"
    (Python/TS/JS). An integer (including ``0``) means the analyzer
    produced a flat ``items`` list of that length. The ``None`` vs ``0``
    distinction is load-bearing: only an integer ``0`` can be a
    silent-zero.
    """
    if ast_summary is None:
        return None
    items = ast_summary.get(SYMBOL_ITEMS_KEY)
    if isinstance(items, list):
        return len(items)
    return None


def _count_imports(ast_summary: dict | None) -> int | None:
    if ast_summary is None:
        return None
    imports = ast_summary.get(IMPORTS_KEY)
    return len(imports) if isinstance(imports, list) else None


def parse_error_node_count(extraction_errors: list[str]) -> int:
    """ERROR/missing-node count from a ``parse_error_nodes:<N>`` diagnostic.

    ``0`` when the diagnostic is absent or malformed — including records
    from analyzers predating the quantified marker (flaw F8), which carry
    only ``parse_errors_present``.
    """
    for e in extraction_errors:
        if e.startswith(PARSE_ERROR_NODES_PREFIX):
            tail = e[len(PARSE_ERROR_NODES_PREFIX):]
            if tail.isdigit():
                return int(tail)
    return 0


@dataclass(frozen=True)
class FileCoverage:
    """Per-file extraction verdict."""
    path: str
    language: str | None
    type_: str
    ast_present: bool
    had_parse_error: bool
    had_extraction_failure: bool
    symbol_count: int | None
    import_count: int | None
    parse_error_nodes: int = 0
    zero_reason: str | None = None

    @property
    def is_full_body(self) -> bool:
        """AST present but symbols not expressed as a flat item list
        (Python/TS/JS) — excluded from the silent-zero detector."""
        return self.ast_present and self.symbol_count is None

    @property
    def is_silent_zero(self) -> bool:
        """Clean parse, no AST failure, item-based language, zero symbols,
        and no analyzer-stated reason — the residual under-capture signal.
        An analyzer that explains its zero (``zero_symbol_reason`` in the
        ast_summary, plan E3) is not silent."""
        return (
            self.ast_present
            and not self.had_parse_error
            and self.symbol_count == 0
            and self.zero_reason is None
        )


def classify_file_coverage(record: Any) -> FileCoverage:
    """Classify one ``FileRecord`` (duck-typed) into a :class:`FileCoverage`."""
    errors = list(getattr(record, "extraction_errors", []) or [])
    ast_summary = getattr(record, "ast_summary", None)
    had_failure = any(
        any(e.startswith(marker) for marker in _FAILURE_MARKERS)
        for e in errors
    )
    return FileCoverage(
        path=getattr(record, "path", ""),
        language=getattr(record, "language", None),
        type_=getattr(record, "type_", ""),
        ast_present=ast_summary is not None,
        had_parse_error=PARSE_ERROR_MARKER in errors,
        had_extraction_failure=had_failure,
        symbol_count=count_symbols(ast_summary),
        import_count=_count_imports(ast_summary),
        parse_error_nodes=parse_error_node_count(errors),
        zero_reason=(ast_summary or {}).get("zero_symbol_reason"),
    )


def _empty_lang_bucket() -> dict[str, int]:
    return {
        "files": 0,
        "files_with_ast": 0,
        "files_with_parse_errors": 0,
        "files_with_extraction_failures": 0,
        "files_zero_ast": 0,
        "full_body_files": 0,
        "silent_zero_symbol_files": 0,
        "explained_zero_symbol_files": 0,
        "symbols_extracted": 0,
        "imports_extracted": 0,
        # Total ERROR/missing nodes across flagged files — severity signal
        # the boolean marker cannot carry (F8): 28,581 kernel C files were
        # indistinguishable whether one node or half the tree failed.
        "parse_error_nodes": 0,
    }


def aggregate_coverage(records: list, *, max_listed: int = DEFAULT_MAX_LISTED) -> dict:
    """Aggregate per-file coverage over ``source_code`` records.

    Only ``type_ == "source_code"`` files are in scope — an analyzer is
    expected to have run on them. The returned dict is deterministic
    (sorted keys/lists) and JSON-serializable.

    ``max_listed`` caps only the inline ``silent_zero_symbol_file_list``
    preview; ``totals.silent_zero_symbol_files`` always reflects the true
    count and ``silent_zero_symbol_file_list_truncated`` discloses when
    the preview was cut — no silent truncation (PALS's Law).
    """
    by_language: dict[str, dict[str, int]] = {}
    totals = _empty_lang_bucket()
    n_source = 0
    silent_files: list[dict[str, str]] = []

    for record in records:
        fc = classify_file_coverage(record)
        if fc.type_ != "source_code":
            continue
        n_source += 1
        lang = fc.language or "(none)"
        bucket = by_language.setdefault(lang, _empty_lang_bucket())

        for b in (bucket, totals):
            b["files"] += 1
            if fc.ast_present:
                b["files_with_ast"] += 1
            else:
                b["files_zero_ast"] += 1
            if fc.had_parse_error:
                b["files_with_parse_errors"] += 1
            b["parse_error_nodes"] += fc.parse_error_nodes
            if fc.had_extraction_failure:
                b["files_with_extraction_failures"] += 1
            if fc.is_full_body:
                b["full_body_files"] += 1
            if isinstance(fc.symbol_count, int):
                b["symbols_extracted"] += fc.symbol_count
            if isinstance(fc.import_count, int):
                b["imports_extracted"] += fc.import_count
            if fc.is_silent_zero:
                b["silent_zero_symbol_files"] += 1
            if fc.symbol_count == 0 and fc.zero_reason is not None:
                b["explained_zero_symbol_files"] += 1

        if fc.is_silent_zero:
            silent_files.append({"path": fc.path, "language": lang})

    silent_files.sort(key=lambda d: (d["language"], d["path"]))
    truncated = len(silent_files) > max_listed

    return {
        "n_source_files": n_source,
        "totals": totals,
        "by_language": {k: by_language[k] for k in sorted(by_language)},
        "silent_zero_symbol_file_list": silent_files[:max_listed],
        "silent_zero_symbol_file_list_truncated": truncated,
        "notes": {
            "silent_zero_symbol_files":
                "Source files that parsed without error yet produced zero "
                "extracted symbols. Expected for macro-generated / data-table "
                "files (tree-sitter does not run the C preprocessor); a "
                "high count on hand-written logic indicates under-capture.",
            "full_body_files":
                "Python/TS/JS files whose structure is a nested AST, not a "
                "flat item list — symbols not counted by this metric.",
        },
    }

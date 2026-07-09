---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-09"
---

# AST extraction coverage (`ast_coverage.json`)

> A mechanically-derived honesty table shipped in every bundle. It states
> what symbol extraction did and did **not** capture, per language. A
> published limitation is credibility; a discovered one is a takedown.

## Why it exists

The tree-sitter language analyzers parse source text; they do **not** run
a C preprocessor. So macro-generated definitions — the Linux kernel's
`SYSCALL_DEFINE*`, `DEFINE_PER_CPU`, `DECLARE_*`, tracepoint macros, and
register/data tables built from expansion macros — are not
`function_definition` / `declaration` nodes. They raise **no parse
error** and contribute **no symbol**.

A coverage report that tracked only parse errors would therefore render
all-green while silently under-capturing a macro-heavy codebase. The
asset's load-bearing column, **`silent_zero_symbol_files`** — source
files that parsed *cleanly* yet produced *zero* symbols — turns that
silent gap into a stated, auditable fact.

## Where it comes from

- Pure logic: [`codebase_mapper/inspection/coverage.py`](../codebase_mapper/inspection/coverage.py)
  (`aggregate_coverage`, `classify_file_coverage`, `count_symbols`).
- Emitted by [`emit_bundle.py`](../codebase_mapper/emission/application/emit_bundle.py)
  (`_emit_coverage_sidecar`) as `ast_coverage.json` plus an `ast_coverage`
  fragment in `run_manifest.json`. Always emitted.
- Contract pinned by [`tests/verify_ast_coverage.py`](../tests/verify_ast_coverage.py)
  (in `make test-core`).

## The asset

`ast_coverage.json` (full, uncapped, deterministic — sorted keys):

| Field | Meaning |
|---|---|
| `n_source_files` | Files with `type_ == "source_code"` (the denominator). |
| `totals` | Aggregate counts across all source files (bucket below). |
| `by_language.<lang>` | Same bucket, per language. |
| `silent_zero_symbol_file_list` | Every clean-parse / zero-symbol file `{path, language}`. |
| `silent_zero_symbol_file_list_truncated` | Whether that list was capped (it is not, in the asset). |
| `notes` | Inline definitions of the two non-obvious columns. |

Each bucket:

| Key | Meaning |
|---|---|
| `files` | Files seen. |
| `files_with_ast` | An AST summary was produced. |
| `files_zero_ast` | No AST at all (analyzer returned nothing / failed). |
| `files_with_parse_errors` | Parse tree contained errors (`parse_errors_present`). |
| `files_with_extraction_failures` | Extraction raised (`extract_failed` / recursion / tree-sitter unavailable). |
| `full_body_files` | Python/TS/JS — structure is a nested AST, not a flat item list; **symbols not counted here**. |
| `silent_zero_symbol_files` | **Clean parse, no failure, item-based language, zero symbols.** |
| `symbols_extracted` | Sum of flat `items` across item-based languages. |
| `imports_extracted` | Sum of extracted imports. |

The `run_manifest.json` `ast_coverage` fragment mirrors the totals and
`by_language`, plus a **capped** `silent_zero_symbol_preview` (with a
`silent_zero_symbol_preview_truncated` flag) and the asset's SHA-256 —
so a consumer sees the headline numbers without opening the file.

## How to read `silent_zero_symbol_files`

- **Expected, not a defect, on data/macro files.** A register table
  (`static const struct … regs[] = { ENTRY(...), ... }`) or a pure
  `#define` header legitimately yields zero *extracted symbols* — the
  macros are not symbols the analyzer models. Reporting it is honest,
  not alarmist.
- **A red flag on hand-written logic.** A high silent-zero rate among
  ordinary `.c`/`.cpp` logic files means real definitions are being
  missed (macro-wrapped functions the preprocessor would reveal).
- **`None` vs `0`.** Python/TS/JS return *not counted* (`full_body_files`),
  never `0`, so they are never mislabeled as silent-zero. The detector
  targets exactly the node-extraction languages where the preprocessor
  gap applies.

## What it is not

It is **not** a correctness claim about the symbols that *were*
extracted, and **not** a preprocessed/config-resolved view. Tree-sitter
parses config-independent text (both `#ifdef` branches), so the picture
is *config-independent, not config-accurate*. Do not read the asset as
"complete."

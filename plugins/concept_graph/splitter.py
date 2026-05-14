"""IdentifierSplitter — RecordEnricher that extracts terms from identifiers.

Reads from:
  - record.ast_summary       (populated by the host's LanguageAnalyzers)
  - record.path              (basename contributes terms too)
Writes to:
  - ctx.scratch["raw_terms"][path] : list[dict] with structure
       {"source": "symbol" | "path" | "type",     # where the term came from
        "owner": "load_users" | "src/user_service.py" | ...,
        "raw":   "load_users",                     # the original identifier
        "tokens": ["load", "users"]}               # post-split, pre-normalize

This plugin only *splits*. Canonicalization (lowercase, stem, dedup) and
concept identity belong to the aggregator. Keeping the boundary sharp lets
us test the splitter in isolation and lets a different aggregator make
different canonicalization choices.

The regex handles: snake_case, camelCase, PascalCase, SCREAMING_SNAKE,
kebab-case, mixed-with-acronyms (URLParser -> URL, Parser), digit runs.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import cast

from codebase_mapper.inspection.models import FileRecord

from codebase_mapper.shared_kernel.extensions import PipelineCtx


# Match runs of letters or digits with sensitivity to camelCase boundaries.
# Patterns:
#   [A-Z]+(?=[A-Z][a-z])   — leading uppercase acronym before a CamelWord
#                            ("URL" in "URLParser")
#   [A-Z]?[a-z]+           — TitleCase or lowercase word ("Parser", "user")
#   [A-Z]+                 — trailing all-caps ("ID")
#   [0-9]+                 — digit run
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

# We pre-split on these so the regex above sees individual word groups.
_PRE_SPLIT_RE = re.compile(r"[_\-.\s/]+")


def split_identifier(s: str) -> list[str]:
    """Split a single identifier into raw tokens (preserves case)."""
    if not s:
        return []
    parts = _PRE_SPLIT_RE.split(s)
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        out.extend(_TOKEN_RE.findall(p))
    return out


# Sources we pull identifiers from in each ast_summary. Keys differ by
# language; the host has converged on `top_level_functions`,
# `top_level_classes`, and a few language-specific lists.
_AST_NAME_FIELDS = (
    "top_level_functions",
    "top_level_classes",
    "mod_decls",       # Rust modules
)


class IdentifierSplitter:
    name = "l3_10_identifier_splitter"

    def enrich(self, record: FileRecord, content: bytes, ctx: PipelineCtx) -> None:
        raw_map = cast(dict, ctx.scratch.setdefault("raw_terms", {}))
        entries: list[dict] = []

        # (1) Path-derived terms: filename and immediate parent directory.
        p = PurePosixPath(record.path)
        # filename without extension
        stem = p.stem
        if stem and stem not in ("__init__", "index", "mod", "lib", "main"):
            toks = split_identifier(stem)
            if toks:
                entries.append({
                    "source": "path", "owner": record.path,
                    "raw": stem, "tokens": toks,
                })
        # parent dir name (top-level project subfolders carry domain vocab)
        parts = p.parts
        if len(parts) >= 2:
            parent = parts[-2]
            if parent and parent not in (".", "src", "lib", "tests", "test"):
                toks = split_identifier(parent)
                if toks:
                    entries.append({
                        "source": "path", "owner": record.path,
                        "raw": parent, "tokens": toks,
                    })

        # (2) Identifier-derived terms from the host's ast_summary.
        summary = record.ast_summary or {}
        for field in _AST_NAME_FIELDS:
            for name in summary.get(field, []) or []:
                if not isinstance(name, str):
                    continue
                toks = split_identifier(name)
                if not toks:
                    continue
                entries.append({
                    "source": "symbol", "owner": name,
                    "raw": name, "tokens": toks,
                })

        raw_map[record.path] = entries

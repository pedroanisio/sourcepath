"""Phase 11 — SPARQL escape hatch.

Disabled by default. Enable by setting ``CBM_ENABLE_SPARQL=1`` (or
``true``/``yes``/``on``). Even when enabled the tool runs under tight
limits:

* 10 s wall-clock budget enforced by ``dispatch_with_budget``
* 10 000 char query length cap
* 1 000 result rows max (truncated silently above that)
* Only SELECT and ASK queries — CONSTRUCT/DESCRIBE/etc. rejected
* Mutating keywords (INSERT, DELETE, UPDATE, DROP, CLEAR, CREATE,
  LOAD, COPY, MOVE, ADD) rejected at the parser layer

The point of the tool is to let an agent answer questions the curated
tools can't. The point of the limits is that an agent shouldn't be
able to hang or DoS the server by mis-using it.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdflib import Graph

from frontend.backend.serving.application import bundle_data as backend_bundle_data

from . import handlers as _h
from .validators import INVALID_ARGUMENT, ToolError

MUTATING_KEYWORDS = (
    "INSERT", "DELETE", "UPDATE", "DROP", "CLEAR", "CREATE",
    "LOAD", "COPY", "MOVE", "ADD",
)
ALLOWED_QUERY_TYPES = ("SELECT", "ASK")
MAX_ROWS = 1000
MAX_QUERY_LEN = 10_000
ENABLE_ENV = "CBM_ENABLE_SPARQL"


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------


def is_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------


_MUTATING_RE = re.compile(
    r"\b(" + "|".join(MUTATING_KEYWORDS) + r")\b", re.IGNORECASE
)


def _validate_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ToolError(INVALID_ARGUMENT, "query must be a non-empty string")
    if len(query) > MAX_QUERY_LEN:
        raise ToolError(
            INVALID_ARGUMENT,
            f"query exceeds {MAX_QUERY_LEN} char limit ({len(query)} given)",
        )
    m = _MUTATING_RE.search(query)
    if m:
        raise ToolError(
            INVALID_ARGUMENT,
            f"mutating keyword {m.group(1).upper()!r} not allowed in read-only SPARQL",
        )


# --------------------------------------------------------------------------
# Graph cache
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load_graph(path_str: str) -> Graph:
    g = Graph()
    g.parse(Path(path_str) / "inventory.ttl", format="turtle")
    return g


def clear_graph_cache() -> None:
    """Clear cached graphs. Wired into manifest-change notifications so a
    re-generated bundle is re-parsed on the next SPARQL call."""
    _load_graph.cache_clear()


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_sparql(query: str, *, bundle_default: str | None = None) -> dict[str, Any]:
    if not is_enabled():
        raise ToolError(
            INVALID_ARGUMENT,
            f"sparql tool is disabled; set {ENABLE_ENV}=1 to enable",
        )
    _validate_query(query)
    bundle = backend_bundle_data.get_bundle(bundle_default)

    g = _load_graph(str(bundle.output_dir))
    try:
        result = g.query(query)
    except Exception as e:  # noqa: BLE001 — rdflib raises various subclasses
        raise ToolError(
            INVALID_ARGUMENT,
            f"query failed to parse or execute: {type(e).__name__}: {e}",
        ) from e

    query_form = getattr(result, "type", "SELECT") or "SELECT"
    if query_form not in ALLOWED_QUERY_TYPES:
        raise ToolError(
            INVALID_ARGUMENT,
            f"only SELECT and ASK queries are supported (got {query_form})",
        )

    if query_form == "ASK":
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "query_form": "ASK",
            "ask_result": bool(result.askAnswer),
        }

    columns = [str(v) for v in (result.vars or [])]
    rows: list[dict[str, str | None]] = []
    truncated = False
    for i, row in enumerate(result):
        if i >= MAX_ROWS:
            truncated = True
            break
        rows.append({
            col: (str(val) if val is not None else None)
            for col, val in zip(columns, row)
        })
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "query_form": "SELECT",
        "ask_result": None,
    }

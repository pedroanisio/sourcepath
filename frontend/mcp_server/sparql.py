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

import hashlib
import os
import re
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

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


def _load_pyoxigraph():
    """Import seam (patched in tests). Returns the module or None."""
    try:
        import pyoxigraph
        return pyoxigraph
    except ImportError:
        return None


class _OxigraphHandle:
    """Cached pyoxigraph store for one bundle directory."""

    engine = "oxigraph"

    def __init__(self, store: Any):
        self.store = store


# One live Store per on-disk store dir, per process. RocksDB holds an
# exclusive lock on the dir for the lifetime of the Store *object*, not of
# the lru_cache entry: after clear_graph_cache(), any surviving reference
# (an exception traceback, a result iterator) keeps the lock, and
# constructing a second Store on the same dir raises
# "OSError: IO error: lock hold by current process". Reuse, never reopen.
_STORES: dict[str, Any] = {}
_STORES_LOCK = threading.Lock()


def _open_store(ox: Any, sdir: Path, key: str) -> Any:
    with _STORES_LOCK:
        store = _STORES.get(str(sdir))
        if store is None:
            # A regenerated inventory maps to a new mtime+size dir; drop
            # superseded handles for the same inventory path so open
            # stores don't accumulate across bundle regenerations.
            for stale in [s for s in _STORES if Path(s).name.startswith(f"{key}_")]:
                del _STORES[stale]
            store = ox.Store(str(sdir))
            _STORES[str(sdir)] = store
        return store


@lru_cache(maxsize=4)
def _load_graph(path_str: str) -> Any:
    """Load the bundle graph for querying.

    Preferred engine is a persistent pyoxigraph (Rust) store cached in
    the system temp dir and keyed on the inventory's path+mtime+size:
    it builds once per bundle generation and re-opens instantly, where
    an rdflib parse of a large bundle could never fit the tool's 10 s
    dispatch budget (a kernel-scale inventory takes rdflib tens of
    minutes to parse). The first, store-building call on a very large
    bundle may still exceed the budget — that run warms the cache; the
    next call answers in milliseconds. Falls back to the original
    rdflib in-memory parse when pyoxigraph is unavailable.
    """
    ttl = Path(path_str) / "inventory.ttl"
    ox = _load_pyoxigraph()
    if ox is not None:
        st = ttl.stat()
        key = hashlib.sha1(str(ttl.resolve()).encode()).hexdigest()[:12]
        sdir = (Path(tempfile.gettempdir()) / "cbm_sparql_store"
                / f"{key}_{int(st.st_mtime)}_{st.st_size}")
        sdir.parent.mkdir(parents=True, exist_ok=True)
        store = _open_store(ox, sdir, key)
        if len(store) == 0:
            store.bulk_load(path=str(ttl), format=ox.RdfFormat.TURTLE)
        return _OxigraphHandle(store)
    g = Graph()
    g.parse(ttl, format="turtle")
    return g


def clear_graph_cache() -> None:
    """Clear cached graphs. Wired into manifest-change notifications so a
    re-generated bundle is re-parsed on the next SPARQL call. Live oxigraph
    stores are NOT closed here — a re-generated inventory gets a new
    mtime+size store dir, while an unchanged one must reuse the live store
    (see _STORES) because its dir is still exclusively locked."""
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

    handle = _load_graph(str(bundle.output_dir))
    if getattr(handle, "engine", None) == "oxigraph":
        return _run_oxigraph(handle, query)
    return _run_rdflib(handle, query)


def _ask_response(answer: bool) -> dict[str, Any]:
    return {
        "columns": [], "rows": [], "row_count": 0, "truncated": False,
        "query_form": "ASK", "ask_result": bool(answer),
    }


def _select_response(columns: list[str], rows: list[dict[str, str | None]],
                     truncated: bool) -> dict[str, Any]:
    return {
        "columns": columns, "rows": rows, "row_count": len(rows),
        "truncated": truncated, "query_form": "SELECT", "ask_result": None,
    }


def _run_oxigraph(handle: _OxigraphHandle, query: str) -> dict[str, Any]:
    ox = _load_pyoxigraph()
    assert ox is not None  # handle exists only when the import succeeded
    try:
        result = handle.store.query(query)
    except Exception as e:  # noqa: BLE001 — engine raises various subclasses
        raise ToolError(
            INVALID_ARGUMENT,
            f"query failed to parse or execute: {type(e).__name__}: {e}",
        ) from e
    if isinstance(result, ox.QueryBoolean):
        return _ask_response(bool(result))
    if not isinstance(result, ox.QuerySolutions):
        raise ToolError(
            INVALID_ARGUMENT,
            "only SELECT and ASK queries are supported (got CONSTRUCT/DESCRIBE)",
        )
    columns = [v.value for v in result.variables]
    rows: list[dict[str, str | None]] = []
    truncated = False
    for i, sol in enumerate(result):
        if i >= MAX_ROWS:
            truncated = True
            break
        # .value gives the bare lexical form / IRI, matching str() on the
        # corresponding rdflib terms so both engines answer identically.
        rows.append({
            col: (sol[col].value if sol[col] is not None else None)
            for col in columns
        })
    return _select_response(columns, rows, truncated)


def _run_rdflib(g: Graph, query: str) -> dict[str, Any]:
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
        return _ask_response(bool(result.askAnswer))

    columns = [str(v) for v in (result.vars or [])]
    rows: list[dict[str, str | None]] = []
    truncated = False
    for i, row in enumerate(result):
        if i >= MAX_ROWS:
            truncated = True
            break
        rows.append({
            col: (str(val) if val is not None else None)
            for col, val in zip(columns, cast("tuple", row))
        })
    return _select_response(columns, rows, truncated)

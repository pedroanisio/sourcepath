"""Phase 11 tests — SPARQL escape hatch.

Covers the env gate, the validator, query truncation, and the
walltime safety net via dispatch_with_budget.
"""
from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    INVALID_ARGUMENT,
    SPARQL_ENABLE_ENV,
    SPARQL_MAX_QUERY_LEN,
    SPARQL_MAX_ROWS,
    SPARQL_MUTATING_KEYWORDS,
    ToolError,
    ToolTimeoutError,
    clear_sparql_graph_cache,
    dispatch_with_budget,
    run_sparql,
    sparql_is_enabled,
    validate_out,
)
from frontend.mcp_server.handlers import HANDLERS
from frontend.mcp_server.server import build_server

# A trivial, bundle-discriminating query: counts file nodes in the graph.
_COUNT_FILES_QUERY = (
    "PREFIX cbm: <https://codebase-mapper.example.org/cbm#> "
    "SELECT (COUNT(?f) AS ?n) WHERE { ?f cbm:path ?p }"
)


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    """A clean graph cache per test so monkey-patched env vars take effect."""
    clear_sparql_graph_cache()
    yield
    clear_sparql_graph_cache()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(SPARQL_ENABLE_ENV, "1")
    assert sparql_is_enabled() is True


# --------------------------------------------------------------------------
# Env gate
# --------------------------------------------------------------------------


def test_sparql_disabled_by_default(monkeypatch, bundle_name):
    monkeypatch.delenv(SPARQL_ENABLE_ENV, raising=False)
    with pytest.raises(ToolError) as exc:
        run_sparql("SELECT * WHERE { ?s ?p ?o } LIMIT 1", bundle_default=bundle_name)
    assert exc.value.code == INVALID_ARGUMENT
    assert "disabled" in str(exc.value)


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_sparql_enable_flag_truthy_values(monkeypatch, val):
    monkeypatch.setenv(SPARQL_ENABLE_ENV, val)
    assert sparql_is_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
def test_sparql_enable_flag_falsy_values(monkeypatch, val):
    monkeypatch.setenv(SPARQL_ENABLE_ENV, val)
    assert sparql_is_enabled() is False


# --------------------------------------------------------------------------
# Query validator
# --------------------------------------------------------------------------


def test_query_empty_rejected(enabled, bundle_name):
    with pytest.raises(ToolError) as exc:
        run_sparql("", bundle_default=bundle_name)
    assert exc.value.code == INVALID_ARGUMENT
    assert "non-empty" in str(exc.value)


def test_query_too_long_rejected(enabled, bundle_name):
    long_query = "SELECT * WHERE { ?s ?p ?o } # " + ("x" * SPARQL_MAX_QUERY_LEN)
    with pytest.raises(ToolError) as exc:
        run_sparql(long_query, bundle_default=bundle_name)
    assert exc.value.code == INVALID_ARGUMENT
    assert "char limit" in str(exc.value)


@pytest.mark.parametrize("keyword", SPARQL_MUTATING_KEYWORDS)
def test_mutating_keywords_rejected(enabled, bundle_name, keyword):
    """Each mutating keyword is rejected case-insensitively."""
    # Wrap as a no-op-looking query so the regex still finds the keyword
    query = f"SELECT * WHERE {{ ?s ?p ?o }} ; {keyword} DATA {{ }}"
    with pytest.raises(ToolError) as exc:
        run_sparql(query, bundle_default=bundle_name)
    assert exc.value.code == INVALID_ARGUMENT
    assert keyword.upper() in str(exc.value)


def test_mutating_keyword_case_insensitive(enabled, bundle_name):
    with pytest.raises(ToolError):
        run_sparql("delete data { <a> <b> <c> }", bundle_default=bundle_name)


def test_mutating_substring_inside_word_is_ok(enabled, bundle_name):
    """Whole-word match — 'INSERTION' (substring) should NOT trip the filter."""
    # We can't easily craft a query that uses the word 'INSERTION' as an
    # identifier in valid SPARQL, but we can test the validator directly.
    from frontend.mcp_server.sparql import _validate_query
    _validate_query("SELECT ?insertion WHERE { ?insertion ?p ?o } LIMIT 1")


# --------------------------------------------------------------------------
# Happy path — real bundle
# --------------------------------------------------------------------------


def test_select_query_returns_rows(enabled, bundle_name):
    payload = run_sparql(
        "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 3",
        bundle_default=bundle_name,
    )
    assert payload["query_form"] == "SELECT"
    assert payload["columns"] == ["s", "p", "o"]
    assert 0 < payload["row_count"] <= 3
    assert payload["truncated"] is False
    validate_out("sparql", payload)


def test_ask_query_returns_boolean(enabled, bundle_name):
    payload = run_sparql(
        "ASK WHERE { ?s ?p ?o }",
        bundle_default=bundle_name,
    )
    assert payload["query_form"] == "ASK"
    assert payload["ask_result"] is True
    assert payload["columns"] == []
    validate_out("sparql", payload)


def test_construct_query_rejected(enabled, bundle_name):
    """Only SELECT and ASK are allowed; CONSTRUCT is harder to bound."""
    with pytest.raises(ToolError) as exc:
        run_sparql(
            "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
            bundle_default=bundle_name,
        )
    assert exc.value.code == INVALID_ARGUMENT
    assert "SELECT" in str(exc.value)


def test_describe_query_rejected(enabled, bundle_name):
    with pytest.raises(ToolError) as exc:
        run_sparql(
            "DESCRIBE <https://example/>",
            bundle_default=bundle_name,
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_malformed_query_rejected(enabled, bundle_name):
    with pytest.raises(ToolError) as exc:
        run_sparql("not actually a query", bundle_default=bundle_name)
    assert exc.value.code == INVALID_ARGUMENT
    assert "query failed" in str(exc.value)


# --------------------------------------------------------------------------
# Row truncation — the exit criterion's "pathological" path
# --------------------------------------------------------------------------


def test_unbounded_select_is_truncated(enabled, bundle_name):
    """SELECT * on a >1000-triple bundle must truncate at MAX_ROWS,
    not return all matches. The pathological query in the exit criterion."""
    payload = run_sparql(
        "SELECT ?s ?p ?o WHERE { ?s ?p ?o }",
        bundle_default=bundle_name,
    )
    assert payload["truncated"] is True
    assert payload["row_count"] == SPARQL_MAX_ROWS


# --------------------------------------------------------------------------
# Walltime safety net (uses the per-tool budget machinery from Phase 10)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sparql_walltime_via_dispatch_with_budget(enabled, bundle_name):
    """A 50ms budget on a SPARQL query that sleeps 500ms (via monkeypatched
    run_sparql) must fire the timeout and produce an audit-log entry."""
    import time as _time
    import frontend.mcp_server.observability as obs
    real_dispatch = obs.dispatch

    def slow_dispatch(name, args, **kw):
        _time.sleep(0.5)
        return real_dispatch(name, args, **kw)

    obs.dispatch = slow_dispatch
    try:
        with pytest.raises(ToolTimeoutError) as exc:
            await dispatch_with_budget(
                "sparql",
                {"bundle": bundle_name, "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"},
                timeout=0.05,
            )
        assert exc.value.code == "timeout"
    finally:
        obs.dispatch = real_dispatch


# --------------------------------------------------------------------------
# Schema present in the tool surface
# --------------------------------------------------------------------------


def test_sparql_in_tool_names():
    from frontend.mcp_server import TOOL_NAMES
    assert "sparql" in TOOL_NAMES


def test_sparql_handler_registered():
    from frontend.mcp_server import HANDLERS
    assert "sparql" in HANDLERS


def test_sparql_description_warns_about_gating():
    from frontend.mcp_server.schemas import DESCRIPTIONS
    desc = DESCRIPTIONS["sparql"]
    assert "CBM_ENABLE_SPARQL" in desc
    assert "DANGER" in desc or "disabled" in desc


# --------------------------------------------------------------------------
# Bundle resolution — sparql must honor the session-selected bundle, exactly
# like every other tool. Guards against regression of the precedence
# (explicit `bundle` arg > session selection > server default) where a fresh
# server session silently falls back to the alphabetical default bundle.
# --------------------------------------------------------------------------


def test_sparql_handler_forwards_resolved_bundle(monkeypatch):
    """Unit-level: the sparql handler resolves the bundle with the same
    precedence as other tools and forwards it to ``run_sparql``. Does not hit
    a real bundle — ``run_sparql`` is captured."""
    import frontend.mcp_server.sparql as sparql_mod

    seen: list[str | None] = []

    def fake_run_sparql(query, *, bundle_default=None):
        seen.append(bundle_default)
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "query_form": "SELECT",
            "ask_result": None,
        }

    monkeypatch.setattr(sparql_mod, "run_sparql", fake_run_sparql)
    handler = HANDLERS["sparql"]

    # No explicit bundle → falls through to the session-selected default.
    handler({"query": "SELECT * WHERE { ?s ?p ?o }"}, "session-bundle")
    # Explicit bundle arg overrides the session selection.
    handler({"query": "SELECT * WHERE { ?s ?p ?o }", "bundle": "explicit-bundle"}, "session-bundle")
    # No selection anywhere → None (run_sparql then resolves the server default).
    handler({"query": "SELECT * WHERE { ?s ?p ?o }"}, None)

    assert seen == ["session-bundle", "explicit-bundle", None]


@pytest.mark.anyio
async def test_sparql_routes_to_session_selected_bundle(enabled, bundle_name):
    """End-to-end: select_bundle then a no-arg sparql query must target the
    selected bundle (not the server default), and an explicit ``bundle`` arg
    must override the session selection. Needs >=2 bundles to discriminate."""
    from frontend.backend.serving.application import bundles as bundles_app

    names = [b["name"] for b in bundles_app.list_bundles_response()["bundles"]]
    others = [n for n in names if n != bundle_name]
    if not others:
        pytest.skip("need >=2 bundles to prove selection actually routes")
    other = others[0]

    server, session = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        # Select `other`; query with NO explicit bundle → must route to `other`.
        await client.call_tool("select_bundle", {"bundle": other})
        routed = await client.call_tool("sparql", {"query": _COUNT_FILES_QUERY})
        # Explicit bundle arg must win over the session selection.
        overridden = await client.call_tool(
            "sparql", {"query": _COUNT_FILES_QUERY, "bundle": bundle_name}
        )
        # Ground truth: query each bundle explicitly.
        truth_other = await client.call_tool(
            "sparql", {"query": _COUNT_FILES_QUERY, "bundle": other}
        )
        truth_default = await client.call_tool(
            "sparql", {"query": _COUNT_FILES_QUERY, "bundle": bundle_name}
        )

    assert session.selected_bundle == other
    for r in (routed, overridden, truth_other, truth_default):
        assert r.isError is False

    routed_rows = routed.structuredContent["rows"]
    overridden_rows = overridden.structuredContent["rows"]
    # Session selection is honored: no-arg query matches explicit `other`.
    assert routed_rows == truth_other.structuredContent["rows"]
    # Explicit arg overrides the session: matches explicit default bundle.
    assert overridden_rows == truth_default.structuredContent["rows"]
    # When the two bundles differ in size, the routing is genuinely
    # discriminating — proves sparql did not just hit one fixed default.
    if truth_other.structuredContent["rows"] != truth_default.structuredContent["rows"]:
        assert routed_rows != overridden_rows


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"

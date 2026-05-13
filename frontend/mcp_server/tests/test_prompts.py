"""Phase 5 tests — prompt discovery + interpolation + transport round-trip.

The freshness invariant matters: prompt messages reference real tool
names. If a tool is removed or renamed, ``test_prompt_messages_only_name_real_tools``
fails — forcing the prompt body to be updated in the same PR.
"""
from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    INVALID_ARGUMENT,
    PROMPTS,
    TOOL_NAMES,
    ToolError,
    get_prompt,
    list_prompts,
)
from frontend.mcp_server.server import build_server


# --------------------------------------------------------------------------
# list_prompts
# --------------------------------------------------------------------------


def test_list_prompts_includes_all_three():
    names = {p.name for p in list_prompts()}
    assert names == {"orient", "explore_concept", "trace_dependency"}


def test_list_prompts_declares_arguments():
    by_name = {p.name: p for p in list_prompts()}
    explore = by_name["explore_concept"]
    arg_required = {a.name: a.required for a in explore.arguments}
    assert arg_required["concept"] is True
    assert arg_required["bundle"] is False
    assert arg_required["depth"] is False


# --------------------------------------------------------------------------
# get_prompt — happy paths
# --------------------------------------------------------------------------


def test_get_orient_with_no_bundle():
    result = get_prompt("orient", None)
    assert result.messages
    text = result.messages[0].content.text
    assert "select_bundle" in text  # tool name appears
    assert "orient_bundle" in text


def test_get_orient_with_bundle_arg():
    result = get_prompt("orient", {"bundle": "alpha"})
    text = result.messages[0].content.text
    assert "'alpha'" in text


def test_get_explore_concept_interpolates_name():
    result = get_prompt("explore_concept", {"concept": "schema", "depth": "3"})
    text = result.messages[0].content.text
    assert "schema" in text
    assert "depth=3" in text
    assert "concept_neighborhood" in text


def test_get_trace_dependency_interpolates_path_and_depth():
    result = get_prompt("trace_dependency", {"path": "src/app.py", "depth": "4"})
    text = result.messages[0].content.text
    assert "src/app.py" in text
    assert "depth=4" in text
    assert "file_impact" in text


def test_get_prompt_description_mentions_argument():
    result = get_prompt("explore_concept", {"concept": "auth"})
    assert "auth" in (result.description or "")


# --------------------------------------------------------------------------
# Validation — missing required args
# --------------------------------------------------------------------------


def test_get_explore_concept_missing_required():
    with pytest.raises(ToolError) as exc:
        get_prompt("explore_concept", {})
    assert exc.value.code == INVALID_ARGUMENT
    assert "concept" in str(exc.value)


def test_get_trace_dependency_missing_required():
    with pytest.raises(ToolError) as exc:
        get_prompt("trace_dependency", None)
    assert exc.value.code == INVALID_ARGUMENT
    assert "path" in str(exc.value)


def test_get_unknown_prompt_raises():
    with pytest.raises(ToolError) as exc:
        get_prompt("does_not_exist", {})
    assert exc.value.code == INVALID_ARGUMENT


# --------------------------------------------------------------------------
# Freshness: prompt messages only name real tools
# --------------------------------------------------------------------------


def test_prompt_messages_only_name_real_tools():
    """If a prompt body references a tool that doesn't exist, the model
    will pick a phantom tool and the call will 404. This test extracts
    every backtick-quoted identifier from each prompt message and asserts
    it's either a real tool name, a real prompt argument, or a known
    keyword."""
    import re

    real_tools = set(TOOL_NAMES)
    # Things we deliberately mention that aren't tools — extend cautiously.
    allowed_non_tools = {
        # arguments mentioned in the body of the message
        "concept", "path", "bundle", "depth", "name", "q",
        "limit", "sort", "import_degree",
        # not a tool but a real Pydantic field referenced as guidance
        "concepts", "concept_detail",  # appears as both ref and tool
    }

    pattern = re.compile(r"`([a-z][a-z0-9_]*)`")
    for name, spec in PROMPTS.items():
        # Build with a representative arg set so the message renders.
        args = {a.name: f"<{a.name}>" for a in spec.arguments if a.required}
        msg = spec.build(args).messages[0].content.text
        for token in pattern.findall(msg):
            assert token in real_tools or token in allowed_non_tools, (
                f"prompt {name!r} references unknown identifier {token!r}; "
                "either add it to TOOL_NAMES or to the allowlist if it's intentional"
            )


# --------------------------------------------------------------------------
# Transport round-trip
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transport_list_prompts(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_prompts()
    names = {p.name for p in result.prompts}
    assert names == {"orient", "explore_concept", "trace_dependency"}


@pytest.mark.anyio
async def test_transport_get_orient(bundle_name):
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.get_prompt("orient", {"bundle": bundle_name})
    assert result.messages
    msg = result.messages[0]
    assert msg.role == "user"
    assert bundle_name in msg.content.text


@pytest.mark.anyio
async def test_transport_get_explore_concept(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.get_prompt("explore_concept", {"concept": "schema"})
    assert "schema" in result.messages[0].content.text


@pytest.mark.anyio
async def test_transport_get_prompt_missing_arg_returns_error(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        with pytest.raises(Exception):  # SDK raises on JSON-RPC error  # noqa: BLE001
            await client.get_prompt("explore_concept", {})


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"

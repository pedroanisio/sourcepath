"""MCP prompts (Phase 5) — workflow templates.

Three prompts that seed an agent's exploration of a bundle:

* ``orient`` — first call after connecting. Hands the agent the cheat
  sheet and a suggested first-five-calls plan.
* ``explore_concept`` — guide a deep-dive on a single SKOS concept:
  neighborhood, lexicalizing files, sample chunks.
* ``trace_dependency`` — guide an impact-analysis on a single file:
  imports both ways, tests, entry points.

Prompts are *templates* — they produce a user message with the framing,
but the model still chooses which tools to call and in what order. The
prompt body references real tool names so a model that's already seen
``tools/list`` knows exactly what to invoke.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent

from .validators import INVALID_ARGUMENT, ToolError

# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSpec:
    name: str
    description: str
    arguments: list[PromptArgument]
    build: Callable[[dict[str, str]], GetPromptResult]


def _msg(text: str) -> PromptMessage:
    return PromptMessage(role="user", content=TextContent(type="text", text=text))


def _require(args: dict[str, str] | None, name: str) -> str:
    """Pull a required argument or raise the canonical ToolError shape."""
    if not args or name not in args or not args[name]:
        raise ToolError(INVALID_ARGUMENT, f"prompt argument {name!r} is required")
    return args[name]


def _optional(args: dict[str, str] | None, name: str, default: str = "") -> str:
    return (args or {}).get(name, default) or default


# --------------------------------------------------------------------------
# orient
# --------------------------------------------------------------------------


def _build_orient(args: dict[str, str] | None) -> GetPromptResult:
    bundle = _optional(args, "bundle")
    bundle_clause = (
        f" (set the session bundle to {bundle!r} first via select_bundle)"
        if bundle
        else " (use the server's default bundle, or select one with select_bundle)"
    )
    text = (
        "You are exploring a codebase that has been pre-mapped into an RDF "
        "knowledge graph with three layers: L1 host (files + imports), "
        "L2 chunks (per-function/class chunks with embeddings), and L3 "
        "concept_graph (SKOS concepts from identifier splitting).\n\n"
        f"Start by orienting yourself{bundle_clause}:\n"
        "  1. Call `orient_bundle` for namespace + layer cheat sheet.\n"
        "  2. Call `bundle_summary` for repo counts and language histogram.\n"
        "  3. Call `list_files` with `sort=import_degree` and `limit=20` "
        "to identify the most-connected files (the spine of the codebase).\n"
        "  4. Pick one file and call `file_detail` to see its imports, "
        "tests, and lexicalized concepts.\n"
        "  5. Pick one concept from that file's `concepts` list and call "
        "`concept_neighborhood` to discover its domain vocabulary.\n\n"
        "Avoid running broad searches before you have orientation — the "
        "first four calls cost almost nothing and tell you where to "
        "look next."
    )
    return GetPromptResult(
        description="Discover the shape of the active bundle and get suggested first calls.",
        messages=[_msg(text)],
    )


# --------------------------------------------------------------------------
# explore_concept
# --------------------------------------------------------------------------


def _build_explore_concept(args: dict[str, str] | None) -> GetPromptResult:
    concept = _require(args, "concept")
    bundle = _optional(args, "bundle")
    depth = _optional(args, "depth", "2")
    bundle_clause = f" in bundle {bundle!r}" if bundle else ""
    text = (
        f"Goal: characterize the SKOS concept `{concept}`{bundle_clause} — "
        "what domain it represents and how it's used in the codebase.\n\n"
        "Run these tools in order, then summarize:\n"
        f"  1. `concept_detail` with `name={concept!r}` — gives frequency, "
        "alt-labels, components, top cooccurring concepts, sample files, "
        "and sample chunks.\n"
        f"  2. `concept_neighborhood` with `name={concept!r}`, `depth={depth}`, "
        "`limit=30` — expands the cooccurrence neighborhood beyond the "
        "first hop so you can see the surrounding domain.\n"
        f"  3. For 2–3 representative chunks from step 1, call "
        "`chunk_detail` to read the source preview and confirm how the "
        "concept appears in real code.\n"
        f"  4. (Optional) `semantic_neighbors` with `q='{concept}'` to "
        "find chunks that *mean* the same thing even when they don't "
        "lexically mention the concept's identifier.\n\n"
        "Produce: a one-paragraph domain summary citing file paths and "
        "chunk URIs as evidence. Flag any obvious false-positives where "
        "the identifier-derived concept doesn't match the actual usage."
    )
    return GetPromptResult(
        description=f"Guided deep-dive on concept {concept!r}: neighborhood, files, chunks.",
        messages=[_msg(text)],
    )


# --------------------------------------------------------------------------
# trace_dependency
# --------------------------------------------------------------------------


def _build_trace_dependency(args: dict[str, str] | None) -> GetPromptResult:
    path = _require(args, "path")
    bundle = _optional(args, "bundle")
    depth = _optional(args, "depth", "2")
    bundle_clause = f" in bundle {bundle!r}" if bundle else ""
    text = (
        f"Goal: trace what happens around the file `{path}`{bundle_clause} — "
        "what it depends on, what depends on it, and which tests exercise "
        "it (directly or via transitive dependents).\n\n"
        "Run these tools in order, then report:\n"
        f"  1. `file_detail` with `path={path!r}` — metadata, direct "
        "imports both ways, lexicalized concepts, chunks.\n"
        f"  2. `file_impact` with `path={path!r}`, `depth={depth}` — full "
        "transitive dependency closure plus the related test set.\n"
        f"  3. For the top 3 transitive dependents, call `file_detail` "
        "again to spot the actual call sites.\n"
        f"  4. (Optional) `semantic_neighbors` with a query derived from "
        f"this file's symbols if you want to find related code that's "
        "not connected via imports.\n\n"
        f"Produce: a bullet list of (a) direct upstream dependencies, "
        "(b) the transitive blast radius (count + a few representative "
        "paths), (c) test files that should be run when changing this "
        "file, (d) any orphaned dependents that lack test coverage."
    )
    return GetPromptResult(
        description=f"Trace direct and transitive dependencies + tests for {path!r}.",
        messages=[_msg(text)],
    )


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------


PROMPTS: dict[str, PromptSpec] = {
    "orient": PromptSpec(
        name="orient",
        description="Discover the shape of the active codebase bundle. Suggests a first-five-calls exploration plan.",
        arguments=[
            PromptArgument(name="bundle", description="Optional bundle name to target.", required=False),
        ],
        build=_build_orient,
    ),
    "explore_concept": PromptSpec(
        name="explore_concept",
        description="Guided deep-dive on a single SKOS concept: neighborhood, lexicalizing files, sample chunks.",
        arguments=[
            PromptArgument(name="concept", description="Concept name (skos:prefLabel) to explore.", required=True),
            PromptArgument(name="bundle", description="Optional bundle name to target.", required=False),
            PromptArgument(name="depth", description="Cooccurrence walk depth (1–3). Default 2.", required=False),
        ],
        build=_build_explore_concept,
    ),
    "trace_dependency": PromptSpec(
        name="trace_dependency",
        description="Trace direct and transitive dependencies for a file + the tests that exercise it.",
        arguments=[
            PromptArgument(name="path", description="Bundle-relative file path to trace.", required=True),
            PromptArgument(name="bundle", description="Optional bundle name to target.", required=False),
            PromptArgument(name="depth", description="Walk depth (1–5). Default 2.", required=False),
        ],
        build=_build_trace_dependency,
    ),
}


def list_prompts() -> list[Prompt]:
    return [
        Prompt(name=spec.name, description=spec.description, arguments=spec.arguments)
        for spec in PROMPTS.values()
    ]


def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
    spec = PROMPTS.get(name)
    if spec is None:
        raise ToolError(INVALID_ARGUMENT, f"unknown prompt: {name!r}")
    return spec.build(arguments)

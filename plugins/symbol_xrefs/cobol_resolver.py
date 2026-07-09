"""COBOL symbol-xref resolver (Tier-1).

Edge kinds covered: ``calls`` (the only cross-reference COBOL expresses at the
procedure level; COBOL has no inheritance or method overriding).

Resolver names on emitted edges:

  - ``cobol_intra_file`` — a ``PERFORM`` targeting a paragraph/section in the
    same program (same file).
  - ``cobol_inter_file`` — a ``CALL 'literal'`` targeting another program's
    compilation unit, resolved across the bundle.

Call shapes in scope:

  - ``PERFORM para``               -> calls edge to that procedure's chunk.
  - ``PERFORM a THRU b``           -> calls edges to both ``a`` and ``b``.
  - ``CALL 'PROG'`` / ``CALL "PROG"`` -> calls edge to program ``PROG``'s
                                     class-like chunk (any COBOL file).

Deliberately out of scope / recorded as data (never silently dropped):

  - ``CALL identifier`` (dynamic — the program name lives in a data item and
    is only known at run time) -> unresolved ``dynamic_dispatch``.
  - ``CALL 'PROG'`` where ``PROG`` is not a program defined in the bundle
    (a system/vendor subprogram) -> unresolved ``module_not_in_repo``.
  - ``PERFORM`` of a paragraph not found in the program (e.g. one pulled in
    via a ``COPY`` copybook) -> silently ignored, matching the posture of the
    Dart/Rust resolvers toward names they cannot bind.

COBOL is case-insensitive, so every name is matched upper-cased.
"""
from __future__ import annotations

import re
from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.languages.cobol import PERFORM_NONTARGET


RESOLVER_INTRA = "cobol_intra_file"
RESOLVER_INTER = "cobol_inter_file"

_GLOBAL_PROGRAMS_CACHE_KEY = "_xrefs_cobol_global_programs"

# PERFORM <target> [THRU|THROUGH <target2>]
_PERFORM_RE = re.compile(
    r"\bPERFORM\s+(?P<t1>[A-Za-z0-9][A-Za-z0-9-]*)"
    r"(?:\s+(?:THRU|THROUGH)\s+(?P<t2>[A-Za-z0-9][A-Za-z0-9-]*))?",
    re.IGNORECASE)
# CALL 'literal' / CALL "literal"
_CALL_LIT_RE = re.compile(
    r"\bCALL\s+['\"](?P<prog>[^'\"]+)['\"]", re.IGNORECASE)
# CALL identifier (dynamic — data-name target)
_CALL_DYN_RE = re.compile(
    r"\bCALL\s+(?P<id>[A-Za-z_][A-Za-z0-9_-]*)", re.IGNORECASE)


def _global_program_cache(ctx: PipelineCtx) -> dict[str, str]:
    """``UPPER(program name) -> chunk_id`` for every COBOL program (class-like
    chunk) in the bundle. Restricted to COBOL files so a Java/C++ ``class``
    chunk can never masquerade as a CALL target."""
    cached = ctx.scratch.get(_GLOBAL_PROGRAMS_CACHE_KEY)
    if cached is not None:
        return cast(dict, cached)
    cobol_paths = {r.path for r in ctx.records if r.language == "cobol"}
    out: dict[str, str] = {}
    for c in cast(list, ctx.indices.get("l2_10_chunks", [])):
        if c.get("kind") != "class" or c.get("path") not in cobol_paths:
            continue
        out.setdefault(c["symbol"].upper(), c["chunk_id"])
    ctx.scratch[_GLOBAL_PROGRAMS_CACHE_KEY] = out
    return out


def resolve_cobol_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if record.language != "cobol":
        return [], []

    chunks_in_file = [
        c for c in cast(list, ctx.indices.get("l2_10_chunks", []))
        if c.get("path") == record.path
    ]
    if not chunks_in_file:
        return [], []

    # Same-file procedure map: UPPER(name) -> chunk_id (paragraphs + sections).
    proc_by_name: dict[str, str] = {}
    for c in chunks_in_file:
        if c.get("kind") == "method":
            proc_by_name.setdefault(c["symbol"].upper(), c["chunk_id"])

    global_programs = _global_program_cache(ctx)

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []

    for c in chunks_in_file:
        if c.get("kind") != "method":
            continue
        src_id = c["chunk_id"]
        body = c.get("text", "")
        seen: set[str] = set()

        # ---- PERFORM: intra-program procedure calls ------------------------
        for m in _PERFORM_RE.finditer(body):
            for grp in (m.group("t1"), m.group("t2")):
                if not grp:
                    continue
                up = grp.upper()
                if up in PERFORM_NONTARGET or up in seen:
                    continue
                seen.add(up)
                dst = proc_by_name.get(up)
                if dst is not None and dst != src_id:
                    edges.append(SymbolXrefEdge(
                        src_chunk_id=src_id, dst_chunk_id=dst,
                        kind="calls", resolution="exact",
                        resolver=RESOLVER_INTRA,
                    ))

        # ---- CALL 'literal': inter-program calls ---------------------------
        for m in _CALL_LIT_RE.finditer(body):
            prog = m.group("prog")
            key = f"call:{prog.upper()}"
            if key in seen:
                continue
            seen.add(key)
            dst = global_programs.get(prog.upper())
            if dst is not None and dst != src_id:
                edges.append(SymbolXrefEdge(
                    src_chunk_id=src_id, dst_chunk_id=dst,
                    kind="calls", resolution="exact",
                    resolver=RESOLVER_INTER,
                ))
            else:
                unresolved.append(UnresolvedSymbolRef(
                    src_chunk_id=src_id, raw_target=prog,
                    kind="calls", reason="module_not_in_repo",
                    resolver=RESOLVER_INTER,
                ))

        # ---- CALL identifier: dynamic dispatch (unresolvable, recorded) ----
        for m in _CALL_DYN_RE.finditer(body):
            name = m.group("id")
            key = f"calldyn:{name.upper()}"
            if key in seen:
                continue
            seen.add(key)
            unresolved.append(UnresolvedSymbolRef(
                src_chunk_id=src_id, raw_target=name,
                kind="calls", reason="dynamic_dispatch",
                resolver=RESOLVER_INTER,
            ))

    return edges, unresolved

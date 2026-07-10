"""Serialization of analyzer output that may out-nest the interpreter.

A full-body CST (TS/JS ``cst_json``, Python ``ast_json``) mirrors the parse
tree, and a deeply nested source expression can exceed Python's recursion
ceiling — ``json.dumps`` then raises RecursionError at emit time, killing a
completed run at its last step (flaw map F19). The contract here:

1. serialize normally when possible;
2. on RecursionError, retry once under a temporarily raised ceiling —
   this preserves the data for the common overflow band;
3. only if a top-level field still out-nests the raised ceiling, replace
   that field with a disclosed omission stub and report truncation so the
   caller can register a degradation (PALS's Law: dropped data is
   disclosed, never silent).
"""
from __future__ import annotations

import json
import sys

#: Ceiling used for the retry. Well above any sane parse tree that must be
#: preserved, low enough that the encoder's own recursion guard still fires
#: before the C stack is at risk.
DEEP_NESTING_LIMIT = 20_000

#: Value of the ``omitted`` key that replaces an un-serializable field.
OMISSION_MARKER = "nesting_exceeds_serialization_depth"


def dump_ast_summary(summary) -> tuple[str, bool]:
    """``json.dumps(summary, sort_keys=True)`` that cannot raise
    RecursionError. Returns ``(json_text, truncated)``.
    """
    try:
        return json.dumps(summary, sort_keys=True), False
    except RecursionError:
        pass

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, DEEP_NESTING_LIMIT))
    try:
        try:
            return json.dumps(summary, sort_keys=True), False
        except RecursionError:
            pass
        if not isinstance(summary, dict):
            return json.dumps({"omitted": OMISSION_MARKER}), True
        out = {}
        for key in summary:
            try:
                json.dumps(summary[key])
                out[key] = summary[key]
            except RecursionError:
                out[key] = {"omitted": OMISSION_MARKER}
        return json.dumps(out, sort_keys=True), True
    finally:
        sys.setrecursionlimit(old_limit)

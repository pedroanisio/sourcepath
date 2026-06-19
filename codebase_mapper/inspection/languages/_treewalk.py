"""Iterative tree-sitter CST traversal primitives.

A recursive Python walk over a tree-sitter CST consumes one interpreter frame
per depth level, so a deeply-nested source file (e.g. a generated or
macro-expanded Chromium Obj-C++ unit) overflows the default recursion limit
(~1000) and raises ``RecursionError`` mid-extraction — aborting the whole run.

These helpers traverse with an explicit stack instead, so depth is bounded only
by available memory. They are duck-typed over tree-sitter ``Node`` (``.children``,
``.is_named``, ``.type``, ``.start_byte``, ``.end_byte``) and the JSON-able CST
dict shape, with no tree-sitter import, so every language analyzer can share them.

See issue: RecursionError from unbounded recursive CST walks.
"""
from __future__ import annotations

from typing import Callable, Iterator


def iter_named_pre_order(root, descend: Callable[[object], bool] | None = None) -> Iterator:
    """Yield ``root`` and its named descendants in pre-order, iteratively.

    Children are visited left-to-right — byte-for-byte the same order as the
    equivalent recursive ``for ch in node.children: if ch.is_named: visit(ch)``.
    For each yielded node, its named children are pushed unless ``descend(node)``
    returns ``False`` (the subtree is pruned — the iterative equivalent of a
    recursive visitor ``return``-ing early without recursing).
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if descend is None or descend(node):
            named = [c for c in node.children if c.is_named]
            # Push reversed so the leftmost child pops next → true pre-order.
            stack.extend(reversed(named))


def find_named_descendant(root, kinds):
    """First node in ``iter_named_pre_order(root)`` whose ``type`` is in
    ``kinds`` (``root`` included), or ``None``. Iterative replacement for a
    recursive first-match descendant search; identical pre-order semantics."""
    for node in iter_named_pre_order(root):
        if node.type in kinds:
            return node
    return None


def node_to_jsonable(root, content: bytes):
    """Serialize a tree-sitter node to a JSON-able structure with full byte
    coverage, iteratively (no recursion-depth ceiling).

    Encoding (byte-identical to the prior recursive serializer):
      * Anonymous leaves whose ``type == text`` (keywords, punctuation) collapse
        to a **bare string**.
      * Named leaves become ``{"type": ..., "text": ...}``.
      * Interstitial gaps between siblings (whitespace) are **bare strings** in
        the children list.
      * Internal nodes are ``{"type": ..., "children": [...]}``.

    Walking the result pre-order and concatenating every string / ``text`` field
    reproduces the original bytes exactly (for valid UTF-8 input). A non-UTF-8
    leaf/gap raises ``UnicodeDecodeError`` — the same signal the recursive
    version raised, for the caller to convert into ``cst_json = None``.
    """
    def leaf(node):
        text = content[node.start_byte:node.end_byte].decode("utf-8")
        if not node.is_named and node.type == text:
            return text
        return {"type": node.type, "text": text}

    # Explicit-stack post-order build. Each frame writes one node's serialized
    # value into a parent slot; internal nodes pre-place their gaps and reserve
    # a placeholder slot per child, which the child frames fill in later. Slot
    # writes are independent, so child processing order is irrelevant.
    holder: list = [None]
    stack = [(root, holder, 0)]
    while stack:
        node, out, slot = stack.pop()
        children = node.children
        if not children:
            out[slot] = leaf(node)
            continue
        out_children: list = []
        cursor = node.start_byte
        pending: list[tuple[object, int]] = []
        for child in children:
            if child.start_byte > cursor:
                gap = content[cursor:child.start_byte].decode("utf-8")
                if gap:
                    out_children.append(gap)
            out_children.append(None)  # placeholder, filled by the child frame
            pending.append((child, len(out_children) - 1))
            cursor = child.end_byte
        if cursor < node.end_byte:
            gap = content[cursor:node.end_byte].decode("utf-8")
            if gap:
                out_children.append(gap)
        out[slot] = {"type": node.type, "children": out_children}
        stack.extend((child, out_children, idx) for child, idx in pending)
    return holder[0]


def regenerate_cst_text(cst_json) -> str:
    """Concatenate every string / ``text`` leaf of a ``node_to_jsonable`` result
    in pre-order, iteratively — the inverse of :func:`node_to_jsonable` for the
    CST body (callers prepend/append the header/footer bytes)."""
    parts: list[str] = []
    stack = [cst_json]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            parts.append(node)
            continue
        if "text" in node:
            parts.append(node["text"])
            continue
        # Push reversed so children concatenate left-to-right (pre-order).
        stack.extend(reversed(node.get("children", ())))
    return "".join(parts)

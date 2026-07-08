"""codebase_mapper.languages.clojure — Tier-1 Clojure / ClojureScript support.

There is no maintained PyPI tree-sitter-clojure grammar, so — like the Python
analyzer (stdlib ``ast``) — this is a self-contained reader. Clojure is
homoiconic: code is data made of s-expressions, so a small string/char/
comment-aware tokenizer plus an iterative (stack-based, no recursion ceiling)
reader recovers the structural surface we index:

  * ``namespace`` — the ``(ns my.app.core ...)`` name.
  * ``imports``   — namespaces pulled in via ``(:require ...)`` / ``(:use ...)``
                    inside the ns form (kind ``"require"``). ``:import`` (Java
                    interop classes) is out of scope — those aren't repo files.
  * ``items``     — one record per top-level ``def``-form (``defn``/``defn-``/
                    ``defmacro``/``defmulti``/``defmethod``/``defonce`` ->
                    function; ``def`` -> var; ``defrecord`` -> record;
                    ``deftype`` -> type; ``defprotocol``/``definterface`` ->
                    protocol; ``ns`` -> namespace), each with line/byte spans
                    (powers L2 chunking + the symbol surface). defn-shaped
                    items additionally carry the canonical signature fields
                    (``signature``/``params``/``returns``/``visibility``;
                    ``bases`` on defrecord/deftype) copied onto L2 chunks —
                    see plugins/chunks_embeddings/signatures.py and
                    tests/test_signatures_clojure.py for the mapping.

Public surface mirrors the other analyzers:

  * ``extract_clojure_ast_summary(content, path) -> (summary, errors)``
  * ``resolve_clojure_imports(src_path, summary, paths_set) -> (in_repo, external)``

Known limits (documented, not silent): ``#_`` form-discard is not honored
(affects only rare item-naming edge cases, never the namespace/require
surface), and map-shaped metadata (``^{:private true}``) is kept structurally
but not interpreted — only ``^:private`` / ``^Type`` shorthands feed the
signature fields.
"""
from __future__ import annotations

CLOJURE_EXTENSIONS = (".clj", ".cljs", ".cljc", ".cljr")

# Top-level def-form head -> item kind.
_DEF_KINDS: dict[str, str] = {
    "defn": "function", "defn-": "function", "defmacro": "function",
    "defmulti": "function", "defmethod": "function", "defonce": "function",
    "def": "var",
    "defrecord": "record", "deftype": "type",
    "defprotocol": "protocol", "definterface": "protocol",
    "ns": "namespace",
}

_WS = " \t\r\n,"
_DELIMS = '()[]{}'


def _tokenize(text: str) -> list[tuple]:
    """Yield (type, value, start, end, line) tokens. String/char-literal/comment
    aware so delimiters inside them never miscount. Types: open, close, sym, kw,
    str, char, meta, discard."""
    toks: list[tuple] = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r,":
            i += 1
            continue
        if c == ";":  # line comment
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == '"':  # string literal
            start, sline = i, line
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            toks.append(("str", text[start:i], start, i, sline))
            continue
        if c == "\\":  # character literal: \x, \newline, A ...
            start = i
            i += 1
            if i < n:
                if text[i].isalpha():
                    i += 1
                    while i < n and (text[i].isalnum() or text[i] == "-"):
                        i += 1
                else:
                    i += 1
            toks.append(("char", text[start:i], start, i, line))
            continue
        if c == "#" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "_":  # discard next form
                toks.append(("discard", "#_", i, i + 2, line))
                i += 2
                continue
            if nxt == '"':  # regex literal #"..."
                start, sline = i, line
                i += 2
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == "\n":
                        line += 1
                    if text[i] == '"':
                        i += 1
                        break
                    i += 1
                toks.append(("str", text[start:i], start, i, sline))
                continue
            if nxt in "({":  # set #{...} or anon-fn #(...)
                toks.append(("open", text[i:i + 2], i, i + 2, line))
                i += 2
                continue
            # #' var-quote, #= etc — fall through; `#` joins the next atom.
        if c == "^":  # metadata marker
            toks.append(("meta", "^", i, i + 1, line))
            i += 1
            continue
        if c in "'`~@":  # quote / syntax-quote / unquote / deref — structural no-ops
            i += 1
            continue
        if c in "([{":
            toks.append(("open", c, i, i + 1, line))
            i += 1
            continue
        if c in ")]}":
            toks.append(("close", c, i, i + 1, line))
            i += 1
            continue
        # atom: symbol / keyword / number
        start = i
        while i < n and text[i] not in _WS and text[i] not in _DELIMS and text[i] != ";":
            i += 1
        val = text[start:i]
        if not val:  # safety: never stall
            i += 1
            continue
        toks.append(("kw" if val.startswith(":") else "sym", val, start, i, line))
    return toks


def _parse(toks: list[tuple]) -> list[dict]:
    """Iterative (stack-based) reader -> list of top-level form nodes.

    A list/vector/map node is ``{"kind": "list", "delim", "children", "start",
    "end", "line"}``; leaves are ``{"kind": "sym"|"kw"|"str"|"char", "value",
    "start", "end", "line"}``. A ``meta`` token flags the node that follows it
    (the metadata VALUE, e.g. ``String`` in ``^String [x]``) with
    ``is_meta: True`` so name/signature extraction can honor type hints and
    ``^:private``; ``discard`` tokens are dropped (see module docstring).
    Stack-based so a deeply-nested file cannot overflow.
    """
    root: list[dict] = []
    stack: list[list[dict]] = [root]
    open_nodes: list[dict] = []
    pending_meta = False
    for ttype, val, start, end, line in toks:
        if ttype == "open":
            node = {"kind": "list", "delim": val[-1], "children": [],
                    "start": start, "end": end, "line": line}
            if pending_meta:
                node["is_meta"] = True
                pending_meta = False
            stack[-1].append(node)
            stack.append(node["children"])
            open_nodes.append(node)
        elif ttype == "close":
            pending_meta = False
            if len(stack) > 1:
                stack.pop()
                open_nodes.pop()["end"] = end
        elif ttype in ("sym", "kw", "str", "char"):
            node = {"kind": ttype, "value": val,
                    "start": start, "end": end, "line": line}
            if pending_meta:
                node["is_meta"] = True
                pending_meta = False
            stack[-1].append(node)
        elif ttype == "meta":
            pending_meta = True
        # discard: ignored structurally
    return root


def _first_name_sym(children: list[dict]) -> str | None:
    """The def-form's name: the first ``sym`` after the head (index 0), skipping
    metadata values (``^:private`` / ``^Tag``) and keywords."""
    for child in children[1:]:
        if child.get("kind") == "sym" and not child.get("is_meta"):
            return child["value"]
    return None


# ---------------------------------------------------------------------------
# Canonical signature fields on items (plugins/chunks_embeddings/signatures.py)
# ---------------------------------------------------------------------------

# def-forms whose tail is a defn-style fn tail: [args] body | ([args] body)+
_FN_HEADS = ("defn", "defn-", "defmacro")


def _src(node: dict, text: str) -> str:
    """A node's source text collapsed to one line (whitespace runs become a
    single space) — signatures and destructuring params stay as written."""
    return " ".join(text[node["start"]:node["end"]].split())


def _hint_text(node: dict, text: str) -> str | None:
    """Metadata value usable as a type hint: ``^Sym`` / ``^"str"`` shorthands
    only. ``^:kw`` and ``^{...}`` map metadata are not type hints."""
    if node.get("kind") in ("sym", "str"):
        return _src(node, text)
    return None


def _vector_params(vec: dict, text: str) -> list[dict]:
    """Param records from an argument/field vector: names as written (a rest
    arg merges into one ``"& name"`` entry, destructuring forms keep their
    source text), ``type`` from a preceding ``^Type`` hint, ``default`` always
    None (Clojure has no default arguments)."""
    params: list[dict] = []
    ptype: str | None = None
    rest = False
    for child in vec.get("children", []):
        if child.get("is_meta"):
            ptype = _hint_text(child, text)
            continue
        if child.get("kind") == "sym" and child["value"] == "&":
            rest = True
            continue
        name = child["value"] if child.get("kind") == "sym" else _src(child, text)
        params.append({"name": ("& " + name) if rest else name,
                       "type": ptype, "default": None})
        ptype = None
        rest = False
    return params


def _fn_tail(children: list[dict], i: int, text: str) -> tuple[dict | None, list[dict], str | None]:
    """Locate the arg vector(s) of a defn-style fn tail starting at index
    ``i``: returns ``(single_arity_vec, multi_arity_vecs, return_hint)``.
    Docstrings and attr-maps are skipped; a ``^Type`` hint is the return hint
    only when it directly precedes the single-arity arg vector."""
    hint: str | None = None
    while i < len(children):
        child = children[i]
        if child.get("is_meta"):
            hint = _hint_text(child, text)
            i += 1
            continue
        kind, delim = child.get("kind"), child.get("delim")
        if kind == "list" and delim == "[":
            return child, [], hint
        if kind == "list" and delim == "(":
            vecs = []
            for arity in children[i:]:
                if arity.get("kind") == "list" and arity.get("delim") == "(":
                    vec = next((c for c in arity.get("children", [])
                                if c.get("kind") == "list" and c.get("delim") == "["
                                and not c.get("is_meta")), None)
                    if vec is not None:
                        vecs.append(vec)
            return None, vecs, None
        if kind == "str" or (kind == "list" and delim == "{"):
            hint = None  # docstring / attr-map breaks hint adjacency
            i += 1
            continue
        break  # anything else: not a defn-shaped tail — extract nothing
    return None, [], None


def _record_fields(head: str, name: str, children: list[dict], name_idx: int,
                   text: str) -> dict:
    """defrecord/deftype: the field vector becomes ``params`` and the
    implemented protocol/interface symbols become ``bases`` (as written)."""
    fields_vec: dict | None = None
    bases: list[str] = []
    for child in children[name_idx + 1:]:
        if child.get("is_meta"):
            continue
        if fields_vec is None:
            if child.get("kind") == "list" and child.get("delim") == "[":
                fields_vec = child
        elif child.get("kind") == "sym":
            bases.append(child["value"])
    if fields_vec is None:
        return {}
    signature = f"({head} {name} {_src(fields_vec, text)}"
    signature += f" {' '.join(bases)})" if bases else ")"
    return {"signature": signature,
            "params": _vector_params(fields_vec, text),
            "bases": bases}


def _item_signature_fields(head: str, form: dict, name: str, text: str) -> dict:
    """Canonical signature fields for one top-level def-form item. Only
    reliably-parsed values are returned; empty/unknown fields are dropped by
    the caller (omission contract). Multi-arity convention: ``params`` come
    from the arity vector with the most parameters and ``signature`` lists
    every arity vector, e.g. ``(defn fetch ([url]) ([url opts]))``."""
    children = form["children"]
    name_idx = next((j for j, c in enumerate(children[1:], start=1)
                     if c.get("kind") == "sym" and not c.get("is_meta")), None)
    if name_idx is None:
        return {}

    out: dict = {}
    if head == "defn-" or any(
            c.get("is_meta") and c.get("kind") == "kw" and c["value"] == ":private"
            for c in children[1:name_idx]):
        out["visibility"] = "private"

    if head not in _FN_HEADS and head != "defmethod":
        if head in ("defrecord", "deftype"):
            out.update(_record_fields(head, name, children, name_idx, text))
        return out

    i = name_idx + 1
    prefix = f"({head} {name}"
    if head == "defmethod":
        # (defmethod multifn dispatch-val & fn-tail): the dispatch value is
        # exactly one form, so skipping it is unambiguous even when it is a
        # vector (e.g. (defmethod convert [Km Mi] [q] ...)).
        while i < len(children) and children[i].get("is_meta"):
            i += 1
        if i >= len(children):
            return out
        prefix += f" {_src(children[i], text)}"
        i += 1

    single, arities, hint = _fn_tail(children, i, text)
    if single is not None:
        out["params"] = _vector_params(single, text)
        if hint:
            out["returns"] = hint
            out["signature"] = f"{prefix} ^{hint} {_src(single, text)})"
        else:
            out["signature"] = f"{prefix} {_src(single, text)})"
    elif arities:
        widest = max(arities, key=lambda v: len(_vector_params(v, text)))
        out["params"] = _vector_params(widest, text)
        out["signature"] = f"{prefix} {' '.join(f'({_src(v, text)})' for v in arities)})"
    return out


def _ns_requires(ns_node: dict) -> list[str]:
    """Required namespaces from an ns form's ``(:require ...)`` / ``(:use ...)``
    clauses. Each spec is a bare symbol (``foo.bar``) or a vector whose first
    element is the namespace (``[foo.bar :as fb :refer [x]]``)."""
    out: list[str] = []
    for clause in ns_node.get("children", []):
        if clause.get("kind") != "list" or not clause.get("children"):
            continue
        head = clause["children"][0]
        if head.get("kind") == "kw" and head["value"] in (":require", ":use"):
            for spec in clause["children"][1:]:
                if spec.get("kind") == "sym":
                    out.append(spec["value"])
                elif (spec.get("kind") == "list" and spec.get("delim") == "["
                      and spec.get("children")):
                    first = spec["children"][0]
                    if first.get("kind") == "sym":
                        out.append(first["value"])
    return out


def extract_clojure_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["clojure_decode_error"]

    forms = _parse(_tokenize(text))

    namespace: str | None = None
    items: list[dict] = []
    funcs: list[str] = []
    types: list[str] = []
    requires: list[tuple[str, int]] = []

    for form in forms:
        if form.get("kind") != "list" or not form.get("children"):
            continue
        head = form["children"][0]
        if head.get("kind") != "sym":
            continue
        h = head["value"]
        kind = _DEF_KINDS.get(h)
        if kind is None:
            continue
        name = _first_name_sym(form["children"])
        if name is None:
            continue
        line_start = form["line"]
        line_end = line_start + text[form["start"]:form["end"]].count("\n")
        item = {
            "kind": kind,
            "name": name,
            "parent": None,
            "line_start": line_start,
            "line_end": line_end,
            "byte_start": form["start"],
            "byte_end": form["end"],
        }
        for key, value in _item_signature_fields(h, form, name, text).items():
            if value or value is True:  # omission contract: no placeholders
                item[key] = value
        items.append(item)
        if h == "ns":
            namespace = name
            for ns in _ns_requires(form):
                requires.append((ns, line_start))
        elif kind == "function":
            funcs.append(name)
        elif kind in ("record", "type", "protocol"):
            types.append(name)

    # Dedupe imports on the namespace, keep first line, sort.
    seen: set[str] = set()
    imports: list[dict] = []
    for ns, lineno in requires:
        if ns in seen:
            continue
        seen.add(ns)
        imports.append({"kind": "require", "source": ns, "lineno": lineno})
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    return {
        "language": "clojure",
        "namespace": namespace,
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(types)),
        "items": items,
    }, []


def _ns_to_relpath(ns: str) -> str:
    """Clojure namespace -> source-relative path stem: dots become slashes and
    dashes become underscores (the Clojure file-naming convention)."""
    return ns.replace("-", "_").replace(".", "/")


def resolve_clojure_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve required namespaces to in-repo files; everything else (stdlib
    ``clojure.*``, third-party) is external. A namespace ``a.b-c`` maps to
    ``a/b_c.clj[cs|c|r]`` under any source root (matched as a path suffix)."""
    in_repo: set[str] = set()
    external: set[str] = set()
    for imp in summary.get("imports", []):
        ns = imp.get("source")
        if not ns:
            continue
        rel = _ns_to_relpath(ns)
        match: str | None = None
        for ext in CLOJURE_EXTENSIONS:
            cand = rel + ext
            hits = [p for p in paths_set if p == cand or p.endswith("/" + cand)]
            if hits:
                match = sorted(hits)[0]
                break
        if match is not None:
            in_repo.add(match)
        else:
            external.add(ns)
    return sorted(in_repo), sorted(external)

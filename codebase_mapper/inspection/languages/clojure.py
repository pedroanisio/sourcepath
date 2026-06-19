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
                    (powers L2 chunking + the symbol surface).

Public surface mirrors the other analyzers:

  * ``extract_clojure_ast_summary(content, path) -> (summary, errors)``
  * ``resolve_clojure_imports(src_path, summary, paths_set) -> (in_repo, external)``

Known limits (documented, not silent): ``#_`` form-discard is not honored, and
reader metadata (``^...``) is dropped — both affect only rare item-naming edge
cases, never the namespace/require surface.
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
    "start", "end", "line"}``. ``meta`` / ``discard`` tokens are dropped (see
    module docstring). Stack-based so a deeply-nested file cannot overflow.
    """
    root: list[dict] = []
    stack: list[list[dict]] = [root]
    open_nodes: list[dict] = []
    for ttype, val, start, end, line in toks:
        if ttype == "open":
            node = {"kind": "list", "delim": val[-1], "children": [],
                    "start": start, "end": end, "line": line}
            stack[-1].append(node)
            stack.append(node["children"])
            open_nodes.append(node)
        elif ttype == "close":
            if len(stack) > 1:
                stack.pop()
                open_nodes.pop()["end"] = end
        elif ttype in ("sym", "kw", "str", "char"):
            stack[-1].append({"kind": ttype, "value": val,
                              "start": start, "end": end, "line": line})
        # meta / discard: ignored structurally
    return root


def _first_name_sym(children: list[dict]) -> str | None:
    """The def-form's name: the first ``sym`` after the head (index 0), skipping
    dropped metadata maps / keywords."""
    for child in children[1:]:
        if child.get("kind") == "sym":
            return child["value"]
    return None


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
        items.append({
            "kind": kind,
            "name": name,
            "parent": None,
            "line_start": line_start,
            "line_end": line_end,
            "byte_start": form["start"],
            "byte_end": form["end"],
        })
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

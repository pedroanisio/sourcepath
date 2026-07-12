"""codebase_mapper.inspection.languages.php — first-class PHP support.

No maintained tree-sitter-php wheel is in this project's dependency set, so —
like Dart / SQL / HTML / CSS / Shell — PHP uses a disciplined pure-Python
extractor. As with shell, the parse is only sound because the lexing is done
honestly first: ``_neutralize`` is a single left-to-right state machine that
blanks (length- and newline-preserving):

  * ``//`` and ``#`` line comments (but NOT ``#[Attribute]``, which is PHP 8
    attribute syntax, not a comment) and ``/* … */`` blocks;
  * ``'single'`` and ``"double"`` quoted strings;
  * **heredoc / nowdoc bodies** (``<<<SQL`` … ``SQL;``).

That is what makes a ``}`` inside a comment, a string, or a heredoc harmless —
and what stops a heredoc containing ``function ghost() {`` from producing a
phantom declaration.

Declarations emitted: ``namespace``, ``class`` / ``interface`` / ``trait`` /
``enum``, top-level ``function``, and methods (parent = the enclosing
class-like, by span containment). Bodyless interface/abstract methods that end
in ``;`` instead of a block are handled.

Import edges:
  * ``require``/``include`` (``_once``) — the first string literal of the
    expression, with the ubiquitous ``__DIR__ . '/…'`` idiom normalised to a
    path relative to the including file;
  * ``use Foo\\Bar;`` — but only at **brace depth 0**. A ``use Loggable;``
    *inside* a class body is trait composition, not an import, and must not
    become an edge. Namespace imports resolve through the composer.json
    ``autoload.psr-4`` map (built lazily by the resolver, per the
    ``host:c_basename_index`` precedent).
"""
from __future__ import annotations

import json          # stdlib json (absolute import; not languages/json.py)
import re

from pathlib import PurePosixPath


def _line_byte_starts(content: bytes) -> list[int]:
    starts = [0, 0]
    for i, b in enumerate(content):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _line_of(byte_idx: int, line_byte_starts: list[int]) -> int:
    lo, hi = 1, len(line_byte_starts) - 1
    if hi <= 0:
        return 1
    if byte_idx >= line_byte_starts[hi]:
        return hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_byte_starts[mid] <= byte_idx:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _collapse(text: str) -> str:
    return " ".join(text.split())


_HEREDOC_RE = re.compile(r"<<<[ \t]*(?P<q>[\"']?)(?P<tag>[A-Za-z_]\w*)(?P=q)")


def _neutralize(text: str, blank_strings: bool = True) -> str:
    """Blank comments, heredoc/nowdoc bodies, and (optionally) strings, in one
    left-to-right pass. Length- and newline-preserving."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue

        if c == "#":
            if i + 1 < n and text[i + 1] == "[":   # PHP 8 attribute, not a comment
                i += 1
                continue
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
            continue

        if c == "<" and text.startswith("<<<", i):
            m = _HEREDOC_RE.match(text, i)
            if m:
                nl = text.find("\n", m.end())
                if nl == -1:
                    blank(i, n)
                    i = n
                    continue
                term = re.compile(r"^[ \t]*" + re.escape(m.group("tag")) + r"\b", re.MULTILINE)
                tm = term.search(text, nl + 1)
                end = tm.end() if tm else n
                blank(nl + 1, end)   # body only
                i = end
                continue

        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "'":
                    j += 1
                    break
                j += 1
            if blank_strings:
                blank(i, j)
            i = j
            continue

        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            if blank_strings:
                blank(i, j)
            i = j
            continue

        i += 1

    return "".join(out)


_NS_RE = re.compile(r"(?m)^[ \t]*namespace[ \t]+(?P<ns>[\w\\]+)[ \t]*[;{]")
_USE_RE = re.compile(
    r"(?m)^[ \t]*use[ \t]+(?:function[ \t]+|const[ \t]+)?(?P<fqn>[\w\\]+)"
    r"(?:[ \t]+as[ \t]+\w+)?[ \t]*;")
_CLASSLIKE_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:abstract|final|readonly)[ \t]+)*"
    r"(?P<kw>class|interface|trait|enum)[ \t]+(?P<name>\w+)")
_FUNC_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:public|private|protected|static|abstract|final|readonly)[ \t]+)*"
    r"function[ \t]+(?P<name>\w+)[ \t]*\(")
_INCLUDE_RE = re.compile(
    r"\b(?:require|include)(?:_once)?\b(?P<expr>[^;]*);")
_LITERAL_RE = re.compile(r"'(?P<a>[^']*)'|\"(?P<b>[^\"]*)\"")

# --- inheritance (BL-037) -------------------------------------------------
# The `extends` clause runs until `implements` or the opening brace; the
# `implements` clause runs to the brace. Both are matched against the
# *neutralized* header, so a comment or string inside the header contributes
# nothing. DOTALL because PHP declarations routinely wrap across lines.
_EXTENDS_RE = re.compile(r"\bextends\b(?P<names>.*?)(?=\bimplements\b|$)", re.S)
_IMPLEMENTS_RE = re.compile(r"\bimplements\b(?P<names>.*)$", re.S)
#: A base name: identifier chars plus the namespace separator.
_BASE_NAME_RE = re.compile(r"[\w\\]+")


def _parse_bases(header: str) -> list[str]:
    """Base types declared in a class-like header, in source order.

    ``header`` is the neutralized text between the declared name and the
    opening brace, e.g. ``" extends User implements A, B"``.

    Two PHP-specific rules are load-bearing here:

    * A **backed enum** writes its storage type where a base would sit --
      ``enum Status: string implements X``. Anchoring on the ``extends`` /
      ``implements`` keywords (rather than "everything after the name") keeps
      ``string`` out of the base list; it is a backing type, not a parent.
    * ``use T;`` inside the class *body* is trait composition, not
      inheritance, and never reaches this function -- it is not in the header.

    Traits themselves declare no parents, so they yield ``[]``.
    """
    bases: list[str] = []
    for pattern in (_EXTENDS_RE, _IMPLEMENTS_RE):
        m = pattern.search(header)
        if not m:
            continue
        for raw in _BASE_NAME_RE.findall(m.group("names")):
            name = raw.lstrip("\\")  # \App\Contracts\X and App\Contracts\X are one type
            if name and name not in bases:
                bases.append(name)
    return bases


def _match(text: str, open_idx: int, opener: str, closer: str) -> int:
    depth = 0
    k, n = open_idx, len(text)
    while k < n:
        c = text[k]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return n - 1


def extract_php_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a PHP file."""
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    neu = _neutralize(raw, blank_strings=True)    # structure scan
    cmd = _neutralize(raw, blank_strings=False)   # keeps string literals
    lbs = _line_byte_starts(content)
    n = len(neu)

    def cb(char_idx: int) -> int:
        return len(raw[:char_idx].encode("utf-8"))

    # brace depth at every offset — used to keep `use` at namespace level only
    depth = [0] * (n + 1)
    d = 0
    for i, c in enumerate(neu):
        depth[i] = d
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
    depth[n] = d

    ns_match = _NS_RE.search(neu)
    namespace = ns_match.group("ns") if ns_match else None

    items: list[dict] = []
    class_spans: list[tuple[str, int, int]] = []

    for m in _CLASSLIKE_RE.finditer(neu):
        brace = neu.find("{", m.end())
        if brace == -1:
            continue
        close = _match(neu, brace, "{", "}")
        b_start, b_end = cb(m.start()), cb(close + 1)
        item = {
            "kind": m.group("kw").lower(),
            "name": m.group("name"),
            "parent": None,
            "line_start": _line_of(b_start, lbs),
            "line_end": _line_of(max(b_start, b_end - 1), lbs),
            "byte_start": b_start,
            "byte_end": b_end,
            "signature": _collapse(raw[m.start():brace])[:120],
        }
        # Inheritance (BL-037). Parse the *neutralized* header so a comment or
        # a string between the name and the brace contributes no base. Peer
        # contract (java, dart, ...): the key is present only when non-empty.
        bases = _parse_bases(neu[m.end():brace])
        if bases:
            item["bases"] = bases
        items.append(item)
        class_spans.append((m.group("name"), m.start(), close + 1))

    for m in _FUNC_RE.finditer(neu):
        p_open = m.end() - 1
        p_close = _match(neu, p_open, "(", ")")
        k = p_close + 1
        while k < n and neu[k] not in "{;":
            k += 1
        if k >= n:
            continue
        if neu[k] == "{":
            end = _match(neu, k, "{", "}") + 1
        else:
            end = k + 1

        parent = None
        best = -1
        for name, cs, ce in class_spans:
            if cs <= m.start() < ce and cs > best:
                parent, best = name, cs

        b_start, b_end = cb(m.start()), cb(end)
        items.append({
            "kind": "method" if parent else "function",
            "name": m.group("name"),
            "parent": parent,
            "line_start": _line_of(b_start, lbs),
            "line_end": _line_of(max(b_start, b_end - 1), lbs),
            "byte_start": b_start,
            "byte_end": b_end,
            "signature": _collapse(raw[m.start():k])[:120],
        })

    imports: list[dict] = []
    for m in _USE_RE.finditer(neu):
        if depth[m.start()] != 0:      # trait composition inside a class body
            continue
        imports.append({
            "kind": "use",
            "source": m.group("fqn"),
            "lineno": _line_of(cb(m.start()), lbs),
        })

    for m in _INCLUDE_RE.finditer(cmd):
        expr = m.group("expr")
        lit = _LITERAL_RE.search(expr)
        if not lit:
            continue
        spec = lit.group("a") if lit.group("a") is not None else lit.group("b")
        if not spec:
            continue
        # `require __DIR__ . '/rel/path.php'` — the literal is a suffix to join
        if "__DIR__" in expr or "dirname" in expr:
            spec = spec.lstrip("/")
        imports.append({
            "kind": "include",
            "source": spec,
            "lineno": _line_of(cb(m.start()), lbs),
        })

    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    summary = {
        "language": "php",
        "extraction_method": "regex",
        "namespace": namespace,
        "imports": imports,
        "items": items,
        "top_level_classes": sorted({
            it["name"] for it in items
            if it["kind"] in ("class", "interface", "trait", "enum")}),
        "top_level_functions": sorted({
            it["name"] for it in items if it["kind"] == "function"}),
    }
    return summary, []


# ---------------------------------------------------------------------------
# composer.json PSR-4 autoload map
# ---------------------------------------------------------------------------


class ComposerManifestError(ValueError):
    """A composer.json that cannot be decoded or parsed as JSON.

    Typed rather than swallowed (BL-038). This function used to return ``{}``
    "for anything unparseable", which made a **corrupt manifest** and a
    manifest with **no psr-4 section** the same value to every caller. The
    index builder then had nothing to disclose, so a broken manifest silently
    un-resolved every ``use`` statement in the package and the bundle reported
    a PHP repository with no internal imports as though that were the truth.

    Libraries raise typed errors; applications degrade gracefully. The caller
    (``_builtins._php_psr4_index``) catches this and records a degradation
    entry, so the loss is disclosed instead of inferred.
    """


def parse_composer_psr4(content: bytes) -> dict[str, str]:
    """Return the merged ``autoload`` + ``autoload-dev`` ``psr-4`` prefix map,
    e.g. ``{"App\\\\": "src/"}``.

    An empty map means the manifest is valid and simply declares no psr-4
    prefixes. A manifest that cannot be decoded or parsed raises
    :class:`ComposerManifestError` — the two are not the same fact and must not
    return the same value.
    """
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise ComposerManifestError(f"{type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise ComposerManifestError(
            f"top-level JSON is {type(data).__name__}, expected an object")
    out: dict[str, str] = {}
    for section in ("autoload", "autoload-dev"):
        block = data.get(section) or {}
        psr4 = block.get("psr-4") or {}
        if not isinstance(psr4, dict):
            continue
        for prefix, target in psr4.items():
            if isinstance(target, list):
                target = target[0] if target else ""
            if isinstance(target, str) and target:
                out[prefix] = target
    return out


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def _normalize_rel(parts: tuple[str, ...]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def _psr4_target(fqn: str, psr4: dict[str, str]) -> str | None:
    """Longest-prefix PSR-4 match: ``App\\Models\\User`` + ``{"App\\": "src/"}``
    → ``src/Models/User.php``."""
    best: str | None = None
    best_len = -1
    for prefix, directory in psr4.items():
        if fqn.startswith(prefix) and len(prefix) > best_len:
            rest = fqn[len(prefix):].replace("\\", "/")
            base = directory if directory.endswith("/") else directory + "/"
            best = _normalize_rel(tuple((base + rest + ".php").split("/")))
            best_len = len(prefix)
    return best


def resolve_php_imports(
    src_path: str,
    summary: dict,
    paths_set: set[str],
    psr4: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve ``use`` (via PSR-4) and ``require``/``include`` (via path) to
    ``(in_repo, external)``. Unresolvable specs are surfaced as external, not
    dropped."""
    psr4 = psr4 or {}
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip()
        if not spec:
            continue
        if imp.get("kind") == "use":
            target = _psr4_target(spec, psr4)
            if target and target in paths_set:
                in_repo.add(target)
            else:
                external.add(spec)
            continue
        if spec.startswith("/"):
            external.add(spec)
            continue
        target = _normalize_rel((src_dir / spec).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)

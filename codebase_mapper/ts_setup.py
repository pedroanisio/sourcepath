"""codebase_mapper.ts_setup."""
from __future__ import annotations

import threading

from pathlib import PurePosixPath

try:
    import tree_sitter as ts
    import tree_sitter_typescript as tst
    import tree_sitter_javascript as tsj
    import tree_sitter_rust as tsr
    import tree_sitter_ruby as tsrb
    import tree_sitter_go as tsgo
    import tree_sitter_c as tsc
    import tree_sitter_cpp as tscpp
    import tree_sitter_kotlin as tsk
    import tree_sitter_swift as tssw
    import tree_sitter_java as tsja
    import tree_sitter_objc as tsobjc
    TS_AVAILABLE = True
except Exception:
    TS_AVAILABLE = False
    # None sentinels so a caller that skips the TS_AVAILABLE guard fails
    # loudly; single-line so the optional-import idiom needs one ignore.
    ts = tst = tsj = tsr = tsrb = tsgo = tsc = tscpp = tsk = tssw = tsja = tsobjc = None  # type: ignore[assignment]



_TS_LANGS: dict[str, "ts.Language"] = {}

_TS_QUERIES: dict[str, "ts.Query"] = {}

# _ts_setup() is called from the pipeline's parallel extraction threads.
# The ready flag flips only after BOTH tables are fully populated — the
# previous dict-truthiness guard let a second thread return mid-population
# and KeyError on a grammar that wasn't loaded yet (observed as silently
# dropped import edges under machine load).
_TS_READY = False
_TS_SETUP_LOCK = threading.Lock()


def _ts_setup() -> None:
    global _TS_READY
    if not TS_AVAILABLE or _TS_READY:
        return
    with _TS_SETUP_LOCK:
        if _TS_READY:
            return
        _ts_populate()
        _TS_READY = True


def _ts_populate() -> None:
    _TS_LANGS["typescript"] = ts.Language(tst.language_typescript())
    _TS_LANGS["tsx"] = ts.Language(tst.language_tsx())
    _TS_LANGS["javascript"] = ts.Language(tsj.language())
    _TS_LANGS["rust"] = ts.Language(tsr.language())
    _TS_LANGS["ruby"] = ts.Language(tsrb.language())
    _TS_LANGS["go"] = ts.Language(tsgo.language())
    _TS_LANGS["c"] = ts.Language(tsc.language())
    _TS_LANGS["cpp"] = ts.Language(tscpp.language())
    _TS_LANGS["objc"] = ts.Language(tsobjc.language())
    _TS_LANGS["kotlin"] = ts.Language(tsk.language())
    _TS_LANGS["swift"] = ts.Language(tssw.language())
    _TS_LANGS["java"] = ts.Language(tsja.language())

    tsjs_q = """
    (import_statement source: (string) @import_src)
    (call_expression
        function: (identifier) @_id
        arguments: (arguments (string) @require_src)
        (#eq? @_id "require"))
    (function_declaration name: (identifier) @func_name)
    (class_declaration name: (type_identifier) @class_name)
    (export_statement (function_declaration name: (identifier) @export_func))
    (export_statement (class_declaration name: (type_identifier) @export_class))
    """
    tsjs_q_js = tsjs_q.replace("(type_identifier)", "(identifier)")
    _TS_QUERIES["typescript"] = ts.Query(_TS_LANGS["typescript"], tsjs_q)
    _TS_QUERIES["tsx"] = ts.Query(_TS_LANGS["tsx"], tsjs_q)
    _TS_QUERIES["javascript"] = ts.Query(_TS_LANGS["javascript"], tsjs_q_js)

    rust_q = """
    (use_declaration argument: (_) @use_arg)
    (mod_item name: (identifier) @mod_name)
    (function_item name: (identifier) @func_name)
    (struct_item name: (type_identifier) @class_name)
    (enum_item name: (type_identifier) @class_name)
    (trait_item name: (type_identifier) @class_name)
    """
    _TS_QUERIES["rust"] = ts.Query(_TS_LANGS["rust"], rust_q)

    ruby_q = """
    (call
        method: (identifier) @_m
        arguments: (argument_list (string) @ruby_str)
        (#match? @_m "^(require|require_relative|load|autoload)$"))
    (method name: (identifier) @func_name)
    (singleton_method name: (identifier) @func_name)
    (class name: (constant) @class_name)
    (module name: (constant) @class_name)
    """
    _TS_QUERIES["ruby"] = ts.Query(_TS_LANGS["ruby"], ruby_q)

    # Go: import_spec holds the package path as an interpreted_string_literal.
    go_q = """
    (import_spec (interpreted_string_literal) @go_import)
    (function_declaration name: (identifier) @func_name)
    (method_declaration name: (field_identifier) @func_name)
    (type_spec name: (type_identifier) @class_name)
    """
    _TS_QUERIES["go"] = ts.Query(_TS_LANGS["go"], go_q)

    # C: #include with either local-quoted (string_literal) or system-bracketed
    # (system_lib_string). The `path:` field carries the include target.
    c_q = """
    (preproc_include path: (string_literal) @c_local_include)
    (preproc_include path: (system_lib_string) @c_system_include)
    (function_definition declarator: (function_declarator declarator: (identifier) @func_name))
    (struct_specifier name: (type_identifier) @class_name)
    """
    _TS_QUERIES["c"] = ts.Query(_TS_LANGS["c"], c_q)

    # C++ — supports namespaces, templates, classes, and out-of-class
    # method definitions (``Type::method``). The analyzer walks more
    # broadly than these captures; the query exists so cheap consumers
    # (e.g. a future "top-level symbol" stat) can read it the same way
    # the other languages do.
    cpp_q = """
    (preproc_include path: (string_literal) @cpp_local_include)
    (preproc_include path: (system_lib_string) @cpp_system_include)
    (namespace_definition (namespace_identifier) @cpp_namespace)
    (class_specifier name: (type_identifier) @class_name)
    (struct_specifier name: (type_identifier) @class_name)
    (union_specifier name: (type_identifier) @class_name)
    (enum_specifier name: (type_identifier) @class_name)
    (function_definition declarator: (function_declarator declarator: (identifier) @func_name))
    """
    _TS_QUERIES["cpp"] = ts.Query(_TS_LANGS["cpp"], cpp_q)

    # Kotlin
    kt_q = """
    (import (qualified_identifier) @kt_import)
    (class_declaration (identifier) @class_name)
    (function_declaration (identifier) @func_name)
    """
    _TS_QUERIES["kotlin"] = ts.Query(_TS_LANGS["kotlin"], kt_q)

    # Swift
    sw_q = """
    (import_declaration (identifier) @sw_import)
    (class_declaration (type_identifier) @class_name)
    (function_declaration (simple_identifier) @func_name)
    """
    _TS_QUERIES["swift"] = ts.Query(_TS_LANGS["swift"], sw_q)

    # Java — captures the package declaration, every import (incl. static
    # and wildcard), every type declaration, and every method/constructor.
    # The Java analyzer further inspects each match to harvest spans,
    # `extends`/`implements` lists, and annotations.
    ja_q = """
    (package_declaration (scoped_identifier) @ja_package)
    (package_declaration (identifier) @ja_package)
    (import_declaration (scoped_identifier) @ja_import)
    (import_declaration (identifier) @ja_import)
    (class_declaration name: (identifier) @class_name)
    (interface_declaration name: (identifier) @class_name)
    (enum_declaration name: (identifier) @class_name)
    (annotation_type_declaration name: (identifier) @class_name)
    (record_declaration name: (identifier) @class_name)
    (method_declaration name: (identifier) @func_name)
    (constructor_declaration name: (identifier) @func_name)
    """
    _TS_QUERIES["java"] = ts.Query(_TS_LANGS["java"], ja_q)

    # Objective-C — the grammar exposes ``preproc_include`` (which also
    # matches ``#import``), ``module_import`` for ``@import Module;``,
    # ``class_interface``/``class_implementation``, ``protocol_declaration``,
    # ``method_declaration``, ``method_definition``, and ``function_definition``.
    # The analyzer walks the AST manually; this query exists for cheap
    # consumers and keeps parity with the other languages.
    objc_q = """
    (preproc_include path: (string_literal) @objc_local_include)
    (preproc_include path: (system_lib_string) @objc_system_include)
    (module_import (identifier) @objc_module_import)
    (class_interface) @objc_class_interface
    (class_implementation) @objc_class_implementation
    (protocol_declaration) @objc_protocol
    """
    _TS_QUERIES["objc"] = ts.Query(_TS_LANGS["objc"], objc_q)

def _ts_grammar_for(path: str) -> str | None:
    p = PurePosixPath(path)
    suffix = p.suffix.lower()
    if suffix == ".tsx":
        return "tsx"
    if suffix in {".ts", ".cts", ".mts"}:
        return "typescript"
    if suffix in {".js", ".jsx", ".cjs", ".mjs"}:
        return "javascript"
    if suffix == ".rs":
        return "rust"
    if suffix in {".rb", ".rake", ".gemspec", ".ru", ".builder", ".ruby"} \
       or p.name in {"Rakefile", "Gemfile", "config.ru"}:
        return "ruby"
    if suffix == ".go":
        return "go"
    if suffix in {".c", ".h"}:
        return "c"
    if suffix in {".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".ipp", ".tpp"}:
        return "cpp"
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    if suffix == ".swift":
        return "swift"
    if suffix == ".java":
        return "java"
    if suffix == ".m":
        return "objc"
    if suffix == ".mm":
        # Objective-C++. The objc grammar parses the ObjC subset
        # correctly; C++ method bodies inside @implementation may show
        # parse_errors but the structural items (classes, methods,
        # imports) are still recovered. A future stage may pair the
        # cpp grammar for the C++ portions.
        return "objc"
    return None

def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in ("'", '"', "`"):
        return s[1:-1]
    return s


def parse_error_diagnostics(root_node) -> list[str]:
    """Quantified parse-error diagnostics for a tree-sitter root node.

    Returns ``[]`` on a clean parse, else the backward-compatible
    ``parse_errors_present`` marker plus ``parse_error_nodes:<N>`` where N
    counts ERROR and missing nodes. A bare boolean cannot distinguish one
    recovered GCC-extension hiccup from a file that half-failed to parse —
    at Linux-kernel scale that flagged 57.7% of C files identically
    (flaw map F8). Consumers threshold on the count; nothing is hidden.
    """
    if not root_node.has_error:
        return []
    n = 0
    cursor = root_node.walk()
    while True:
        node = cursor.node
        if node.is_error or node.is_missing:
            n += 1
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return ["parse_errors_present", f"parse_error_nodes:{n}"]

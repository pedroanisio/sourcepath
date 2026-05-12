"""codebase_mapper.ts_setup."""
from __future__ import annotations

from pathlib import PurePosixPath

try:
    import tree_sitter as ts
    import tree_sitter_typescript as tst
    import tree_sitter_javascript as tsj
    import tree_sitter_rust as tsr
    import tree_sitter_ruby as tsrb
    import tree_sitter_go as tsgo
    import tree_sitter_c as tsc
    import tree_sitter_kotlin as tsk
    import tree_sitter_swift as tssw
    TS_AVAILABLE = True
except Exception:
    TS_AVAILABLE = False
    ts = None
    tst = None
    tsj = None
    tsr = None
    tsrb = None
    tsgo = None
    tsc = None
    tsk = None
    tssw = None



_TS_LANGS: dict[str, "ts.Language"] = {}

_TS_QUERIES: dict[str, "ts.Query"] = {}

def _ts_setup() -> None:
    if not TS_AVAILABLE or _TS_LANGS:
        return
    _TS_LANGS["typescript"] = ts.Language(tst.language_typescript())
    _TS_LANGS["tsx"] = ts.Language(tst.language_tsx())
    _TS_LANGS["javascript"] = ts.Language(tsj.language())
    _TS_LANGS["rust"] = ts.Language(tsr.language())
    _TS_LANGS["ruby"] = ts.Language(tsrb.language())
    _TS_LANGS["go"] = ts.Language(tsgo.language())
    _TS_LANGS["c"] = ts.Language(tsc.language())
    _TS_LANGS["kotlin"] = ts.Language(tsk.language())
    _TS_LANGS["swift"] = ts.Language(tssw.language())

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
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    if suffix == ".swift":
        return "swift"
    return None

def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in ("'", '"', "`"):
        return s[1:-1]
    return s

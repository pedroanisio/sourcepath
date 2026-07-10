"""TDD spec — CFML (ColdFusion Markup Language) Tier-1 extraction.

Grammar reality (verified 2026-07-10 against PyPI): ``tree-sitter-cfml``
ships a maintained wheel (github.com/cfmleditor/tree-sitter-cfml) exposing
``language_cfml`` (tag syntax) and ``language_cfscript`` (script syntax).
The analyzer consumes both: ``.cfc``/``.cfm`` files are sniffed per file
(leading ``<`` → tag grammar, else cfscript), and ``<cfscript>`` islands
inside tag files are re-parsed with the cfscript grammar at their byte/line
offsets.

Contract under test (see plugins/chunks_embeddings/signatures.py): the CFML
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    open-tag text (tag syntax) / declaration header up to
                        the body brace (cfscript), whitespace-collapsed
    params       list[{name, type, default}]   from <cfargument> attributes or
                        cfscript formal parameters; the CFML ``required``
                        marker is not a param field (it survives in signature)
    returns      str | None   ``returntype`` attribute / cfscript return type
    bases        list[str]    component items only: the ``extends`` target
    visibility   str    ``access`` attribute / cfscript access modifier, set
                        only when explicitly written — CFML's implicit
                        ``public`` default is NOT emitted
    type_params  NEVER SET — CFML has no generics
    decorators   NEVER SET — CFML has no decorator syntax

Structural mapping: component/interface → item kind ``component`` /
``interface`` (chunk ``class``); a function inside a component/interface →
kind ``method`` with ``parent`` = component name; a free function (.cfm
template, script outside a component) → kind ``function``. A CFML
component's canonical identifier is its file stem (that is the name
``createObject``/``extends`` resolve), so ``displayname`` is signature
material only.

Import surface: ``<cfinclude template=…>`` / script ``include "…";`` (kind
``cfinclude``), ``<cfimport taglib=…>`` (kind ``cfimport``), script
``import a.b.C;`` (kind ``import``), ``extends="a.b.C"`` (kind ``extends``),
``createObject("component", "a.b.C")`` (kind ``createObject``),
``new a.b.C()`` (kind ``new``), ``<cfobject component="a.b.C">`` (kind
``cfobject``).

Run: python -m pytest tests/test_signatures_cfml.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.cfml import (
    CFML_TS_AVAILABLE,
    extract_cfml_ast_summary,
    resolve_cfml_imports,
)


pytestmark = pytest.mark.skipif(
    not CFML_TS_AVAILABLE, reason="tree-sitter-cfml not available")

NEVER_SET = ("type_params", "decorators", "is_async")


def _summary(src: bytes, path: str = "Greeter.cfc") -> dict:
    summary, errors = extract_cfml_ast_summary(src, path)
    assert summary is not None and not errors
    return summary


def _items(src: bytes, path: str = "Greeter.cfc") -> dict[str, dict]:
    return {i["name"]: i for i in _summary(src, path)["items"]}


# ---------------------------------------------------------------------------
# tag-syntax component
# ---------------------------------------------------------------------------

TAG_COMPONENT = (
    b'<cfcomponent displayname="Greeting service" extends="base.Component">\n'
    b'  <cffunction name="hello" access="public" returntype="string">\n'
    b'    <cfargument name="who" type="string" required="true">\n'
    b'    <cfargument name="times" type="numeric" default="1">\n'
    b'    <cfreturn "hi">\n'
    b'  </cffunction>\n'
    b'  <cffunction name="secret" access="private">\n'
    b'  </cffunction>\n'
    b'</cfcomponent>\n'
)


def test_tag_component_named_by_file_stem():
    summary = _summary(TAG_COMPONENT, "lib/util/Greeter.cfc")
    assert summary["language"] == "cfml"
    assert summary["extraction_method"] == "tree_sitter"
    assert summary["top_level_classes"] == ["Greeter"]
    it = {i["name"]: i for i in summary["items"]}["Greeter"]
    assert it["kind"] == "component"
    assert it["parent"] is None
    assert it["bases"] == ["base.Component"]
    assert it["line_start"] == 1
    assert it["line_end"] == 9


def test_tag_component_extends_surfaces_as_import():
    summary = _summary(TAG_COMPONENT)
    assert {"kind": "extends", "source": "base.Component", "lineno": 1} \
        in summary["imports"]


def test_tag_function_is_method_with_signature_fields():
    items = _items(TAG_COMPONENT)
    hello = items["hello"]
    assert hello["kind"] == "method"
    assert hello["parent"] == "Greeter"
    assert hello["visibility"] == "public"
    assert hello["returns"] == "string"
    assert hello["params"] == [
        {"name": "who", "type": "string", "default": None},
        {"name": "times", "type": "numeric", "default": "1"},
    ]
    assert hello["signature"].startswith("<cffunction")
    assert 'name="hello"' in hello["signature"]
    assert hello["line_start"] == 2
    assert hello["line_end"] == 6


def test_tag_function_span_slices_to_source():
    items = _items(TAG_COMPONENT)
    hello = items["hello"]
    sliced = TAG_COMPONENT[hello["byte_start"]:hello["byte_end"]]
    assert sliced.startswith(b"<cffunction")
    assert sliced.endswith(b"</cffunction>")


def test_tag_function_without_access_omits_visibility():
    src = (
        b'<cfcomponent>\n'
        b'  <cffunction name="plain"></cffunction>\n'
        b'</cfcomponent>\n'
    )
    it = _items(src, "P.cfc")["plain"]
    for absent in ("visibility", "returns", "params") + NEVER_SET:
        assert absent not in it, f"{absent} must be omitted when empty"


def test_tag_secret_function_visibility_private():
    assert _items(TAG_COMPONENT)["secret"]["visibility"] == "private"


def test_top_level_functions_lists_methods():
    summary = _summary(TAG_COMPONENT)
    assert summary["top_level_functions"] == ["hello", "secret"]


def test_uppercase_tags_are_recognized():
    src = (
        b'<CFCOMPONENT>\n'
        b'  <CFFUNCTION NAME="Hi"></CFFUNCTION>\n'
        b'</CFCOMPONENT>\n'
    )
    items = _items(src, "Upper.cfc")
    assert items["Upper"]["kind"] == "component"
    assert items["Hi"]["kind"] == "method"
    assert items["Hi"]["parent"] == "Upper"


# ---------------------------------------------------------------------------
# cfscript-syntax component
# ---------------------------------------------------------------------------

SCRIPT_COMPONENT = (
    b'import cfml.utils.StringHelper;\n'
    b'\n'
    b'component extends="base.Component" accessors="true" {\n'
    b'    public string function greet(required string who, numeric times = 1) {\n'
    b'        var f = new lib.util.Formatter();\n'
    b'        return "hi";\n'
    b'    }\n'
    b'\n'
    b'    private function helper() {\n'
    b'        var svc = createObject("component", "a.b.Service");\n'
    b'    }\n'
    b'}\n'
)


def test_script_component_named_by_file_stem():
    summary = _summary(SCRIPT_COMPONENT, "models/Svc.cfc")
    assert summary["top_level_classes"] == ["Svc"]
    it = {i["name"]: i for i in summary["items"]}["Svc"]
    assert it["kind"] == "component"
    assert it["bases"] == ["base.Component"]


def test_script_function_signature_fields():
    items = _items(SCRIPT_COMPONENT, "Svc.cfc")
    greet = items["greet"]
    assert greet["kind"] == "method"
    assert greet["parent"] == "Svc"
    assert greet["visibility"] == "public"
    assert greet["returns"] == "string"
    assert greet["params"] == [
        {"name": "who", "type": "string", "default": None},
        {"name": "times", "type": "numeric", "default": "1"},
    ]
    assert greet["signature"] == (
        "public string function greet(required string who, numeric times = 1)"
    )


def test_script_function_bare_params_and_private():
    items = _items(SCRIPT_COMPONENT, "Svc.cfc")
    helper = items["helper"]
    assert helper["visibility"] == "private"
    assert "params" not in helper
    assert "returns" not in helper


def test_script_imports_all_forms():
    summary = _summary(SCRIPT_COMPONENT, "Svc.cfc")
    kinds = {(i["kind"], i["source"]) for i in summary["imports"]}
    assert ("import", "cfml.utils.StringHelper") in kinds
    assert ("extends", "base.Component") in kinds
    assert ("new", "lib.util.Formatter") in kinds
    assert ("createObject", "a.b.Service") in kinds


def test_script_interface_is_item_kind_interface():
    src = (
        b'interface {\n'
        b'    public string function greet(required string who);\n'
        b'}\n'
    )
    items = _items(src, "api/IGreeter.cfc")
    assert items["IGreeter"]["kind"] == "interface"
    assert items["greet"]["kind"] == "method"
    assert items["greet"]["parent"] == "IGreeter"


def test_script_bare_param_gets_name_not_type():
    src = b'function topLevel(x) { return x; }\n'
    it = _items(src, "u.cfm")["topLevel"]
    assert it["kind"] == "function"
    assert it["parent"] is None
    assert it["params"] == [{"name": "x", "type": None, "default": None}]


# ---------------------------------------------------------------------------
# .cfm templates: tag imports + cfscript islands
# ---------------------------------------------------------------------------

TEMPLATE = (
    b'<cfinclude template="../shared/header.cfm">\n'
    b'<cfimport taglib="/tags/ui" prefix="ui">\n'
    b'<cfobject component="a.b.Legacy" name="legacy">\n'
    b'<cfset formatter = createObject("component", "lib.util.Formatter")>\n'
    b'<cfscript>\n'
    b'function localAdd(a, b) {\n'
    b'    return a + b;\n'
    b'}\n'
    b'</cfscript>\n'
)


def test_template_tag_imports():
    summary = _summary(TEMPLATE, "views/page.cfm")
    imports = {(i["kind"], i["source"]): i["lineno"] for i in summary["imports"]}
    assert imports[("cfinclude", "../shared/header.cfm")] == 1
    assert imports[("cfimport", "/tags/ui")] == 2
    assert imports[("cfobject", "a.b.Legacy")] == 3
    assert imports[("createObject", "lib.util.Formatter")] == 4


def test_cfscript_island_function_offsets():
    summary = _summary(TEMPLATE, "views/page.cfm")
    it = {i["name"]: i for i in summary["items"]}["localAdd"]
    assert it["kind"] == "function"
    assert it["parent"] is None
    assert it["line_start"] == 6
    assert it["line_end"] == 8
    sliced = TEMPLATE[it["byte_start"]:it["byte_end"]]
    assert sliced.startswith(b"function localAdd")
    assert sliced.endswith(b"}")


def test_script_include_statement_is_cfinclude():
    src = b'include "shared/header.cfm";\n'
    summary = _summary(src, "page.cfm")
    assert {"kind": "cfinclude", "source": "shared/header.cfm", "lineno": 1} \
        in summary["imports"]


def test_island_inside_component_parents_functions():
    src = (
        b'<cfcomponent>\n'
        b'<cfscript>\n'
        b'function scripted() { return 1; }\n'
        b'</cfscript>\n'
        b'</cfcomponent>\n'
    )
    items = _items(src, "Mixed.cfc")
    assert items["scripted"]["kind"] == "method"
    assert items["scripted"]["parent"] == "Mixed"


# ---------------------------------------------------------------------------
# never-set fields / item shape invariants
# ---------------------------------------------------------------------------

def test_never_set_fields_are_never_present():
    for src, path in ((TAG_COMPONENT, "G.cfc"),
                      (SCRIPT_COMPONENT, "S.cfc"),
                      (TEMPLATE, "t.cfm")):
        for name, it in _items(src, path).items():
            for key in NEVER_SET:
                assert key not in it, f"{key} must never be set on {name!r}"


def test_item_span_fields_always_present():
    for it in _items(TAG_COMPONENT).values():
        for key in ("kind", "name", "parent", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it


# ---------------------------------------------------------------------------
# degradation: malformed / undecodable input
# ---------------------------------------------------------------------------

def test_malformed_tag_soup_degrades_gracefully():
    src = b'<cffunction name="broken\n<cfargument\n'
    summary, errors = extract_cfml_ast_summary(src, "broken.cfm")
    assert summary is not None
    assert "parse_errors_present" in errors


def test_invalid_utf8_reports_decode_error():
    summary, errors = extract_cfml_ast_summary(b"\xff\xfe<cfset x=1>", "b.cfm")
    assert summary is None
    assert errors and errors[0].startswith("decode_error:")


def test_empty_file_yields_empty_summary_without_errors():
    summary, errors = extract_cfml_ast_summary(b"", "empty.cfm")
    assert summary is not None and not errors
    assert summary["items"] == []
    assert summary["imports"] == []


# ---------------------------------------------------------------------------
# import resolution
# ---------------------------------------------------------------------------

def _resolve(imports: list[dict], src_path: str, paths: set[str]):
    return resolve_cfml_imports(
        src_path, {"imports": imports}, paths)


def test_resolve_cfinclude_relative():
    in_repo, external = _resolve(
        [{"kind": "cfinclude", "source": "../shared/header.cfm", "lineno": 1}],
        "app/views/page.cfm",
        {"app/views/page.cfm", "app/shared/header.cfm"})
    assert in_repo == ["app/shared/header.cfm"]
    assert external == []


def test_resolve_cfinclude_webroot_absolute():
    in_repo, external = _resolve(
        [{"kind": "cfinclude", "source": "/layout/main.cfm", "lineno": 1}],
        "views/page.cfm",
        {"views/page.cfm", "layout/main.cfm"})
    assert in_repo == ["layout/main.cfm"]


def test_unresolvable_cfinclude_is_dropped_not_external():
    in_repo, external = _resolve(
        [{"kind": "cfinclude", "source": "missing.cfm", "lineno": 1}],
        "page.cfm", {"page.cfm"})
    assert in_repo == [] and external == []


def test_resolve_dotted_component_path_from_root():
    for kind in ("import", "extends", "createObject", "new", "cfobject"):
        in_repo, external = _resolve(
            [{"kind": kind, "source": "a.b.Widget", "lineno": 1}],
            "app/Svc.cfc",
            {"app/Svc.cfc", "a/b/Widget.cfc"})
        assert in_repo == ["a/b/Widget.cfc"], kind
        assert external == [], kind


def test_resolve_dotted_component_path_relative_to_source_dir():
    in_repo, _ = _resolve(
        [{"kind": "createObject", "source": "model.User", "lineno": 1}],
        "app/Svc.cfc",
        {"app/Svc.cfc", "app/model/User.cfc"})
    assert in_repo == ["app/model/User.cfc"]


def test_resolve_bare_extends_to_sibling():
    in_repo, _ = _resolve(
        [{"kind": "extends", "source": "Base", "lineno": 1}],
        "app/models/Svc.cfc",
        {"app/models/Svc.cfc", "app/models/Base.cfc"})
    assert in_repo == ["app/models/Base.cfc"]


def test_unresolved_dotted_path_surfaces_package_root_external():
    in_repo, external = _resolve(
        [{"kind": "extends", "source": "coldbox.system.EventHandler",
          "lineno": 1}],
        "handlers/Main.cfc", {"handlers/Main.cfc"})
    assert in_repo == []
    assert external == ["coldbox"]


def test_wildcard_import_resolves_direct_cfc_children():
    in_repo, external = _resolve(
        [{"kind": "import", "source": "a.b.*", "lineno": 1}],
        "Svc.cfc",
        {"Svc.cfc", "a/b/One.cfc", "a/b/Two.cfc", "a/b/deep/Three.cfc",
         "a/b/readme.md"})
    assert in_repo == ["a/b/One.cfc", "a/b/Two.cfc"]
    assert external == []


def test_resolve_cfimport_taglib_directory():
    in_repo, external = _resolve(
        [{"kind": "cfimport", "source": "/tags/ui", "lineno": 1}],
        "views/page.cfm",
        {"views/page.cfm", "tags/ui/button.cfm", "tags/ui/card.cfc",
         "tags/ui/deep/inner.cfm"})
    assert in_repo == ["tags/ui/button.cfm", "tags/ui/card.cfc"]
    assert external == []


def test_self_import_is_not_an_edge():
    in_repo, _ = _resolve(
        [{"kind": "cfinclude", "source": "page.cfm", "lineno": 1}],
        "page.cfm", {"page.cfm"})
    assert in_repo == []


# ---------------------------------------------------------------------------
# L2 chunker integration (items-based, like dart/cobol)
# ---------------------------------------------------------------------------

def _record(path: str, content: bytes):
    from codebase_mapper.inspection.models import FileRecord
    rec = FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=len(content), language="cfml", type_="source_code",
        phases=["runtime"], atime=None, mtime=None, ctime=None,
        git_commit_time=None,
    )
    summary, _errors = extract_cfml_ast_summary(content, path)
    rec.ast_summary = summary
    return rec


def test_chunker_emits_symbol_level_chunks():
    from plugins.chunks_embeddings.chunker import _chunk_cfml
    rec = _record("Greeter.cfc", TAG_COMPONENT)
    chunks = _chunk_cfml(TAG_COMPONENT, rec)
    by_symbol = {c["symbol"]: c for c in chunks}
    assert by_symbol["Greeter"]["kind"] == "class"
    assert by_symbol["hello"]["kind"] == "method"
    assert by_symbol["hello"]["parent_symbol"] == "Greeter"
    # items-based chunkers slice whole lines (house convention — see
    # _chunk_dart/_chunk_cobol), so leading indentation is retained.
    assert by_symbol["hello"]["text"].lstrip().startswith("<cffunction")
    # signature fields copied through onto the chunk
    assert by_symbol["hello"]["visibility"] == "public"
    assert by_symbol["hello"]["returns"] == "string"


def test_chunker_falls_back_to_whole_file():
    from plugins.chunks_embeddings.chunker import _chunk_cfml
    content = b"<html><p>static page, nothing chunkable</p></html>\n"
    rec = _record("static.cfm", content)
    chunks = _chunk_cfml(content, rec)
    assert len(chunks) == 1
    assert chunks[0]["kind"] == "file"
    assert chunks[0]["symbol"] == "<file>"

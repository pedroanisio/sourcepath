"""TDD spec — Objective-C signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the ObjC
analyzer is an items-based producer, so it must place the canonical signature
fields directly on ``ast_summary["items"]`` records; the L2 chunker copies
them onto chunks via ``signature_fields_from_item``.

    signature  str    declaration header as written, single-line-collapsed,
                      excluding the body ``{`` / trailing ``;``
    params     list[{name, type, default}]   one entry per selector segment
                      that takes an argument; ``default`` is always None
                      (ObjC has no default arguments)
    returns    str | None   the return type in the leading parens, as written
    bases      list[str]    [superclass] + adopted protocols, as written
    type_params  list[str]  lightweight generics (`Box<ObjectType>`), only
                      when positionally unambiguous
    visibility / is_async / decorators — never emitted for ObjC

Fields are OMITTED when empty/unknown — never emitted as empty lists or None
placeholders. Existing item fields (kind, name, parent, selector, spans,
extends, implements) must remain exactly as before.

Run: python -m pytest tests/test_signatures_objc.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.objc import extract_objc_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")


DOWNLOADER_SRC = b"""#import <Foundation/Foundation.h>

@interface Downloader : NSObject <NSURLSessionDelegate, NSCopying>
- (void)downloadURL:(NSURL *)url completion:(void (^)(NSData *))handler;
+ (instancetype)sharedDownloader;
- (NSInteger)retryCount;
@end

@implementation Downloader
- (void)downloadURL:(NSURL *)url
         completion:(void (^)(NSData *))handler {
    handler(nil);
}
+ (instancetype)sharedDownloader { return nil; }
- (NSInteger)retryCount { return 3; }
@end
"""


def _items(src: bytes, path: str = "m.m") -> list[dict]:
    summary, _errors = extract_objc_ast_summary(src, path)
    assert summary is not None
    return summary["items"]


def _one(items: list[dict], kind: str, name: str) -> dict:
    matches = [it for it in items if it["kind"] == kind and it["name"] == name]
    assert len(matches) == 1, f"expected one ({kind}, {name}), got {len(matches)}"
    return matches[0]


def _methods(items: list[dict], name: str) -> list[dict]:
    return [it for it in items if it["kind"] == "method" and it["name"] == name]


# ---------------------------------------------------------------------------
# types — @interface / @implementation / category / @protocol
# ---------------------------------------------------------------------------
def test_interface_bases_and_signature():
    items = _items(DOWNLOADER_SRC)
    it = _one(items, "class_interface", "Downloader")
    assert it["bases"] == ["NSObject", "NSURLSessionDelegate", "NSCopying"]
    assert it["signature"] == (
        "@interface Downloader : NSObject <NSURLSessionDelegate, NSCopying>"
    )


def test_implementation_signature_and_omitted_bases():
    items = _items(DOWNLOADER_SRC)
    it = _one(items, "class_implementation", "Downloader")
    assert it["signature"] == "@implementation Downloader"
    assert "bases" not in it, "an @implementation states no supertypes"


def test_category_bases_and_signature():
    src = (
        b"@interface NSString (Greet) <NSCopying>\n"
        b"- (NSString *)greet:(NSString *)name;\n"
        b"@end\n"
        b"\n"
        b"@implementation NSString (Greet)\n"
        b"- (NSString *)greet:(NSString *)name { return name; }\n"
        b"@end\n"
    )
    items = _items(src)
    cat = _one(items, "category", "NSString(Greet)")
    assert cat["signature"] == "@interface NSString (Greet) <NSCopying>"
    assert cat["bases"] == ["NSCopying"]
    impl = _one(items, "category_impl", "NSString(Greet)")
    assert impl["signature"] == "@implementation NSString (Greet)"
    assert "bases" not in impl


def test_protocol_bases_and_signature():
    src = (
        b"@protocol Walker <NSObject, NSCopying>\n"
        b"- (void)walk;\n"
        b"@end\n"
    )
    items = _items(src)
    it = _one(items, "protocol", "Walker")
    assert it["signature"] == "@protocol Walker <NSObject, NSCopying>"
    assert it["bases"] == ["NSObject", "NSCopying"]


def test_lightweight_generics_do_not_pollute_bases():
    src = (
        b"@interface Box<ObjectType> : NSObject <NSCopying>\n"
        b"- (ObjectType)take;\n"
        b"@end\n"
    )
    items = _items(src)
    it = _one(items, "class_interface", "Box")
    assert it["bases"] == ["NSObject", "NSCopying"]
    assert it["type_params"] == ["ObjectType"]
    assert it["signature"] == "@interface Box<ObjectType> : NSObject <NSCopying>"


def test_interface_header_signature_stops_before_ivar_block():
    src = (
        b"@interface Foo : NSObject {\n"
        b"    int _x;\n"
        b"}\n"
        b"- (void)go;\n"
        b"@end\n"
    )
    items = _items(src)
    it = _one(items, "class_interface", "Foo")
    assert it["signature"] == "@interface Foo : NSObject"
    assert it["bases"] == ["NSObject"]


# ---------------------------------------------------------------------------
# methods — declarations and definitions
# ---------------------------------------------------------------------------
def test_instance_method_multisegment_params_returns_signature():
    items = _items(DOWNLOADER_SRC)
    decl, definition = _methods(items, "downloadURL")
    for it in (decl, definition):
        assert it["returns"] == "void"
        assert it["params"] == [
            {"name": "url", "type": "NSURL *", "default": None},
            {"name": "handler", "type": "void (^)(NSData *)", "default": None},
        ]
        # The definition spans two physical lines; both collapse to the
        # same single-line header, excluding the ``;`` / body ``{``.
        assert it["signature"] == (
            "- (void)downloadURL:(NSURL *)url "
            "completion:(void (^)(NSData *))handler"
        )


def test_class_method_signature_and_returns():
    items = _items(DOWNLOADER_SRC)
    decl, definition = _methods(items, "sharedDownloader")
    for it in (decl, definition):
        assert it["signature"] == "+ (instancetype)sharedDownloader"
        assert it["returns"] == "instancetype"
        assert "params" not in it, "no selector segment takes an argument"


def test_unlabeled_selector_segment_params():
    src = (
        b"@interface Grid : NSObject\n"
        b"- (void)moveTo:(int)x :(int)y;\n"
        b"@end\n"
    )
    items = _items(src)
    (it,) = _methods(items, "moveTo")
    assert it["signature"] == "- (void)moveTo:(int)x :(int)y"
    assert it["returns"] == "void"
    assert it["params"] == [
        {"name": "x", "type": "int", "default": None},
        {"name": "y", "type": "int", "default": None},
    ]


# ---------------------------------------------------------------------------
# omission contract + existing-field preservation
# ---------------------------------------------------------------------------
def test_omission_contract_no_placeholder_fields():
    items = _items(DOWNLOADER_SRC)
    assert items, "analyzer produced no items"
    for it in items:
        for absent in ("visibility", "is_async", "decorators"):
            assert absent not in it, f"{absent} must never be emitted for ObjC"
        for key in ("signature", "params", "returns", "bases", "type_params"):
            assert it.get(key) != [] and it.get(key) is not None or key not in it, (
                f"{key} must be omitted when empty, not a placeholder"
            )
        for p in it.get("params", []):
            assert p["default"] is None, "ObjC has no default arguments"


def test_existing_item_fields_preserved():
    items = _items(DOWNLOADER_SRC)
    decl, definition = _methods(items, "downloadURL")
    for it in (decl, definition):
        assert it["parent"] == "Downloader"
        assert it["selector"] == "downloadURL:completion:"
        assert DOWNLOADER_SRC[it["byte_start"]:it["byte_end"]].decode().startswith(
            "- (void)downloadURL:"
        )
        for key in ("line_start", "line_end", "byte_start", "byte_end"):
            assert isinstance(it[key], int)
    iface = _one(items, "class_interface", "Downloader")
    assert iface["extends"] == "NSObject"
    assert iface["implements"] == ["NSURLSessionDelegate", "NSCopying"]
    assert iface["parent"] is None

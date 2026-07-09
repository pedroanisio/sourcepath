"""TDD regression — ``.m`` dialect sniff + evidence-based ObjC header retag.

Bug (observed on a real Linux-kernel run): a single GNU Octave script,
``tools/testing/selftests/cgroup/memcg_protection.m``, was tagged
"objective-c" purely by its ``.m`` extension (shared_kernel/constants.py
``LANG_BY_EXT``). That one file made ``refine_objc_header_languages``'s
``has_objc_source`` gate true, and its project-wide fallback — "retag any
``.h`` tagged 'c' whose directory has no sibling ``.c``" — then flipped
13,537 header-only-directory kernel headers (include/, dt-bindings,
asic_reg, ...) from "c" to "objective-c". Those headers were parsed with
the ObjC grammar and silently dropped from L4 enrichment.

Contract under test:

  1. ``language_of(path, content_head)`` — a ``.m`` file whose content
     carries MATLAB/Octave signals (``%`` line comments, ``function`` defs)
     and no C-family/ObjC markers is NOT Objective-C. A ``.m`` file with
     ObjC markers (``#import``, ``@interface``, ...) stays "objective-c",
     as does a ``.m`` with no content evidence at all (extension default).
  2. ``refine_objc_header_languages(records, read_content)`` — the
     project-wide fallback retag fires only on positive ObjC evidence
     inside the header itself (``@interface`` / ``@protocol`` / ``#import``
     / ...). Without a content reader the fallback is inert (no evidence
     available means no retag). The same-directory sibling rule — a ``.h``
     in a directory that contains ObjC sources — is unchanged and needs no
     content evidence.
  3. Genuine ObjC repos keep working: ``Foo.h`` next to ``Foo.m`` retags;
     an ``include/`` header-only split with ``@interface`` content retags.

Run: python -m pytest tests/test_objc_header_retag.py
"""
from __future__ import annotations

from codebase_mapper.inspection.classify import language_of
from codebase_mapper.inspection.languages.objc import (
    OBJC_LANGUAGE_TAGS,
    refine_objc_header_languages,
)
from codebase_mapper.inspection.models import FileRecord


# ---------------------------------------------------------------------------
# Content fixtures
# ---------------------------------------------------------------------------

# Representative of tools/testing/selftests/cgroup/memcg_protection.m:
# an Octave script — ``%`` line comments, a MATLAB-style function def,
# no ObjC markers anywhere.
OCTAVE_M = b"""\
% SPDX-License-Identifier: GPL-2.0
%
% This script simulates reclaim protection behavior on a single level of
% memcg hierarchy to illustrate how overcommitted protection spreads among
% siblings.
%
% Run as: octave-cli memcg_protection.m

function protected = effective_protection(usage, protection)
  protected = min(usage, protection);
end

usage = [10240, 10240, 10240];
protection = effective_protection(usage, [5120, 5120, 5120]);
disp(protection);
"""

# A kernel-style pure-C header: include guards, #include, struct — and
# NO ObjC markers.
KERNEL_C_HEADER = b"""\
/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LINUX_FOO_H
#define _LINUX_FOO_H

#include <linux/types.h>

struct foo_dev {
\tunsigned int flags;
};

static inline int foo_ready(const struct foo_dev *d)
{
\treturn d->flags != 0;
}

#endif /* _LINUX_FOO_H */
"""

# A genuine ObjC header: @interface ... @end.
OBJC_HEADER = b"""\
#import <Foundation/Foundation.h>

@interface Foo : NSObject
- (instancetype)initWithName:(NSString *)name;
@end
"""

# A genuine ObjC implementation file.
OBJC_IMPL = b"""\
#import "Foo.h"

@implementation Foo
- (instancetype)initWithName:(NSString *)name { return self; }
@end
"""

# A C-looking header that ships in the SAME directory as a .m file
# (Apple convention: sibling rule retags it without content evidence).
PLAIN_SIBLING_HEADER = b"""\
/* legacy C-style declarations shipped next to Foo.m */
#include <stdint.h>
typedef struct foo_ctx foo_ctx;
"""


def _record(path: str, lang: str | None, type_: str = "source_code") -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="", content_sha256="", size_bytes=100,
        language=lang, type_=type_, phases=["runtime"],
    )


# ---------------------------------------------------------------------------
# 1. Classification-time dialect sniff (language_of)
# ---------------------------------------------------------------------------


def test_language_of_octave_m_content_is_not_objc() -> None:
    lang = language_of(
        "tools/testing/selftests/cgroup/memcg_protection.m", OCTAVE_M[:8192],
    )
    assert lang not in OBJC_LANGUAGE_TAGS, (
        f"Octave-content .m classified as {lang!r}; a MATLAB/Octave script "
        "must not be tagged Objective-C"
    )


def test_language_of_genuine_objc_m_stays_objc() -> None:
    assert language_of("Animals/Foo.m", OBJC_IMPL[:8192]) == "objective-c"


def test_language_of_m_without_content_defaults_to_objc() -> None:
    # Back-compat: extension-only call sites keep the extension default.
    assert language_of("Foo.m") == "objective-c"


def test_language_of_mm_unaffected() -> None:
    assert language_of("Foo.mm", OBJC_IMPL[:8192]) == "objective-cpp"


# ---------------------------------------------------------------------------
# 2. Kernel scenario — Octave .m + header-only directories stay "c"
# ---------------------------------------------------------------------------


def test_kernel_headers_not_retagged_by_octave_m() -> None:
    contents = {
        "tools/testing/selftests/cgroup/memcg_protection.m": OCTAVE_M,
        # Header-only directories: no sibling .c anywhere.
        "include/linux/foo.h": KERNEL_C_HEADER,
        "include/dt-bindings/clock/bar.h": KERNEL_C_HEADER,
        "drivers/gpu/drm/amd/include/asic_reg/gc/gc_9_0_offset.h": KERNEL_C_HEADER,
    }
    # Pipeline-faithful record construction: language from path + head sniff.
    records = [
        _record(p, language_of(p, c[:8192])) for p, c in contents.items()
    ]

    refine_objc_header_languages(records, contents.__getitem__)

    headers = [r for r in records if r.path.endswith(".h")]
    assert headers, "fixture must contain headers"
    flipped = [(r.path, r.language) for r in headers if r.language != "c"]
    assert not flipped, (
        f"header-only-directory kernel headers were retagged: {flipped}"
    )


def test_fallback_is_inert_without_content_reader() -> None:
    # Even when a .m record IS tagged objective-c (however it got there),
    # the project-wide fallback must not blanket-retag headers it has no
    # evidence for. Old-signature call: no reader, no evidence, no retag.
    records = [
        _record("src/whatever.m", "objective-c"),
        _record("include/plain.h", "c"),
    ]
    refine_objc_header_languages(records)
    plain = next(r for r in records if r.path == "include/plain.h")
    assert plain.language == "c", (
        "project-wide fallback retagged include/plain.h to "
        f"{plain.language!r} with no ObjC evidence in the header"
    )


def test_fallback_skips_header_without_objc_evidence_despite_real_objc() -> None:
    # A repo that genuinely contains ObjC must still not swallow unrelated
    # pure-C headers in header-only directories.
    contents = {
        "app/Foo.m": OBJC_IMPL,
        "app/Foo.h": OBJC_HEADER,
        "vendor/include/foo_dev.h": KERNEL_C_HEADER,
    }
    records = [
        _record(p, language_of(p, c[:8192])) for p, c in contents.items()
    ]
    refine_objc_header_languages(records, contents.__getitem__)
    vendor = next(r for r in records if r.path == "vendor/include/foo_dev.h")
    assert vendor.language == "c", (
        f"pure-C header retagged to {vendor.language!r} despite carrying "
        "no ObjC markers"
    )


# ---------------------------------------------------------------------------
# 3. Genuine ObjC repos — zero regressions
# ---------------------------------------------------------------------------


def test_sibling_rule_still_retags_co_resident_header() -> None:
    # Foo.h next to Foo.m: Apple convention, retag WITHOUT content
    # evidence (the sibling rule is directory-level).
    contents = {
        "Animals/Foo.m": OBJC_IMPL,
        "Animals/Foo.h": PLAIN_SIBLING_HEADER,
    }
    records = [
        _record(p, language_of(p, c[:8192])) for p, c in contents.items()
    ]
    refine_objc_header_languages(records, contents.__getitem__)
    header = next(r for r in records if r.path == "Animals/Foo.h")
    assert header.language == "objective-c", (
        f"sibling-rule header stayed {header.language!r}"
    )


def test_fallback_retags_split_header_with_objc_evidence() -> None:
    # include/ vs src/ split: the header lives in a directory with no
    # ObjC sibling and no .c sibling, but its content is unmistakably
    # ObjC — the fallback must still retag it.
    contents = {
        "src/Foo.m": OBJC_IMPL,
        "include/Foo.h": OBJC_HEADER,
    }
    records = [
        _record(p, language_of(p, c[:8192])) for p, c in contents.items()
    ]
    refine_objc_header_languages(records, contents.__getitem__)
    header = next(r for r in records if r.path == "include/Foo.h")
    assert header.language == "objective-c", (
        f"split ObjC header stayed {header.language!r}"
    )


def test_fallback_still_suppressed_by_c_sibling() -> None:
    # A directory with a real .c file keeps its headers C even if the
    # header, for some reason, carries an ObjC-looking marker.
    contents = {
        "src/Foo.m": OBJC_IMPL,
        "lib/util.c": b"#include \"util.h\"\nint util(void) { return 1; }\n",
        "lib/util.h": KERNEL_C_HEADER,
    }
    records = [
        _record(p, language_of(p, c[:8192])) for p, c in contents.items()
    ]
    refine_objc_header_languages(records, contents.__getitem__)
    header = next(r for r in records if r.path == "lib/util.h")
    assert header.language == "c"

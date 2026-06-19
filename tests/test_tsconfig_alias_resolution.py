"""Regression tests for TypeScript/JavaScript path-alias resolution.

Background: an earlier ``load_tsconfigs`` only read files literally named
``tsconfig.json`` / ``tsconfig.base.json`` and consumed ``compilerOptions.paths``
directly off them. That missed the *default* Vite scaffold layout, where the
root ``tsconfig.json`` is a paths-less solution file and the real ``paths`` live
in a referenced/extended ``tsconfig.app.json``. The symptom on real repos was a
deflated internal-import graph: aliased ``@/...`` imports resolved to nothing
and were dropped (never an internal edge, and — being undeclared packages — not
an external edge either), so ``imported_by`` / centrality understated the true
graph and key components falsely reported zero importers.

These tests pin the fixed behavior:

  * ``tsconfig.app.json`` / any ``tsconfig*.json`` / ``jsconfig.json`` are loaded.
  * ``extends`` chains (string and array forms) inherit ``baseUrl`` / ``paths``.
  * a paths-less root config never shadows a sibling that declares ``paths``.
  * package-specifier ``extends`` (node_modules, untracked) is skipped, not fatal.
  * relative imports and genuinely-unresolvable aliases keep their prior behavior.

Run from the repo root:  python3 -m pytest tests/test_tsconfig_alias_resolution.py
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from codebase_mapper.inspection.languages.tsjs import (
    _strip_jsonc_comments,
    find_governing_tsconfig,
    load_tsconfigs,
    resolve_tsjs_import,
)


def _world(files: dict[str, str]):
    """Build the (tsconfigs, paths_set) a resolver call needs from a path->text map.

    ``load_tsconfigs`` only reads ``record.path`` and the ``read`` callable, so a
    SimpleNamespace stands in for FileRecord without dragging in the full model.
    """
    records = [SimpleNamespace(path=p) for p in files]
    blobs = {p: c.encode("utf-8") for p, c in files.items()}

    def read(path: str) -> bytes:
        return blobs[path]

    tsconfigs = load_tsconfigs(records, read)
    return tsconfigs, set(files)


def test_vite_app_config_paths_are_resolved():
    """The canonical failing case: paths declared in tsconfig.app.json, not the
    paths-less root tsconfig.json that only holds project references."""
    files = {
        "tsconfig.json": json.dumps(
            {"files": [], "references": [{"path": "./tsconfig.app.json"}]}
        ),
        "tsconfig.app.json": json.dumps(
            {
                "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}},
                "include": ["src"],
            }
        ),
        "src/components/Button.tsx": "export const Button = () => null;",
        "src/lib/util.ts": "export const x = 1;",
    }
    tsconfigs, paths_set = _world(files)
    dst = resolve_tsjs_import(
        "src/components/Button.tsx", "@/lib/util", paths_set, tsconfigs
    )
    assert dst == "src/lib/util.ts"


def test_extends_chain_inherits_paths():
    """A config that declares no paths of its own inherits them from the base
    config it ``extends`` (string form)."""
    files = {
        "tsconfig.json": json.dumps(
            {"extends": "./tsconfig.base.json", "compilerOptions": {}}
        ),
        "tsconfig.base.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}
        ),
        "src/core/engine.ts": "export const e = 1;",
        "src/main.ts": "import { e } from '@app/core/engine';",
    }
    tsconfigs, paths_set = _world(files)
    dst = resolve_tsjs_import("src/main.ts", "@app/core/engine", paths_set, tsconfigs)
    assert dst == "src/core/engine.ts"


def test_extends_array_form_merges_left_to_right():
    """TS 5.0 array ``extends``: later entries override earlier; the inheriting
    config overrides all. Here the alias comes from the second base."""
    files = {
        "tsconfig.json": json.dumps(
            {"extends": ["./tsconfig.flags.json", "./tsconfig.paths.json"]}
        ),
        "tsconfig.flags.json": json.dumps(
            {"compilerOptions": {"strict": True}}
        ),
        "tsconfig.paths.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["src/*"]}}}
        ),
        "src/widget.ts": "export const w = 1;",
        "src/app.ts": "import { w } from '~/widget';",
    }
    tsconfigs, paths_set = _world(files)
    dst = resolve_tsjs_import("src/app.ts", "~/widget", paths_set, tsconfigs)
    assert dst == "src/widget.ts"


def test_pathsless_root_does_not_shadow_sibling_with_paths():
    """Two configs govern at the same directory depth; the one declaring paths
    must win the tie so the alias resolves."""
    files = {
        "tsconfig.json": json.dumps({"compilerOptions": {"strict": True}}),
        "tsconfig.app.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["src/*"]}}}
        ),
        "src/x.ts": "export const x = 1;",
        "src/y.ts": "import { x } from '~/x';",
    }
    tsconfigs, paths_set = _world(files)
    cfg = find_governing_tsconfig("src/y.ts", tsconfigs)
    assert cfg is not None and cfg["paths"], "must pick the config that declares paths"
    assert resolve_tsjs_import("src/y.ts", "~/x", paths_set, tsconfigs) == "src/x.ts"


def test_jsconfig_paths_resolved():
    """JS projects use jsconfig.json for the same alias mechanism."""
    files = {
        "jsconfig.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}
        ),
        "src/util.js": "export const u = 1;",
        "src/app.js": "import { u } from '@/util';",
    }
    tsconfigs, paths_set = _world(files)
    assert resolve_tsjs_import("src/app.js", "@/util", paths_set, tsconfigs) == "src/util.js"


def test_nested_package_root_governs_over_repo_root():
    """A deeper config (monorepo package) governs files under it, even when a
    shallower root config also matches."""
    files = {
        "tsconfig.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["other/*"]}}}
        ),
        "packages/web/tsconfig.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}
        ),
        "packages/web/src/hooks.ts": "export const h = 1;",
        "packages/web/src/page.ts": "import { h } from '@/hooks';",
    }
    tsconfigs, paths_set = _world(files)
    dst = resolve_tsjs_import(
        "packages/web/src/page.ts", "@/hooks", paths_set, tsconfigs
    )
    assert dst == "packages/web/src/hooks.ts"


def test_package_extends_is_skipped_without_error():
    """``extends`` to a published preset lives under node_modules (untracked),
    so it is unresolvable — it must be skipped silently, not raise, and the
    config's own paths must still work."""
    files = {
        "tsconfig.json": json.dumps(
            {
                "extends": "@tsconfig/node18/tsconfig.json",
                "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}},
            }
        ),
        "src/a.ts": "export const a = 1;",
        "src/b.ts": "import { a } from '@/a';",
    }
    tsconfigs, paths_set = _world(files)  # must not raise
    assert resolve_tsjs_import("src/b.ts", "@/a", paths_set, tsconfigs) == "src/a.ts"


def test_unresolvable_alias_returns_none():
    """An alias whose target file does not exist resolves to None (the import is
    then dropped, never miscounted as an internal edge)."""
    files = {
        "tsconfig.app.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}
        ),
        "src/real.ts": "export const r = 1;",
        "src/app.ts": "import { ghost } from '@/does/not/exist';",
    }
    tsconfigs, paths_set = _world(files)
    assert (
        resolve_tsjs_import("src/app.ts", "@/does/not/exist", paths_set, tsconfigs)
        is None
    )


def test_comment_like_sequences_in_strings_do_not_break_paths():
    """The octavia bug: a tsconfig whose string *values* contain comment-like
    sequences must still parse. ``"@/*"`` in ``paths`` contains ``/*`` and
    ``"**/*.ts"`` in ``include`` contains ``*/``; a string-naive comment
    stripper treats the ``/*`` as the start of a block comment and runs to that
    ``*/``, deleting ``paths`` entirely and silently disabling alias resolution.
    """
    files = {
        "tsconfig.json": json.dumps(
            {
                "compilerOptions": {
                    "moduleResolution": "bundler",
                    "paths": {"@/*": ["./*"]},
                },
                "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", "types/**/*.d.ts"],
                "exclude": ["node_modules"],
            }
        ),
        "components/responsive-layout.tsx": "export const L = () => null;",
        "app/add-content/page.tsx": "import { L } from '@/components/responsive-layout';",
    }
    tsconfigs, paths_set = _world(files)
    cfg = find_governing_tsconfig("app/add-content/page.tsx", tsconfigs)
    assert cfg is not None and cfg["paths"], "paths must survive comment stripping"
    assert (
        resolve_tsjs_import(
            "app/add-content/page.tsx",
            "@/components/responsive-layout",
            paths_set,
            tsconfigs,
        )
        == "components/responsive-layout.tsx"
    )


def test_strip_jsonc_only_strips_real_comments():
    """``_strip_jsonc_comments`` must remove genuine ``//`` and ``/* */``
    comments and trailing commas, but never touch comment-like sequences that
    appear inside string literals (globs, URLs)."""
    src = (
        "{\n"
        "  // a real line comment\n"
        '  "paths": {"@/*": ["./*"]}, /* a real block comment */\n'
        '  "url": "https://example.com/a//b",\n'
        '  "globs": ["**/*.ts", "**/*.tsx"],\n'
        '  "trailing": 1,\n'
        "}\n"
    )
    parsed = json.loads(_strip_jsonc_comments(src))
    assert parsed["paths"] == {"@/*": ["./*"]}
    assert parsed["url"] == "https://example.com/a//b"
    assert parsed["globs"] == ["**/*.ts", "**/*.tsx"]
    assert parsed["trailing"] == 1


def test_relative_import_unaffected_by_changes():
    """Relative-import resolution is independent of tsconfig and must not
    regress."""
    files = {
        "src/a.ts": "export const a = 1;",
        "src/sub/b.ts": "import { a } from '../a';",
    }
    tsconfigs, paths_set = _world(files)
    assert resolve_tsjs_import("src/sub/b.ts", "../a", paths_set, tsconfigs) == "src/a.ts"

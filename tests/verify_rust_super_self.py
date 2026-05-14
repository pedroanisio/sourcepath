#!/usr/bin/env python3
"""verify_rust_super_self.py — Stage 5 contract for self::/super:: resolution.

Exercises Rust ``use`` paths whose head is ``self`` or ``super``,
across a 3-level module hierarchy fixture:

    src/
    ├── lib.rs          # pub mod foo; pub mod bar;
    ├── foo.rs          # use super::bar::bar_fn;
    ├── foo/
    │   └── sub.rs      # use super::foo_fn;
    ├── bar.rs          # use self::nested::nested_fn;
    └── bar/
        └── nested.rs   # pub fn nested_fn() ...

Resolution expectations:

  - ``foo.rs``::``use super::bar::bar_fn``   → ``src/bar.rs``
  - ``foo/sub.rs``::``use super::foo_fn``    → ``src/foo.rs``
  - ``bar.rs``::``use self::nested::nested_fn`` → ``src/bar/nested.rs``

Two layers under test:

  - ``codebase_mapper.languages.rust._file_module_path`` — the helper
    that maps a file path to its Rust module segments.
  - ``codebase_mapper.languages.rust.resolve_rust_imports`` — the
    public host resolver that ``tests_edges.py`` and the Rust xref
    resolver both consume.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rust" / "module_hierarchy"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_mapper.inspection.languages.rust import (  # noqa: E402
    _file_module_path,
    extract_rust_ast_summary,
    resolve_rust_imports,
)


# --------------------------------------------------------------------------
# _file_module_path unit tests
# --------------------------------------------------------------------------


def test_file_module_path_crate_root() -> None:
    assert _file_module_path("src/lib.rs", "") == []
    assert _file_module_path("src/main.rs", "") == []
    assert _file_module_path("packages/foo/src/lib.rs", "packages/foo") == []


def test_file_module_path_one_level() -> None:
    assert _file_module_path("src/foo.rs", "") == ["foo"]
    assert _file_module_path("src/foo/mod.rs", "") == ["foo"]
    assert _file_module_path("packages/c/src/foo.rs", "packages/c") == ["foo"]


def test_file_module_path_two_levels() -> None:
    assert _file_module_path("src/foo/bar.rs", "") == ["foo", "bar"]
    assert _file_module_path("src/foo/bar/mod.rs", "") == ["foo", "bar"]


def test_file_module_path_outside_src_returns_empty() -> None:
    """Files outside ``<crate_dir>/src/`` (e.g. integration tests under
    ``tests/``) have no in-source module path."""
    assert _file_module_path("tests/integration.rs", "") == []
    assert _file_module_path("benches/bench_main.rs", "") == []


# --------------------------------------------------------------------------
# resolve_rust_imports with self::/super:: across the fixture
# --------------------------------------------------------------------------


def _read_fixture() -> tuple[list[str], list[dict], set[str], dict[str, dict]]:
    """Load all .rs files in the fixture, parse them with the extractor,
    return (file paths, crates list, paths set, path→summary)."""
    summaries: dict[str, dict] = {}
    paths: list[str] = []
    for fs in sorted(FIXTURE.rglob("*.rs")):
        rel = fs.relative_to(FIXTURE).as_posix()
        summary, _ = extract_rust_ast_summary(fs.read_bytes(), rel)
        if summary is not None:
            summaries[rel] = summary
            paths.append(rel)
    crates = [{"name": "module_hierarchy", "crate_dir": ""}]
    paths_set = set(paths)
    return paths, crates, paths_set, summaries


def _resolve(file_relpath: str) -> tuple[list[str], list[str]]:
    """Call resolve_rust_imports with the fixture's full file inventory."""
    _, crates, paths_set, summaries = _read_fixture()
    summary = summaries[file_relpath]
    return resolve_rust_imports(file_relpath, summary, crates, paths_set)


def test_super_resolves_to_sibling_module() -> None:
    """``src/foo.rs`` does ``use super::bar::bar_fn`` — super at depth 1
    reaches the crate root; bar::bar_fn lives at ``src/bar.rs``."""
    in_repo, external = _resolve("src/foo.rs")
    assert "src/bar.rs" in in_repo, (
        f"super::bar::bar_fn did not resolve to src/bar.rs; "
        f"in_repo={in_repo!r}, external={external!r}"
    )


def test_super_resolves_to_parent_module() -> None:
    """``src/foo/sub.rs`` does ``use super::foo_fn`` — super at depth 2
    reaches the parent module ``foo``, found at ``src/foo.rs``."""
    in_repo, external = _resolve("src/foo/sub.rs")
    assert "src/foo.rs" in in_repo, (
        f"super::foo_fn did not resolve to src/foo.rs; "
        f"in_repo={in_repo!r}, external={external!r}"
    )


def test_self_resolves_to_child_module() -> None:
    """``src/bar.rs`` does ``use self::nested::nested_fn`` — self refers
    to module ``bar``; ``bar::nested`` lives at ``src/bar/nested.rs``."""
    in_repo, external = _resolve("src/bar.rs")
    assert "src/bar/nested.rs" in in_repo, (
        f"self::nested::nested_fn did not resolve to src/bar/nested.rs; "
        f"in_repo={in_repo!r}, external={external!r}"
    )


def test_no_spurious_external_classification() -> None:
    """``super`` and ``self`` are NEVER external crates. Previous v0.3
    silently dropped these; the regression here would be classifying
    them as external (unresolved package names)."""
    _, crates, paths_set, summaries = _read_fixture()
    for relpath in ("src/foo.rs", "src/foo/sub.rs", "src/bar.rs"):
        _in_repo, external = resolve_rust_imports(
            relpath, summaries[relpath], crates, paths_set,
        )
        assert "super" not in external and "self" not in external, (
            f"{relpath}: super/self leaked into external={external!r}"
        )


def test_super_above_root_skipped_silently() -> None:
    """``super`` from ``src/lib.rs`` would walk above the crate root —
    invalid Rust. Resolver must skip silently (no crash, no edge)."""
    summary = {
        "language": "rust",
        "imports": [{"path": "super::X", "raw": "super::X", "lineno": 1}],
    }
    in_repo, external = resolve_rust_imports(
        "src/lib.rs", summary,
        [{"name": "test", "crate_dir": ""}],
        {"src/lib.rs", "src/X.rs"},
    )
    assert in_repo == [], f"super from crate root spuriously resolved: {in_repo}"
    assert external == [], (
        f"super from crate root leaked into external: {external}"
    )


def test_double_super_strips_two_levels() -> None:
    """``super::super::X`` from ``src/foo/bar.rs`` reaches the crate
    root and looks for X there."""
    summary = {
        "language": "rust",
        "imports": [
            {"path": "super::super::widget", "raw": "super::super::widget", "lineno": 1},
        ],
    }
    in_repo, external = resolve_rust_imports(
        "src/foo/bar.rs", summary,
        [{"name": "test", "crate_dir": ""}],
        {"src/lib.rs", "src/foo.rs", "src/foo/bar.rs", "src/widget.rs"},
    )
    assert "src/widget.rs" in in_repo, (
        f"super::super::widget from depth-2 file did not resolve to "
        f"src/widget.rs; in_repo={in_repo}, external={external}"
    )


# --------------------------------------------------------------------------
# Integration via the symbol-xref resolver
# --------------------------------------------------------------------------


def test_xref_resolver_handles_super() -> None:
    """The Rust xref resolver's _resolve_use_to_path should also honor
    super::. Catches drift between the two parallel resolution paths."""
    from plugins.symbol_xrefs.rust_resolver import _UseBinding, _resolve_use_to_path

    paths_set = {"src/lib.rs", "src/foo.rs", "src/foo/sub.rs",
                 "src/bar.rs", "src/bar/nested.rs"}
    crates = [{"name": "module_hierarchy", "crate_dir": ""}]
    # super::foo_fn from src/foo/sub.rs (file_module = ["foo", "sub"]).
    # Stripping one super → module "foo", target name "foo_fn" → src/foo.rs.
    binding = _UseBinding(module="super", target_name="foo_fn")
    target = _resolve_use_to_path(binding, crates, paths_set, "src/foo/sub.rs")
    assert target == "src/foo.rs", (
        f"xref resolver missed super::foo_fn; got {target!r}"
    )


def test_xref_resolver_handles_self() -> None:
    from plugins.symbol_xrefs.rust_resolver import _UseBinding, _resolve_use_to_path

    paths_set = {"src/lib.rs", "src/bar.rs", "src/bar/nested.rs"}
    crates = [{"name": "module_hierarchy", "crate_dir": ""}]
    # self::nested from src/bar.rs (file_module = ["bar"]).
    # Rewrite to "bar::nested" → src/bar/nested.rs.
    binding = _UseBinding(module="self::nested", target_name="nested_fn")
    target = _resolve_use_to_path(binding, crates, paths_set, "src/bar.rs")
    assert target == "src/bar/nested.rs", (
        f"xref resolver missed self::nested::nested_fn; got {target!r}"
    )


def main() -> int:
    tests = [
        ("_file_module_path crate root", test_file_module_path_crate_root),
        ("_file_module_path one level", test_file_module_path_one_level),
        ("_file_module_path two levels", test_file_module_path_two_levels),
        ("_file_module_path outside src", test_file_module_path_outside_src_returns_empty),
        ("super resolves to sibling module", test_super_resolves_to_sibling_module),
        ("super resolves to parent module", test_super_resolves_to_parent_module),
        ("self resolves to child module", test_self_resolves_to_child_module),
        ("no spurious external classification", test_no_spurious_external_classification),
        ("super above root skipped silently", test_super_above_root_skipped_silently),
        ("double super strips two levels", test_double_super_strips_two_levels),
        ("xref resolver handles super", test_xref_resolver_handles_super),
        ("xref resolver handles self", test_xref_resolver_handles_self),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"ERROR {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            failures += 1
        else:
            print(f"PASS  {name}")
    if failures:
        print(f"\n{failures} test(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

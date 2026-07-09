"""codebase_mapper.tests_edges."""
from __future__ import annotations

import re

from collections import defaultdict
from pathlib import PurePosixPath

from .models import FileRecord, ImportEdge, TestsEdge


# Match attributes that make a Rust function a test:
#   #[test]              — built-in test attribute
#   #[tokio::test]       — async runtime variants
#   #[async_std::test]
#   #[test_case::test_matrix(...)] — third-party (any path ending in ::test or named test*)
#   #[test(...)]         — parameterized form
# Excludes #[cfg(test)] (a module gate, not a test marker on a function).
_RUST_TEST_ATTR_RE = re.compile(r"^#\[\s*(?:[\w]+\s*::\s*)*test(?:\s*[\(\]])")


def _rust_function_is_test(item: dict) -> bool:
    """A function item whose attribute list marks it as a test.

    ``#[cfg(test)]`` on a function or module is NOT a test marker —
    only ``#[test]``-shaped attributes count.
    """
    if item.get("kind") not in {"function", "method"}:
        return False
    for attr in item.get("attributes") or []:
        if _RUST_TEST_ATTR_RE.match(attr.strip()):
            return True
    return False


def count_rust_inline_test_files(records: list[FileRecord]) -> int:
    """Source-code Rust files containing at least one function marked as
    a test via ``#[test]`` / ``#[tokio::test]`` / etc.

    This catches the Rust idiom of putting unit tests inside the
    production-source file under ``#[cfg(test)] mod tests { #[test] fn
    … }`` — which our path-based test classifier misses entirely.
    """
    n = 0
    for r in records:
        if r.language != "rust" or r.type_ != "source_code":
            continue
        if not r.ast_summary:
            continue
        items = r.ast_summary.get("items") or []
        for item in items:
            if _rust_function_is_test(item):
                n += 1
                break  # one per file
    return n


def _rust_inferred_subjects(
    test_record: FileRecord,
    rust_crates: list[dict],
    paths_set: set[str],
) -> list[str]:
    """For a Rust test file, return the in-repo source files it covers
    by walking its ``use`` statements.

    Path-based filename matching (``test_foo.rs`` ↔ ``foo.rs``) is
    weak for Rust because integration tests under ``tests/`` typically
    don't mirror module names — they have flat aggregate names like
    ``integration_test.rs`` or ``api_tests.rs``. Walking the test's
    own ``use`` bindings gives a precise answer: an integration test
    that does ``use my_crate::api::Client`` covers the file containing
    ``Client``.

    Returns the (deduped, sorted) list of in-repo paths the test
    imports from. Empty list when the test has no ast_summary, no
    imports, or only imports external crates.
    """
    if not test_record.ast_summary:
        return []
    # Reuse the host's existing batch resolver. It returns
    # (in_repo_paths, external_packages); we only need the former.
    # Imported lazily to avoid a cycle: languages.rust doesn't import
    # us, but tests_edges sits near the top of the pipeline graph.
    from .languages.rust import resolve_rust_imports

    in_repo, _external = resolve_rust_imports(
        test_record.path,
        test_record.ast_summary,
        rust_crates,
        paths_set,
    )
    return sorted(set(in_repo))


def infer_tests_edges(
    records: list[FileRecord],
    *,
    rust_crates: list[dict] | None = None,
    paths_set: set[str] | None = None,
    import_edges: list[ImportEdge] | None = None,
) -> list[TestsEdge]:
    """Produce test→subject edges.

    Three strategies, applied in order per test file:

      1. **Path-based**: strip test prefixes/suffixes from the file's
         stem (``test_foo`` → ``foo``, ``foo_test`` → ``foo``, etc.)
         and match against any source file with the same basename.
         Tie-broken by longest directory-prefix overlap. Works well
         for Python (``test_foo.py`` ↔ ``foo.py``) and TS/JS
         (``Foo.test.tsx`` ↔ ``Foo.tsx``).

      2. **Rust ``use``-analysis fallback**: when the path heuristic
         produces no edge for a Rust ``test_code`` file, walk that
         file's ``use`` bindings and emit one edge per in-repo subject
         it imports. Activated only when ``rust_crates`` and
         ``paths_set`` are passed; absent, only step 1 runs.

      3. **Typed-import fallback** (flaw F17): when neither produced an
         edge and ``import_edges`` is passed, emit one edge per resolved
         import from the test file to a ``source_code``-typed target.
         Test-infrastructure targets (themselves ``test_code``) are not
         subjects. This generalizes strategy 2 to every language and is
         what makes kselftest-style suites (flat names mirroring no
         subject file) produce evidence at all; it is the canonical
         derivation reports must cite instead of re-deriving their own.

    Phase / strategy notes are intentional: a Rust integration test
    typically imports many modules of its parent crate. Emitting one
    edge per imported module is correct — those *are* the subjects the
    test covers.
    """
    subjects_by_basename: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.type_ != "source_code":
            continue
        bn = PurePosixPath(r.path).stem
        if bn in ("__init__", "index", "mod", "lib", "main"):
            continue
        subjects_by_basename[bn].append(r.path)

    source_paths = {r.path for r in records if r.type_ == "source_code"}
    imports_by_src: dict[str, list[str]] = defaultdict(list)
    for e in import_edges or ():
        imports_by_src[e.src_path].append(e.dst_path)

    edges: set[TestsEdge] = set()
    for r in records:
        if r.type_ != "test_code":
            continue
        p = PurePosixPath(r.path)
        stem = p.stem
        in_tests_dir = "__tests__" in p.parts or "tests" in p.parts
        cand: str | None = None
        m = re.fullmatch(r"test_(.+)", stem)
        if m:
            cand = m.group(1)
        else:
            m = re.fullmatch(r"(.+)_test", stem)
            if m:
                cand = m.group(1)
            else:
                m = re.fullmatch(r"(.+)_(test|spec)", stem)
                if m:
                    cand = m.group(1)
                else:
                    m = re.fullmatch(r"(.+)\.(test|spec)", stem)
                    if m:
                        cand = m.group(1)
                    else:
                        # Foo-test.X → Foo.X (React/Jest convention)
                        m = re.fullmatch(r"(.+)-(test|spec)", stem)
                        if m:
                            cand = m.group(1)
                        elif in_tests_dir and stem not in ("index", "main"):
                            # __tests__/Foo.X → Foo.X (React/Jest convention,
                            # bare stem inside a test directory).
                            cand = stem
                        else:
                            # Java / Kotlin convention: FooTest.java ↔ Foo.java
                            # (no underscore separator). Match `(.+)Test` or
                            # `(.+)Tests`, but only as a *trailing* qualifier
                            # on a CamelCase identifier so we don't strip
                            # legitimate suffixes from things like `Latest`.
                            m = re.fullmatch(r"([A-Z][A-Za-z0-9]+?)Tests?", stem)
                            if m:
                                cand = m.group(1)
        edges_before = len(edges)
        if cand:
            candidates = subjects_by_basename.get(cand, [])
            if len(candidates) == 1:
                edges.add(TestsEdge(r.path, candidates[0]))
            elif len(candidates) > 1:
                # Prefer candidates whose extension matches the test's
                # extension first. This is the right call for C/C++
                # repos where `dog.h` (header) and `dog.cpp`
                # (implementation) share a basename; the test
                # `dog_test.cpp` exercises the implementation, not the
                # declaration.
                test_suffix = p.suffix
                same_ext = [c for c in candidates
                            if PurePosixPath(c).suffix == test_suffix]
                if len(same_ext) == 1:
                    edges.add(TestsEdge(r.path, same_ext[0]))
                    ext_pool = []
                else:
                    ext_pool = same_ext if same_ext else candidates
                test_dir = list(p.parts[:-1])
                best, best_score, tie = None, -1, False
                for c in ext_pool:
                    cd = list(PurePosixPath(c).parts[:-1])
                    score = 0
                    for a, b in zip(test_dir, cd):
                        if a == b:
                            score += 1
                        else:
                            break
                    if score > best_score:
                        best, best_score, tie = c, score, False
                    elif score == best_score:
                        tie = True
                if best is not None and not tie:
                    edges.add(TestsEdge(r.path, best))

        # Rust use-analysis fallback. Runs when path-based produced
        # nothing for this test AND the caller supplied the crate map.
        if (
            len(edges) == edges_before
            and r.language == "rust"
            and rust_crates is not None
            and paths_set is not None
        ):
            for subject in _rust_inferred_subjects(r, rust_crates, paths_set):
                edges.add(TestsEdge(r.path, subject))

        # Typed-import fallback: the test's resolved imports into
        # source-typed files are its subjects. Only when the two more
        # precise strategies produced nothing for this test file.
        if len(edges) == edges_before:
            for dst in imports_by_src.get(r.path, ()):
                if dst in source_paths:
                    edges.add(TestsEdge(r.path, dst))

    return sorted(edges, key=lambda e: (e.test_path, e.subject_path))

---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.7 (1M context) via Claude Code"
  date: "2026-05-14"
status: normative
audience: contributors, AI agents implementing language support
---

# SPEC — First-Class Language Support in `code-base-mapper`

> Subject to [@DISCLAIMER.md](../DISCLAIMER.md). All requirements below are
> normative and grounded in current code. Every claim is keyed to a
> `path:line` reference for verification.

---

## 0. Scope & status

This spec defines what it means for a programming language `L` to be a
**first-class citizen** of the `code-base-mapper` ingestion pipeline.

The spec is **derived from the existing implementations** of Python (the
reference), Rust (the most-complete tree-sitter case), and TS/JS — not
invented. Languages that satisfy all MUST clauses are first-class.
Languages that satisfy only a subset are *partial* and SHALL be labelled
as such in the bundle manifest.

RFC 2119 keywords (MUST / SHOULD / MAY) apply throughout.

### 0.1 Languages targeted for promotion

Per [docs/FEATURE_REPORT.md §13](FEATURE_REPORT.md#13-flutter-repo-ingestion--predicted-issues), the following currently-partial languages are scheduled for promotion to first-class:

| Language        | Current state                                              | Promotion priority |
| --------------- | ---------------------------------------------------------- | ------------------ |
| **Dart**        | **Promoted to Tier-1** (2026-05-14) — multi-pubspec workspace, per-symbol chunks, calls/subclassOf/overrides resolver, codegen detection. Verified by [`tests/verify_dart.py`](../tests/verify_dart.py) (52/52). | ✔ done             |
| **C++**         | **Promoted to Tier-1** (2026-05-14) — tree-sitter-cpp grammar, namespace-aware item walker, out-of-class method definition handling (`Dog::speak`), header retag heuristic (sibling .cpp + project-wide rule), per-class/method chunks, calls/subclassOf/overrides resolver including `new Foo(...)` and direct-init `Foo x(args)` shapes. Verified by [`tests/verify_cpp.py`](../tests/verify_cpp.py) (42/42). | ✔ done             |
| **Java**        | **Promoted to Tier-1** (2026-05-14) — tree-sitter-java grammar, FQN + per-package indices, Maven source-root detection, `pom.xml` manifest parser, `*Test.java` / `FooIT.java` test conventions, per-class/method chunks, calls/subclassOf/overrides resolver. Verified by [`tests/verify_java.py`](../tests/verify_java.py) (51/51). | ✔ done             |
| **Objective-C** | **Promoted to Tier-1** (2026-05-15) — tree-sitter-objc grammar, `@interface`/`@implementation`/`@protocol`/category coverage with selector preservation, `#import` + `@import Module;` dual-form import resolution, header-retag heuristic so `.h` files in ObjC dirs are parsed by the ObjC analyzer (runs before the C++ retag), per-class/method chunks with interface-vs-implementation deduplication, calls/subclassOf/overrides resolver covering class messages, `[self ...]`, `[super ...]`, and nested `[[Class alloc] init...]`. Verified by [`tests/verify_objc.py`](../tests/verify_objc.py) (53/53). | ✔ done             |
| **Objective-C++** | **Promoted to Tier-1** (2026-05-15) — shares the ObjC analyzer/resolver/chunker (same tree-sitter-objc grammar handles both dialects; C++ method bodies may surface `parse_errors_present` diagnostics but the ObjC superstructure is recovered). | ✔ done             |

The same checklist applies to any future language (Zig, Nim, Elixir, …).

---

## 1. Definition

A language `L` is **first-class** when the bundle produced for a repo
where `L` is the dominant language is **observably indistinguishable in
shape** from a bundle produced for a Python or Rust repo of equivalent
size. Concretely:

1. Every `.L`-extension file appears in the inventory with a non-null
   `ast_summary` whenever its `type_` is `source_code` or `test_code`.
2. Every in-repo import/include in `L` produces an `ImportEdge`; every
   declared external dependency surfaces as an `ImportExternalEdge`.
3. Every `L` source file produces **at least one chunk per top-level
   declaration** (function / class / equivalent) — not a single
   whole-file chunk.
4. Cross-references between `L` symbols (call, subclass, override,
   reference) produce `SymbolXrefEdge` triples with `XREF_KINDS` values
   from [constants.py:41](../codebase_mapper/shared_kernel/constants.py#L41).
5. Concept extraction (L3) sees the same identifier density as for a
   Python file of equivalent line count, because chunks carry per-symbol
   text rather than per-file blobs.
6. SHACL conformance for the bundle remains `true` (no shape violations
   are introduced by the new analyzer).
7. The roundtrip test (`tests/verify_roundtrip.py`) passes for a fixture
   repo written in `L`.

Anything less is *partial*. Dart at commit `07f38de` satisfies (1) and
(2) only — that is the current bar.

---

## 2. Required components (12-point checklist)

A pull request promoting a language to first-class MUST add or update
every item in this list. Items are ordered by where they appear in the
pipeline.

### 2.1 Classifier (§L1)

* **C2.1.1** Extensions for `L` MUST appear in `LANG_BY_EXT`
  ([constants.py:62-89](../codebase_mapper/shared_kernel/constants.py#L62-L89)).
  Today: Dart, C++, Java, ObjC, ObjC++ are *all* present in this map but
  that alone is insufficient — see C2.2.
* **C2.1.2** If `L` distinguishes "source" from "test" by filename
  convention (e.g. Dart uses `*_test.dart`), the convention MUST be
  encoded in [classify.py](../codebase_mapper/inspection/classify.py)
  so the file gets `type_="test_code"`.
* **C2.1.3** If `L` has machine-generated outputs that pollute analysis
  (Dart `.g.dart`, `.freezed.dart`, `.mocks.dart`; C++ `*.pb.cc`, moc
  outputs; Java protobuf), the canonical pattern set SHOULD be added to
  a default `.cbmignore` template under `docs/cbmignore/<L>.txt`.

### 2.2 Tree-sitter grammar (§L1)

* **C2.2.1** If `L` has a maintained PyPI tree-sitter package, it MUST
  be added to [pyproject.toml](../pyproject.toml) and imported in
  [ts_setup.py:6-27](../codebase_mapper/ts_setup.py#L6-L27).
* **C2.2.2** A `ts.Language` entry MUST be created in `_ts_setup()`
  ([ts_setup.py:35-119](../codebase_mapper/ts_setup.py#L35-L119)).
* **C2.2.3** A `ts.Query` MUST be created with **at minimum** captures
  for: imports/includes, function declarations, class/struct/enum
  declarations. The existing Rust/Kotlin queries
  ([ts_setup.py:64-119](../codebase_mapper/ts_setup.py#L64-L119)) are
  the reference pattern.
* **C2.2.4** `_ts_grammar_for(path)`
  ([ts_setup.py:121-143](../codebase_mapper/ts_setup.py#L121-L143)) MUST
  return the grammar name for every extension belonging to `L`. **This
  is the single most-frequent bug source** — see §6.1 (C++ headers).
* **C2.2.5** If `L` has no tree-sitter grammar (Dart, Erlang, …), the
  analyzer MUST be regex-based **and** declare
  `extraction_method: "regex"` in its returned summary
  ([dart.py:74](../codebase_mapper/inspection/languages/dart.py#L74)).
  The regex extractor MUST handle, at minimum, all forms enumerated in
  §3.

### 2.3 LanguageAnalyzer (§L1)

* **C2.3.1** A module under
  [codebase_mapper/inspection/languages/](../codebase_mapper/inspection/languages/)
  named `<L>.py` MUST expose:
  * `extract_<L>_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]`
  * Optional helpers used by the resolver/host (see §2.5).
* **C2.3.2** The returned `dict` MUST contain:
  ```python
  {
      "language": "<L>",
      "imports": [{"kind": "<kind>", "source": "<spec>", "lineno": <int>}, ...],
      "top_level_functions": [<name>, ...],   # sorted, deduped
      "top_level_classes":   [<name>, ...],   # sorted, deduped
  }
  ```
  Optional extra keys are allowed (e.g. `extraction_method`, `package`,
  `imports_ext`) but MUST NOT be required by the resolver path.
* **C2.3.3** Error reporting: the second return value is a list of
  string codes drawn from the project lexicon (
  `"tree_sitter_unavailable"`, `"parse_errors_present"`,
  `"decode_error: …"`, …). New codes MUST be documented in this spec
  (Appendix A) before use.
* **C2.3.4** A wrapper class in
  [_builtins.py](../codebase_mapper/inspection/_builtins.py) MUST be
  added implementing the `LanguageAnalyzer` Protocol
  ([extensions.py:38-43](../codebase_mapper/shared_kernel/extensions.py#L38-L43)).
  Naming: `class <L>Analyzer` with `name = "lang_<L>"`. The class MUST
  be added to `_BUILTIN_ANALYZERS`
  ([_builtins.py:300-303](../codebase_mapper/inspection/_builtins.py#L300-L303)),
  preserving alphabetical ordering of the tuple.
* **C2.3.5** `matches(record, ctx)` MUST be `record.language == "<L>"`
  conjoined with `TS_AVAILABLE` iff the analyzer requires tree-sitter.
  Compound-language matches (e.g. `("typescript", "javascript")`) are
  permitted only when the analyzer truly handles both — see TS/JS
  ([_builtins.py:60-61](../codebase_mapper/inspection/_builtins.py#L60-L61)).

### 2.4 Host index (§L1)

If `L` has a project-level concept needed for import resolution (Python
source-root, Rust crate map, TS tsconfig, Go module, Swift module map,
Dart package, Kotlin FQN), the host MUST:

* **C2.4.1** Add a `detect_<L>_<thing>(records, read) -> ...` function
  in `languages/<L>.py`. References: `detect_rust_workspaces`
  ([rust.py:266-289](../codebase_mapper/inspection/languages/rust.py#L266-L289)),
  `detect_dart_package_name`
  ([dart.py:77-94](../codebase_mapper/inspection/languages/dart.py#L77-L94)).
* **C2.4.2** Call it from `map_codebase`
  ([pipeline.py:117-126](../codebase_mapper/inspection/pipeline.py#L117-L126))
  and stash the result on `ctx.indices["host:<L>_<thing>"]`
  ([pipeline.py:141-150](../codebase_mapper/inspection/pipeline.py#L141-L150)).
* **C2.4.3** The index SHALL be the **collection** form (dict / set /
  list of all instances), not a scalar, unless the language genuinely
  has a singleton concept. The current `host:dart_pkg_name` is **scalar
  and wrong for monorepos** — see §6.2.
* **C2.4.4** The detector MUST be deterministic across runs (same
  records → same index). No filesystem-order dependence.

### 2.5 ImportResolver (§L1)

* **C2.5.1** A `resolve_<L>_imports(...)` function MUST exist in
  `languages/<L>.py`, returning `(in_repo: list[str], external: list[str])`
  (or with a third `annotations` element when the resolver needs to
  pass provenance back to the host, as Kotlin does at
  [kotlin.py](../codebase_mapper/inspection/languages/kotlin.py) and
  [_builtins.py:254-264](../codebase_mapper/inspection/_builtins.py#L254-L264)).
* **C2.5.2** A wrapper class in `_builtins.py` MUST implement
  `ImportResolver` ([extensions.py:46-50](../codebase_mapper/shared_kernel/extensions.py#L46-L50)),
  named `<L>Resolver` with `name = "resolve_<L>"`, added to
  `_BUILTIN_RESOLVERS`
  ([_builtins.py:304-307](../codebase_mapper/inspection/_builtins.py#L304-L307)).
* **C2.5.3** Resolution MUST handle every import form the language
  supports — see §3 for the per-language minimum list.
* **C2.5.4** External-package classification MUST emit the canonical
  package name (e.g. `package:foo/x.dart` → `"foo"`, not `"foo/x.dart"`).
  This is the key matched against `declared_pkgs` in
  [pipeline.py:172-174](../codebase_mapper/inspection/pipeline.py#L172-L174)
  to surface `ImportExternalEdge`.
* **C2.5.5** Unresolved cases MUST surface a category, not be silently
  dropped. SDK imports (`dart:async`, `<stdio.h>`) MUST go to `external`
  with their original spec, **not** be discarded.

### 2.6 Dependency manifest parser (§L1)

* **C2.6.1** If `L` has a canonical dependency manifest (`pubspec.yaml`,
  `pom.xml`, `build.gradle`, `CMakeLists.txt`, …), a parser MUST be
  added to [manifests.py](../codebase_mapper/inspection/manifests.py)
  following the pattern of `parse_package_json`
  ([manifests.py:97-107](../codebase_mapper/inspection/manifests.py#L97-L107)).
* **C2.6.2** The parser MUST be dispatched from `declared_dependencies`
  ([manifests.py:251-276](../codebase_mapper/inspection/manifests.py#L251-L276)),
  keyed by basename match against the file path.
* **C2.6.3** The parser MUST return `Iterable[str]` of normalised
  package names. Version specifiers are explicitly out of scope at this
  stage (they live in lockfiles per C2.7).

### 2.7 Lockfile parser (§L1)

* **C2.7.1** If `L` has a deterministic lockfile (`pubspec.lock`,
  `Cargo.lock`, `package-lock.json`, …), a parser MUST be added to
  [lockfiles.py](../codebase_mapper/inspection/lockfiles.py).
* **C2.7.2** Output: `Iterable[tuple[str, str]]` of `(name, version)`.
  Consumed by `pinned_dependencies`
  ([pipeline.py:183-188](../codebase_mapper/inspection/pipeline.py#L183-L188))
  to produce `PinsDependencyEdge`.
* **C2.7.3** A repo without the lockfile MUST NOT fail ingestion —
  the parser is opportunistic.

### 2.8 Tests-edge heuristic (§L1)

* **C2.8.1** The conventions used by `L` (e.g. Dart `test/foo_test.dart`,
  Java `src/test/java/...`, C++ `*_test.cc` / GoogleTest) MUST be added
  to [tests_edges.py:infer_tests_edges](../codebase_mapper/inspection/tests_edges.py).
* **C2.8.2** Where the convention is filename-based, both directions
  (test → subject inference) MUST be implemented. Where the convention
  is import-based (test imports the SUT), the resolver's existing edges
  satisfy the requirement.

### 2.9 L2 chunker (§L2)

* **C2.9.1** A new branch MUST be added to `ChunkExtractor.enrich`
  ([chunker.py:60-70](../plugins/chunks_embeddings/chunker.py#L60-L70))
  invoking a `_chunk_<L>(content, path) -> list[dict]` helper.
* **C2.9.2** The chunker MUST emit one chunk per top-level function,
  one per top-level class, **and** one per method inside a class.
  Reference: Python (`_chunk_python`,
  [chunker.py:96-130](../plugins/chunks_embeddings/chunker.py#L96-L130))
  emits a class-chunk plus per-method chunks at L122-L124.
* **C2.9.3** Each chunk MUST carry the schema produced by
  `_whole_file_chunk` ([chunker.py:75-93](../plugins/chunks_embeddings/chunker.py#L75-L93))
  with `kind` ∈ `{"file", "function", "method", "class", "module"}`,
  byte and line spans, content sha, and the chunk text.
* **C2.9.4** When parsing fails the chunker MUST fall back to a
  whole-file chunk ([chunker.py:107-109](../plugins/chunks_embeddings/chunker.py#L107-L109)),
  never to an empty list, so embedding coverage stays uniform.
* **C2.9.5** Chunk symbol names MUST be the language's identifier form
  (Dart uses lowerCamelCase methods, Rust uses snake_case, C++ uses
  qualified names with `::`). The chunker MUST NOT canonicalize names
  — that is the concept-graph's job.

### 2.10 Symbol-xref resolver (§L1 cross-cutting plugin)

* **C2.10.1** A `<L>_resolver.py` MUST be added under
  [plugins/symbol_xrefs/](../plugins/symbol_xrefs/) following the shape
  of the existing Python / Rust / TS-JS resolvers (20–23 kB each).
* **C2.10.2** The resolver MUST emit `SymbolXrefEdge` records with
  `kind` drawn from `XREF_KINDS`
  ([constants.py:41](../codebase_mapper/shared_kernel/constants.py#L41))
  and `resolution` drawn from `XREF_RESOLUTIONS`
  ([constants.py:43](../codebase_mapper/shared_kernel/constants.py#L43)).
* **C2.10.3** Unresolvable edges MUST surface a reason from
  `XREF_UNRESOLVED_REASONS`
  ([constants.py:45-51](../codebase_mapper/shared_kernel/constants.py#L45-L51)).
  Returning `"language_unsupported"` for a first-class language is a
  spec violation.
* **C2.10.4** The resolver MUST be registered into `XrefAggregator`
  via [plugins/symbol_xrefs/__init__.py:register_all](../plugins/symbol_xrefs/__init__.py#L54-L63).

### 2.11 Test fixtures and verify-* tests

A first-class language MUST have:

* **C2.11.1** At least one self-contained fixture project under
  [tests/fixtures/<L>/](../tests/fixtures/) — mirror
  [tests/fixtures/rust/xref_crate/](../tests/fixtures/rust/xref_crate/).
* **C2.11.2** A `tests/verify_<L>_imports.py` (or richer) that runs
  `map_codebase` against the fixture and asserts the expected
  `ImportEdge` set.
* **C2.11.3** A `tests/verify_<L>_xrefs.py` exercising at least one of
  each `XREF_KINDS` value the language supports.
* **C2.11.4** A `tests/verify_roundtrip.py` extension that includes the
  fixture in its roundtrip set, so emit → reconstruct → diff is
  exercised against `L`.
* **C2.11.5** A `tests/verify_<L>_regenerate.py` is RECOMMENDED when
  the analyzer is tree-sitter-based and exposes a `regenerate_<L>_source`
  function (Rust has this; see
  [rust.py:64-95](../codebase_mapper/inspection/languages/rust.py#L64-L95)).
  This is the strongest possible AST-fidelity test.

### 2.12 Documentation

* **C2.12.1** This SPEC table (§0.1) MUST be updated when a language is
  promoted.
* **C2.12.2** The bundle's `repository_summary` already exposes a
  language histogram; no doc change needed there.
* **C2.12.3** [docs/FEATURE_REPORT.md §13](FEATURE_REPORT.md#13-flutter-repo-ingestion--predicted-issues)
  SHOULD be regenerated against the post-promotion bundle to
  confirm gaps are closed.

---

## 3. Per-language minimum import-form coverage

The resolver of a first-class language MUST handle these forms (at
minimum). Items in **bold** are not handled today by the regex Dart
implementation and are typical of what regex extractors miss.

### 3.1 Dart

* `import 'package:foo/bar.dart'` — in-repo when `foo` is a workspace
  package; external otherwise.
* `import 'dart:async'` — always external.
* `import 'relative.dart'` and `../parent.dart` — in-repo via path resolution.
* `export ...` — same resolution rules; emits the same `ImportEdge`.
* **`part 'file.dart'` / `part of '...'`** — both directions MUST emit
  edges so library/part relationships survive.
* **Conditional imports** (`import 'x' if (dart.library.html) 'y'`) —
  both branches MUST emit `external` entries.
* **Deferred imports** (`import 'x' deferred as y`) — same as plain.
* Show / hide / `as` clauses — MUST NOT affect resolution.

### 3.2 C++

* `#include "local.h"` — relative search per
  [c.py:50-94](../codebase_mapper/inspection/languages/c.py#L50-L94),
  plus an in-repo basename suffix fallback.
* `#include <system>` — external.
* `import std;` / `import :module;` (C++20 modules) — emit as imports
  with `kind: "module_import"`.
* Forward-declarations are not imports and MUST NOT be reported.
* `extern "C"` blocks and `namespace { … }` MUST be entered for symbol
  collection.

### 3.3 Java

* `import com.example.Foo;` → in-repo when a file under `**/com/example/Foo.java`
  exists; external when matching a declared dependency package.
* `import static com.example.Foo.bar;` — same module-level resolution.
* `import com.example.*;` — emit a single edge to the package directory,
  not per-file; annotate with `prefix_matched` (see Kotlin precedent at
  [kotlin.py](../codebase_mapper/inspection/languages/kotlin.py)).

### 3.4 Objective-C / Objective-C++

* `#import "Foo.h"` and `#import <Framework/Foo.h>` — local vs framework.
* `@import Foo;` (modules) — emit as external module reference.
* `.mm` files MUST be analysed by the same analyzer as `.m` plus C++
  rules merged in.

---

## 4. Acceptance test

A PR promoting language `L` is acceptable iff the following commands
all succeed against the canonical fixture under `tests/fixtures/<L>/`:

```bash
# 1. Inventory + edges
python -m codebase_mapper tests/fixtures/<L>/ --out _tmp/<L>-bundle/

# 2. L2 chunks
python -m scripts.run_l2 --bundle _tmp/<L>-bundle/

# 3. L3 concepts
python -m scripts.run_l3 --bundle _tmp/<L>-bundle/

# 4. Xrefs
python -m scripts.run_xrefs --bundle _tmp/<L>-bundle/

# 5. Verify-* harness
pytest tests/verify_<L>_imports.py tests/verify_<L>_xrefs.py tests/verify_roundtrip.py -q
```

…and on the produced bundle:

```python
from frontend.backend.serving.application.bundle_data import load_bundle

b = load_bundle("_tmp/<L>-bundle/")
assert b.shacl_conforms is True                                            # invariant
assert all(f.ast_summary is not None for f in b.files
           if f.type_ in ("source_code", "test_code") and f.language == "<L>")  # C1
assert any(c.symbol != "<file>" for c in b.chunks
           if c.file.language == "<L>")                                    # C3 (sub-file chunks exist)
assert any(e.kind in {"calls", "subclassOf"} for e in b.xref_edges
           if e.src_language == "<L>")                                     # C4
```

If any assertion fails the language remains *partial* until fixed.

---

## 5. Backwards compatibility & migration

* Promoting a language MUST NOT change the RDF graph for **other**
  languages. A regression of the `repository_summary` numbers for an
  unrelated bundle is a spec violation.
* Bundles emitted before promotion remain readable; the loader at
  [bundle_data.load_bundle](../frontend/backend/serving/application/bundle_data.py#L64-L235)
  tolerates absent `extraction_method`, missing sidecars, etc.
* New host indices (`host:<L>_<thing>`) MUST be added with `dict.setdefault`
  semantics — existing keys MUST NOT be reused.
* SHACL shapes are additive. New `<L>Shapes` contributors register via
  [register_shape_contributor](../codebase_mapper/shared_kernel/extensions.py#L112-L113).

---

## 6. Known gaps to fix during the first wave (Dart, C++)

These are concrete bugs in current code that the promotion work must
correct, not optional polish.

### 6.1 `.h` always routes to C *(resolved 2026-05-14)*

Previously, [ts_setup.py:137-138](../codebase_mapper/ts_setup.py#L137-L138)
routed every `.h` to the C grammar, silently dropping namespaces and
templates. The Tier-1 C++ promotion ships
[`refine_cpp_header_languages`](../codebase_mapper/inspection/languages/cpp.py)
which runs between classify and AST extraction in the pipeline. It
applies a two-pass heuristic:

1. **Sibling rule** — a `.h` in a directory that contains any C++
   source/header is C++.
2. **Project-wide rule** — if the repo contains any `.cpp/.cc/.cxx`
   AND no co-resident `.c` file rules it out, the `.h` is C++.

Pure-C repos are untouched.

### 6.2 `host:dart_pkg_name` is a scalar

[pipeline.py:148](../codebase_mapper/inspection/pipeline.py#L148) and
[dart.py:77-94](../codebase_mapper/inspection/languages/dart.py#L77-L94).
For a monorepo, only the shallowest `pubspec.yaml` is read. The
promoted Dart implementation MUST replace this with
`host:dart_pkg_map: dict[str, str]` mapping each `pubspec.yaml` to its
package name, and the resolver MUST pick the nearest enclosing entry
for a given source path.

### 6.3 `CAnalyzer.matches` rejects C++ language tag *(resolved 2026-05-14)*

The Tier-1 C++ promotion ships
[`CppAnalyzer`](../codebase_mapper/inspection/_builtins.py) (matches
`record.language == "cpp"`) alongside the existing `CAnalyzer`. Each
predicate is single-language; the dispatcher's first-match-wins
iteration picks the correct one. The Objective-C++ (`language ==
"objective-cpp"`) tag is *not yet* routed to either analyzer and
remains an open promotion target.

### 6.4 Dart codegen pollution

`.g.dart`, `.freezed.dart`, `.mocks.dart`, `.config.dart` are
machine-generated. The promotion PR MUST ship a default ignore template
at `docs/cbmignore/dart.txt` and a one-line note in the Dart analyzer's
docstring telling users to seed `.cbmignore` from it. The classifier
MUST also set `type_="generated"` when the trailing extension prefix
matches one of these patterns (see
[constants.py:53-58](../codebase_mapper/shared_kernel/constants.py#L53-L58)
for the `TYPE_VOCABULARY` entry `"generated"`).

---

## 7. Reference implementations to copy from

When implementing a new first-class language, mirror the closest of:

| Aspect                      | Best reference                                                        |
| --------------------------- | --------------------------------------------------------------------- |
| Tree-sitter analyzer        | [rust.py](../codebase_mapper/inspection/languages/rust.py)            |
| Tree-sitter resolver        | [tsjs.py](../codebase_mapper/inspection/languages/tsjs.py)            |
| Regex analyzer (no grammar) | [dart.py](../codebase_mapper/inspection/languages/dart.py) (improve, don't copy verbatim) |
| Host index + FQN map        | [kotlin.py](../codebase_mapper/inspection/languages/kotlin.py)        |
| Workspace detection         | `detect_rust_workspaces` at [rust.py:266](../codebase_mapper/inspection/languages/rust.py#L266) |
| Manifest parser             | `parse_package_json` at [manifests.py:97](../codebase_mapper/inspection/manifests.py#L97) |
| Chunker — AST module-based  | `_chunk_python` at [chunker.py:96](../plugins/chunks_embeddings/chunker.py#L96) |
| Chunker — tree-sitter       | `_chunk_rust` at [chunker.py:353](../plugins/chunks_embeddings/chunker.py#L353) |
| Symbol-xref resolver        | [plugins/symbol_xrefs/rust_resolver.py](../plugins/symbol_xrefs/rust_resolver.py) (22 kB, ground truth) |
| Verify-* test               | [tests/verify_xrefs.py](../tests/verify_xrefs.py) — 54 kB; pattern is overkill but exhaustive |

---

## 8. Definition of done (per-language checklist)

A checklist a contributor MUST tick on the promotion PR. Items map 1:1
to §2 above.

```
[ ] C2.1.1 — extensions in LANG_BY_EXT
[ ] C2.1.2 — test-file convention in classify.py
[ ] C2.1.3 — codegen ignore template added (if applicable)
[ ] C2.2.1 — tree-sitter package added to pyproject (if applicable)
[ ] C2.2.2 — ts.Language entry in _ts_setup
[ ] C2.2.3 — ts.Query with import + func + class captures
[ ] C2.2.4 — _ts_grammar_for returns grammar for all extensions
[ ] C2.2.5 — regex analyzer declares extraction_method (if no grammar)
[ ] C2.3.1 — languages/<L>.py with extract_<L>_ast_summary
[ ] C2.3.2 — summary dict has language/imports/top_level_functions/top_level_classes
[ ] C2.3.3 — error codes drawn from spec lexicon
[ ] C2.3.4 — <L>Analyzer wrapper in _builtins.py + _BUILTIN_ANALYZERS
[ ] C2.3.5 — matches() predicate correctly gated
[ ] C2.4.1 — detect_<L>_<thing> implemented (if applicable)
[ ] C2.4.2 — wired into map_codebase
[ ] C2.4.3 — collection (not scalar) form for multi-package languages
[ ] C2.4.4 — deterministic
[ ] C2.5.1 — resolve_<L>_imports implemented
[ ] C2.5.2 — <L>Resolver wrapper + _BUILTIN_RESOLVERS
[ ] C2.5.3 — all import forms in §3 handled
[ ] C2.5.4 — canonical external package name surfaced
[ ] C2.5.5 — unresolved categories not dropped
[ ] C2.6.1 — manifest parser added (if applicable)
[ ] C2.6.2 — wired into declared_dependencies
[ ] C2.6.3 — returns Iterable[str]
[ ] C2.7.1 — lockfile parser added (if applicable)
[ ] C2.7.2 — returns Iterable[tuple[name, version]]
[ ] C2.7.3 — graceful when lockfile absent
[ ] C2.8.1 — test-file convention in tests_edges.py
[ ] C2.8.2 — both directions handled
[ ] C2.9.1 — chunker branch added
[ ] C2.9.2 — per-function/class/method chunks
[ ] C2.9.3 — chunk schema preserved
[ ] C2.9.4 — fallback to whole-file on parse failure
[ ] C2.9.5 — native identifier form preserved
[ ] C2.10.1 — symbol-xref resolver added
[ ] C2.10.2 — kinds from XREF_KINDS
[ ] C2.10.3 — no "language_unsupported" emitted
[ ] C2.10.4 — registered in symbol_xrefs/__init__.py
[ ] C2.11.1 — fixture project added
[ ] C2.11.2 — verify_<L>_imports.py passes
[ ] C2.11.3 — verify_<L>_xrefs.py passes
[ ] C2.11.4 — roundtrip extended and passes
[ ] C2.11.5 — verify_<L>_regenerate.py (recommended, not required)
[ ] C2.12.1 — this spec's §0.1 table updated
[ ] C2.12.3 — FEATURE_REPORT §13 regenerated against post-promotion bundle
```

Forty-nine checkboxes total. A "partial" language ticks a subset of
these; first-class ticks all.

---

## Appendix A — Canonical extraction error codes

| Code                          | Meaning                                                   |
| ----------------------------- | --------------------------------------------------------- |
| `tree_sitter_unavailable`     | tree-sitter or grammar package missing                    |
| `parse_errors_present`        | tree-sitter root_node.has_error == True                   |
| `decode_error: <msg>`         | UTF-8 decode failed; the message carries the byte offset  |
| `regex_fallback`              | Tree-sitter available but regex path was used as fallback |
| `partial_extraction`          | A subset of declarations was recovered; analyzer flagged it |

New codes MUST be added to this table in the same PR that introduces them.

---

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

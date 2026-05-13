#!/usr/bin/env python3
"""verify_xrefs.py — contract suite for the symbol-xref layer.

Phase 1 — schema, vocab, empty plumbing (no L2 registered, no resolvers fire):
  1. Empty contract: manifest fragment reports zero edges/unresolved,
     xrefs.jsonl exists and is empty.
  2. Vocabulary bound: ``cbmxr:EdgeShape`` in shapes.shacl.ttl; the
     ``cbmxr:`` prefix round-trips when an edge is mutated in.
  3. SHACL conforms with no edges.
  4. Determinism: two consecutive runs are byte-identical.
  5. Aggregator unit: ``XrefAggregator(resolvers={}).run(ctx)`` returns
     the documented empty shape.
  6-9. SHACL mutation suite: a well-formed edge validates; missing
     src / bad kind / bad resolution are flagged.

Phase 2 — Python intra-file ``calls`` resolver (L2 + symbol_xrefs registered):
  10. The expected (src_symbol, dst_symbol, kind, resolution) triples land
      in xrefs.jsonl. Negative cases (Attribute calls, builtins,
      module-level calls, nested-function calls) do not.
  11. Each emitted edge's src/dst chunk_id corresponds to a real
      ``cbml2:Chunk`` in inventory.ttl (reference integrity).
  12. SHACL conforms with real edges + real chunks.
  13. xrefs.jsonl round-trips: each line parses to a dict matching the
      in-graph triples; lines are sorted (deterministic).
  14. Determinism with L2 + resolver: two runs byte-identical.
  15. Pure resolver unit: ``resolve_python_intra_file`` called directly
      on a record + chunks index returns the same edge set.

Phase 3 — persistence contract (TTL + JSON-LD + sidecar + CLI):
  16. Sidecar lines reconstruct as ``SymbolXrefEdge`` dataclasses and
      equal the aggregator's in-memory edge set (the actual downstream
      consumer contract).
  17. JSON-LD round-trip: ``inventory.jsonld`` survives the host's
      canonicalization step and still contains every ``cbmxr:Edge``
      and its src/dst/kind/resolution/resolver predicates.
  18. CLI parity: ``scripts/run_xrefs.py`` over the same fixture
      produces a byte-identical ``xrefs.jsonl`` and the same edge count
      as in-process registration.

Phase 4 — Python inter-file `calls` resolution (`from X import Y`):
  19. Cross-file resolution: ``from lib import foo; foo()`` produces
      an inter-file edge whose resolver is ``python_inter_file``.
  20. Alias support: ``from lib import foo as bar; bar()`` resolves to
      the chunk for ``foo``.
  21. Relative imports: ``from .sibling import foo; foo()`` resolves
      against the source file's package.
  22. ``module_not_in_repo``: ``from external_pkg import x; x()``
      → entry in ``unresolved`` with the correct reason; no edge.
  23. ``symbol_not_exported``: ``from lib import nonexistent;
      nonexistent()`` (lib in repo but symbol missing) → entry in
      ``unresolved`` with the correct reason; no edge.
  24. Intra-file still wins when both bindings exist (shadowing rule
      documented in python_resolver.py).
  25. Cross-file ``cbml2:Chunk`` reference integrity: src/dst IRIs
      resolve to existing chunks in inventory.ttl, including the
      ones in other files.

Phase 10 — subclassOf + overrides edges:
  35. Python intra-file subclassOf: `class Sub(Base):` where Base is in
      the same module → edge Sub → Base with kind="subclassOf" and
      resolver="python_intra_file".
  36. Python inter-file subclassOf: `class Sub(Base):` where Base is
      imported via `from .base import Base` → edge with
      resolver="python_inter_file".
  37. Python overrides: `class Sub(Base):` whose method `m` has the
      same name as `Base.m` → edge Sub.m → Base.m with
      kind="overrides".
  38. Python silent skip: `class Sub(object):` (builtin base) produces
      no subclassOf edge.
  39. Python silent skip: `class Sub(pkg.Base):` (Attribute base) is
      deferred — produces no edge.
  40. TS intra-file subclassOf + overrides via `class Sub extends Base`.
  41. TS inter-file subclassOf via named import.
  42. JS subclassOf + overrides over a same-file base.
  43. Reference integrity: every subclassOf / overrides cbmxr:src/dst
      resolves to a real cbml2:Chunk in inventory.ttl.

Phase 8 — TypeScript / JavaScript ``calls`` resolver:
  26. L2 chunker emits symbol-level chunks for TS/JS (function /
      class / method / arrow-const-binding).
  27. TS intra-file: ``function caller() { localTarget() }`` → edge
      with resolver ``tsjs_intra_file``.
  28. TS inter-file via named import: ``import { foo } from "./lib";
      caller() { foo() }`` → edge to lib.foo with resolver
      ``tsjs_inter_file``.
  29. TS alias: ``import { foo as bar } from "./lib"; bar()`` →
      edge to lib.foo (dedup with the un-aliased call).
  30. TS ``module_not_in_repo``: ``import { x } from "external-pkg";
      x()`` → ``unresolved`` entry.
  31. TS ``symbol_not_exported``: ``import { missing } from "./lib";
      missing()`` (lib exists but symbol absent) → ``unresolved`` entry.
  32. TS ``import type {...}`` and ``import * as ns from ...`` do
      NOT bind: a call using such a name produces neither an edge
      nor an unresolved entry.
  33. JS intra-file edge works the same way.
  34. Reference integrity: every TS/JS edge endpoint resolves to a
      real ``cbml2:Chunk`` in inventory.ttl.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF

from codebase_mapper import emit, map_codebase, reset_registries
from codebase_mapper.constants import CBMI_NS, CBMXR, CBMXR_NS
from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import FileRecord
from plugins import chunks_embeddings, symbol_xrefs
from plugins.symbol_xrefs.aggregator import XREF_INDEX_KEY, XrefAggregator
from plugins.symbol_xrefs.python_resolver import (
    RESOLVER_NAME as PYTHON_INTRA_FILE,
    resolve_python_intra_file,
)


CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:12]:
                print(f"        {line}")
        FAIL += 1


def build_minimal_fixture(target: Path) -> None:
    """Phase 1 fixture: enough to exercise the empty contract."""
    _init_git(target)
    (target / "hello.py").write_text('def hi():\n    return "hi"\n')
    _commit(target)


# Phase 2 fixture — every call pattern Phase 2 must handle (positive + negative).
# Line numbers are not asserted; the test matches edges by symbol name only.
PHASE2_SRC = '''\
def helper():
    return 1


def main():
    helper()  # → edge: main → helper
    helper()  # dedup; same edge


def recursive(n):
    if n > 0:
        recursive(n - 1)  # → edge: recursive → recursive (self-call)


class User:
    def greet(self):
        helper()  # → edge: User.greet → helper

    def chained(self):
        self.greet()  # Attribute call; Phase 2 skips


def top_call():
    print("hi")  # builtin / not in this file; skipped


top_call()  # module-level call; no enclosing chunk; skipped
'''


def build_phase2_fixture(target: Path) -> None:
    _init_git(target)
    (target / "app.py").write_text(PHASE2_SRC)
    _commit(target)


# Phase 4 fixture — exercises every cross-file case Phase 4 must handle.
# Layout:
#   pkg/__init__.py    (empty package marker)
#   pkg/lib.py         (target module: defines `foo`, `Helper`)
#   pkg/sibling.py     (defines `sib` — exercised via relative import)
#   pkg/app.py         (caller: from-import + alias + relative + bad cases)
#
# Expected outcome:
#   inter-file edges:
#     app.main         -> pkg.lib.foo           (from-import)
#     app.main         -> pkg.lib.foo           (aliased; dedup with above)
#     app.use_sibling  -> pkg.sibling.sib       (relative import)
#     app.use_class    -> pkg.lib.Helper        (class as call target)
#   unresolved:
#     app.use_external  -> module_not_in_repo   (from external_pkg ...)
#     app.use_missing   -> symbol_not_exported  (from .lib import nope)
#   intra-file (shadowing) wins over import:
#     app.use_shadow   -> app.shadow            (local shadow takes precedence)

PHASE4_LIB_SRC = '''\
def foo():
    return 1


def shadow():
    return "imported shadow"


class Helper:
    def hello(self):
        return "hi"
'''

PHASE4_SIBLING_SRC = '''\
def sib():
    return 2
'''

PHASE4_APP_SRC = '''\
from pkg.lib import foo, Helper, shadow
from pkg.lib import foo as foo_alias
from .sibling import sib
from external_pkg import gone
from .lib import nope


def shadow():
    """Locally shadows the import above; later binding wins in Python."""
    return "local shadow"


def main():
    foo()              # inter-file: app.main -> pkg.lib.foo
    foo_alias()        # inter-file via alias; dedups with the above edge


def use_sibling():
    sib()              # inter-file via relative import


def use_class():
    Helper()           # class as call target


def use_external():
    gone()             # unresolved: module_not_in_repo


def use_missing():
    nope()             # unresolved: symbol_not_exported


def use_shadow():
    shadow()           # intra-file beats import: edge to local app.shadow
'''


def build_phase4_fixture(target: Path) -> None:
    _init_git(target)
    pkg = target / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text(PHASE4_LIB_SRC)
    (pkg / "sibling.py").write_text(PHASE4_SIBLING_SRC)
    (pkg / "app.py").write_text(PHASE4_APP_SRC)
    _commit(target)


# Phase 8 fixtures: TS/JS chunker + resolver coverage.
#
# Layout:
#   src/lib.ts     — exports foo (function) and Helper (class)
#   src/app.ts     — covers intra-file, inter-file, alias, type-only,
#                    namespace, symbol-not-exported, module-not-in-repo
#   src/legacy.js  — JS intra-file edge
PHASE8_LIB_TS = '''\
export function foo(x: number): number {
    return x + 1;
}

export class Helper {
    run(): number { return 42; }
}
'''

PHASE8_APP_TS = '''\
import { foo } from "./lib";
import { foo as renamed } from "./lib";
import { missing } from "./lib";
import { external } from "external-pkg";
import type { Helper } from "./lib";
import * as ns from "./lib";

function localTarget(): void {}

function caller(): void {
    foo(1);          // inter-file: caller → lib.foo
    renamed(2);      // inter-file (alias): caller → lib.foo (dedup)
    localTarget();   // intra-file: caller → localTarget
    missing();       // unresolved: symbol_not_exported
    external();      // unresolved: module_not_in_repo
}

class App {
    method(): void {
        foo(3);      // inter-file: App.method → lib.foo
        ns.foo(4);   // Attribute call (namespace import); skipped silently
    }
}
'''

PHASE8_LEGACY_JS = '''\
function add(a, b) { return a + b; }

function caller() {
    add(1, 2);  // intra-file: caller → add
}
'''


def build_phase8_fixture(target: Path) -> None:
    _init_git(target)
    src = target / "src"
    src.mkdir()
    (src / "lib.ts").write_text(PHASE8_LIB_TS)
    (src / "app.ts").write_text(PHASE8_APP_TS)
    (src / "legacy.js").write_text(PHASE8_LEGACY_JS)
    _commit(target)


# Phase 10 fixtures: subclassOf + overrides for Python and TS/JS.
PHASE10_PY_BASE = '''\
class Animal:
    def speak(self):
        return "generic"

    def belly(self):
        return "soft"


class _Mixin:
    def shared(self):
        return 1
'''

PHASE10_PY_APP = '''\
from base import Animal as _Animal


class Local(_Animal):
    """Same-file class with no subclass relation locally — anchors the
    inter-file subclassOf to an imported base."""
    def speak(self):
        return "local"  # overrides Animal.speak

    def unique(self):
        return "no-override"


class Wraps(Local):
    """Intra-file subclassOf: Wraps → Local."""
    def speak(self):
        return "wrapped"  # overrides Local.speak (intra-file)


class Builtin(object):
    """Base is a builtin; no subclassOf edge."""
    pass


class Qualified(pkg.Base):  # noqa: F821 — Attribute base, deferred
    pass
'''


PHASE10_TS_BASE = '''\
export class Animal {
    speak(): string { return "generic"; }
    belly(): string { return "soft"; }
}
'''

PHASE10_TS_APP = '''\
import { Animal } from "./base";

export class Dog extends Animal {
    speak(): string { return "woof"; }
}

class Local {
    greet(): string { return "hi"; }
}

class LocalSub extends Local {
    greet(): string { return "hi2"; }
}
'''

PHASE10_JS_APP = '''\
class A {
    greet() { return "a"; }
}

class B extends A {
    greet() { return "b"; }
}
'''


def build_phase10_fixture(target: Path) -> None:
    _init_git(target)
    (target / "base.py").write_text(PHASE10_PY_BASE)
    (target / "app.py").write_text(PHASE10_PY_APP)
    src = target / "src"
    src.mkdir()
    (src / "base.ts").write_text(PHASE10_TS_BASE)
    (src / "app.ts").write_text(PHASE10_TS_APP)
    (src / "legacy.js").write_text(PHASE10_JS_APP)
    _commit(target)


def _init_git(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True,
    )


def run_pipeline(fixture: Path, out_dir: Path, *, with_l2: bool = False) -> dict:
    reset_registries()
    if with_l2:
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
    symbol_xrefs.register_all()
    mapped = map_codebase(fixture.resolve(), "HEAD")
    return emit(fixture.name, mapped, out_dir.resolve(), emit_blobs_flag=False)


def _add_chunk(g: Graph, chunk_id: str = "demo#hello.py#hi") -> URIRef:
    """Inject a minimal ``cbml2:Chunk`` so SHACL src/dst-class checks fire."""
    import urllib.parse
    safe = urllib.parse.quote(chunk_id, safe="")
    ciri = URIRef(f"{CBMI_NS}chunk/{safe}")
    g.add((ciri, RDF.type, CBML2.Chunk))
    return ciri


def _symbol_from_chunk_id(chunk_id: str) -> str:
    """Parse the L2 chunk_id ``path#kind:symbol:L<a>-L<b>`` → ``symbol``.

    Method chunks encode the parent as ``parent.symbol``; returned as-is.
    """
    _path, _hash, rest = chunk_id.partition("#")
    _kind, _colon, rest = rest.partition(":")
    symbol, _colon, _lines = rest.rpartition(":")
    return symbol


def _add_edge(g: Graph, *, src: URIRef, dst: URIRef,
              kind: str = "calls", resolution: str = "exact",
              resolver: str = "test", omit: str | None = None) -> URIRef:
    import hashlib
    key = f"{src}|{dst}|{kind}|{resolver}|{omit}"
    eiri = URIRef(f"{CBMI_NS}xref/{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((eiri, RDF.type, CBMXR.Edge))
    if omit != "src":
        g.add((eiri, CBMXR.src, src))
    if omit != "dst":
        g.add((eiri, CBMXR.dst, dst))
    if omit != "kind":
        g.add((eiri, CBMXR.kind, Literal(kind)))
    if omit != "resolution":
        g.add((eiri, CBMXR.resolution, Literal(resolution)))
    if omit != "resolver":
        g.add((eiri, CBMXR.resolver, Literal(resolver)))
    return eiri


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true", help="don't delete the workdir on exit")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_xrefs_"))
    try:
        # =====================================================
        # Phase 1: empty plumbing (no L2 registered)
        # =====================================================
        fixture = work / "fixture"
        build_minimal_fixture(fixture)

        out1 = work / "out1"
        out2 = work / "out2"
        manifest1 = run_pipeline(fixture, out1)
        manifest2 = run_pipeline(fixture, out2)

        # --- 1. Empty contract: manifest fragment reports zeros ---
        frag = manifest1.get("extensions", {}).get("l3_50_xrefs_artifact")
        check(
            "manifest fragment present under extensions.l3_50_xrefs_artifact",
            frag is not None,
            detail=json.dumps(manifest1.get("extensions", {}), indent=2),
        )
        if frag is not None:
            check(
                "fragment.n_edges == 0",
                frag.get("n_edges") == 0,
                detail=str(frag),
            )
            check(
                "fragment.n_unresolved == 0",
                frag.get("n_unresolved") == 0,
                detail=str(frag),
            )
            for k in ("by_kind", "by_resolution", "by_language"):
                check(
                    f"fragment.{k} is empty dict",
                    frag.get(k) == {},
                    detail=str(frag),
                )
            check(
                "fragment.files lists xrefs.jsonl with sha256",
                "xrefs.jsonl" in frag.get("files", {})
                and frag["files"]["xrefs.jsonl"].get("size_bytes") == 0
                and len(frag["files"]["xrefs.jsonl"].get("sha256", "")) == 64,
                detail=str(frag.get("files")),
            )

        # --- 1b. Sidecar exists and is empty ---
        sidecar = out1 / "xrefs.jsonl"
        check("xrefs.jsonl written", sidecar.exists())
        check(
            "xrefs.jsonl is empty (0 bytes)",
            sidecar.exists() and sidecar.stat().st_size == 0,
            detail=f"size={sidecar.stat().st_size if sidecar.exists() else 'missing'}",
        )

        # --- 2. Vocabulary registered ---
        # rdflib only emits a namespace binding when some triple uses it,
        # so with no edges `cbmxr:` is correctly absent from inventory.ttl.
        # The contract we want here is: shapes carry the vocabulary, AND
        # adding an edge round-trips through the `cbmxr:` prefix.
        shapes_text = (out1 / "shapes.shacl.ttl").read_text()
        check("EdgeShape declared in shapes.shacl.ttl",
              "EdgeShape" in shapes_text and "cbmxr" in shapes_text)

        inv_with_edge = Graph()
        inv_with_edge.parse(str(out1 / "inventory.ttl"), format="turtle")
        c_a = _add_chunk(inv_with_edge, "demo#a#f")
        c_b = _add_chunk(inv_with_edge, "demo#b#g")
        _add_edge(inv_with_edge, src=c_a, dst=c_b)
        inv_with_edge.bind("cbmxr", CBMXR)
        serialized = inv_with_edge.serialize(format="turtle")
        check(
            "cbmxr namespace binding round-trips when an edge exists",
            "cbmxr:" in serialized,
            detail=serialized[:400],
        )

        # --- 3. SHACL conforms with no edges ---
        check("shacl conforms (empty edges)",
              manifest1["shacl_self_check"]["conforms"],
              detail=manifest1["shacl_self_check"].get("report_excerpt", ""))

        # --- 4. Determinism ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl", "xrefs.jsonl"):
            a = (out1 / fname).read_bytes()
            b = (out2 / fname).read_bytes()
            check(
                f"determinism: {fname} byte-identical across runs",
                a == b,
                detail=f"len {len(a)} vs {len(b)}",
            )

        # --- 5. Aggregator unit: empty resolver dict yields empty shape ---
        ctx = PipelineCtx(
            repo=fixture, commit="HEAD", records=[],
            blob_by_path={}, mode_by_path={}, paths_set=set(),
            read_path=lambda p: b"",
        )
        agg = XrefAggregator(resolvers={})
        result = agg.run(ctx)
        check(
            "XrefAggregator(empty).run() returns documented empty shape",
            result == {"edges": [], "unresolved": [], "by_language": {},
                       "by_kind": {}, "by_resolution": {}},
            detail=json.dumps(result, default=repr),
        )

        # --- 6/7/8/9. SHACL mutation suite ---
        from pyshacl import validate

        shapes_graph = Graph()
        shapes_graph.parse(str(out1 / "shapes.shacl.ttl"), format="turtle")

        def mutated(mutate) -> bool:
            data = Graph()
            data.parse(str(out1 / "inventory.ttl"), format="turtle")
            mutate(data)
            conforms, _, _ = validate(
                data, shacl_graph=shapes_graph, inference="none",
                abort_on_first=False, meta_shacl=False,
            )
            return bool(conforms)

        def case_well_formed(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2)

        check("SHACL accepts a well-formed cbmxr:Edge",
              mutated(case_well_formed),
              "should conform")

        def case_missing_src(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, omit="src")

        check("SHACL rejects edge missing cbmxr:src",
              not mutated(case_missing_src),
              "should fail")

        def case_bad_kind(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, kind="totally_made_up")

        check("SHACL rejects edge with kind outside enum",
              not mutated(case_bad_kind),
              "should fail")

        def case_bad_resolution(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, resolution="bogus")

        check("SHACL rejects edge with resolution outside enum",
              not mutated(case_bad_resolution),
              "should fail")

        # =====================================================
        # Phase 2: Python intra-file `calls` resolver
        # =====================================================
        p2_fixture = work / "p2_fixture"
        build_phase2_fixture(p2_fixture)
        p2_out1 = work / "p2_out1"
        p2_out2 = work / "p2_out2"
        p2_manifest = run_pipeline(p2_fixture, p2_out1, with_l2=True)
        run_pipeline(p2_fixture, p2_out2, with_l2=True)

        # --- 10. Edges match the expected (src, dst, kind, resolution) set ---
        sidecar_lines = (p2_out1 / "xrefs.jsonl").read_text().splitlines()
        emitted = [json.loads(line) for line in sidecar_lines]
        actual_triples = {
            (
                _symbol_from_chunk_id(e["src_chunk_id"]),
                _symbol_from_chunk_id(e["dst_chunk_id"]),
                e["kind"],
                e["resolution"],
            )
            for e in emitted
        }
        expected_triples = {
            ("main", "helper", "calls", "exact"),
            ("recursive", "recursive", "calls", "exact"),
            ("User.greet", "helper", "calls", "exact"),
        }
        check(
            "Phase 2: emitted call edges match expected (src, dst, kind, resolution)",
            actual_triples == expected_triples,
            f"expected={sorted(expected_triples)}\nactual={sorted(actual_triples)}",
        )
        check(
            "Phase 2: every edge resolver is python_intra_file",
            all(e["resolver"] == PYTHON_INTRA_FILE for e in emitted),
            f"resolvers={set(e['resolver'] for e in emitted)}",
        )
        check(
            "Phase 2: manifest fragment counts agree with sidecar",
            (
                p2_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"]
                == len(expected_triples)
                and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_kind"]
                == {"calls": len(expected_triples)}
                and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_resolution"]
                == {"exact": len(expected_triples)}
            ),
            json.dumps(p2_manifest["extensions"]["l3_50_xrefs_artifact"], indent=2),
        )
        check(
            "Phase 2: manifest by_language reports python resolved count",
            p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_language"].get("python", {}).get("resolved")
            == len(expected_triples)
            and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_language"].get("python", {}).get("unresolved") == 0,
            str(p2_manifest["extensions"]["l3_50_xrefs_artifact"].get("by_language")),
        )

        # --- 11. Reference integrity: every edge's src/dst exists as cbml2:Chunk ---
        g_p2 = Graph()
        g_p2.parse(str(p2_out1 / "inventory.ttl"), format="turtle")
        chunk_subjects = set(g_p2.subjects(RDF.type, CBML2.Chunk))
        edge_subjects = list(g_p2.subjects(RDF.type, CBMXR.Edge))
        check(
            "Phase 2: cbmxr:Edge count in inventory.ttl matches sidecar",
            len(edge_subjects) == len(emitted),
            f"ttl_edges={len(edge_subjects)} sidecar_edges={len(emitted)}",
        )
        bad_refs = []
        for e_iri in edge_subjects:
            for pred in (CBMXR.src, CBMXR.dst):
                tgt = next(iter(g_p2.objects(e_iri, pred)), None)
                if tgt is None or tgt not in chunk_subjects:
                    bad_refs.append((str(e_iri), str(pred), str(tgt)))
        check(
            "Phase 2: every cbmxr:src/dst resolves to a real cbml2:Chunk",
            not bad_refs,
            f"bad={bad_refs[:3]}",
        )

        # --- 12. SHACL conforms with real edges ---
        check(
            "Phase 2: SHACL conforms with real edges + chunks",
            p2_manifest["shacl_self_check"]["conforms"],
            p2_manifest["shacl_self_check"].get("report_excerpt", ""),
        )

        # --- 13. Sidecar lines are ordered by edge tuple (deterministic) ---
        # The contract is "edges sorted by (src, dst, kind, resolution, resolver)",
        # not "lines sorted lexicographically as strings" (the JSON keys are
        # alphabetical, so dst_chunk_id leads each line — sorting by string
        # would shuffle the intended edge order).
        edge_tuples = [
            (e["src_chunk_id"], e["dst_chunk_id"], e["kind"], e["resolution"], e["resolver"])
            for e in emitted
        ]
        check(
            "Phase 2: xrefs.jsonl edges sorted by (src, dst, kind, resolution, resolver)",
            edge_tuples == sorted(edge_tuples),
            f"first divergence at line {next((i for i, (a, b) in enumerate(zip(edge_tuples, sorted(edge_tuples))) if a != b), -1)}",
        )

        # --- 14. Determinism under L2 + resolver ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl", "xrefs.jsonl"):
            a = (p2_out1 / fname).read_bytes()
            b = (p2_out2 / fname).read_bytes()
            check(
                f"Phase 2 determinism: {fname} byte-identical",
                a == b,
                f"len {len(a)} vs {len(b)}",
            )

        # --- 15. Pure resolver unit: same edges from direct call ---
        # Rebuild the pipeline state in-process and call the resolver directly
        # to prove it doesn't depend on emit / graph_writer.
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        symbol_xrefs.register_all()
        from codebase_mapper.pipeline import map_codebase as _map
        mapped = _map(p2_fixture.resolve(), "HEAD")
        unit_ctx = mapped["ctx"]
        py_records = [r for r in mapped["records"] if r.language == "python"]
        unit_edges = []
        for rec in py_records:
            e, u = resolve_python_intra_file(rec, unit_ctx)
            unit_edges.extend(e)
            check(
                f"Phase 2: resolver returned empty unresolved for {rec.path}",
                u == [],
                f"got {u}",
            )
        unit_triples = {
            (
                _symbol_from_chunk_id(e.src_chunk_id),
                _symbol_from_chunk_id(e.dst_chunk_id),
                e.kind,
                e.resolution,
            )
            for e in unit_edges
        }
        check(
            "Phase 2: pure resolver unit produces the same edge set",
            unit_triples == expected_triples,
            f"unit={sorted(unit_triples)}\nexpected={sorted(expected_triples)}",
        )

        # =====================================================
        # Phase 3: persistence contract
        # =====================================================
        from codebase_mapper.models import SymbolXrefEdge

        # --- 16. Sidecar → SymbolXrefEdge round-trip ---
        unit_edge_set = set(unit_edges)
        reconstructed = {
            SymbolXrefEdge(**json.loads(line)) for line in sidecar_lines
        }
        check(
            "Phase 3: xrefs.jsonl reconstructs as SymbolXrefEdge dataclasses "
            "equal to the resolver's in-memory edge set",
            reconstructed == unit_edge_set,
            f"sidecar={len(reconstructed)} unit={len(unit_edge_set)} "
            f"diff_sidecar_only={list(reconstructed - unit_edge_set)[:2]} "
            f"diff_unit_only={list(unit_edge_set - reconstructed)[:2]}",
        )

        # --- 17. JSON-LD round-trip ---
        jsonld_path = p2_out1 / "inventory.jsonld"
        jsonld_text = jsonld_path.read_text()
        # The custom canonicalization in emit_bundle.py expands/compacts —
        # the cleanest check is to re-parse as RDF and confirm the same
        # edge/predicate counts as inventory.ttl.
        g_jsonld = Graph()
        g_jsonld.parse(str(jsonld_path), format="json-ld")
        jsonld_edges = list(g_jsonld.subjects(RDF.type, CBMXR.Edge))
        check(
            "Phase 3: inventory.jsonld carries the same cbmxr:Edge count "
            "as inventory.ttl",
            len(jsonld_edges) == len(edge_subjects),
            f"jsonld={len(jsonld_edges)} ttl={len(edge_subjects)}",
        )
        # And every required predicate is reachable from each edge.
        missing_preds = []
        for e_iri in jsonld_edges:
            for pred in (CBMXR.src, CBMXR.dst, CBMXR.kind,
                         CBMXR.resolution, CBMXR.resolver):
                if not list(g_jsonld.objects(e_iri, pred)):
                    missing_preds.append((str(e_iri), str(pred)))
        check(
            "Phase 3: every cbmxr:Edge in JSON-LD has src/dst/kind/resolution/resolver",
            not missing_preds,
            f"missing={missing_preds[:3]}",
        )

        # --- 18. CLI parity: scripts/run_xrefs.py matches in-process bundle ---
        cli_out = work / "p2_cli"
        repo_root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(repo_root)}
        cli = subprocess.run(
            [
                sys.executable, "scripts/run_xrefs.py",
                "--repo", str(p2_fixture),
                "--out", str(cli_out),
                "--backend", "hash",
                "--hash-dim", "64",
                "--no-emit-blobs",
            ],
            env=env, cwd=str(repo_root), capture_output=True, text=True,
        )
        check(
            "Phase 3: scripts/run_xrefs.py exits 0",
            cli.returncode == 0,
            f"stderr_tail={cli.stderr[-600:]}",
        )
        if cli.returncode == 0:
            check(
                "Phase 3: CLI xrefs.jsonl is byte-identical to in-process bundle",
                (cli_out / "xrefs.jsonl").read_bytes()
                == (p2_out1 / "xrefs.jsonl").read_bytes(),
                "byte mismatch",
            )
            cli_manifest = json.loads((cli_out / "run_manifest.json").read_text())
            check(
                "Phase 3: CLI manifest n_edges matches in-process",
                cli_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"]
                == p2_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"],
                f"cli={cli_manifest['extensions']['l3_50_xrefs_artifact']['n_edges']} "
                f"inproc={p2_manifest['extensions']['l3_50_xrefs_artifact']['n_edges']}",
            )

        # =====================================================
        # Phase 4: Python inter-file `calls` resolution
        # =====================================================
        p4_fixture = work / "p4_fixture"
        build_phase4_fixture(p4_fixture)
        p4_out = work / "p4_out"
        p4_manifest = run_pipeline(p4_fixture, p4_out, with_l2=True)

        p4_lines = (p4_out / "xrefs.jsonl").read_text().splitlines()
        p4_edges = [json.loads(line) for line in p4_lines]
        edge_tuples = {
            (
                e["src_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(e["src_chunk_id"]),
                e["dst_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(e["dst_chunk_id"]),
                e["resolver"],
            )
            for e in p4_edges
        }
        expected_edges = {
            # (src_path, src_sym, dst_path, dst_sym, resolver)
            ("pkg/app.py", "main", "pkg/lib.py", "foo", "python_inter_file"),
            ("pkg/app.py", "use_sibling", "pkg/sibling.py", "sib", "python_inter_file"),
            ("pkg/app.py", "use_class", "pkg/lib.py", "Helper", "python_inter_file"),
            ("pkg/app.py", "use_shadow", "pkg/app.py", "shadow", "python_intra_file"),
        }
        check(
            "Phase 4: cross-file + alias + relative + class-target + shadow edges match",
            edge_tuples == expected_edges,
            f"expected={sorted(expected_edges)}\nactual={sorted(edge_tuples)}",
        )

        # Alias dedup: only one main→foo edge despite two call sites (foo() and foo_alias()).
        main_to_foo = [
            e for e in p4_edges
            if _symbol_from_chunk_id(e["src_chunk_id"]) == "main"
            and _symbol_from_chunk_id(e["dst_chunk_id"]) == "foo"
        ]
        check(
            "Phase 4: alias and original target produce a single deduped edge",
            len(main_to_foo) == 1,
            f"got {len(main_to_foo)} edges",
        )

        # Manifest fragment: 4 edges total; 1 intra-file (shadow win) + 3 inter-file;
        # 2 unresolved (external + missing-symbol).
        p4_frag = p4_manifest["extensions"]["l3_50_xrefs_artifact"]
        check(
            "Phase 4: manifest counts 4 edges, 2 unresolved",
            p4_frag["n_edges"] == 4 and p4_frag["n_unresolved"] == 2,
            json.dumps(p4_frag, indent=2),
        )
        check(
            "Phase 4: by_language python = {resolved: 4, unresolved: 2}",
            p4_frag["by_language"].get("python") == {"resolved": 4, "unresolved": 2},
            str(p4_frag.get("by_language")),
        )

        # Reach into the in-memory unresolved list via a fresh resolver invocation,
        # since the manifest only carries counts. The aggregator's edges + unresolved
        # are deterministic from the same inputs, so re-running is safe.
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        symbol_xrefs.register_all()
        from codebase_mapper.pipeline import map_codebase as _map4
        p4_mapped = _map4(p4_fixture.resolve(), "HEAD")
        p4_ctx = p4_mapped["ctx"]
        p4_app_record = next(
            r for r in p4_mapped["records"] if r.path == "pkg/app.py"
        )
        _, p4_unresolved = resolve_python_intra_file(p4_app_record, p4_ctx)

        reasons_by_target = {
            (u.src_chunk_id.split("#")[0], _symbol_from_chunk_id(u.src_chunk_id), u.reason)
            for u in p4_unresolved
        }
        check(
            "Phase 4: unresolved has module_not_in_repo for use_external",
            ("pkg/app.py", "use_external", "module_not_in_repo") in reasons_by_target,
            str(reasons_by_target),
        )
        check(
            "Phase 4: unresolved has symbol_not_exported for use_missing",
            ("pkg/app.py", "use_missing", "symbol_not_exported") in reasons_by_target,
            str(reasons_by_target),
        )
        check(
            "Phase 4: unresolved resolver is python_inter_file for both entries",
            all(u.resolver == "python_inter_file" for u in p4_unresolved),
            f"resolvers={set(u.resolver for u in p4_unresolved)}",
        )
        check(
            "Phase 4: unresolved raw_target includes the import statement",
            all("from " in u.raw_target and "import " in u.raw_target for u in p4_unresolved),
            f"raw_targets={[u.raw_target for u in p4_unresolved]}",
        )

        # Reference integrity across files.
        g_p4 = Graph()
        g_p4.parse(str(p4_out / "inventory.ttl"), format="turtle")
        chunk_subjects = set(g_p4.subjects(RDF.type, CBML2.Chunk))
        bad_p4 = []
        for e_iri in g_p4.subjects(RDF.type, CBMXR.Edge):
            for pred in (CBMXR.src, CBMXR.dst):
                tgt = next(iter(g_p4.objects(e_iri, pred)), None)
                if tgt is None or tgt not in chunk_subjects:
                    bad_p4.append((str(e_iri), str(pred), str(tgt)))
        check(
            "Phase 4: every cbmxr:src/dst (including cross-file) resolves to cbml2:Chunk",
            not bad_p4,
            f"bad={bad_p4[:3]}",
        )

        # =====================================================
        # Phase 8: TypeScript / JavaScript resolver
        # =====================================================
        p8_fixture = work / "p8_fixture"
        build_phase8_fixture(p8_fixture)
        p8_out = work / "p8_out"
        p8_manifest = run_pipeline(p8_fixture, p8_out, with_l2=True)

        # --- 26. L2 chunker emits symbol-level chunks for TS/JS. ---
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        symbol_xrefs.register_all()
        from codebase_mapper.pipeline import map_codebase as _map8
        p8_mapped = _map8(p8_fixture.resolve(), "HEAD")
        p8_ctx = p8_mapped["ctx"]
        all_chunks = p8_ctx.indices["l2_10_chunks"]

        def _chunks_by_path(path):
            return [c for c in all_chunks if c.get("path") == path]

        tsjs_lib_chunks = _chunks_by_path("src/lib.ts")
        tsjs_app_chunks = _chunks_by_path("src/app.ts")
        js_chunks = _chunks_by_path("src/legacy.js")
        lib_symbols = {(c["symbol"], c.get("parent_symbol")) for c in tsjs_lib_chunks}
        app_symbols = {(c["symbol"], c.get("parent_symbol")) for c in tsjs_app_chunks}
        js_symbols = {(c["symbol"], c.get("parent_symbol")) for c in js_chunks}
        check(
            "Phase 8: TS chunker emits {foo, Helper, Helper.run} for lib.ts",
            {("foo", None), ("Helper", None), ("run", "Helper")}.issubset(lib_symbols),
            str(lib_symbols),
        )
        check(
            "Phase 8: TS chunker emits {caller, localTarget, App, App.method} for app.ts",
            {("caller", None), ("localTarget", None), ("App", None),
             ("method", "App")}.issubset(app_symbols),
            str(app_symbols),
        )
        check(
            "Phase 8: JS chunker emits {add, caller} for legacy.js",
            {("add", None), ("caller", None)}.issubset(js_symbols),
            str(js_symbols),
        )

        # --- 27-33. Resolver edges + unresolved entries ---
        p8_lines = (p8_out / "xrefs.jsonl").read_text().splitlines()
        p8_edges = [json.loads(line) for line in p8_lines]
        p8_tuples = {
            (
                e["src_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(e["src_chunk_id"]),
                e["dst_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(e["dst_chunk_id"]),
                e["resolver"],
            )
            for e in p8_edges
        }
        expected_tsjs_edges = {
            # (src_path, src_sym, dst_path, dst_sym, resolver)
            ("src/app.ts", "caller", "src/app.ts", "localTarget", "tsjs_intra_file"),
            ("src/app.ts", "caller", "src/lib.ts", "foo", "tsjs_inter_file"),
            ("src/app.ts", "App.method", "src/lib.ts", "foo", "tsjs_inter_file"),
            ("src/legacy.js", "caller", "src/legacy.js", "add", "tsjs_intra_file"),
        }
        check(
            "Phase 8: TS/JS edges match expected set (intra + inter + alias dedup)",
            p8_tuples == expected_tsjs_edges,
            f"expected={sorted(expected_tsjs_edges)}\nactual={sorted(p8_tuples)}",
        )

        # Alias dedup: only one caller→foo edge despite two call sites.
        caller_to_foo = [
            e for e in p8_edges
            if _symbol_from_chunk_id(e["src_chunk_id"]) == "caller"
            and _symbol_from_chunk_id(e["dst_chunk_id"]) == "foo"
            and e["resolver"] == "tsjs_inter_file"
        ]
        check(
            "Phase 8: aliased and original imports dedup to one edge",
            len(caller_to_foo) == 1,
            f"got {len(caller_to_foo)} edges",
        )

        # Unresolved buckets — fetch the in-memory list via the pure resolver.
        from plugins.symbol_xrefs.tsjs_resolver import resolve_tsjs_calls
        app_record = next(
            r for r in p8_mapped["records"] if r.path == "src/app.ts"
        )
        _, p8_unresolved = resolve_tsjs_calls(app_record, p8_ctx)
        reasons = {(u.reason, u.resolver) for u in p8_unresolved}
        check(
            "Phase 8: unresolved has module_not_in_repo from tsjs_inter_file",
            ("module_not_in_repo", "tsjs_inter_file") in reasons,
            str(reasons),
        )
        check(
            "Phase 8: unresolved has symbol_not_exported from tsjs_inter_file",
            ("symbol_not_exported", "tsjs_inter_file") in reasons,
            str(reasons),
        )

        # Type-only and namespace imports must NOT contribute edges or
        # unresolved entries (they never bind a callable identifier).
        helper_calls = [
            t for t in p8_tuples
            if t[3] == "Helper"
        ]
        check(
            "Phase 8: type-only import `Helper` does not produce an edge",
            helper_calls == [],
            str(helper_calls),
        )
        # The namespace `ns` is used only as `ns.foo(...)` (Attribute call);
        # the resolver shouldn't add `ns` to unresolved either.
        ns_unresolved = [
            u for u in p8_unresolved if "ns" in u.raw_target or u.raw_target == "ns"
        ]
        check(
            "Phase 8: namespace import `ns` does not contribute to unresolved",
            ns_unresolved == [],
            str([u.raw_target for u in p8_unresolved]),
        )

        # --- 34. Reference integrity for TS/JS edges. ---
        g_p8 = Graph()
        g_p8.parse(str(p8_out / "inventory.ttl"), format="turtle")
        chunk_subjects_p8 = set(g_p8.subjects(RDF.type, CBML2.Chunk))
        bad_p8 = []
        for e_iri in g_p8.subjects(RDF.type, CBMXR.Edge):
            for pred in (CBMXR.src, CBMXR.dst):
                tgt = next(iter(g_p8.objects(e_iri, pred)), None)
                if tgt is None or tgt not in chunk_subjects_p8:
                    bad_p8.append((str(e_iri), str(pred), str(tgt)))
        check(
            "Phase 8: every TS/JS cbmxr:src/dst resolves to cbml2:Chunk",
            not bad_p8,
            f"bad={bad_p8[:3]}",
        )

        # Manifest fragment reflects the new languages.
        p8_frag = p8_manifest["extensions"]["l3_50_xrefs_artifact"]
        check(
            "Phase 8: by_language reports typescript + javascript buckets",
            "typescript" in p8_frag["by_language"]
            and "javascript" in p8_frag["by_language"],
            str(p8_frag.get("by_language")),
        )

        # =====================================================
        # Phase 10: subclassOf + overrides
        # =====================================================
        p10_fixture = work / "p10_fixture"
        build_phase10_fixture(p10_fixture)
        p10_out = work / "p10_out"
        run_pipeline(p10_fixture, p10_out, with_l2=True)

        p10_lines = (p10_out / "xrefs.jsonl").read_text().splitlines()
        p10_edges = [json.loads(line) for line in p10_lines]

        def _tuple_of(edge: dict) -> tuple:
            return (
                edge["src_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(edge["src_chunk_id"]),
                edge["dst_chunk_id"].split("#")[0],
                _symbol_from_chunk_id(edge["dst_chunk_id"]),
                edge["kind"],
                edge["resolver"],
            )

        all_tuples = {_tuple_of(e) for e in p10_edges}

        # --- 35. Python intra-file subclassOf: Wraps → Local in app.py ---
        check(
            "Phase 10: Python intra-file subclassOf (Wraps → Local)",
            ("app.py", "Wraps", "app.py", "Local", "subclassOf",
             "python_intra_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'subclassOf')}",
        )

        # --- 36. Python inter-file subclassOf: Local → Animal (imported from base.py) ---
        check(
            "Phase 10: Python inter-file subclassOf (Local → Animal)",
            ("app.py", "Local", "base.py", "Animal", "subclassOf",
             "python_inter_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'subclassOf')}",
        )

        # --- 37. Python overrides: Local.speak → Animal.speak (inter-file)
        # AND Wraps.speak → Local.speak (intra-file)
        check(
            "Phase 10: Python override Local.speak → Animal.speak (inter-file)",
            ("app.py", "Local.speak", "base.py", "Animal.speak", "overrides",
             "python_inter_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'overrides')}",
        )
        check(
            "Phase 10: Python override Wraps.speak → Local.speak (intra-file)",
            ("app.py", "Wraps.speak", "app.py", "Local.speak", "overrides",
             "python_intra_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'overrides')}",
        )

        # --- 37b. Local.unique does not override anything ---
        unique_overrides = [
            t for t in all_tuples
            if t[4] == "overrides" and t[1] == "Local.unique"
        ]
        check(
            "Phase 10: Local.unique produces no override edge",
            unique_overrides == [],
            f"unexpected={unique_overrides}",
        )

        # --- 38. Builtin base (`class Builtin(object):`) produces no edge ---
        builtin_subclass = [
            t for t in all_tuples
            if t[4] == "subclassOf" and t[1] == "Builtin"
        ]
        check(
            "Phase 10: builtin base `object` produces no subclassOf edge",
            builtin_subclass == [],
            f"unexpected={builtin_subclass}",
        )

        # --- 39. Attribute base (`pkg.Base`) is deferred — no edge ---
        attr_subclass = [
            t for t in all_tuples
            if t[4] == "subclassOf" and t[1] == "Qualified"
        ]
        check(
            "Phase 10: Attribute base produces no subclassOf edge (deferred)",
            attr_subclass == [],
            f"unexpected={attr_subclass}",
        )

        # --- 40. TS intra-file subclassOf + overrides (LocalSub extends Local) ---
        check(
            "Phase 10: TS intra-file subclassOf (LocalSub → Local)",
            ("src/app.ts", "LocalSub", "src/app.ts", "Local", "subclassOf",
             "tsjs_intra_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'subclassOf')}",
        )
        check(
            "Phase 10: TS intra-file override (LocalSub.greet → Local.greet)",
            ("src/app.ts", "LocalSub.greet", "src/app.ts", "Local.greet",
             "overrides", "tsjs_intra_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'overrides')}",
        )

        # --- 41. TS inter-file subclassOf (Dog extends imported Animal) ---
        check(
            "Phase 10: TS inter-file subclassOf (Dog → Animal in src/base.ts)",
            ("src/app.ts", "Dog", "src/base.ts", "Animal", "subclassOf",
             "tsjs_inter_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'subclassOf')}",
        )
        check(
            "Phase 10: TS inter-file override (Dog.speak → Animal.speak)",
            ("src/app.ts", "Dog.speak", "src/base.ts", "Animal.speak",
             "overrides", "tsjs_inter_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] == 'overrides')}",
        )

        # --- 42. JS subclassOf + overrides intra-file (B extends A) ---
        check(
            "Phase 10: JS intra-file subclassOf (B → A) + override",
            ("src/legacy.js", "B", "src/legacy.js", "A", "subclassOf",
             "tsjs_intra_file") in all_tuples
            and ("src/legacy.js", "B.greet", "src/legacy.js", "A.greet",
                 "overrides", "tsjs_intra_file") in all_tuples,
            f"present={sorted(t for t in all_tuples if t[4] in ('subclassOf', 'overrides'))}",
        )

        # --- 43. Reference integrity for subclassOf / overrides edges ---
        g_p10 = Graph()
        g_p10.parse(str(p10_out / "inventory.ttl"), format="turtle")
        chunk_subjects_p10 = set(g_p10.subjects(RDF.type, CBML2.Chunk))
        bad_p10 = []
        for e_iri in g_p10.subjects(RDF.type, CBMXR.Edge):
            kind_lit = next(iter(g_p10.objects(e_iri, CBMXR.kind)), None)
            if kind_lit is None or str(kind_lit) not in ("subclassOf", "overrides"):
                continue
            for pred in (CBMXR.src, CBMXR.dst):
                tgt = next(iter(g_p10.objects(e_iri, pred)), None)
                if tgt is None or tgt not in chunk_subjects_p10:
                    bad_p10.append((str(e_iri), str(pred), str(tgt)))
        check(
            "Phase 10: every subclassOf/overrides src+dst resolves to cbml2:Chunk",
            not bad_p10,
            f"bad={bad_p10[:3]}",
        )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

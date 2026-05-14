#!/usr/bin/env python3
"""verify_vocab_pipeline.py — end-to-end pipeline test for the L3
controlled vocabulary (Stage 7 of the absorption).

Stages 1-4 are covered by three single-purpose verifiers that drive the
loader / writer / aggregator in isolation. This verifier closes the
loop: it builds a git fixture with identifiers known to exercise the
bundled vocabulary, runs `scripts/run_l3.py` three times (default,
``--no-builtin-vocab``, ``--concept-vocab <override>``), and asserts:

  1. Default run emits >0 ``cbml3:conceptKind`` triples and at least
     one ``skos:Collection`` node, all SHACL-conforming.
  2. Every concept that carries ``cbml3:conceptKind`` also carries a
     ``cbml3:broaderCollection`` pointing at a real ``skos:Collection``.
  3. Every emitted ``skos:Collection`` has a ``cbml3:conceptKindBacking``
     literal matching the closed set of legal kind values, plus at
     least one ``skos:member``.
  4. Aliases collapse end-to-end: ``behaviour``/``behaviors`` and
     ``func``/``funcs`` map to ``behavior``/``function`` in the final
     concept set.
  5. ``--no-builtin-vocab`` mode emits zero typed concepts and zero
     ``skos:Collection`` nodes; SHACL still conforms.
  6. Alias-collapse invariant (the "superset modulo collapse" property
     from the Stage 7 plan): every concept name in the no-vocab run is
     either present in the with-vocab run, OR collapses via the
     bundled vocabulary's alias map onto a canonical that IS present.
     This is the operationally correct form of "no concept is silently
     dropped by the vocab".
  7. ``--concept-vocab <custom.yaml>`` override: only terms declared in
     the custom YAML get tagged; the bundled vocab is bypassed.
  8. Sidecar parity: every concept that has ``cbml3:conceptKind`` in
     ``inventory.ttl`` has matching ``kind``/``broader`` keys in
     ``concepts.json`` (the RDF and JSON layers agree).

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

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF


CBM = Namespace("https://codebase-mapper.example.org/cbm#")
CBML3 = Namespace("https://codebase-mapper.example.org/cbml3#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")

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
            for line in detail.splitlines()[:8]:
                print(f"        {line}")
        FAIL += 1


# Identifiers chosen so the bundled software_primitives.yaml lights up
# on multiple kinds at once:
#   - domain-primitive: behavior (via "behaviour" alias), contract,
#                       intent, effect, expression, operation
#   - structural-primitive: function (via "func" alias),
#                           parameter (via "params" alias)
#   - relational-primitive: edge (via "edge_kind" alias)
# Plus uncurated names (User, Auth, Frobnicator, Widget) to confirm
# typing is gated to the curated set.
FIXTURE_APP = '''"""Behaviour and intent demo for vocabulary pipeline tests."""

from dataclasses import dataclass


@dataclass
class UserBehaviour:
    """A behaviour for the user (intentional British spelling)."""

    name: str

    def authenticate(self, params: dict) -> bool:
        """Authenticate the user — exercises the params alias."""
        return bool(params)


class AuthContract:
    """A contract a user must satisfy."""

    pass


class IntentRegistry:
    """Tracks intents and their effects."""

    pass


class OperationExpression:
    """An expression that is part of an operation."""

    pass


def make_func(edge_kind: str) -> int:
    """A helper func that takes an edge_kind. Exercises func + edge_kind."""

    return len(edge_kind)


class FrobnicatorWidget:
    """Uncurated names — should never be typed."""

    pass
'''


FIXTURE_SVC = '''"""Multi-token compound test."""

from app import UserBehaviour


class BehaviourFactory:
    """Build behaviours. The `behaviour` alias collapses to `behavior`."""

    def build(self, behaviours: list) -> UserBehaviour:
        return behaviours[0]
'''


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    (target / "app.py").write_text(FIXTURE_APP)
    (target / "svc.py").write_text(FIXTURE_SVC)
    (target / "README.md").write_text("# Vocab pipeline fixture\n")
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def run_pipeline(
    fixture: Path, out: Path, repo_root: Path, *,
    no_builtin_vocab: bool = False,
    concept_vocab: Path | None = None,
) -> None:
    cmd = [sys.executable, "scripts/run_l3.py",
           "--repo", str(fixture), "--out", str(out),
           "--backend", "hash"]
    if no_builtin_vocab:
        cmd.append("--no-builtin-vocab")
    if concept_vocab is not None:
        cmd += ["--concept-vocab", str(concept_vocab)]
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    r = subprocess.run(cmd, env=env, cwd=str(repo_root),
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL: pipeline exit", r.returncode)
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(1)


def write_custom_vocab(path: Path) -> None:
    """A vocabulary that bears no resemblance to the bundled one."""
    path.write_text(
        "version: 1\n"
        "kinds:\n"
        "  structural-primitive: [widget, frobnicator]\n"
        "aliases:\n"
        "  widget: [widgets]\n"
        "broader:\n"
        "  structural-primitive: custom_collection\n"
    )


def load_graph(bundle: Path) -> Graph:
    g = Graph()
    g.parse(str(bundle / "inventory.ttl"), format="turtle")
    return g


def concept_names_in(g: Graph) -> set[str]:
    """Local name of every cbmi:concept/<x> in the graph."""
    out: set[str] = set()
    for s in g.subjects(RDF.type, SKOS.Concept):
        iri = str(s)
        # cbmi:concept/<safe_name>
        if "#concept/" in iri:
            out.add(iri.split("#concept/", 1)[1])
    return out


def collection_kinds_in(g: Graph) -> set[str]:
    out: set[str] = set()
    for s in g.subjects(RDF.type, SKOS.Collection):
        for _, _, o in g.triples((s, CBML3.conceptKindBacking, None)):
            out.add(str(o))
    return out


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true",
                   help="Keep the temp workdir for inspection.")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_vocab_pipeline_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        repo_root = Path(__file__).resolve().parent.parent

        out_default = work / "out_default"
        out_off = work / "out_off"
        out_custom = work / "out_custom"
        custom_yaml = work / "custom_vocab.yaml"
        write_custom_vocab(custom_yaml)

        run_pipeline(fixture, out_default, repo_root)
        run_pipeline(fixture, out_off, repo_root, no_builtin_vocab=True)
        run_pipeline(fixture, out_custom, repo_root,
                     concept_vocab=custom_yaml)

        # --- 1. Default run emits typed triples + collections, SHACL conforms ---
        manifest_def = json.loads(
            (out_default / "run_manifest.json").read_text()
        )
        check(
            "default run: SHACL conforms",
            manifest_def["shacl_self_check"]["conforms"],
            manifest_def.get("shacl_self_check", {}).get("report_excerpt", ""),
        )

        g_def = load_graph(out_default)
        kind_count = sum(1 for _ in g_def.triples(
            (None, CBML3.conceptKind, None)
        ))
        collections = list(g_def.subjects(RDF.type, SKOS.Collection))
        check(
            f"default run: {kind_count} cbml3:conceptKind triples (>0)",
            kind_count > 0,
        )
        check(
            f"default run: {len(collections)} skos:Collection nodes (>0)",
            len(collections) > 0,
        )

        # --- 2. Every typed concept points at a real collection ---
        broken_broader: list[str] = []
        for s, _, o in g_def.triples((None, CBML3.broaderCollection, None)):
            if (o, RDF.type, SKOS.Collection) not in g_def:
                broken_broader.append(f"{s} -> {o}")
        check(
            "every conceptKind bearer points at a real skos:Collection",
            not broken_broader,
            "\n".join(broken_broader[:5]),
        )

        # --- 3. Every emitted collection is well-formed ---
        legal_kinds = {"domain-primitive", "structural-primitive",
                       "relational-primitive"}
        bad_collections: list[str] = []
        for c in collections:
            kinds = list(g_def.objects(c, CBML3.conceptKindBacking))
            members = list(g_def.objects(c, SKOS.member))
            if len(kinds) != 1 or str(kinds[0]) not in legal_kinds:
                bad_collections.append(
                    f"{c}: conceptKindBacking={kinds}"
                )
                continue
            if not members:
                bad_collections.append(f"{c}: no skos:member edges")
        check(
            "every skos:Collection has 1 legal conceptKindBacking + ≥1 member",
            not bad_collections,
            "\n".join(bad_collections[:5]),
        )

        # --- 4. Alias collapse end-to-end ---
        names_default = concept_names_in(g_def)
        check(
            "'behaviour' collapses to 'behavior' in default emit",
            "behavior" in names_default and "behaviour" not in names_default,
            f"behavior={('behavior' in names_default)} "
            f"behaviour={('behaviour' in names_default)}",
        )
        check(
            "'func' collapses to 'function' in default emit",
            "function" in names_default and "func" not in names_default,
            f"function={('function' in names_default)} "
            f"func={('func' in names_default)}",
        )

        # --- 5. --no-builtin-vocab disables typing entirely ---
        manifest_off = json.loads(
            (out_off / "run_manifest.json").read_text()
        )
        check(
            "no-vocab run: SHACL conforms",
            manifest_off["shacl_self_check"]["conforms"],
            manifest_off.get("shacl_self_check", {}).get("report_excerpt", ""),
        )
        g_off = load_graph(out_off)
        off_kinds = sum(1 for _ in g_off.triples(
            (None, CBML3.conceptKind, None)
        ))
        off_colls = list(g_off.subjects(RDF.type, SKOS.Collection))
        check(
            f"no-vocab run: 0 cbml3:conceptKind triples (got {off_kinds})",
            off_kinds == 0,
        )
        check(
            f"no-vocab run: 0 skos:Collection nodes (got {len(off_colls)})",
            not off_colls,
        )

        # --- 6. Alias-collapse equivalence (the "superset modulo collapse") ---
        names_off = concept_names_in(g_off)
        from codebase_mapper.emission.infrastructure.vocab import builtin_vocabulary
        vocab = builtin_vocabulary()
        unaccounted: list[str] = []
        for n in names_off:
            if n in names_default:
                continue
            # Try resolving through the bundled aliases. If the alias
            # target is in the default run, we've accounted for n via
            # collapse. Compound names ("_"-joined) aren't in the vocab
            # by design; for those we accept either presence in default
            # or absence (vocab can change cooccurrence frequencies, so
            # compounds may shift).
            canon = vocab.by_alias.get(n)
            if canon and canon in names_default:
                continue
            if "_" in n:
                continue  # compound: lenient
            unaccounted.append(n)
        check(
            "every no-vocab concept either survives or alias-collapses "
            f"(unaccounted: {len(unaccounted)})",
            not unaccounted,
            "\n".join(sorted(unaccounted)[:10]),
        )

        # --- 7. Custom-vocab override bypasses the bundled vocab ---
        manifest_custom = json.loads(
            (out_custom / "run_manifest.json").read_text()
        )
        check(
            "custom-vocab run: SHACL conforms",
            manifest_custom["shacl_self_check"]["conforms"],
            manifest_custom.get("shacl_self_check", {}).get(
                "report_excerpt", "",
            ),
        )
        g_custom = load_graph(out_custom)
        # Only `widget` and `frobnicator` are curated in the custom YAML.
        # The fixture contains `FrobnicatorWidget` so both should appear.
        tagged_in_custom: dict[str, str] = {}
        for s, _, o in g_custom.triples((None, CBML3.conceptKind, None)):
            iri = str(s)
            if "#concept/" in iri:
                tagged_in_custom[iri.split("#concept/", 1)[1]] = str(o)
        check(
            "custom-vocab run: only YAML-declared terms get tagged",
            set(tagged_in_custom).issubset({"widget", "frobnicator"})
            and len(tagged_in_custom) > 0,
            f"tagged: {tagged_in_custom}",
        )
        # The bundled vocab's `behavior` MUST NOT be tagged in custom mode.
        if "behavior" in concept_names_in(g_custom):
            check(
                "custom-vocab run: 'behavior' from bundled vocab is NOT tagged",
                (URIRef(
                    "https://codebase-mapper.example.org/cbm/instance#concept/behavior"
                ), CBML3.conceptKind, None) not in g_custom,
            )

        # --- 8. Sidecar parity: concepts.json matches RDF ---
        concepts_json = json.loads(
            (out_default / "concepts.json").read_text()
        )["concepts"]
        rdf_typed: dict[str, tuple[str, str]] = {}
        for s, _, kind in g_def.triples((None, CBML3.conceptKind, None)):
            iri = str(s)
            if "#concept/" not in iri:
                continue
            name = iri.split("#concept/", 1)[1]
            broader_uri = next(iter(g_def.objects(s, CBML3.broaderCollection)),
                               None)
            broader = (str(broader_uri).split("#collection/", 1)[1]
                       if broader_uri else "")
            rdf_typed[name] = (str(kind), broader)

        mismatches: list[str] = []
        for name, (kind, broader) in rdf_typed.items():
            j = concepts_json.get(name, {})
            if j.get("kind") != kind:
                mismatches.append(
                    f"{name}: RDF kind={kind!r} JSON kind={j.get('kind')!r}"
                )
            if j.get("broader") != broader:
                mismatches.append(
                    f"{name}: RDF broader={broader!r} "
                    f"JSON broader={j.get('broader')!r}"
                )
        check(
            "concepts.json kind/broader matches inventory.ttl for every typed concept",
            not mismatches,
            "\n".join(mismatches[:5]),
        )

    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"workdir kept at {work}")

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

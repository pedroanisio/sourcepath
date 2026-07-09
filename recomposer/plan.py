"""Recomposition scheduler.

Consumes a Decomposer document (the Part II YAML, already parsed to a dict) and
produces a :class:`BuildPlan`: an ordered, dependency-aware sequence of
natural-language construction steps able to recreate the system.

Algorithm
---------
1. **Units.** Module/package parts are the unit of construction. Mutually
   dependent modules (SCCs of the decomposition's own dependency edges) merge
   into one *joint unit*; when the decomposition carries a ``cycle_resolutions``
   file-level order for the group, the joint step lists files in that order.
2. **Nominal phase.** Each unit gets a canonical Part III phase from its
   classification (domain→3, ports/shared-kernel→4, core/supporting→5,
   adapter/infrastructure→6, test→10).
3. **Phase relaxation.** Dependency evidence overrides the canonical order:
   processing units in descending build-order layer, every unit pulls its
   dependencies' phase down to its own (a dependency may never be scheduled
   after its dependent). Joint units take the minimum phase of their members.
4. **Ordering.** Steps sort by ``(effective phase, build-order layer, name)``.
   Because a dependency always has a strictly lower layer than its dependents
   (SCC-condensed DAG) and never a later phase (step 3), every ``requires``
   reference points backward. This invariant is asserted, not assumed.
5. **Fixed steps** (skeleton, environment, schemas, ops, validation, docs) are
   woven around the unit steps at their canonical phases.

Determinism: the plan is a pure function of the decomposition document — all
collections are sorted and no wall-clock input is used.
"""
from __future__ import annotations

from typing import Any

# Pure graph math shared with the Decomposer (no bundle/repository access —
# the "consumes only the decomposition document" boundary is about data
# provenance, not code reuse).
from decomposer.metrics import cycles as _cycles

from .model import PHASES, PHASE_TITLE, SCHEMA_VERSION, BuildPlan, BuildStep

_PHASE_BY_TITLE = {title: n for n, _key, title in PHASES}

_CONF_RANK = {"certain": 0, "strong": 1, "probable": 2, "weak": 3, "unknown": 4}
_BIG_LAYER = 10**6   # sort-after-everything layer for parts missing from build_order


def recompose(doc: dict[str, Any]) -> BuildPlan:
    parts: dict[str, dict] = {p["id"]: p for p in doc.get("parts", [])}
    module_parts = {
        pid: p for pid, p in parts.items() if p.get("kind") in {"module", "package"}
    }
    layer_of = _layer_index(doc)
    units = _units(doc, module_parts)
    _relax_phases(units, module_parts, layer_of)

    ext_deps = _external_deps_by_module(doc)
    test_edges = _tests_by_subject(doc)
    test_gaps = {
        q["subject"] for q in doc.get("quality_gates", [])
        if q.get("gate") == "test_gap"
    }
    dead_code_by_module = _dead_code_by_module(doc, module_parts)
    resolutions = {
        frozenset(c.get("members", [])): c
        for c in doc.get("cycle_resolutions", [])
    }
    file_orderings = {
        f["part"]: f for f in doc.get("file_orderings", []) if f.get("part")
    }

    specs: list[dict] = []
    specs.append(_skeleton_spec(doc, parts))
    specs.append(_environment_spec(doc, parts, module_parts))
    fixture_spec = _fixture_manifest_spec(parts, module_parts)
    if fixture_spec:
        specs.append(fixture_spec)
    schema_spec, schema_skip = _schema_spec(doc, parts)
    if schema_spec:
        specs.append(schema_spec)
    for u in units.values():
        specs.append(_unit_spec(u, module_parts, layer_of, ext_deps,
                                test_edges, test_gaps, dead_code_by_module,
                                resolutions, file_orderings))
    specs.extend(_delivery_specs(doc, parts, units))
    persistence_skip = _persistence_skip(module_parts)
    specs.extend(_ops_specs(parts))
    specs.append(_validation_spec(doc))
    docs_spec, docs_skip = _docs_spec(parts)
    if docs_spec:
        specs.append(docs_spec)

    # ── order, number, resolve requires ──────────────────────────────────────
    specs.sort(key=lambda s: (s["phase"], s["layer"], s["name"]))
    step_of_part: dict[str, int] = {}
    for i, s in enumerate(specs, start=1):
        s["number"] = i
        for pid in s["parts"]:
            step_of_part[pid] = i

    # Single-owner pass: the first step (in execution order) to list a file
    # owns its creation; any later step re-listing it *modifies* an existing
    # file. Without this, an executor would overwrite earlier output (e.g. a
    # wiring step re-creating the entry file its module step already built).
    claimed: set[str] = set()
    for s in specs:
        s["modifies"] = [f for f in s["creates"] if f in claimed]
        s["creates"] = [f for f in s["creates"] if f not in claimed]
        claimed.update(s["creates"])

    steps: list[BuildStep] = []
    for s in specs:
        requires = sorted({
            step_of_part[pid] for pid in s["requires_parts"]
            if pid in step_of_part and step_of_part[pid] != s["number"]
        } | set(s.get("requires_steps", [])))
        forward = [r for r in requires if r >= s["number"]]
        if forward:
            raise ValueError(
                f"forward dependency in plan: step {s['number']} "
                f"({s['goal'][:60]}) requires {forward}"
            )
        steps.append(BuildStep(
            number=s["number"], phase=s["phase"], goal=s["goal"],
            rationale=s["rationale"], requires=requires,
            creates=s["creates"], creates_ordered=s.get("creates_ordered", False),
            modifies=s["modifies"], contracts=s["contracts"],
            dependencies_introduced=s["deps_introduced"],
            tests_required=s["tests_required"], evidence=s["evidence"],
            expected_result=s["expected_result"], confidence=s["confidence"],
            assumptions=s["assumptions"], parts=s["parts"],
        ))

    skipped = [x for x in (schema_skip, persistence_skip, docs_skip) if x]
    _fill_silent_phase_gaps(steps, skipped)
    open_assumptions = _open_assumptions(doc, steps, skipped)
    unassigned_files = _unassigned_files(parts, claimed)

    return BuildPlan(
        repository=dict(doc.get("repository", {})),
        architecture_intent=_architecture_intent(doc),
        steps=steps,
        skipped_phases=skipped,
        open_assumptions=open_assumptions,
        unassigned_files=unassigned_files,
        provenance={
            "tool": f"codebase-mapper recomposer v{SCHEMA_VERSION}",
            "schema_version": SCHEMA_VERSION,
            "consumes": "Decomposer YAML only (no raw-bundle access)",
            "source_decomposition": doc.get("provenance", {}).get("tool"),
            "source_bundle": doc.get("provenance", {}).get("bundle_dir"),
            "determinism": "pure function of the decomposition document",
        },
    )


def _fill_silent_phase_gaps(steps: list[BuildStep], skipped: list[dict]) -> None:
    """Every canonical phase either has steps or an explicit skip entry.

    The curated skip reasons (schema/persistence/docs) explain *why* in
    domain terms; a phase can still end up empty for reasons none of those
    cover (e.g. no adapter/infrastructure-layer modules, no delivery
    surfaces, no operational parts) — those get a generic but explicit note
    instead of silently vanishing from the rendered plan. Sorted back into
    phase order so ``skipped_phases`` stays deterministic regardless of
    which reasons were curated vs. generic.
    """
    present = {s.phase for s in steps}
    covered = {s["phase"] for s in skipped}
    for n, _key, title in PHASES:
        if n not in present and title not in covered:
            skipped.append({
                "phase": title,
                "reason": "no parts in the decomposition map to this phase "
                          "(nothing classified into it, and no fixed step "
                          "for it fired)",
            })
    skipped.sort(key=lambda s: _PHASE_BY_TITLE.get(s["phase"], 99))


def _unassigned_files(parts: dict[str, dict], owned: set[str]) -> list[dict[str, str]]:
    """Files the decomposition carries that no step creates or modifies.

    Swept once, after every spec has claimed its files, against every part's
    evidence — not just the kinds ``recompose`` knows how to schedule. A part
    kind added upstream without a matching spec builder here shows up as an
    unassigned file with a generic reason instead of disappearing.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for pid, p in sorted(parts.items()):
        for f in (p.get("evidence") or {}).get("files", []):
            if f in owned or f in seen:
                continue
            seen.add(f)
            if p.get("kind") == "generated_artifact":
                reason = (f"generated artifact (regenerated by its build tool, "
                          f"not hand-authored) — see part `{pid}`")
            else:
                reason = (f"part `{pid}` (kind={p.get('kind')}) has no matching "
                          f"build-step rule in this recomposer version — verify "
                          f"manually whether it belongs in the plan")
            out.append({"path": f, "part": pid, "reason": reason})
    return sorted(out, key=lambda u: u["path"])


# ── units ─────────────────────────────────────────────────────────────────────
class _Unit:
    __slots__ = ("members", "phase", "layer", "name")

    def __init__(self, members: list[str]):
        self.members = sorted(members)
        self.name = self.members[0]
        self.phase = 5
        self.layer = 0


def _units(doc: dict, module_parts: dict[str, dict]) -> dict[str, _Unit]:
    """Merge mutually dependent modules into joint units; others stand alone.

    Cycle groups are the SCCs of the decomposition's *own* module dependency
    edges (``dependencies.outgoing``) — not parsed out of quality-gate findings,
    whose names and evidence formats are reporting policy and may change. The
    scheduler's no-forward-references invariant therefore only depends on data
    the Decomposer is contractually required to emit.
    """
    adjacency = {
        pid: sorted(
            d for d in (p.get("dependencies") or {}).get("outgoing", [])
            if d in module_parts and d != pid
        )
        for pid, p in module_parts.items()
    }
    cycle_groups = _cycles(sorted(module_parts), adjacency)

    owner: dict[str, _Unit] = {}
    units: dict[str, _Unit] = {}
    for g in cycle_groups:
        u = _Unit(sorted(g))
        units[u.name] = u
        for pid in u.members:
            owner[pid] = u
    for pid in sorted(module_parts):
        if pid not in owner:
            u = _Unit([pid])
            units[u.name] = u
            owner[pid] = u

    for u in units.values():
        u.phase = min(_nominal_phase(module_parts[pid]) for pid in u.members)
    return units


def _owner_map(units: dict[str, _Unit]) -> dict[str, _Unit]:
    return {pid: u for u in units.values() for pid in u.members}


def _nominal_phase(part: dict) -> int:
    role = (part.get("classification") or {}).get("role")
    layer = part.get("layer")
    name = part.get("name") or ""
    if role == "test":
        return 10
    if name.endswith("/ports") or name.endswith("ports"):
        return 4
    if layer == "shared_kernel":
        return 4
    if layer == "domain":
        return 3
    if role in {"adapter", "infrastructure", "generated"}:
        return 6
    return 5   # core, supporting, unclassified


def _layer_index(doc: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, layer in enumerate(doc.get("build_order", [])):
        for pid in layer:
            out[pid] = i
    return out


def _relax_phases(
    units: dict[str, _Unit],
    module_parts: dict[str, dict],
    layer_of: dict[str, int],
) -> None:
    """Pull dependencies' phases down to their dependents' (never push later).

    Units are processed in *descending* layer order. In the SCC-condensed
    dependency DAG a dependency always sits at a strictly lower layer than its
    dependents, so every unit's phase is already final when it propagates to
    its dependencies — a single pass suffices.
    """
    owner = _owner_map(units)
    for u in units.values():
        u.layer = min((layer_of.get(pid, _BIG_LAYER) for pid in u.members),
                      default=_BIG_LAYER)
    ordered = sorted(units.values(), key=lambda u: (-u.layer, u.name))
    for u in ordered:
        dep_units: set[str] = set()
        for pid in u.members:
            for dep_pid in (module_parts[pid].get("dependencies") or {}).get("outgoing", []):
                du = owner.get(dep_pid)
                if du is not None and du.name != u.name:
                    dep_units.add(du.name)
        for dn in dep_units:
            units[dn].phase = min(units[dn].phase, u.phase)


# ── fixed step specs ──────────────────────────────────────────────────────────
def _spec(
    *, phase: int, layer: int, name: str, goal: str, rationale: str,
    parts: list[str], requires_parts: list[str], creates: list[str],
    contracts: list[str], deps_introduced: list[str], tests_required: list[str],
    evidence: list[str], expected_result: str, confidence: str,
    assumptions: list[str], requires_steps: list[int] | None = None,
) -> dict:
    return {
        "phase": phase, "layer": layer, "name": name, "goal": goal,
        "rationale": rationale, "parts": parts, "requires_parts": requires_parts,
        "creates": creates, "contracts": contracts,
        "deps_introduced": deps_introduced, "tests_required": tests_required,
        "evidence": evidence, "expected_result": expected_result,
        "confidence": confidence, "assumptions": assumptions,
        "requires_steps": requires_steps or [],
    }


def _skeleton_spec(doc: dict, parts: dict[str, dict]) -> dict:
    repo = doc.get("repository", {})
    top_dirs = sorted({
        p["name"].split("/")[0]
        for p in parts.values()
        if p.get("kind") in {"module", "package"} and p.get("name") not in ("(root)",)
    })
    style = (doc.get("detected_architecture") or {}).get("style", "undetermined")
    return _spec(
        phase=1, layer=-1, name="",
        goal=f"Establish the project skeleton for `{repo.get('name')}`.",
        rationale=(f"The decomposition identified {repo.get('n_parts')} parts in a "
                   f"'{style}' organization; create the top-level package layout "
                   f"before any code so every later step has a stable home."),
        parts=["plan:skeleton"], requires_parts=[],
        creates=[f"{d}/" for d in top_dirs],
        contracts=[], deps_introduced=[],
        tests_required=["repository initializes (VCS) and directory layout matches"],
        evidence=[f"repository header (files={repo.get('files')}, "
                  f"commit {str(repo.get('commit_sha'))[:12]})"],
        expected_result="Empty, versioned project tree with the top-level directories in place.",
        confidence="certain",
        assumptions=[],
    )


def _test_dir_owned(path: str, module_parts: dict[str, dict]) -> bool:
    """True when *path* belongs to a test-owned subtree.

    Two evidence-grounded shapes (module==directory):
    * an *ancestor* directory is a test-role module (file inside a test tree);
    * the file sits at a fixture-package root: its own directory is non-root
      and every module at-or-under it is test-role (e.g. a fixture repo's
      ``pubspec.yaml`` whose code lives in ``<pkg>/lib``). A mixed subtree
      (like a UI package that merely *contains* a __tests__ module) stays
      non-test.
    """
    roles = {
        p["name"]: (p.get("classification") or {}).get("role")
        for p in module_parts.values()
    }
    segs = path.split("/")[:-1]
    if any(roles.get("/".join(segs[:i + 1])) == "test" for i in range(len(segs))):
        return True
    d = "/".join(segs)
    if not d:
        return False
    under = [r for n, r in roles.items() if n == d or n.startswith(d + "/")]
    return bool(under) and all(r == "test" for r in under)


def _split_manifests(
    parts: dict[str, dict], module_parts: dict[str, dict]
) -> tuple[list[str], list[str]]:
    dep_mgmt = parts.get("ops:dependency_management")
    manifests = (dep_mgmt or {}).get("evidence", {}).get("files", [])
    env = [m for m in manifests if not _test_dir_owned(m, module_parts)]
    fixture = [m for m in manifests if _test_dir_owned(m, module_parts)]
    return env, fixture


def _environment_spec(
    doc: dict, parts: dict[str, dict], module_parts: dict[str, dict]
) -> dict:
    ext = sorted(
        (p for p in parts.values() if p.get("kind") == "external_dependency"),
        key=lambda p: (-(p.get("metrics") or {}).get("importer_modules", 0), p["name"]),
    )
    dep_mgmt = parts.get("ops:dependency_management")
    manifests, fixture = _split_manifests(parts, module_parts)
    assumptions = []
    if manifests:
        assumptions.append(
            "dependency versions are pinned in lockfiles; exact versions must "
            "be taken from the original lockfiles, which the decomposition "
            "lists but does not inline")
    else:
        assumptions.append("no dependency manifests were captured in the decomposition")
    if fixture:
        assumptions.append(
            f"{len(fixture)} manifest(s) under test-owned directories are test "
            f"fixtures, not environment inputs — they are set up with the test "
            f"phase, not installed here")
    return _spec(
        phase=2, layer=-1, name="",
        goal="Configure the package/build/runtime environment and declare all external dependencies.",
        rationale=("Every later step imports from this dependency set; declaring it "
                   "up front makes each subsequent step independently buildable."),
        parts=["plan:environment"] + ([dep_mgmt["id"]] if dep_mgmt else []),
        requires_parts=[], requires_steps=[1],
        creates=manifests,
        contracts=[],
        deps_introduced=[f"{p['name']} (used by {(p.get('metrics') or {}).get('importer_modules', '?')} modules)"
                         for p in ext],
        tests_required=["dependency install succeeds from the declared manifests"],
        evidence=[p["id"] for p in ext] + ([dep_mgmt["id"]] if dep_mgmt else []),
        expected_result="Reproducible environment: all third-party packages install from manifests.",
        confidence="certain",
        assumptions=assumptions,
    )


def _fixture_manifest_spec(
    parts: dict[str, dict], module_parts: dict[str, dict]
) -> dict | None:
    _, fixture = _split_manifests(parts, module_parts)
    if not fixture:
        return None
    return _spec(
        phase=10, layer=-2, name="",   # before the test modules that use them
        goal="Recreate test-fixture dependency manifests.",
        rationale=("These manifests live inside test-owned directories: they are "
                   "inputs *to tests* (fixture repositories), not environment "
                   "declarations, so they are built with the test phase."),
        parts=["plan:fixture-manifests"], requires_parts=[], requires_steps=[1],
        creates=fixture,
        contracts=[], deps_introduced=[],
        tests_required=["fixture-consuming tests can parse these manifests"],
        evidence=["ops:dependency_management (test-subtree slice)"],
        expected_result="Fixture manifests in place for the test modules that read them.",
        confidence="certain",
        assumptions=[],
    )


def _schema_spec(doc: dict, parts: dict[str, dict]) -> tuple[dict | None, dict | None]:
    schemas = sorted(
        (p for p in parts.values() if p.get("kind") == "data_schema"),
        key=lambda p: p["name"],
    )
    domains = sorted(
        (p for p in parts.values() if p.get("kind") == "domain"),
        key=lambda p: p["name"],
    )
    if not schemas and not domains:
        return None, {"phase": PHASE_TITLE[3],
                      "reason": "no data_schema or domain parts in the "
                                "decomposition — the schema-definition step is "
                                "omitted (domain-layer modules, if any, still "
                                "build in this phase)"}
    assumptions = [
        f"domain '{d['name']}' groups {(d.get('metrics') or {}).get('n_concepts')} "
        f"concepts ({d.get('overall_confidence')}) — interpretive, validate against "
        f"the rebuilt code" for d in domains
    ]
    llm_hints = [
        s for p in schemas for s in (p.get("evidence") or {}).get("llm_summaries", [])
    ][:5]
    if llm_hints:
        assumptions.append(
            "schema purposes below are LLM-authored and unverified (PALS's Law); "
            "verify each against the actual schema file content"
        )
    return _spec(
        phase=3, layer=-1, name="",
        goal="Define the data contracts: schemas and serialization formats.",
        rationale=("Data contracts are consumed by core logic and adapters alike; "
                   "fixing them first prevents later rework."),
        parts=[p["id"] for p in schemas] + [d["id"] for d in domains],
        requires_parts=[], requires_steps=[1, 2],
        creates=sorted({f for p in schemas for f in (p.get("evidence") or {}).get("files", [])}),
        contracts=[p["name"] for p in schemas],
        deps_introduced=[],
        tests_required=["each schema validates against at least one example document"],
        evidence=[p["id"] for p in schemas + domains] + llm_hints,
        expected_result="All serialization contracts exist and validate example payloads.",
        confidence="probable",
        assumptions=assumptions,
    ), None


# Languages whose modules can be validated by importing/compiling them; other
# languages (css, protobuf, shell, ...) get a parse-level check instead.
_IMPORTABLE = frozenset({
    "python", "typescript", "javascript", "rust", "go", "java", "kotlin",
    "swift", "dart", "ruby", "c", "cpp", "objective-c", "clojure",
})


def _precise_check(m: dict) -> str | None:
    """A toolchain-exact validation command for module *m*, when it's derivable
    from data the decomposition actually carries (module path, crate name) —
    never a guessed build tool. Java/Kotlin/C/C++/... stay on the generic path:
    inventing a Maven/Gradle/CMake invocation without evidence of which one the
    project uses would be an unverifiable claim (PALS's Law), worse than the
    honest "smoke-import/compile" default.
    """
    langs = set((m.get("metrics") or {}).get("languages", []))
    crate = (m.get("metrics") or {}).get("crate")
    if langs == {"rust"} and crate:
        return f"cargo check -p {crate}"
    if langs == {"go"}:
        return f"go build ./{m['name']}/..."
    if langs and langs <= {"typescript", "javascript"} and "typescript" in langs:
        return "tsc --noEmit"
    return None


def _unit_validation(members: list[dict]) -> str:
    precise = sorted({c for m in members if (c := _precise_check(m))})
    generic_members = [m for m in members if not _precise_check(m)]
    if not generic_members:
        return "; ".join(precise)

    langs = sorted({
        lang for m in generic_members
        for lang in (m.get("metrics") or {}).get("languages", [])
    })
    importable = [l for l in langs if l in _IMPORTABLE]
    other = [l for l in langs if l not in _IMPORTABLE]
    if importable and not other:
        generic = f"smoke-import/compile the module ({', '.join(importable)})"
    elif importable and other:
        generic = (f"smoke-import/compile the {', '.join(importable)} files; "
                   f"validate the {', '.join(other)} files parse")
    elif other:
        generic = f"validate files parse/lint cleanly ({', '.join(other)})"
    else:
        generic = "verify files are well-formed (language not recorded in decomposition)"
    return "; ".join(precise + [generic]) if precise else generic


def _unit_spec(
    u: _Unit, module_parts: dict[str, dict], layer_of: dict[str, int],
    ext_deps: dict[str, list[str]], test_edges: dict[str, list[str]],
    test_gaps: set[str], dead_code_by_module: dict[str, int],
    resolutions: dict[frozenset, dict], file_orderings: dict[str, dict],
) -> dict:
    members = [module_parts[pid] for pid in u.members]
    joint = len(members) > 1
    names = [m["name"] for m in members]
    files = sorted({f for m in members for f in (m.get("evidence") or {}).get("files", [])})
    contracts = sorted({c for m in members for c in m.get("interface_symbols", [])})
    dep_parts = sorted({
        d for m in members
        for d in (m.get("dependencies") or {}).get("outgoing", [])
        if d not in set(u.members)
    })
    ext = sorted({e for pid in u.members for e in ext_deps.get(pid, [])})
    tests_required = sorted({
        f"covered by tests in `{t.split(':', 1)[1]}`"
        for pid in u.members for t in test_edges.get(pid, [])
    })
    for pid in u.members:
        if pid in test_gaps:
            tests_required.append(
                f"no test evidence for `{pid.split(':', 1)[1]}` — author tests "
                f"(test_gap finding, probable)")
    tests_required.sort()

    # Cycle resolution: if the decomposition carries a file-level topological
    # order for this group, list the files in that order instead of asking the
    # executor to "build together" blind.
    resolution = resolutions.get(frozenset(u.members)) if joint else None
    file_order = (resolution or {}).get("file_order") or []
    # Non-cyclic ordering evidence for a single unit (currently: Alembic
    # revision chains) — a different source than cycle_resolutions but the
    # same idea: list files in a proven order instead of plain lexicographic.
    ordering = file_orderings.get(u.members[0]) if not joint else None
    ordering_file_order = (ordering or {}).get("file_order") or []
    creates_ordered = False
    if joint and file_order:
        in_order = set(file_order)
        files = list(file_order) + [f for f in files if f not in in_order]
        creates_ordered = True
    elif not joint and ordering_file_order:
        in_order = set(ordering_file_order)
        files = list(ordering_file_order) + [f for f in files if f not in in_order]
        creates_ordered = True

    assumptions: list[str] = []
    if joint and creates_ordered:
        assumptions.append(
            "these modules are cyclic at directory granularity, but the cycle "
            "dissolves at file granularity — the `creates` list is a valid "
            "file-level construction order (cycle_resolutions evidence)")
    elif joint:
        assumptions.append(
            "these modules form a dependency cycle at the decomposition's "
            "module granularity (module==directory is a `probable` model)"
            + ((" and " + resolution["note"]) if resolution and resolution.get("note")
               else "; no file-level order is available") +
            ". Build them together — breaking the cycle diverges from the "
            "original structure")
    elif creates_ordered:
        note = ordering.get("note") or "see decomposition file_orderings"
        assumptions.append(
            f"file order is proven by evidence outside the import graph, not "
            f"by dependency analysis ({note})")
    for m in members:
        if _CONF_RANK.get(m.get("overall_confidence", "unknown"), 4) >= 3:
            assumptions.append(
                f"`{m['name']}` responsibility is {m.get('overall_confidence')}-"
                f"confidence; inspect original sources when rebuilding")
    dead = sum(dead_code_by_module.get(pid, 0) for pid in u.members)
    if dead:
        assumptions.append(
            f"{dead} file(s) in this unit are dead-code candidates (probable); "
            f"verify necessity before reimplementing them")

    role = members[0].get("classification", {}).get("role", "supporting")
    resp = "; ".join(
        f"`{m['name']}`: {m.get('responsibility', '')}" for m in members
    )
    if joint and creates_ordered:
        goal = (f"Build the {len(members)}-module group in file order: "
                + ", ".join(f"`{n}`" for n in names) + ".")
        rationale = ("These directories are mutually dependent as aggregates, "
                     "but their files form a DAG — follow the `creates` list "
                     "top to bottom and every import target exists before its "
                     "importer. " + resp)
    elif joint:
        goal = (f"Build the {len(members)}-module group together: "
                + ", ".join(f"`{n}`" for n in names) + ".")
        rationale = ("Dependency evidence shows these modules are mutually "
                     "dependent (one SCC); no linear order exists among them. " + resp)
    elif creates_ordered:
        goal = f"Implement module `{names[0]}` ({role}) in the given file order."
        rationale = (resp + " Files must be created in the listed order — it "
                     "comes from evidence outside the import graph (see "
                     "assumptions), not from dependency analysis.")
    else:
        goal = f"Implement module `{names[0]}` ({role})."
        rationale = resp

    metrics = members[0].get("metrics", {})
    conf = max((m.get("overall_confidence", "unknown") for m in members),
               key=lambda c: _CONF_RANK.get(c, 4))
    spec = _spec(
        phase=u.phase, layer=u.layer, name=u.name,
        goal=goal, rationale=rationale,
        parts=list(u.members), requires_parts=dep_parts,
        requires_steps=[2] if ext else [],
        creates=files,
        contracts=contracts,
        deps_introduced=[d for d in dep_parts] + [f"external: {e}" for e in ext],
        tests_required=tests_required or [_unit_validation(members)],
        evidence=[f"{pid} (Ca={module_parts[pid].get('metrics', {}).get('ca')}, "
                  f"Ce={module_parts[pid].get('metrics', {}).get('ce')}, "
                  f"I={module_parts[pid].get('metrics', {}).get('instability')})"
                  for pid in u.members],
        expected_result=(
            f"Module(s) import cleanly and expose the listed contracts"
            + (f"; {metrics.get('n_symbols')} symbols expected in `{names[0]}` "
               f"(full inventory in the decomposition part's evidence.symbols)"
               if not joint and metrics.get("n_symbols") else "") + "."),
        confidence=conf,
        assumptions=assumptions,
    )
    spec["creates_ordered"] = creates_ordered
    return spec


def _delivery_specs(
    doc: dict, parts: dict[str, dict], units: dict[str, _Unit]
) -> list[dict]:
    owner = _owner_map(units)
    entries_by_dir: dict[str, list[dict]] = {}
    for p in parts.values():
        if p.get("kind") != "entrypoint":
            continue
        d = p["name"].rsplit("/", 1)[0] if "/" in p["name"] else "(root)"
        entries_by_dir.setdefault(d, []).append(p)

    specs: list[dict] = []
    apps = sorted(
        (p for p in parts.values() if p.get("kind") in {"application", "service"}),
        key=lambda p: p["name"],
    )
    for app in apps:
        eps = sorted(entries_by_dir.get(app["name"], []), key=lambda p: p["name"])
        ep_files = sorted({f for e in eps for f in (e.get("evidence") or {}).get("files", [])})
        dep_parts = sorted({
            d for e in eps for d in (e.get("dependencies") or {}).get("outgoing", [])
        } | ({f"module:{app['name']}"} if f"module:{app['name']}" in parts else set()))
        # Map module deps through cycle ownership so requires resolve to units.
        dep_parts = sorted({owner[d].members[0] if d in owner else d for d in dep_parts})
        llm_all = [s for e in eps
                   for s in (e.get("evidence") or {}).get("llm_summaries", [])]
        llm_flat = llm_all[:2]
        if len(llm_all) > 2:
            llm_flat.append(f"(+{len(llm_all) - 2} more LLM summaries in the "
                            f"decomposition's entrypoint parts)")
        specs.append(_spec(
            phase=7, layer=_BIG_LAYER, name=app["name"],
            goal=(f"Wire the `{app['name']}` {app['kind']}: create its entry "
                  f"point(s) and expose the delivery surface."),
            rationale=(app.get("responsibility", "") +
                       " Entry points bind previously built modules into a runnable surface."),
            parts=[app["id"]] + [e["id"] for e in eps],
            requires_parts=dep_parts,
            creates=ep_files,
            contracts=sorted({s for e in eps for s in (e.get("evidence") or {}).get("symbols", [])}),
            deps_introduced=dep_parts,
            tests_required=[f"launch `{f}` and verify it starts" for f in ep_files],
            evidence=[app["id"]] + [e["id"] for e in eps] +
                     [f"LLM (unverified): {s}" for s in llm_flat],
            expected_result=f"`{app['name']}` starts and serves/executes end to end.",
            confidence=app.get("overall_confidence", "strong"),
            assumptions=(["entry-point detection is heuristic (strong); confirm "
                          "invocation commands against original docs"]),
        ))
    return specs


def _persistence_skip(module_parts: dict[str, dict]) -> dict | None:
    hits = [
        p["name"] for p in module_parts.values()
        if any(k in p["name"].lower() for k in ("migration", "database", "/db", "orm", "sql"))
    ]
    if hits:
        return None
    return {"phase": PHASE_TITLE[8],
            "reason": "no persistence/migration evidence in the decomposition "
                      "(no module names matching migration/database/orm/sql)"}


def _ops_specs(parts: dict[str, dict]) -> list[dict]:
    specs = []
    ops = sorted(
        (p for p in parts.values()
         if p.get("kind") == "operational" and p["id"] != "ops:dependency_management"),
        key=lambda p: p["name"],
    )
    for p in ops:
        files = (p.get("evidence") or {}).get("files", [])
        specs.append(_spec(
            phase=9, layer=_BIG_LAYER, name=p["name"],
            goal=f"Recreate {p['name'].replace('_', ' ')}: {p.get('responsibility', '')}",
            rationale="Operational files are graph-typed with certainty; their "
                      "contents must be authored to match the rebuilt system.",
            parts=[p["id"]], requires_parts=[], requires_steps=[1],
            creates=files,
            contracts=[], deps_introduced=[],
            tests_required=[f"{p['name']} executes/validates (e.g. build runs, CI parses)"],
            evidence=[p["id"], f"{(p.get('metrics') or {}).get('n_files')} file(s), "
                              f"types {(p.get('metrics') or {}).get('file_types')}"],
            expected_result=f"{p['name'].replace('_', ' ')} operational.",
            confidence=p.get("overall_confidence", "certain"),
            assumptions=[],
        ))
    return specs


def _validation_spec(doc: dict) -> dict:
    checklist: list[str] = ["full test suite passes"]
    for q in doc.get("quality_gates", []):
        if q.get("severity") in {"error", "warning"}:
            checklist.append(
                f"[{q.get('gate')}] {q.get('description')} "
                f"(reproduce or deliberately improve; confidence {q.get('confidence')})")
    viol = (doc.get("detected_architecture") or {}).get("violations", [])
    return _spec(
        phase=11, layer=_BIG_LAYER, name="",
        goal="Validate full-system behavior against the decomposition's findings.",
        rationale=("The original system's measured structure — including its "
                   "known defects — is the acceptance baseline; deviations must "
                   "be deliberate, not accidental."),
        parts=["plan:validation"], requires_parts=[],
        creates=[], contracts=[], deps_introduced=[],
        tests_required=sorted(set(checklist)),
        evidence=[f"{len(doc.get('quality_gates', []))} quality findings, "
                  f"{len(viol)} architecture violations in the decomposition"],
        expected_result="System behaves per baseline; every intentional deviation is documented.",
        confidence="strong",
        assumptions=["behavioral equivalence cannot be fully proven from static "
                     "evidence alone; runtime comparison against the original is advised"],
    )


def _docs_spec(parts: dict[str, dict]) -> tuple[dict | None, dict | None]:
    doc_part = parts.get("docs:documentation")
    if not doc_part:
        return None, {"phase": PHASE_TITLE[12],
                      "reason": "no documentation part in the decomposition"}
    files = (doc_part.get("evidence") or {}).get("files", [])
    return _spec(
        phase=12, layer=_BIG_LAYER, name="",
        goal="Document usage and extension points.",
        rationale="Documentation captures the operating knowledge the graph cannot.",
        parts=[doc_part["id"]], requires_parts=[],
        creates=files,
        contracts=[], deps_introduced=[],
        tests_required=["docs build/lint passes; links resolve"],
        evidence=[doc_part["id"], f"{len(files)} documentation file(s)"],
        expected_result="Usage, architecture, and extension documentation in place.",
        confidence="certain",
        assumptions=["documentation *content* must be re-authored; only the file "
                     "inventory is evidenced"],
    ), None


# ── evidence extraction helpers ───────────────────────────────────────────────
def _external_deps_by_module(doc: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in doc.get("relationships", []):
        if r.get("type") == "imports_external":
            out.setdefault(r["from"], []).append(r["to"].split(":", 1)[1])
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def _tests_by_subject(doc: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in doc.get("relationships", []):
        if r.get("type") == "tests":
            out.setdefault(r["to"], []).append(r["from"])
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def _dead_code_by_module(doc: dict, module_parts: dict[str, dict]) -> dict[str, int]:
    file_to_module: dict[str, str] = {}
    for pid, p in module_parts.items():
        for f in (p.get("evidence") or {}).get("files", []):
            file_to_module[f] = pid
    out: dict[str, int] = {}
    for q in doc.get("quality_gates", []):
        if q.get("gate") != "dead_code_candidate":
            continue
        pid = file_to_module.get(q.get("subject", ""))
        if pid:
            out[pid] = out.get(pid, 0) + 1
    return out


def _architecture_intent(doc: dict) -> dict[str, Any]:
    a = doc.get("detected_architecture") or {}
    return {
        "style": a.get("style"),
        "confidence": a.get("confidence"),
        "honor": [h.get("statement") for h in a.get("hypotheses", [])],
        "known_violations_to_not_replicate_blindly": [
            {"kind": v.get("kind"), "description": v.get("description")}
            for v in a.get("violations", [])
        ],
    }


def _open_assumptions(doc: dict, steps: list[BuildStep], skipped: list[dict]) -> list[str]:
    out: list[str] = []
    repo = doc.get("repository", {})
    if _CONF_RANK.get(repo.get("purpose_confidence", "unknown"), 4) >= 3:
        out.append(f"repository purpose is {repo.get('purpose_confidence')}-confidence: "
                   f"{repo.get('purpose')}")
    for p in doc.get("parts", []):
        if p.get("kind") == "generated_artifact":
            out.append(f"generated artifact `{p.get('name')}` must be regenerated "
                       f"by its build tool, not hand-written")
    for s in skipped:
        out.append(f"phase '{s['phase']}' skipped: {s['reason']}")
    seen: set[str] = set()
    for st in steps:
        for a in st.assumptions:
            if a not in seen:
                seen.add(a)
    out.append(f"{len(seen)} step-level assumptions across {len(steps)} steps "
               f"(each listed on its step)")
    return out

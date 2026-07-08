---
title: Typed-Graph Diagnostic Framework (TGDF) for Multi-Repository Codebases
author: Claude (Anthropic), under the system-architect skill
date: 2026-07-08
disclaimer: |
  Nothing in this document should be taken for granted. Any statement,
  premise, or citation not backed by a verifiable reference or a real
  logical definition may be invalid, erroneous, or a hallucination.
  References are cited in good faith; the reader is expected to verify
  them independently.
status: proposal
---

# Typed-Graph Diagnostic Framework (TGDF)

Language-independent diagnosis of large multi-repository codebases represented as typed software graphs.

## 1. Stance and non-goals

The framework is a **diagnostic instrument, not an oracle**. Every finding carries a locus (graph elements + source spans), evidence, a calibrated confidence, and a disposition (auto-fix / review / exploratory). It never claims beyond the soundness level of the data that produced it.

Non-goals: replacing compilers or type checkers; whole-program soundness for dynamic languages (impossible in general — managed instead by edge-confidence labeling and runtime confirmation); prescribing one "true" architecture.

Honesty note on the mathematical menu requested: category theory is genuinely load-bearing in exactly four places here — typed-graph schemas (slice category **Graph**/T), architectural conformance as graph homomorphisms (T13), effect systems as graded monads (T25), and sheaf-style gluing for cross-repo consistency (T14). Everywhere else it is framing, and this document does not pretend otherwise.

## 2. Layered architecture

```
L3  Runtime / telemetry enrichment      (traces, coverage, logs, deploy topology)
L2  Framework convention overlays       (declarative rule packs → convention edges)
L1  Language extraction adapters        (per-language frontends → L0 schema)
L0  Universal typed property graph      (schema, algebra, projections, storage)
        ↓ feeds
Analysis engine (T1–T35) → Evidence fusion (§7) → Disposition & remediation (§8)
```

**L0 — universal core.** One typed multigraph schema (§3), a small algebra of projections (§4), snapshot-per-commit storage with deltas. All analyses are written against L0 only; nothing above L0 knows any language.

**L1 — adapter contract.** Each language frontend emits L0 elements and declares a *soundness level* per edge kind:

| Level | Guarantee | Enables |
|---|---|---|
| S0 | syntactic (parse only) | file/module structure, clones (token-level) |
| S1 | name-resolved | imports; most structural analyses (T1–T21 except T11, which needs S2) |
| S2 | type-resolved | type lattice, API diff (T11, T34) |
| S3 | flow-resolved mini-IR (SSA + CFG/DFG) | program analyses T22–T29 |
| S4 | whole-program points-to | precise call graphs, alias-dependent checks |

Contract rules: every edge is labeled `{certain | likely | speculative}`; emitting unlabeled speculation is a contract violation; each adapter ships golden-graph conformance tests. Cross-language program analysis requires normalizing to a shared mini-IR at S3 (the single largest adapter investment — see §12).

**L2 — framework overlays.** Declarative, reviewable rule packs (data, not code) that materialize edges which exist only by convention: DI wiring, HTTP routing, ORM entity↔schema, serialization endpoints, pub/sub topics, plugin registration, lifecycle callbacks. Each produced edge carries the pack's confidence. Without L2, cycle, dead-code, and coupling analyses systematically misfire on framework-heavy code.

**L3 — runtime enrichment.** Traces upgrade `speculative` call edges to `observed` and add weights; coverage populates the tests×code bipartite graph; logs contribute exception-flow edges; deployment topology adds Service nodes and `deploysTo` edges. Epistemic rule: **runtime evidence is existential** (it happened at least once), **static evidence is universal up to its soundness level**. Fusion (§7) exploits exactly this asymmetry — neither kind may impersonate the other.

## 3. Formal core (L0)

A typed property multigraph **G = (V, E, src, tgt, τ_V, τ_E, λ)** with node kinds ⊇ {File, Module, Package, Class, Interface, TypeDef, Method, Function, Field, Param, Return, Annotation, Accessor, Test, BuildArtifact, Commit, Author, Service, TraceSpan, LogEvent} and edge kinds ⊇ {contains, imports, exports, declares, extends, implements, composes, calls, reads, writes, throws, catches, flowsTo, succ (control), transitions (state), covers, buildsFrom, touches, authoredBy, deploysTo, observedCall}.

The schema is itself a type graph **T**; a valid instance is a graph with a homomorphism G→T plus local constraints (typed graphs = objects of the slice category **Graph**/T). This makes "schema conformance" a precise, checkable property (T1) rather than a convention.

Every element carries provenance: `(adapter, source span, soundness level, status)`. Time is modeled as one snapshot per commit, indexed by the commit DAG (whose reachability order is a poset; the snapshot family is functorial over it); deltas are the practical storage form and what makes evolution analyses (T18–T21) and incremental recomputation (§9) cheap.

Universal operations all analyses are built from:
- typed selection σ_φ(G) — pick node/edge kinds and predicates;
- quotient q along `contains` — lift function-level edges to class/module/repo level;
- transitive closure G⁺ and condensation C(G);
- bipartite slices B(K₁, K₂, e-kind);
- edge reweighting from telemetry, w: E→ℝ≥0.

## 4. Projection catalog

| Projection | Nodes / edges | Built by | Feeds |
|---|---|---|---|
| ModuleDep | modules/packages; `imports` lifted via quotient | σ + q | T2,3,5,6,7,9,10,13,19 |
| CallGraph | functions/methods; `calls` ∪ `observedCall` | σ (+T24 precision) | T4,5,8,25,27,28 |
| TypeLattice | classes/interfaces/types; `extends`/`implements` | σ | T11,12 |
| CFG / DFG / PDG | per-function control, data, and dependence graphs | S3 adapters | T22–T29 |
| TestCov | bipartite tests × code, `covers` | L3 coverage + static links | T30,31,32 |
| CoChange | code nodes; edges weighted by commit co-occurrence | commit history | T19 |
| Ownership | bipartite authors × nodes | `authoredBy`, `touches` | T20 |
| CrossRepoDep | packages × version constraints across repos | manifests + registries | T33,34,35 |
| Build / Deploy | artifacts, services; `buildsFrom`, `deploysTo` | build system + L3 | T6,35 |
| StateMachines | explicit `transitions` subgraphs | L2 protocol packs | T26 |

## 5. Technique catalog

Legend — **Reliability**: H (exact/sound given its inputs) · M (heuristic, good precision with tuning) · L (exploratory). **Disposition**: A (automatable with gates, §8) · R (human review) · X (exploratory/attention-routing only); “semi-A” = auto-prepared patch that a human approves — a strengthened R, not A. Each entry answers the ten required questions in order: Math · Projection · Diagnoses · Evidence · Output · Reliability · False positives · Cost/scale · Combines with · Disposition.

### Family A — Graph integrity and structure (practical core)

**T1 · Schema and referential integrity.** Math: typed-graph conformance — existence of a homomorphism into the type graph T, plus local constraints (dangling-edge, arity, kind compatibility). Projection: whole L0. Diagnoses: broken dependencies (unresolved imports/exports/symbols), boundary type mismatches, adapter defects. Evidence: each violating element with source span and the constraint it breaks. Output: violation table, per-repo integrity score. Reliability: H given S1. FPs: adapter gaps — codegen, optional deps, plugin systems make edges look dangling; route these to L2 packs, not suppression. Cost: linear; runs on every delta. Combines: hard gate for everything downstream — analyses on an invalid graph are noise. Disposition: A for trivia (remove unused import); R otherwise.

**T2 · Strongly connected components + condensation.** Math: Tarjan/Kosaraju SCC; condensation to a DAG. Projection: ModuleDep; CallGraph. Diagnoses: cyclic dependencies at any granularity. Evidence: explicit node sets per cycle; the edges sustaining each cycle. Output: SCCs ranked by size × churn; candidate break-edges. Reliability: H (exact) given edges. FPs: phantom cycles from over-eager L2 rules; intentional cycles in generated code (the dual failure — missing L2 edges hiding real cycles — is a false negative, tracked as L2 pack coverage). Cost: O(V+E); 10⁸-edge scale is routine. Combines: condensation is the input to T3; weight cycles by T18 churn to rank. Disposition: R for choosing the break-edge; A only for mechanical import splits with test gates.

**T3 · Layering and minimum feedback arc set.** Math: topological order on the condensation; minimum FAS is NP-hard — use greedy heuristics (Eades–Lin–Smyth) or LP relaxations. Projection: ModuleDep. Diagnoses: layer violations; drift against intended layering. Evidence: minimal edge set whose removal restores a DAG/declared order. Output: inferred layer assignment + violating edges with removal cost. Reliability: M–H (heuristic minimality; violations themselves exact). FPs: dependency-inversion and ports-and-adapters patterns look like violations unless the declared model (T13) marks inversion edges. Cost: near-linear heuristics. Combines: with T13 to distinguish "drift" from "the declared model is stale". Disposition: R.

**T4 · Reachability and dominators.** Math: BFS/DFS reachability; Lengauer–Tarjan dominator trees (near-linear). Projection: CallGraph from a declared entry-point set; CFG per function. Diagnoses: dead code (unreachable); chokepoints (dominators — every path passes through them). Evidence: unreachable node sets; dominator tree. Output: dead-code candidates ranked by size and age; chokepoint list with blast radius. Reliability: H for dominators; M for dead code — hostage to entry-point completeness. FPs: reflective/DI/plugin entry points; library exports dead internally but alive in downstream repos; test-only reachability. Cost: linear. Combines: the strong version is triple agreement — static unreachable ∧ never executed (L3 coverage over N days) ∧ no cross-repo consumers (T34). Disposition: A only under triple agreement + green tests; else R.

**T5 · Centrality and instability metrics.** Math: degree, betweenness (Brandes O(VE); sampled variants with VC-dimension bounds — Riondato–Kornaropoulos), PageRank/Katz, HITS; Martin metrics I = Ce/(Ca+Ce), abstractness A, distance D = |A+I−1|. Projection: ModuleDep, CallGraph. Diagnoses: dependency bottlenecks, god modules, unstable-yet-widely-depended-on interfaces. Evidence: metric vectors with percentile ranks over the corpus. Output: ranked watchlist per metric family. Reliability: M — these are symptoms, not verdicts. FPs: legitimate hubs (logging, core domain types, stdlib-like utilities). Cost: PageRank linear per iteration; betweenness sampled beyond ~10⁶ nodes. Combines: centrality × churn (T18) → "unstable hub" alarm; × downstream usage (T34) → external blast radius. Disposition: R/X.

**T6 · Cut structure: articulation points, bridges, k-cores.** Math: articulation points and bridges (Tarjan, linear); min s–t cuts (max-flow); k-core decomposition (linear). Projection: ModuleDep, CrossRepoDep, Deploy. Diagnoses: single points of failure, fragile ecosystem topology, dependency bottlenecks. Evidence: the node/edge whose removal disconnects consumers from providers; core numbers. Output: SPOF list with blast radius = reachability lost on removal. Reliability: H structurally; M as "risk" (structure ≠ probability of failure). FPs: structurally critical but practically stable dependencies. Cost: linear/near-linear. Combines: SPOF ∧ low bus factor (T20) ∧ stale (T35) → composite ecosystem risk. Disposition: R.

**T7 · Community detection vs. declared modules.** Math: modularity/CPM optimization (Leiden — fixes Louvain's badly-connected communities); partition comparison via NMI/ARI. Projection: file/class-level dependency graph per repo. Diagnoses: poor modular boundaries, low cohesion, excessive coupling between declared modules. Evidence: emergent partition; per-module mismatch score; the specific nodes assigned elsewhere. Output: modules ranked by NMI gap; concrete move suggestions. Reliability: M — resolution limit, near-tie instability (run ensembles). FPs: cross-cutting utilities; generated code; framework layers that legitimately span clusters. Cost: near-linear; 10⁷ nodes fine. Combines: only surface a move when T7, co-change (T19), and embedding distance (T15) agree. Disposition: R; intra-repo moves semi-A with test gates.

**T8 · Motif / anti-pattern subgraph matching.** Math: typed subgraph isomorphism (VF2-class; exponential worst case, fast for small typed patterns); equivalently Datalog/graph-query evaluation. Projection: any typed slice; patterns may reference metric attributes. Diagnoses: design smells encoded declaratively — god class (high fan-in + many members + low T12 cohesion), cyclic hub, feature envy, shotgun-surgery motifs over CoChange. Evidence: bound pattern instances with spans. Output: smell instances per pattern, versioned pattern library. Reliability: M — as good as the pattern definitions and thresholds. FPs: threshold sensitivity; generated code; DSLs. Cost: fast with kind-pruned search; cap patterns at ~8 nodes. Combines: patterns are the natural place to *encode* composite signals from T5/T12/T18. Disposition: R; a whitelisted few (trivial accessor smells) A.

### Family B — Matrix and spectral methods

**T9 · Design Structure Matrix (DSM) analysis.** Math: adjacency-matrix reordering/triangularization; propagation cost = density of the transitive-closure (visibility) matrix; core–periphery decomposition (MacCormack–Rusnak–Baldwin). Projection: ModuleDep as a matrix. Diagnoses: hidden coupling economics, drift as rising propagation cost, core bloat. Evidence: propagation-cost number per snapshot; reordered matrix heatmap. Output: trendline + DSM visualization; core size over time. Reliability: M–H for trends; M for absolute levels (incomparable across codebases without normalization). FPs: mixing edge kinds inflates closure; vendored code distorts the core. Cost: closure via bitset BFS, O(V·E/w); fine to ~10⁶ nodes. Combines: feed the time series to T21 changepoints; contrast with T13 conformance. Disposition: R (portfolio-level decisions).

**T10 · Spectral analysis.** Math: normalized Laplacian spectrum; algebraic connectivity λ₂ and Fiedler-vector partitioning; eigengap as natural-cluster count; spectral distance between snapshots as a drift signal. Projection: symmetrized, weighted ModuleDep. Diagnoses: modularization quality; partition suggestions; drift trend. Evidence: λ₂, eigengap plots, snapshot spectral distances. Output: scalar quality indicators + suggested bisections. Reliability: M — symmetrization discards direction semantics; weighting choices dominate results. FPs: weight artifacts read as structure. Cost: Lanczos-type sparse eigensolvers for a few eigenpairs, ~10⁶–10⁷ nodes. Combines: cross-check T7 partitions; feed T21. Disposition: X/R.

### Family C — Order, algebra, category theory

**T11 · Type-lattice and behavioral-subtyping conformance.** Math: inheritance/subtyping as a poset (a lattice where the language guarantees meets/joins); variance rules; behavioral-subtyping (Liskov–Wing) proxies — detecting precondition strengthening, exception widening, nullability widening in overrides. Projection: TypeLattice + signature attributes. Diagnoses: inconsistent abstractions, LSP violations, semantically unstable interfaces. Evidence: specific override pairs with the violated rule and contract delta. Output: violation list by severity. Reliability: H for variance/signature-level; M for behavioral proxies. FPs: intentional covariant conveniences in dynamic languages; S2 resolution gaps. Cost: linear in override pairs. Combines: with T34 to catch semantic breaks that signature diffs miss; most valuable at cross-language/serialization boundaries where no compiler checks. Disposition: R.

**T12 · Formal Concept Analysis (FCA).** Math: the Galois connection between object and attribute sets induces a concept lattice. Projection: bipartite contexts — (methods × fields accessed) per class; (clients × API members used) per interface. Diagnoses: low cohesion (a multi-summit lattice = the class is k things), fat interfaces (concepts are the natural sub-interfaces — Interface Segregation made computable), repeated abstraction shapes. Evidence: the lattice; member-to-concept assignments. Output: extract-class / split-interface proposals with exact member lists. Reliability: M–H — proposals are well-formed; whether to act is judgment. FPs: tiny contexts are unstable; accessor noise (filter via Accessor kind). Cost: exponential worst case, trivial at per-class/per-interface context sizes. Combines: validate a split against T19 — members that co-change belong together. Disposition: R.

**T13 · Reflexion models / architectural conformance.** Math: a declared architecture is a target graph; conformance is a structure-preserving map (graph homomorphism — functorially, from the concrete module category to the declared one) from the quotiented ModuleDep; edges classify as convergent / divergent / absent (Murphy–Notkin). Projection: ModuleDep quotiented to declared components via a mapping file. Diagnoses: architectural drift — precisely, edge by edge, with causes. Evidence: each divergent edge with the underlying concrete edges that induce it. Output: conformance report; drift diff per release; CI signal. Reliability: H given a maintained target model — the best drift detector in this document. FPs: a stale model (then the finding is "the model is wrong", still useful); mapping-file gaps. Cost: linear. Combines: T3 suggests minimal repair sets; T18 ranks divergences by activity. Disposition: R; "no new divergent edges" as an automated CI gate is policy-grade A.

**T14 · Sheaf-style cross-repo consistency (research-grade).** Math: interface data as a presheaf over the repo/module cover; consistency = the gluing (sheaf) condition; obstructions are localized Čech-style; numeric variants via sheaf Laplacians (Hansen–Ghrist). Projection: cross-repo usage of shared contracts (schemas, units, nullability, protocol assumptions). Diagnoses: inconsistent abstractions across repositories — same nominal contract, incompatible local assumptions. Evidence: local sections that fail to glue, with the witness pair of repos/fields. Output: inconsistency loci. Reliability: L–M; young tooling, modeling-sensitive. FPs: artifacts of how the presheaf is built. Cost: small per contract; scales by contract count. Combines: feeds T11 and T34; runtime serialization errors (L3) confirm. Disposition: X.

### Family D — Vector-space and learned methods

**T15 · Structural embeddings and misplacement detection.** Math: random-walk embeddings (node2vec) or GNN encoders over the heterogeneous graph; distance ratios (own-module centroid vs. nearest foreign centroid); density-based outliers. Projection: ModuleDep + CallGraph slice. Diagnoses: misplaced units (computable feature envy), boundary suggestions, anomalous nodes. Evidence: distance margins; nearest-neighbor lists. Output: ranked move candidates. Reliability: M–L — stochastic (fix seeds, use ensembles). FPs: utility code; embedding artifacts. Cost: node2vec near-linear-ish; GNNs need GPU budget. Combines: never surfaced alone — require agreement with T7 and T19. Disposition: X → R.

**T16 · Clone and near-duplicate detection.** Math: fingerprinting/winnowing (Schleimer–Wilkerson–Aiken); token-bag overlap with filtering (SourcererCC-style); tree/PDG matching; neural code embeddings + LSH/ANN for type-3/4 and cross-language clones. Projection: function/method corpus; PDG for semantic clones. Diagnoses: duplicated logic. Evidence: clone classes with similarity scores and aligned diffs. Output: clone groups ranked by size × churn (divergence risk). Reliability: H for type-1/2; M type-3; L–M type-4/cross-language. FPs: boilerplate, generated code, idioms, test scaffolding. Cost: LSH near-linear; PDG matching expensive — reserve for high-value candidates. Combines: churn on a clone class predicts divergence bugs; T28 slicing checks extractability. Disposition: R; extract-function consolidation semi-A with tests.

**T17 · Learned defect prediction.** Math: supervised models (gradient boosting; GNNs) over graph metrics + process metrics; labels mined via SZZ-style bug-fix linking (noisy by construction). Projection: derived feature table over ModuleDep/Ownership nodes (metrics from T5/T18/T20). Diagnoses: bug-likelihood ranking — a prioritizer, not a detector. Evidence: scores with feature attributions (e.g., SHAP). Output: risk-ranked files. Reliability: M and dataset-dependent; label noise caps it. FPs: popularity bias — churny-but-healthy files flagged forever. Cost: cheap inference; periodic retraining. Combines: its only sanctioned role is directing expensive analyses (T23, T29, T31) at the right targets. Disposition: X (prioritization only; never triggers action alone).

### Family E — Statistics and information theory on evolution

**T18 · Hotspot analysis (churn × complexity).** Math: rank aggregation — product of percentiles of change frequency and a complexity proxy (size, T27 metrics); time-decayed churn. Projection: files/functions joined with `touches`. Diagnoses: where defects and maintenance cost concentrate. Evidence: scatter of churn vs. complexity; rank list. Output: top-N hotspots. Reliability: M–H as a prioritizer (one of the most replicated results in empirical SE). FPs: healthy active development; bulk refactors inflating churn (exclude mechanical commits, apply decay). Cost: trivial. Combines: multiplies into nearly every priority score in §7. Disposition: R/X.

**T19 · Logical (co-change) coupling.** Math: association mining over commit baskets — support/confidence/lift; temporal windowing. Projection: CoChange graph. Diagnoses: hidden coupling and drift — pairs with high lift but **no static edge** are the interesting anomaly; shotgun surgery. Evidence: pairs with lift and example commits. Output: hidden-coupling edges overlaid on ModuleDep; contradiction report (static-says-independent vs. history-says-coupled). Reliability: M; hostage to commit hygiene. FPs: tangled commits, lockfiles/codegen artifacts, repo-wide mechanical changes (filter all three). Cost: near-linear with support pruning. Combines: the static/evolutionary contradiction detector — pairs where T2/T5 and T19 disagree are top review candidates; validates T7/T12/T15 proposals. Disposition: R.

**T20 · Ownership, entropy, bus factor.** Math: Shannon entropy of the change distribution per period (Hassan's change complexity); degree-of-authorship; truck-factor estimation (Avelino et al.). Projection: Ownership bipartite + commit history. Diagnoses: knowledge risk — orphaned critical modules; chaotic-change periods that empirically precede defects. Evidence: bus factor per module; entropy trend. Output: knowledge-risk heatmap. Reliability: M. FPs: bots, squash merges, contractor patterns — needs per-organization normalization. Cost: trivial. Combines: critical (T5/T6) ∧ orphaned (T20) is the actionable compound. Disposition: R — staffing calls are inherently human.

**T21 · Metric drift and changepoint detection.** Math: two-sample tests (KS, PSI) on metric distributions; CUSUM / Bayesian online changepoint detection on time series (propagation cost, λ₂, violation counts, coverage). Projection: any metric series from T5/T9/T10/T13/T30. Diagnoses: architectural drift as a measured trend; regression alarms after large merges. Evidence: changepoint dates aligned to commits/releases. Output: annotated timelines. Reliability: M–H that *something* changed; attribution is manual. FPs: repo migrations and mass renames (track identity through renames). Cost: trivial. Combines: the temporal wrapper around Families A–B outputs. Disposition: R.

### Family F — Classical program analysis (language-parametric via the S3 mini-IR)

**T22 · Monotone data-flow frameworks.** Math: finite-height lattices + monotone transfer functions + Kleene/Knaster–Tarski fixpoints (Kildall); instances: liveness, reaching definitions, constant propagation, very-busy expressions. Projection: CFG/DFG per function. Diagnoses: dead stores, use of uninitialized state, redundant computation, simple state errors. Evidence: program points with the offending data-flow fact. Output: warnings with spans. Reliability: H intra-procedurally given S3; degrades at FFI/dynamic boundaries. FPs: dynamic features, concurrency invalidating sequential assumptions. Cost: fast, near-linear per function; embarrassingly parallel. Combines: the substrate for T23–T29. Disposition: some A (dead-store removal) with tests; mostly R.

**T23 · Interprocedural taint analysis (IFDS/IDE).** Math: data-flow as context-sensitive CFL/graph reachability on the exploded supergraph (Reps–Horwitz–Sagiv), O(E·D³) in domain size D. Projection: interprocedural DFG. Diagnoses: unsafe side effects crossing boundaries — untrusted input → sink, config → global mutation, PII → logs. Evidence: full witness path source→sink. Output: flow findings with paths. Reliability: M–H; bounded by call-graph precision. FPs: unmodeled sanitizers; infeasible paths; reflection. Cost: heavy — run only on slices prioritized by T17/T18. Combines: T24 sharpens call edges; L3 traces confirm path feasibility (Tier-A upgrade); T29 can prove/refute a path. Disposition: R (security triage).

**T24 · Points-to, alias, and escape analysis.** Math: inclusion-constraint solving (Andersen, cubic) vs. unification (Steensgaard, near-linear); escape analysis; call-graph construction as a precision spectrum (Grove–Chambers). Projection: heap/reference model over the DFG. Diagnoses: shared mutable state across threads/modules; devirtualization for call-graph precision; a class of state-management errors. Evidence: alias sets; escaping objects with escape routes. Output: shared-state map. Reliability: M — sound but over-approximate; precision/cost tuned via context sensitivity. FPs: giant alias sets from context insensitivity. Cost: Steensgaard corpus-wide; Andersen on hotspots only. Combines: precision substrate for T4/T23/T26; T32 intersects with it. Disposition: X/R (substrate, rarely a finding by itself).

**T25 · Effect and purity inference.** Math: type-and-effect systems; effects as a join-semilattice of labels {pure, read, write, IO, alloc, sync, throws} — categorically, graded monads; interprocedural join over call-graph condensation. Projection: CallGraph + per-function effect summaries. Diagnoses: unsafe side effects (writes behind getters, IO in constructors), purity islands (safe to cache/parallelize), preconditions for safe automated refactoring elsewhere. Evidence: effect signature per function with a provenance chain to the primitive effect. Output: corpus effect map; violations of declared/conventional purity. Reliability: M–H in typed languages; M in dynamic ones. FPs: FFI defaults to ⊤ ("any effect") and spreads — allow L2 annotations to cap it. Cost: linear passes to fixpoint; summary-based and incremental. Combines: gates every A-disposition transform in this document; feeds T8 patterns. Disposition: computing the map is A; acting on violations is R.

**T26 · Typestate and abstract interpretation.** Math: abstract interpretation (Galois connections, widening/narrowing); typestate as per-resource DFAs (open→use→close; init-once; acquire/release). Projection: CFG + protocol automata supplied by L2 packs (files, locks, connections, framework lifecycles). Diagnoses: state-management errors — use-after-close, double-init, missing dispose/await, invalid transitions. Evidence: abstract trace reaching the bad state. Output: protocol violations with paths. Reliability: M–H with good protocol specs. FPs: alias confusion (needs T24); exceptional-path modeling. Cost: moderate intra-procedurally; interprocedural via summaries. Combines: L3 logs showing the bad transition actually occurring upgrade to Tier A. Disposition: R; narrow fixes (insert dispose) semi-A.

**T27 · Control-flow quality and hot paths.** Math: cyclomatic complexity (E−N+2P — the circuit rank of the CFG with an exit→entry edge added per component), NPATH, natural-loop nesting via dominators; expected-cost weighting from profiles; research-grade extension: static resource-bound analysis via recurrence solving. Projection: CFG, weighted by L3. Diagnoses: inefficient control flow; untestably complex functions. Evidence: metric values; hot paths with expensive callees. Output: complexity/heat ranking. Reliability: metrics H to compute, M as "a problem"; hot paths H with real profiles. FPs: generated parsers and state machines are legitimately complex — tag generated code. Cost: trivial statically; profiling is an infrastructure cost. Combines: complex ∧ uncovered (T30) is the priority compound. Disposition: R.

**T28 · Program slicing (PDG-based).** Math: backward/forward reachability on the program dependence graph; chopping between two criteria. Projection: interprocedural PDG. Diagnoses: not a detector — the *impact quantifier*: change blast radius, dead-code confirmation (empty forward slice to any output/effect), remediation scoping, minimal repro extraction. Evidence: slice sets with spans. Output: blast-radius numbers consumed by §7 scoring; per-finding slice views. Reliability: M–H, bounded by call-graph/alias precision (over-wide slices under coarse aliasing). FPs: inflated slices. Cost: expensive globally — computed on demand per finding, cached. Combines: supplies the impact term for every priority score; verifies T16 extractability. Disposition: X (a service, not a verdict).

**T29 · Bounded symbolic execution / model checking.** Math: SMT-backed path exploration; bounded model checking. Projection: mini-IR of selected functions. Diagnoses: confirms or refutes candidate deep bugs (null deref, assertion violation, overflow) — the feasibility filter that converts M-grade warnings into H-grade findings with concrete inputs. Evidence: a witness input, or an UNSAT-within-bound certificate. Output: auto-generated repro test cases. Reliability: H when it fires; incomplete by construction (bounds). FPs: environment-modeling gaps. Cost: very high — strictly budgeted, directed by T17/T18 at T22/T23 candidates. Combines: consumes static candidates; emits repros into T30's test corpus. Disposition: R (with automatic repro filing).

### Family G — Tests as first-class graph citizens

**T30 · Test–code bipartite coverage and gap analysis.** Math: bipartite graph tests × code; coverage as the image of the test set; risk-weighted gap = (reachable production code ∖ covered) ∩ (hotspots ∪ high centrality); greedy set cover for test selection. Projection: TestCov + CallGraph reachability + L3 coverage. Diagnoses: missing tests where they matter; also minimal test sets for CI. Evidence: uncovered spans with risk weights. Output: ranked untested-and-risky list; suggested test targets. Reliability: H for "uncovered"; M for "matters". FPs: covered-but-unasserted code (coverage ≠ verification — T31 closes this); integration-tested in another repo (import cross-repo coverage). Cost: cheap given coverage infrastructure. Combines: weights from T5/T18/T27; feeds T31. Disposition: R; test scaffolding generation semi-A.

**T31 · Mutation testing (sampled).** Math: syntactic mutation operators; kill ratio as a test-adequacy estimator; statistical sampling of the mutant space. Projection: hotspot-selected covered code + its tests. Diagnoses: weak assertions despite coverage. Evidence: surviving mutants with diffs. Output: survival lists per module. Reliability: H per mutant; sampling variance overall. FPs: equivalent mutants (undecidable in general; heuristic filtering). Cost: very high (test-suite executions) — sample tightly, only where T30 says coverage exists and T18 says it matters. Combines: the verification layer on top of T30. Disposition: R.

**T32 · Test-failure and flakiness clustering.** Math: co-occurrence/correlation clustering of failures across CI runs; environment covariates to separate infra noise. Projection: CI run history × TestCov. Diagnoses: hidden shared state, test-order dependence, concurrency bugs. Evidence: co-failing clusters + the intersection of the code they cover. Output: suspect shared-state loci. Reliability: M. FPs: infrastructure flakes — control for machine/queue covariates. Cost: cheap on existing CI data. Combines: intersect suspect loci with T24's shared-mutable-state map — agreement is a strong state-error signal. Disposition: R.

### Family H — Repository-ecosystem level

**T33 · Version-constraint satisfiability.** Math: semver ranges over version posets; resolution as SAT/CP (CDCL, as in several production resolvers — e.g. libsolv-based managers, Dart pub); minimal unsatisfiable subset (MUS) extraction for explanations. Projection: CrossRepoDep. Diagnoses: broken/unsatisfiable dependency sets, diamond conflicts; computes minimal upgrade plans. Evidence: the MUS — the smallest set of constraints that cannot hold together. Output: human-readable conflict explanations; upgrade plans. Reliability: H (it's logic) — about the manifests. FPs: manifests ≠ reality (vendoring, dynamic loading, private registries). Cost: SAT scales comfortably at ecosystem sizes. Combines: T34 estimates breakage risk of a proposed plan before executing it. Disposition: A for solver-proven-safe bumps + green tests (own and downstream); R otherwise.

**T34 · API evolution diff with downstream weighting.** Math: interface diff as tree/graph diff; rule-based breaking-change classification; downstream impact = usage-weighted in-edges across repos; stability index over releases. Projection: exported-symbol graphs per version + CrossRepoDep usage edges. Diagnoses: unstable interfaces, semver violations, deprecation blast radius. Evidence: per-change classification + the exact affected callers. Output: breakage forecast per release; per-API stability score. Reliability: H syntactically; M behaviorally. FPs: flagged-but-harmless changes (unused parameters, dead overloads); the dual false negative — behavioral breaks invisible to signature diffs — needs T11/T14 and contract tests. Cost: cheap. Combines: supplies "external liveness" to T4 dead-code; CI gate on unintended breaking changes. Disposition: R; the CI block itself is policy-grade A.

**T35 · Advisory, staleness, and exposure propagation.** Math: weighted reachability with decay from advisory/staleness sources; exposure = aggregation over paths from deployed artifacts; call-level refinement via cross-library reachability. Projection: CrossRepoDep + Build + Deploy (+ CallGraph across library boundaries). Diagnoses: ecosystem risk — vulnerable or abandoned transitive dependencies actually reachable from what you deploy. Evidence: the path deployed-artifact → dependency, and (call-level) whether the vulnerable function is reachable at all. Output: exposure ranking. Reliability: M at manifest level; M–H at call level (needs S2+ cross-library call graphs). FPs: manifest-level analysis massively over-reports (unreached vulnerable code) — call-level refinement is the point. Cost: moderate. Combines: SPOF (T6) ∧ low bus factor (T20) ∧ high exposure (T35) → the composite ecosystem-risk score. Disposition: R; automated bump PRs travel the T33-A path.

## 6. Problem → technique matrix

| Problem | Primary detectors | Corroborators (raise tier) |
|---|---|---|
| Bugs | T22, T23, T29 | T17, T18 (prioritize), L3 traces |
| Broken dependencies | T1, T33 | T34 |
| Cyclic dependencies | T2 | T3 (repair sets) |
| Architectural drift | T13 | T9, T19, T21 |
| Inconsistent abstractions | T11, T12 | T14, T34 |
| Excessive coupling | T5, T9 | T7, T19 |
| Low cohesion | T12, T7 | T15 |
| Unstable interfaces | T34, T5 (I-metric) | T11, T21 |
| Inefficient control flow | T27 | L3 profiles, T29 |
| Dead code | T4 | T30 coverage, T34 external usage, T28 slice |
| Duplicated logic | T16 | T15 |
| State-management errors | T26, T24 | T32 |
| Unsafe side effects | T25, T23 | T22 |
| Missing tests | T30 | T31, T18, T27 |
| Poor modular boundaries | T7 | T12, T15, T19 |
| Dependency bottlenecks | T5, T6 | T9 |
| Design smells | T8 | T5, T12, T18 |
| Ecosystem risks | T35, T33 | T6, T20, T34 |

## 7. Evidence fusion and confidence

Finding record: `{problem_class, locus (elements + spans), evidence[], per-detector scores, tier, impact, disposition}`.

**Calibration.** Per-detector precision is estimated from reviewer verdicts on a labeled sample and refit periodically; detectors are re-weighted from measured precision, not from enthusiasm.

**Combination.** Within a family, take the max (detectors in one family share failure modes — summing them double-counts). Across families, sum log-odds (cross-family independence is an approximation; within-family it is simply false).
<!-- ANCHOR:fusion-design-law -->
**The reduction behind the tiers (design law).** Every §6 problem is detected through one of four forms: (a) distinguished structure on a single projection (cycles, taint paths, typestate violations, cuts); (b) disagreement between two projections of the same system (declared vs. emergent modules — T13/T7; static vs. co-change coupling — T19; coverage vs. mutation adequacy — T30/T31; manifest vs. call-level exposure — T35); (c) distributional anomaly over the corpus or its evolution (T18, T21, metric outliers); (d) approximate-equivalence discovery (T16). The corollary the tier system operationalizes: M-grade signals are promoted by pairing projections or modalities (form b), almost never by sharpening a single detector — and detectors reading the same projection share extraction errors, so they are not independent whatever their family. Extension rule: a new technique declares its form; a form-(b) claim must name its second projection, else it is a form-(a) detector and will be over-trusted.

**Tiers.** A = ≥2 independent families agree, at least one from Family E (evolution) or L3 (runtime); B = two static families on distinct projections (per the design law — same-projection pairs share extraction errors); C = a single detector, or any same-projection pair. The static/runtime asymmetry from §2 does the work: static claims universality up to soundness, runtime supplies existence proofs — agreement between them is the strongest signal available.

**Priority** = tier weight × impact × exposure, where impact = blast radius (T28 slice size or T4 reachability) × centrality percentile (T5) × churn percentile (T18), and exposure = deployed reachability (T35). This is what orders the review queue; it embodies the rule that reviewer attention goes to high-impact uncertainty, while known-solution items flow to the A-path or a low-priority queue.

## 8. Disposition policy

**A (automated remediation)** is allowed iff all hold: the transform is semantics-preserving by construction; blast radius is bounded and computed (T28); full test gate is green, including affected downstream repos (T34); tier ≥ B. Standing whitelist: unused-import removal (T1), solver-proven dependency bumps (T33), dead-code deletion under the T4 triple agreement, whitelisted trivial smells (T8). CI gates (T13 “no new divergent edges”, T34 breaking-change block) are automated *decisions*, not transforms — they run outside this whitelist under their own policy.

**R (human review)**: everything structural — cycle breaking, module moves, interface splits, protocol fixes — delivered as a prepared patch plus the evidence bundle, never as a bare complaint.

**X (exploratory)**: embeddings, sheaf consistency, learned predictors, slicing. X outputs never trigger action alone; their only job is routing attention and budget.

## 9. Scale engineering

- **Incremental everything**: per-commit graph deltas; analyses recompute only dirty regions.
- **Compositional program analysis**: function summaries keyed by content hash, computed bottom-up over call-graph condensation (the approach behind Infer — Calcagno et al.). This is the single decision that makes Family F viable at monorepo scale — summaries are reused across commits and repos.
- **Condensation-first**: run T2 before anything on directed graphs; most algorithms then operate on a much smaller DAG.
- **Sharding**: per-repo graphs + a deliberately small cross-repo boundary graph (exports, manifests, deploy edges).
- **Approximation where exactness doesn't pay**: sampled betweenness (T5), LSH/ANN for similarity (T15/T16), sampled mutants (T31).
- **Budgets**: T23/T29/T31 run under explicit compute budgets directed by T17/T18; T8 patterns capped at ~8 nodes.

## 10. Blind spots and false-positive management

Structural blind spots: reflection/metaprogramming and dynamic dispatch (edges only L2/L3 can supply); generated code (tag it; analyze the generators instead); conditional compilation and feature flags — a flagged codebase is a *family* of graphs; the honest model is a union graph with presence conditions (the "150% model" from product-line research), which is research-grade but correct; FFI boundaries (default to ⊤ effects, cap via L2 annotations); vendored code distorting corpus statistics; and concurrency, which is underserved here — races and deadlocks are only proxied (T24 escape analysis + T32 clustering); dedicated happens-before or dynamic race detection enters, if at all, as L3 evidence.

Systemic mitigations: L2 packs as the designated home for "the framework does it" knowledge; L3 confirmation before any destructive action; the §7 calibration loop; identity tracking through renames so evolution analyses survive refactors.

## 11. Adoption order

- **Wave 1** (days, high yield; S1 adapters plus existing CI coverage feeds): T1, T2, T4, T5, T18, T30, T33.
- **Wave 2** (structure and evolution): T7, T9, T13, T16, T19.
- **Wave 3** (requires S3 mini-IR adapters): T22, T25, T26, T27; T23 under budget.
- **Wave 4** (research-grade): T10, T14, T15, T17, T29, T31.

**The two decisions that genuinely need an owner** (everything else in this document has a defaultable answer):

1. **Which languages get S3/S4 adapters, in what order.** This gates all of Family F, is the dominant cost driver, and depends on your language mix and where the risk lives — it cannot be decided from inside the framework.
2. **Whether the organization will maintain a declared architecture model.** T13 is the best drift detector available, but only if the model and mapping files are kept alive as part of the definition of done. If that commitment won't hold, invest Wave-2 effort in T9/T19/T21 trend detection instead.

## References — verify independently

- Tarjan, "Depth-first search and linear graph algorithms", SIAM J. Comput., 1972 (SCC; also articulation points/bridges).
- Lengauer & Tarjan, "A fast algorithm for finding dominators in a flowgraph", TOPLAS, 1979.
- Eades, Lin & Smyth, "A fast and effective heuristic for the feedback arc set problem", Inf. Process. Lett., 1993.
- Brandes, "A faster algorithm for betweenness centrality", J. Math. Sociol., 2001; Riondato & Kornaropoulos, "Fast approximation of betweenness centrality through sampling", WSDM, 2014.
- Traag, Waltman & van Eck, "From Louvain to Leiden: guaranteeing well-connected communities", Sci. Rep., 2019.
- Martin, *Agile Software Development: Principles, Patterns, and Practices*, Prentice Hall, 2002 (instability/abstractness metrics).
- Liskov & Wing, "A behavioral notion of subtyping", TOPLAS, 1994.
- MacCormack, Rusnak & Baldwin, "Exploring the structure of complex software designs: an empirical study of open source and proprietary code", Manage. Sci., 2006 (DSM, propagation cost).
- Ganter & Wille, *Formal Concept Analysis: Mathematical Foundations*, Springer, 1999.
- Ehrig, Ehrig, Prange & Taentzer, *Fundamentals of Algebraic Graph Transformation*, Springer, 2006 (typed graphs as a slice category).
- Murphy, Notkin & Sullivan, "Software reflexion models: bridging the gap between source and high-level models", FSE, 1995.
- Hansen & Ghrist, "Toward a spectral theory of cellular sheaves", J. Appl. Comput. Topol., 2019.
- Kildall, "A unified approach to global program optimization", POPL, 1973; Cousot & Cousot, "Abstract interpretation…", POPL, 1977.
- Katsumata, "Parametric effect monads and semantics of effect systems", POPL, 2014 (graded monads).
- Reps, Horwitz & Sagiv, "Precise interprocedural dataflow analysis via graph reachability", POPL, 1995 (IFDS).
- Andersen, *Program Analysis and Specialization for the C Programming Language*, PhD thesis, DIKU, 1994; Steensgaard, "Points-to analysis in almost linear time", POPL, 1996.
- Strom & Yemini, "Typestate: a programming language concept for enhancing software reliability", IEEE TSE, 1986.
- Calcagno, Distefano et al., "Moving fast with software verification", NASA Formal Methods, 2015 (Infer's compositional summaries).
- Schleimer, Wilkerson & Aiken, "Winnowing: local algorithms for document fingerprinting", SIGMOD, 2003; Sajnani et al., "SourcererCC: scaling code clone detection to big-code", ICSE, 2016.
- Hassan, "Predicting faults using the complexity of code changes", ICSE, 2009.
- Śliwerski, Zimmermann & Zeller, "When do changes induce fixes?", MSR, 2005 (SZZ).
- Avelino et al., "A novel approach for estimating truck factors", ICPC, 2016.
- Grove & Chambers, "A framework for call graph construction algorithms", TOPLAS, 2001.
- node2vec: Grover & Leskovec, KDD, 2016.
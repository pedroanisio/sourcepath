---
title: "Codebase-as-Graph: A Language-Independent Catalog of Analytic Operations and a Scenario Engine for Architecture Change"
version: 0.2.0
date: 2026-07-08
status: draft-for-review (revised after an adversarial pass; see §7.4 and the companion revision log)
disclaimer: >
  No information in this document should be taken for granted. Any statement or
  premise not backed by an explicit logical definition or a verifiable external
  reference may be invalid, erroneous, or a hallucination. Every operation is
  tagged with an epistemic label ([M] measured, [D] derived, [I] inferred,
  [S] speculative) and a confidence level; unlabeled claims default to
  UNVERIFIED. Citations marked "✓ verified in-session" were checked against
  primary sources on 2026-07-08; all other citations were produced from model
  memory and MUST be independently verified before formal or external use.
  Numeric constants presented as defaults (decay factors, thresholds, weights)
  are engineering starting points, not empirical results, unless a reference
  says otherwise.
---

# Codebase-as-Graph: Operations Catalog and Scenario Engine

> **Read the frontmatter disclaimer first.** This is a design/formalization
> document, not an empirical study. It distinguishes throughout between what a
> graph analyzer can *measure*, what it can *derive*, and what it can only
> *infer or speculate*.

## 0. Scope, Epistemic Conventions, and Notation

### 0.1 What this document claims and does not claim

- It claims to define a **canonical, language-independent graph model** for
  codebases and a **catalog of operation families** over it, each with its
  mathematical basis, inputs, outputs, diagnostic value, and failure modes.
- It claims to define a **scenario engine** that turns architecture questions
  ("what is the minimum change set from state A to state B?") into search /
  optimization problems over graph rewrites, with cost, risk, reversibility,
  and confidence attached to every candidate plan.
- It does **not** claim the catalog is complete. "The full set of operations"
  is not a well-defined mathematical object; completeness over an open-ended
  space of analyses is unprovable. What is offered instead is a catalog that
  (a) covers every diagnostic target listed in the problem statement at least
  once, (b) is closed under the composition rules of §4, and (c) is explicitly
  extensible. [D — coverage is checkable against the target list; completeness
  is not]
- It does **not** claim that any inferred score (defect risk, cost estimate,
  semantic similarity) is valid without per-organization calibration. This is
  restated wherever it applies.

### 0.2 Epistemic labels (used on every operation and metric)

| Label | Meaning | Validity condition |
|---|---|---|
| **[M] Measured** | A direct fact recorded by an extractor (an edge exists, a node has 412 LOC). | Valid up to extractor soundness; carries per-fact `confidence` and `provenance`. |
| **[D] Derived** | A deterministic, reproducible computation over [M] facts (SCCs, PageRank, a min-cut). | Mathematically exact **conditional on** the [M] inputs; garbage-in propagates. |
| **[I] Inferred** | A statistical or heuristic estimate (defect probability, refactoring cost, semantic similarity). | Valid only with in-domain calibration; must ship with uncertainty. |
| **[S] Speculative** | Plausible mechanism with weak or no direct evidence in the literature known to the author. | Treat as hypothesis; never gate decisions on it alone. |

Confidence levels **H / M / L** are attached to *methods* (is the technique
itself sound and well-attested?) independently of the epistemic label (what
kind of knowledge it produces). A [D]-labeled metric can still be worthless if
its [M] inputs are unsound — this is why edge-level confidence (§1.4) threads
through everything.

### 0.3 Notation

- $G = (V, E)$ — directed, typed, attributed multigraph; $s,t : E \to V$ give
  source/target; $\tau_V : V \to \Sigma_V$, $\tau_E : E \to \Sigma_E$ give
  types; $A$ is the adjacency matrix of a chosen projection ($A_{ij} = 1$ iff
  an edge $i \to j$).
- $c : V \cup E \to (0,1]$ — extraction confidence.
- $G_t$ — snapshot at commit/time $t$; $\Delta G_t = \mathrm{diff}(G_{t-1},G_t)$.
- $\pi_\ell(G)$ — quotient projection of $G$ to containment level $\ell$
  (file, module, package, repo).
- $\kappa(G)$ — condensation (quotient by strongly connected components).
- $G^{+}$ — transitive closure of a chosen edge-type subset.
- Edge direction convention: **a dependency/call/use edge points from the
  user to the used** ($u \to v$ means "$u$ needs $v$"). Impact of changing $v$
  therefore propagates along **reversed** edges. Fixing this convention once
  removes the single most common class of implementation bugs in these tools.

### 0.4 Specification traceability

| Specification ask | Where answered |
|---|---|
| Canonical graph model (nodes, edges, attributes, views, layers) | §1 |
| ~15 operation families, each with the 10 required fields | §2 (F1–F15) |
| Diagnostic dimensions | §3 |
| Scenario engine (states, transformations, constraints, cost/risk, ranking) | §4 |
| Eight concrete scenarios + component decision rules | §5 (5.1–5.9) |
| Implementation outputs (schema, catalogs, pseudocode, queries, formulas, visuals, roadmap) | §6 |
| Measured vs. inferred distinction, confidence labeling | §0.2, applied throughout; summarized §7.1 |
| Distance-to-target-architecture | §4.1 |
| Local defects vs. systemic problems | §4.6, item 5 |

### 0.5 Glossary of coined or overloaded terms

- **Keystone** — a structurally critical node: top-percentile transitive
  reliance (F4). The interpretation as *importance* is [I], never automatic.
- **Layer (two senses).** *Lk layer* (L0–L7) = a stratum of the multilayer
  graph model (§1.5). *Declared layer* = an architectural layering the
  organization asserts (UI / domain / infrastructure …) — an **input**,
  numbered increasing upward with 0 = foundation. The bare word "layer" in
  this document means the Lk sense; architectural layering is always
  written "declared layer".
- **Component vs. module.** *Module* = a current code-level unit (L1).
  *Component* = a unit of the target decomposition in assignment problems
  (F14). At module grain the two coincide.
- **Boundary tax** — interface + adapter + mapping LOC divided by logic
  LOC for a module or estate (first used §3; computed §5.2). High values
  indicate structure that costs more than it separates [S threshold].
- **mLCOM (module-grain cohesion components)** — the number of connected
  components of a module's *internal* dependency graph (its members/types
  with their `CALLS`/`READS`/`WRITES`/`USES_TYPE` edges), by analogy with
  LCOM4 at class grain. mLCOM > 1 ⇒ the module contains structurally
  independent parts.
- **Ratchet mode** — a CI gate on a monotone quantity: the tracked
  violation count may only decrease; any increase fails the build.
- **Blast radius** — the impacted-node set/size from F7's operator.
  **Footprint** — the reverse notion for a dependency: what internally
  reaches it (§5.3).

---

## 1. Canonical Codebase Graph Model

### 1.1 Formal definition

**Definition 1 (Codebase graph).** A codebase graph is a tuple

$$G = (V,\ E,\ s,\ t,\ \tau_V,\ \tau_E,\ \alpha,\ \lambda,\ c,\ p)$$

where $V, E$ are finite sets of node and edge identifiers; $s,t: E \to V$;
$\tau_V: V \to \Sigma_V$ and $\tau_E: E \to \Sigma_E$ assign types from
**closed** vocabularies (§1.2, §1.3); $\alpha: (V \cup E) \to (K \rightharpoonup
\mathit{Val})$ assigns attributes; $\lambda: (V \cup E) \to 2^{\mathcal{L}}$
assigns layers; $c: (V \cup E) \to (0,1]$ assigns extraction confidence; and
$p$ assigns provenance (extractor id, language frontend, timestamp, source
span). [D — definition]

**Schema invariants** (checkable, Datalog-style; all [D]):

1. `CONTAINS` edges form a forest rooted at `Repository` nodes; every non-root
   structural node has exactly one `CONTAINS` parent. This is the *spine* that
   makes level projections well-defined.
2. Endpoint typing: each edge type constrains admissible endpoint node types
   (e.g. `CALLS` only between behavior nodes; `COVERS` only Test → behavior).
3. Aggregate edges are derivable: any `DEPENDS_ON` at level $\ell$ must be
   witnessed by at least one lower-level edge whose endpoints project into its
   endpoints. No orphan aggregates → every architecture-level claim can be
   drilled down to source spans.
4. $c(e) \le \min(c(s(e)), c(t(e)))$ — an edge is never more certain than its
   endpoints.

**Time.** The full object of study is the family $\{G_t\}$ indexed by commits,
plus the edit scripts $\Delta G_t$. History-based operations (F9, F11) consume
$\{G_t\}$; structural operations consume a single snapshot.

### 1.2 Node taxonomy (closed vocabulary + open attributes)

Design rule: keep node *types* few and closed; push variability into
attributes. Language frontends must normalize their constructs into this
vocabulary or log an explicit **normalization gap** — nothing language-specific
leaks upward. [D — design rule; the gap log is what keeps "language-independent"
honest rather than aspirational]

| Group | Node types | Key attributes |
|---|---|---|
| Artifact scope | `Repository`, `Package`, `Module`, `File`, `Namespace` | `path`, `language`, `loc`, `generated?`, `vendored?` |
| Type system | `Class`, `Interface`, `Trait`, `Struct`, `Enum`, `Protocol`, `TypeAlias`, `GenericParam` | `abstractness` (0/1 or ratio), `visibility`, `variance`, `stereotype` |
| Behavior | `Function`, `Method`, `Constructor`, `Accessor`, `Parameter`, `Variable`, `Constant`, `Field`, `Property`, `BasicBlock`, `CallSite`, `State` | `cyclomatic` [D], `pure?` [I], `effects` (normalized set: `io`, `mutate`, `throw`, `alloc`), `signatureHash` |
| Architecture roles | (attribute `role` on the above: `domain`, `application`, `port`, `adapter`, `controller`, `repositoryImpl`, `infrastructure`, `utility`) | roles are **declared or inferred [I]** — record which |
| Verification | `Test`, `Fixture`, `MockDef`, `Assertion` | `kind` (unit/integration/e2e), `flaky?` [M if tracked] |
| Config & runtime | `ConfigKey`, `EnvVar`, `SecretRef`, `ExternalDependency`, `RuntimeService`, `InfraResource`, `BuildTarget`, `ApiEndpoint` | `version`, `license`, `cveCount` [M via feeds], `freshnessLagDays` |
| Socio-temporal | `Author`, `Team`, `Commit`, `Review` | `timestamp`, `linesAdded/Deleted` |
| Findings | `Defect`, `Incident`, `Todo`, `LintViolation`, `SecurityFinding` | `severity`, `status`, `openedAt/closedAt` |

### 1.3 Edge taxonomy

| Group | Edge types (direction: user → used unless noted) | Notes |
|---|---|---|
| Containment | `CONTAINS`, `DECLARES` | the spine; forest invariant |
| Static structure | `IMPORTS`, `EXPORTS`, `DEPENDS_ON` (derived roll-up), `BUILDS_WITH` | `DEPENDS_ON` carries `weight` = count of witnessing edges |
| Type relations | `EXTENDS`, `IMPLEMENTS`, `MIXES_IN`, `COMPOSES` (has-field-of-type), `USES_TYPE`, `CONSTRAINED_BY`, `OVERRIDES` | |
| Behavior | `CALLS` (`dispatch`: static\|virtual\|dynamic\|reflective), `READS`, `WRITES`, `ALIASES`, `FLOWS_TO` (data), `CFG_NEXT` (`branch` kind), `THROWS`, `HANDLES`, `TRANSITIONS` (`event`, `guard`), `EMITS`, `LISTENS` | dispatch kind drives confidence (§1.4) |
| API | `EXPOSES`, `CONSUMES` | crossing a process boundary is an *attribute* (`remote?`) — this matters for cost models (§4.4) |
| Verification | `COVERS` (`strength`: line\|branch\|mutation), `ASSERTS_ON`, `MOCKS` | |
| Config/runtime | `CONFIGURES`, `READS_CONFIG`, `REFERENCES_SECRET`, `BINDS_TO`, `DEPLOYS_TO` | |
| Socio-temporal | `AUTHORED`, `TOUCHED` (commit → file, `churnLines`), `REVIEWED`, `OWNS` (derived), `CO_CHANGED` (derived; `support`, `confidence`, `lift`), `FIXES` (commit → defect) | |
| Findings | `LOCATED_IN` | finding → any node |

### 1.4 Confidence and provenance (the anti-garbage layer)

Static extraction of behavior edges is **unsound and incomplete** for real
languages: virtual dispatch, reflection, dynamic loading, code generation, and
string-keyed lookup all defeat naive resolution. The honest response — argued
as the "soundiness" position by Livshits et al. (CACM 2015, *unverified-memory,
conf. H*) — is to ship the unsoundness as data rather than hide it:

- Every edge carries $c(e) \in (0,1]$. Suggested defaults (engineering
  priors, **[S]** until calibrated per extractor): statically resolved call
  1.0; virtual dispatch resolved by class-hierarchy analysis 0.8; duck-typed /
  dynamic 0.5; reflective or string-keyed 0.2.
- Call-graph construction algorithms trade precision for cost along a known
  lattice (Grove & Chambers, TOPLAS 2001, *unverified-memory, conf. M*); record
  **which** algorithm produced each edge in `provenance`.
- Downstream [D] operations must either (a) threshold on $c$, (b) propagate $c$
  multiplicatively along paths, or (c) run twice (optimistic $c>0$ vs.
  pessimistic $c \ge 0.8$) and report both. Reporting both is the recommended
  default: the gap between the two answers *is* the measurement of extraction
  risk. [D — the discipline; the 0.8 threshold is [S]]

### 1.5 Multilayer structure

The model is a multilayer network in the sense of Kivelä et al. (J. Complex
Networks 2014, *unverified-memory, conf. H*): the same entity participates in
several layers, coupled by identity, plus genuinely cross-layer edges (a
`Commit` `TOUCHED` a `File` couples the socio-temporal layer to the artifact
layer).

| Layer | Contents | Primary consumers |
|---|---|---|
| L0 containment | the `CONTAINS` forest | all projections |
| L1 static structure | imports, type relations, `DEPENDS_ON` | F1–F6, F10 |
| L2 behavior | calls, CFG, data flow, effects | F7, F8, F9a |
| L3 API surface | `EXPOSES`/`CONSUMES`, signatures | F10 |
| L4 verification | tests, coverage, mocks | testability dimension |
| L5 config/runtime | config, secrets, external deps, infra | dependency & security risk |
| L6 socio-temporal | authors, commits, co-change | F9b, F11 |
| L7 findings | defects, incidents, lint, security | calibration targets for [I] models |

### 1.6 Derived views (all [D], defined once, reused everywhere)

1. **Level projection** $\pi_\ell(G)$: quotient by containment to level
   $\ell$; inter-group edges aggregate with summed weights; **self-loops are
   kept as a cohesion signal** (internal edge mass), not discarded.
2. **Condensation** $\kappa(G)$: SCC quotient; always a DAG. The scaffold for
   layering and for "is this cyclic mess local or systemic?".
3. **Closure / visibility** $G^{+}$: boolean transitive closure per edge-type
   subset; the matrix behind the DSM and propagation cost (F2).
4. **Slices** $\sigma(v, \mathit{dir}, T)$: forward/backward reachable
   subgraph over edge types $T$ — the impact primitive (F7).
5. **Bipartite views**: Test×Code (`COVERS`), Author×Module (`OWNS`),
   Config×Code, Client×InterfaceMember (drives interface-segregation analysis,
   F10).
6. **Co-change graph**: module pairs weighted by `lift` from commit history
   (F9b) — deliberately kept *separate* from L1 so that structural vs.
   evolutionary coupling can be compared (their disagreement is a diagnostic,
   §5.8-adjacent and F9).
7. **Reference-architecture mapping**: a user-declared target model $M$ plus a
   partial map $m: V \to V_M$; the substrate of conformance checking
   (reflexion models, F13/F2).
8. **DSM**: adjacency matrix of $\pi_{\text{module}}(G)$ in a chosen node
   order; with cluster-ordering it doubles as the primary visualization
   (§6.7).

### 1.7 Why this model and not an AST-per-language

The model deliberately generalizes the **code property graph** — the merge of
AST, CFG, and program-dependence views into one queryable graph introduced by
Yamaguchi, Golde, Arp & Rieck (IEEE S&P 2014, pp. 590–604,
DOI 10.1109/SP.2014.44 — **✓ verified in-session**) — by (a) extending it above
the file level (architecture, build, ops), (b) below the snapshot (history),
and (c) sideways (tests, owners, findings). Industrial precedents for the
extraction substrate exist and should be reused rather than rebuilt: SCIP/LSIF
indexers, Kythe, Glean, CodeQL's relational extraction, Joern's CPG
(*tool references from memory, conf. M-H; verify current status before
depending on any*).

---

## 2. Operation Families

Each family follows the ten-field template required by the problem statement:
**(1) Name · (2) Mathematical/computational basis · (3) What it measures ·
(4) Required graph inputs · (5) Language-independent abstraction ·
(6) Diagnostic uses · (7) Example findings · (8) Failure modes / false
positives · (9) How it supports scenario simulation · (10) Output artifacts.**

A composition note up front: families are not islands. F1 produces the derived
views everything else consumes; F3/F5/F6 produce *candidate structures*; F7
produces *impact sets*; F11/F15 attach *probabilities and costs*; F14 searches
over all of it. §4 makes the composition explicit.

---

### F1. Structural graph operations

1. **Name.** Reachability, transitive closure, condensation, level projection
   (quotient), articulation points & bridges, k-core decomposition, degree
   distributions, motif census, dominance-in-DAG (unique-path providers).
2. **Basis.** Elementary graph theory; DFS/BFS; Tarjan's SCC algorithm
   ($O(V{+}E)$, Tarjan 1972, *unverified-memory, conf. H*); Datalog fixpoints
   for closures; boolean matrix semirings for bulk closure.
3. **Measures.** Existence and shape facts: who can reach whom, what is
   unreachable, which single nodes/edges disconnect the graph, how deeply
   nested the dependency core is. All [D] given [M] edges.
4. **Inputs.** Any single layer or union of layers; entry-point set
   $\mathit{Roots}$ (exports, main functions, handlers, scheduled jobs, tests).
5. **Language-independent abstraction.** Pure graph facts — no language
   semantics beyond the normalized edge vocabulary.
6. **Diagnostic uses.** Dead/unreachable code candidates
   ($V \setminus \mathrm{Reach}(\mathit{Roots})$); orphan modules; **bridge
   edges** = fragile single links between subsystems; **articulation nodes** =
   single points of structural failure (keystone components); deep k-core =
   the entangled center that resists any decomposition; monolithic
   concentration (one giant weakly-connected mass with a deep core) vs.
   fragmentation (many tiny weak components).
7. **Example findings.** "217 functions are unreachable from all 41 declared
   entry points *and* have no coverage and no runtime trace — deletion
   candidates." "Module `billing-core` is an articulation node: its removal
   disconnects payments from invoicing."
8. **Failure modes.** Entry-point incompleteness is the classic one:
   dependency-injection containers, reflective dispatch, framework callbacks,
   and public-library APIs all create *external* callers invisible to the
   graph — so "unreachable" is [D] relative to a root set that is itself [I].
   Mitigation: treat exported/public nodes as roots by default; require
   corroboration (no coverage, no runtime hits, no reflective-risk marker)
   before recommending deletion; report the root-set assumption with every
   dead-code claim.
9. **Scenario support.** Reachability is the substrate of impact (F7);
   condensation is the scaffold of every layering target; articulation/bridge
   sets identify where isolation moves (facades) buy the most.
10. **Outputs.** Node/edge lists with witnesses, condensation DAG, k-core
    index per node, weak-component inventory.

---

### F2. Dependency analysis

1. **Name.** Fan-in/out; afferent/efferent coupling $C_a, C_e$; instability
   $I$; abstractness $A$; main-sequence distance $D$; layering assignment;
   allowed-dependency conformance; DSM visibility & **propagation cost**;
   cumulative component dependency (CCD/ACD/NCCD); external-dependency
   footprint.
2. **Basis.** Order theory (topological levels on $\kappa(G)$); matrix
   algebra (boolean closure); the metric suite of R.C. Martin ("OO Design
   Quality Metrics", 1994; *Agile Software Development*, 2002 —
   *unverified-memory, conf. H*); DSM analysis per Baldwin & Clark (*Design
   Rules*, 2000) and MacCormack, Rusnak & Baldwin, *Management Science*
   52(7):1015–1030, 2006, DOI 10.1287/mnsc.1060.0552 — **✓ verified
   in-session**; Lakos' CCD family (*Large-Scale C++ Software Design*, 1996,
   *unverified-memory, conf. M-H*).
3. **Measures.** [D]: $I = C_e/(C_a{+}C_e) \in [0,1]$ (0 = maximally stable /
   depended-upon, 1 = maximally unstable);
   $A = \frac{\#\text{abstract types}}{\#\text{types}}$;
   $D = |A + I - 1|$ (distance from the "main sequence");
   **propagation cost** $PC = \frac{1}{n^2}\sum_{ij} V_{ij}$ where
   $V = \bigvee_{k\ge 0} A^k$ is the boolean visibility (closure) matrix —
   the expected fraction of the system visible to a random element
   (MacCormack et al. 2006, ✓).
4. **Inputs.** $\pi_{\text{module}}$ or $\pi_{\text{package}}$ of L1; type
   abstractness attributes; declared layer/role attributes and an
   allowed-dependency matrix if conformance is wanted.
5. **Abstraction.** All defined on the projected dependency digraph; the only
   language-touching input is "which types count as abstract", which the
   frontend normalization contract already answers.
6. **Diagnostic uses.** Bad dependency direction (edges violating the allowed
   matrix; concrete→concrete where an inversion was declared); **zone of
   pain** (low $A$, low $I$: concrete *and* widely depended-upon — rigid);
   **zone of uselessness** (high $A$, high $I$); system-level rigidity trend
   via $PC$ over $\{G_t\}$; over-modularization signal when NCCD grows while
   median module size shrinks.
7. **Example findings.** "`shared-utils` has $I=0.03$, $A=0.05$, $C_a=61$:
   61 modules are rigidly coupled to concrete code — inversion or split
   indicated." "$PC$ rose from 0.11 to 0.24 over 18 months: a random change
   now potentially touches ~a quarter of modules."
8. **Failure modes.** Genuinely stable *leaf* utilities (string helpers)
   legitimately sit in the "pain" corner — stereotype-aware exemptions needed;
   generated and vendored code inflates every coupling number (exclude via
   attributes); aggregated `DEPENDS_ON` hides *why* (always allow drill-down
   via invariant 3, §1.1); $PC$ compares poorly across systems of very
   different size (use it longitudinally within one system, comparatively only
   with size caveats — the 2006 paper's own usage pattern).
9. **Scenario support.** The allowed-dependency matrix **is** a target-state
   predicate; violation count is an admissible lower bound on repair moves;
   $\Delta PC$ is a scalar objective for "reduce ripple" scenarios.
10. **Outputs.** DSM heatmap (cluster-ordered), $I{\times}A$ scatter with the
    main sequence drawn, violation edge list with source-span witnesses,
    $PC$/CCD time series.

---

### F3. Cycle analysis

1. **Name.** SCC detection; elementary-cycle enumeration; minimum feedback
   arc/vertex set (FAS/FVS); cycle severity scoring; giant-SCC erosion index.
2. **Basis.** Tarjan (1972) for SCCs [D]; Johnson's algorithm for enumerating
   elementary circuits, $O((V{+}E)(c{+}1))$ for $c$ circuits (Johnson, SIAM
   J. Comput. 1975, *unverified-memory, conf. H*); minimum FAS is NP-hard
   (in Karp's 1972 list, *unverified-memory, conf. H*) — use the
   Eades–Lin–Smyth linear-time heuristic (*unverified-memory, conf. M*) or
   exact ILP for small instances (§6.4).
3. **Measures.** Where and how large the non-DAG regions are; the *minimum
   set of edges whose removal restores a DAG* — i.e., the cheapest
   decycling frontier.
4. **Inputs.** Any directed layer; most valuable on
   $\pi_{\text{module}}(\text{L1})$ and on the type graph.
5. **Abstraction.** Pure digraph property; language enters only through edge
   confidence.
6. **Diagnostic uses.** Broken/dangerous cycles: an SCC that **spans
   packages or declared layers** is architecture erosion, whereas mutual
   recursion inside one module is often benign — severity must encode
   *spread*, not just size. Suggested severity (weights [S] until tuned):
   $\mathrm{sev}(S) = |S| \cdot \mathrm{spread}(S) \cdot
   \left(1{+}\log(1{+}\mathrm{churn}(S))\right) \cdot \bar{w}(S)$,
   where spread = number of distinct packages/layers touched. Giant-SCC share
   $\frac{\max_S |S|}{|V|}$ tracks "big ball of mud" formation over time
   (empirical cycle prevalence in Java: Melton & Tempero, ~2007,
   *unverified-memory, conf. M*).
7. **Example findings.** "One SCC of 143 classes spans 9 packages and 3
   declared layers; FAS says 6 edges break it, 4 of which are `USES_TYPE`
   edges fixable by interface extraction."
8. **Failure modes.** Phantom cycles created by low-confidence dynamic edges
   (run optimistic/pessimistic per §1.4 and report both); intentional
   co-recursive pairs (parser↔AST, visitor patterns) — allow annotated
   exemptions; enumerating *all* cycles explodes ($c$ can be exponential) —
   bound enumeration, rank by severity, and rely on SCC+FAS instead of
   exhaustive listing.
9. **Scenario support.** "Break dependency cycle" compiles directly to:
   compute FAS under per-edge *repair costs* (invert via interface, move
   member, merge modules), pick the min-cost repair set — §5.5.
10. **Outputs.** Ranked SCC inventory; FAS edge sets each annotated with its
    cheapest repair rule; before/after condensation DAGs.

---

### F4. Centrality and influence analysis

1. **Name.** Degree/weighted degree; betweenness; closeness/harmonic;
   Katz; PageRank; HITS hubs & authorities; eigenvector centrality; k-shell;
   ownership-adjusted criticality; truck/bus factor.
2. **Basis.** Freeman's betweenness $C_B(v)=\sum_{s\ne v\ne t}
   \sigma_{st}(v)/\sigma_{st}$, computed by Brandes' algorithm in $O(VE)$
   unweighted (Brandes, J. Math. Sociol. 2001, *unverified-memory, conf. H*);
   PageRank $PR(v) = \frac{1-d}{N} + d\sum_{u \to v} \frac{PR(u)}{\deg^+(u)}$
   (Brin & Page 1998, *unverified-memory, conf. H*); Katz
   $x = \left((\mathbb{I}-\alpha A^{\top})^{-1} - \mathbb{I}\right)\mathbf{1}$,
   convergent for $\alpha < 1/\lambda_{\max}(A)$ (*unverified-memory,
   conf. H*); HITS (Kleinberg, JACM 1999, *unverified-memory, conf. H*).
   Truck-factor estimation from authorship (Avelino et al., ~ICPC 2016,
   *unverified-memory, conf. M*).
3. **Measures.** Different notions of "how much does the system lean on
   this node": PageRank/Katz on the **reversed** use-graph ≈ accumulated
   reliance; betweenness ≈ brokerage/bottleneck; HITS separates heavy callers
   (hubs) from heavy callees (authorities). All [D]; the *interpretation* as
   importance is [I].
4. **Inputs.** L1/L2 projections; L6 authorship for ownership-adjusted
   variants; coverage (L4) for risk quadrants.
5. **Abstraction.** Spectral/path statistics on digraphs; nothing
   language-specific.
6. **Diagnostic uses.** Keystone (structurally critical) components =
   top-percentile reliance; **hidden hubs** = high betweenness with modest
   degree (quiet brokers that reviews overlook); god-component candidates =
   simultaneously top-decile in size, degree, and low cohesion (join with F5);
   staffing risk = high reliance × truck factor 1.
7. **Example findings.** "`OrderService.apply()` is in the top 1% by Katz on
   the reversed call graph, has 0% branch coverage, and one effective owner —
   the single highest-priority test-reinforcement target."
8. **Failure modes.** *Centrality ≠ importance*: loggers, serializers, and
   utility roots dominate raw rankings — normalize within `stereotype`
   strata or exclude declared-utility roles before ranking; damping/attenuation
   constants shift rankings (report rank stability across a parameter sweep,
   not a single list); betweenness on huge graphs needs sampling
   (approximation error must be shown).
9. **Scenario support.** Reliance scores parameterize the risk function
   $\mathrm{risk}(\cdot)$ of every plan step (§4.4): touching a keystone costs more risk
   budget; sequencing heuristics do low-centrality moves first.
10. **Outputs.** Percentile-normalized ranking tables per stratum;
    centrality×coverage quadrant chart; truck-factor report per module.

---

### F5. Clustering and community detection

1. **Name.** Modularity maximization (Louvain/Leiden); flow-based
   communities (Infomap); spectral partitioning; edge-betweenness hierarchical
   splitting; software-specific MQ hill-climbing (Bunch); consensus
   clustering; declared-vs-detected comparison (MoJoFM, ARI/NMI).
2. **Basis.** Newman–Girvan modularity
   $Q = \frac{1}{2m}\sum_{ij}\left[A_{ij} - \frac{k_i k_j}{2m}\right]
   \delta(c_i,c_j)$ (*unverified-memory, conf. H*; directed variant exists,
   Leicht & Newman 2008, *unverified-memory, conf. M*). Louvain (Blondel et
   al., J. Stat. Mech. 2008, *unverified-memory, conf. H*); **Leiden**, which
   fixes Louvain's defect of arbitrarily badly connected — even disconnected —
   communities and guarantees connected communities: Traag, Waltman & van Eck,
   *Scientific Reports* 9:5233, 2019, DOI 10.1038/s41598-019-41695-z —
   **✓ verified in-session** (the paper reports up to 25% badly connected /
   16% disconnected communities from iterated Louvain, and offers the CPM
   objective as a resolution-limit-free alternative). Infomap's map equation
   (Rosvall & Bergstrom, PNAS 2008, *unverified-memory, conf. H*) — natural
   for **directed call graphs** because it models flow. Bunch's MQ objective
   over the module dependency graph (Mancoridis/Mitchell, late 1990s–TSE 2006,
   *unverified-memory, conf. M*): with $\mu_i$ intra-edges and
   $\varepsilon_{ij}$ inter-edges, cluster factor
   $CF_i = \frac{2\mu_i}{2\mu_i + \sum_{j\ne i}(\varepsilon_{ij}+\varepsilon_{ji})}$,
   $MQ = \sum_i CF_i$ (*formula from memory — verify against the TSE 2006
   paper before implementing*). MoJoFM for partition distance (Wen & Tzerpos,
   ~2004, *unverified-memory, conf. M*).
3. **Measures.** Latent modular structure implied by actual edges [D per run],
   and its divergence from the *declared* package structure [D]. The claim
   "the latent structure is the better design" is [I].
4. **Inputs.** Weighted $\pi_{\text{file}}$ or $\pi_{\text{class}}$ of L1∪L2;
   optionally blended edge weights
   $w = \lambda_1 w_{\text{static}} + \lambda_2 w_{\text{co-change}} +
   \lambda_3 \cos(\text{embeddings})$ (λ's [S] until tuned; the
   structural+semantic blend follows Bavota et al., ~WCRE 2010,
   *unverified-memory, conf. M*).
5. **Abstraction.** Partitioning of a weighted digraph; the only semantic
   input is the weight blend, which is already normalized.
6. **Diagnostic uses.** Excessive coupling/fragmentation read directly off
   modularity and community size distribution; **misplaced components**
   ("feature envy" at module scale): node whose affinity to a foreign
   community exceeds affinity to its declared home; candidate split lines for
   monoliths; candidate merge groups for over-fragmented systems.
7. **Example findings.** "Detected 9 communities vs. 23 declared packages;
   MoJoFM 0.44 — the declared structure explains less than half the latent
   one. 17 classes have ≥70% of their weighted edges into `pricing` while
   living in `checkout`."
8. **Failure modes.** **Resolution limit** of modularity — small genuine
   modules get absorbed (Fortunato & Barthélemy, PNAS 2007,
   *unverified-memory, conf. H*; mitigation: Leiden with CPM objective, ✓
   above, or multi-resolution sweeps). Non-determinism: run $n$ seeds and keep
   the consensus partition (Lancichinetti & Fortunato, ~2012,
   *unverified-memory, conf. M*); never present a single stochastic run as
   "the" structure. Detected ≠ desirable: communities reflect what *is*, which
   may itself be eroded — always confront with declared intent and with F13
   conformance. Architecture-recovery techniques disagree with each other
   substantially (comparative study: Garcia, Ivkovic & Medvidović, ~ASE 2013,
   *unverified-memory, conf. M*) — treat outputs as candidates, not truth.
9. **Scenario support.** Communities are the **candidate target modules** in
   monolith-split scenarios (§5.1) and the merge groups in de-fragmentation
   (§5.2); inter-community edge sets seed the cut computation (F6).
10. **Outputs.** Consensus partition + stability score per node; alluvial
    diagram declared→detected; misfit node list with affinity evidence.

---

### F6. Cut / min-cut / refactoring-boundary discovery

1. **Name.** s–t min-cut (max-flow); Gomory–Hu tree (all-pairs min-cuts);
   normalized cut; conductance-based sweep; sparsest cut; balanced
   multiway cut; minimum vertex separators; constrained cuts
   (must-link/cannot-link).
2. **Basis.** Max-flow/min-cut duality (Ford–Fulkerson; practical
   push-relabel; $O(VE)$ known via Orlin 2013 — *unverified-memory,
   conf. M-H*); Gomory–Hu 1961 builds all pairwise min-cuts with $n{-}1$
   max-flows (*unverified-memory, conf. H*); normalized cut
   $\mathrm{Ncut}(A,B) = \frac{\mathrm{cut}(A,B)}{\mathrm{assoc}(A,V)} +
   \frac{\mathrm{cut}(A,B)}{\mathrm{assoc}(B,V)}$ (Shi & Malik, PAMI 2000,
   *unverified-memory, conf. H*); conductance
   $\phi(S)=\frac{\mathrm{cut}(S,\bar S)}{\min(\mathrm{vol}(S),\mathrm{vol}(\bar S))}$;
   sparsest cut has an $O(\sqrt{\log n})$ approximation (Arora–Rao–Vazirani,
   STOC 2004, *unverified-memory, conf. M-H*); multiway cut and balanced
   partitioning are NP-hard — use ILP or spectral/KL heuristics.
3. **Measures.** The **width of the interface** that a proposed boundary
   would create: cut weight ≈ how much API you will have to design, stabilize,
   and maintain if you split there. [D] for the number; [I] for its reading as
   effort.
4. **Inputs.** Weighted module/class projection; weights should blend static
   call/type mass, co-change lift, and **shared mutable state** (F8's
   READS/WRITES on common fields — data coupling is the cut-killer that pure
   call weights miss).
5. **Abstraction.** Weighted-graph partition geometry; the semantics live
   entirely in the weight function, which is declared and auditable.
6. **Diagnostic uses.** Weak boundaries = declared module borders whose
   internal cut value is *lower* than some undeclared line through the middle
   (the "real" seam is elsewhere); extraction feasibility = min-cut around a
   seed set; single vertex separators of small size = natural facade points.
7. **Example findings.** "Extracting `invoicing` as drawn costs a 214-edge
   interface; shifting 6 classes per the min-cut reduces it to 38 edges, 30 of
   which are already interface-typed."
8. **Failure modes.** Unweighted cuts split at *accidental* thin points
   (through a config file or an enum) — weights are not optional; min-cut
   ignores balance (can return a trivial 1-node side) — use Ncut/conductance
   or balance constraints; shared-database coupling is invisible unless L5
   `BINDS_TO`/schema edges were extracted — a cut that looks cheap in code can
   be expensive in data (state this caveat on every cut report where L5 is
   missing).
9. **Scenario support.** Every split/extract/isolate rule (§4.4) prices its
   cost from the cut it induces; Gomory–Hu gives the global "where is
   splitting ever cheap" map that seeds scenario generation.
10. **Outputs.** Cut sets with per-edge repair suggestions ("this edge becomes
    API endpoint / this one argues for moving class X"), proposed-interface
    tables, conductance sweep plots.

---

### F7. Path and shortest-path analysis

1. **Name.** BFS/Dijkstra shortest paths; k-shortest (Yen); widest/bottleneck
   paths; DAG longest path; **change-impact radius** (attenuated forward
   closure on reversed use-edges); reverse impact ("what can break me");
   affected-test selection; path witnesses for explainability.
2. **Basis.** Classical path algorithms [D]; attenuation via geometric decay
   or Katz-style resolvent $\iota = (\mathbb{I}-\alpha W^{\top})^{-1}
   e_\Delta$ on the confidence-weighted matrix $W$; safe regression-test
   selection as the formal frame for "which tests must rerun" (Rothermel &
   Harrold, TOPLAS 1997, *unverified-memory, conf. M-H*); change-impact
   analysis surveys exist (Bohner & Arnold 1996; Li et al., STVR ~2013 —
   *unverified-memory, conf. M*).
3. **Measures.** For a change set $\Delta$: the set, size, and strength of
   downstream effects. With edge confidences: $\mathrm{imp}(v) = \max_{\pi:
   \Delta \rightsquigarrow v} \prod_{e \in \pi} c(e)\,\alpha$ — [D] as a
   computation, [I] as a prediction of real breakage.
4. **Inputs.** Union behavioral graph $B$ = reversed(CALLS ∪ DEPENDS_ON) ∪
   FLOWS_TO ∪ TRANSITIONS, with $c$; L4 for test joins.
5. **Abstraction.** Weighted-path algebra over a declared edge-type union;
   semantics confined to the union choice (declared per query).
6. **Diagnostic uses.** Change-impact radius / blast size
   $|\{v : \mathrm{imp}(v) > \theta\}|$; inefficient control flow at the
   architecture scale (long mandatory chains between entry and effect — every
   hop is comprehension and latency cost [S as a productivity claim]); test
   gap = impacted nodes with no covering test.
7. **Example findings.** "Changing `TaxRule.rate` impacts 312 nodes at
   $\theta{=}0.1$; 74 are uncovered; the 12 integration tests selected cover
   only 41% of the impacted set."
8. **Failure modes.** $\alpha$ and $\theta$ are arbitrary until calibrated —
   the honest calibration is retrospective: for historical fixes, measure the
   graph distance between fix-site and regression-site and fit the decay to
   that distribution [I]; low-confidence edges both create false impact and
   hide true impact (report optimistic/pessimistic per §1.4); impact through
   *data at rest* (shared DB rows, message schemas) needs L5 edges or it is
   silently absent.
9. **Scenario support.** Impact is **the** engine primitive: every candidate
   plan step calls it to price risk and to bound the verification workload
   (which tests, which manual checks).
10. **Outputs.** Impact subgraphs with per-node scores, affected-test lists,
    radius trend per module, and always a **path witness** — the concrete
    chain explaining *why* v is claimed impacted (non-negotiable for trust).

---

### F8. Control-flow and data-flow analysis

1. **Name.** CFG construction; dominator trees; natural-loop detection;
   cyclomatic and essential complexity; reaching definitions / liveness /
   constant propagation; def-use chains; program slicing; taint
   (source→sink) reachability; alias/points-to as a confidence provider;
   purity/effect inference.
2. **Basis.** McCabe's cyclomatic number $M = E - N + 2P$ on the CFG
   (McCabe, TSE 1976, *unverified-memory, conf. H*); Lengauer–Tarjan
   dominators (TOPLAS 1979, *unverified-memory, conf. H*); the dataflow
   lattice/fixpoint framework (Kildall, POPL 1973; Dragon Book —
   *unverified-memory, conf. H*); program dependence graph (Ferrante,
   Ottenstein & Warren, TOPLAS 1987) and interprocedural slicing on the
   system dependence graph (Horwitz, Reps & Binkley, ~1988–1990) —
   *unverified-memory, conf. H*; slicing originally Weiser (1981/84,
   *unverified-memory, conf. H*); points-to: Andersen 1994 / Steensgaard 1996
   (*unverified-memory, conf. H*). The CPG (✓ Yamaguchi et al. 2014, §1.7) is
   the packaging of exactly these views into one graph.
3. **Measures.** Intra- and inter-procedural behavior facts: unreachable
   blocks, dead stores, loop structure, which values flow where, which
   entry-controlled data reaches which dangerous operations.
4. **Inputs.** L2 at BasicBlock granularity; effect attributes; source/sink
   role annotations for taint.
5. **Abstraction.** The frontend contract lowers language semantics into a
   normalized effect vocabulary — `reads(x)`, `writes(x)`, `io(kind)`,
   `throws(T)`, `alloc` — plus CFG/def-use edges. What cannot be lowered is
   recorded as a normalization gap with degraded confidence, never silently
   approximated (§1.2, §1.4).
6. **Diagnostic uses.** Dead code and unreachable paths at statement
   granularity [D]; complexity hotspots; duplicated flow patterns (feeds F12);
   probable defect zones via taint paths (injection candidates) [D-path,
   I-verdict]; the **loop-carried IO heuristic**: a natural loop containing a
   call path to an `io`-effect node ≈ N+1 query pattern [S — heuristic,
   expect false positives, always show the path].
7. **Example findings.** "9 taint paths from `EnvVar:REQUEST_BODY`-derived
   data to `io:sql` sinks bypass the sanitizer node." "`report.render` has
   $M{=}61$ and 3-deep nested loops each crossing an IO boundary."
8. **Failure modes.** Everything here inherits call-graph unsoundness —
   dynamic dispatch and reflection make both false paths and missed paths;
   the two-run (optimistic/pessimistic) discipline of §1.4 applies with the
   most force in this family. Cyclomatic complexity correlates strongly with
   plain size; report it size-normalized or alongside LOC to avoid
   double-counting [I-claim about correlation, conf. M].
9. **Scenario support.** Slices bound the rewrite scope of any plan step
   (what must be understood/retested); purity marks cheap-to-move behavior;
   taint results gate security-motivated isolation scenarios.
10. **Outputs.** Slices as subgraphs, complexity tables, taint-path reports
    with sanitizer status, dead-block lists with root-set assumptions attached.

### F9. State-machine and temporal analysis

This family has **two distinct meanings** that the specification conflates and
that must be kept separate because their math, inputs, and failure modes are
different: (a) *program state machines* — lifecycle/protocol analysis over L2
`TRANSITIONS` edges; (b) *temporal evolution of the codebase itself* — the
history layer L6. Both are covered here as F9a and F9b.

#### F9a. Lifecycle / protocol state machines

1. **Name.** FSM extraction and temporal-logic checking.
2. **Mathematical basis.** Finite automata; model checking of CTL/LTL
   properties (Clarke & Emerson 1981; the NuSMV/SPIN tool families)
   [unverified-memory, conf. H for the theory, M for exact tool citations].
3. **Measures.** Whether the state machine implied by the code (enum-typed
   state fields + `TRANSITIONS` edges + guard conditions) is complete, safe,
   and live: unreachable states, missing transitions, states with no exit,
   transitions that skip mandatory intermediate states, violated invariants
   such as "never `Closed → Active` without `Reopened`".
4. **Inputs.** L2 `TRANSITIONS` edges with `guard`/`trigger` attributes;
   `Enum`/`State` nodes; optionally runtime traces (L5) to weight actually
   observed transitions.
5. **Abstraction.** Any language construct that encodes a state set and
   transition function — enums + switch, sealed class hierarchies, sum
   types with pattern matching, status columns mutated by services — is
   normalized by the frontend into the same `(State, TRANSITIONS)` shape.
   Extraction confidence varies sharply by construct: exhaustive sum-type
   matches extract at high confidence; string-typed status fields mutated in
   many places extract at low confidence [S — confidence schedule is an
   engineering prior].
6. **Diagnostic uses.** Detect incomplete lifecycle handling (a common
   correctness-risk class); prove or refute reachability of error states;
   verify protocol conformance ("every `open` is followed by `close` on all
   paths" — an LTL property); find dead states (defined but unreachable —
   feeds F1's dead-code evidence pool).
7. **Example findings.** "State `PENDING_REVIEW` has no outgoing transition
   in code, but 214 rows in production hold it [L5 corroboration] — stuck
   states." "LTL check: `G(acquire → F release)` fails via exception path
   `p`; witness path attached."
8. **Failure modes.** State encoded in data (DB rows, external workflow
   engines) is invisible to static extraction — the FSM extracted from code
   may be a strict subset of the real one; model checking suffers state
   explosion when guards depend on unbounded data (mitigate by predicate
   abstraction, at the price of spurious counterexamples) [I]. A missing
   transition is only a defect if the business ever needs it — the graph
   cannot know intent; findings are *questions*, not verdicts.
9. **Scenario support.** Before a plan step rewrites a stateful component,
   the extracted FSM is the behavioral contract the rewrite must preserve;
   counterexample traces become regression tests. Protocol checks gate
   `ExtractService` steps (a state machine split across a new network
   boundary acquires partial-failure transitions that did not exist before —
   the engine must add them to the target FSM or flag the gap).
10. **Outputs.** Extracted FSM diagrams per stateful entity; property-check
    verdicts with witness/counterexample paths; stuck-state and dead-state
    lists with corroboration status.

#### F9b. Temporal / evolutionary analysis of the codebase

1. **Name.** History mining: churn, age, change coupling, hotspots,
   socio-technical congruence.
2. **Mathematical basis.** Time-series statistics; association-rule mining
   (support / confidence / lift) over commit transactions (Zimmermann,
   Weißgerber, Diehl & Zeller, "Mining Version Histories to Guide Software
   Changes", ICSE 2004; extended TSE 2005) [unverified-memory, conf. H];
   Shannon entropy of change distribution as complexity-of-change (Hassan,
   "Predicting Faults Using the Complexity of Code Changes", ICSE 2009)
   [unverified-memory, conf. H]; changepoint detection on activity series;
   Conway's law (Conway 1968) operationalized as socio-technical congruence
   (Cataldo et al., ~2008) [unverified-memory, conf. M].
3. **Measures.** Where change actually happens (churn, relative churn), how
   scattered each change is (entropy $H = -\sum_f p_f \log p_f$ over the
   probability $p_f$ that a change in a period touches file $f$), which
   artifacts change *together* without structural connection (hidden
   coupling), how ownership maps onto structure, and how all of these trend.
4. **Inputs.** L6: `TOUCHED` edges with timestamps and diff sizes,
   `CO_CHANGED` aggregate edges with `support`/`confidence`/`lift`,
   `AUTHORED`/`OWNS`, plus L1 for the structural-overlay comparison.
5. **Abstraction.** VCS history is already language-independent; the only
   normalization needed is commit → transaction mapping and bot/bulk-commit
   filtering (exclude commits touching more than $k$ files, $k \approx 30$,
   and known reformat/codegen commits — otherwise one formatting sweep
   fabricates thousands of co-change pairs) [S — the cutoff is a prior;
   sensitivity-check it per repository].
6. **Diagnostic uses.** **Hotspots** — rank by churn × complexity (the
   composition popularized by Tornhill, *Your Code as a Crime Scene*, 2015
   [unverified-memory, conf. H]) as review/refactor priority; **hidden
   coupling** — high-lift `CO_CHANGED` pairs with no L1 path of length ≤ 2
   between them (candidate shared hidden state, copy-paste, or missing
   abstraction); **change entropy** trend as an early-warning signal of
   eroding modularity; **congruence** — mismatch between `OWNS` boundaries
   and high-co-change clusters predicts coordination failures [I, conf. M].
7. **Example findings.** "`billing/rates.calc` and `reports/tax.calc`
   co-change with lift 6.2 (support 19) and have no structural edge —
   inspect for cloned tax logic (F12 confirms 83% token similarity)."
   "Change entropy rose 41% over two quarters while module count was flat —
   changes increasingly cut across module boundaries."
8. **Failure modes.** Commit hygiene dominates data quality: squash merges,
   monorepo-wide sweeps, and vendored-code imports all corrupt co-change
   statistics — filtering is mandatory, and residual corruption should be
   assumed [M-data, I-inference]. Churn confounds with team size and with
   deliberate improvement work (a heavily refactored file looks "hot" for
   healthy reasons); never treat a hotspot ranking as a defect verdict —
   route it into F11's calibrated risk model instead. Renames break file
   identity unless tracked (use rename detection; log confidence).
9. **Scenario support.** Co-change lift feeds edge weights in F6 cuts and
   the cost model (§4): splitting a high-co-change pair into separate
   components is priced as expensive coordination. Hotspot × low-coverage
   intersections are where scenario plans schedule test reinforcement
   before touching code. Congruence analysis shapes *who* executes which
   plan step (steps inside one ownership boundary are cheaper and lower
   risk than cross-team steps) [I].
10. **Outputs.** Hotspot rankings with trend arrows; co-change overlay
    graphs (structural edges vs. evolutionary edges, with the diff
    highlighted); entropy time series with changepoints; ownership-vs-
    cluster congruence matrices.

### F10. Type-system and API-surface analysis

1. **Name.** Type/API-surface analysis: cohesion, coupling, inheritance
   health, interface segregation, encapsulation leakage, contract churn.
2. **Mathematical basis.** The Chidamber–Kemerer metric suite (WMC, DIT,
   NOC, CBO, RFC, LCOM; Chidamber & Kemerer, TSE 1994) [unverified-memory,
   conf. H]; LCOM4 as the number of connected components of the
   member–field access graph inside a type (Hitz & Montazeri, 1995)
   [unverified-memory, conf. M]; bipartite graph analysis (clients ×
   interface members) and biclustering for interface segregation; simple
   set/counting measures over exported surfaces.
3. **Measures.** Internal cohesion of types; width and depth of inheritance;
   how much of a module's internals leak through its public signatures; how
   many distinct client groups an interface serves; how often published
   contracts change.
4. **Inputs.** L1 type edges (`EXTENDS`, `IMPLEMENTS`, `COMPOSES`,
   `USES_TYPE`), member-level `READS`/`WRITES`/`CALLS` within types, L3
   `EXPOSES` with visibility attributes, `signatureHash` history from L6.
5. **Abstraction.** "Type" is any named unit with members and a visibility
   boundary — class, struct, trait, protocol, module-as-record. Nominal vs.
   structural typing is normalized by the frontend into explicit
   `IMPLEMENTS` edges with confidence < 1.0 for structural/duck conformance
   (§1.4); metrics that assume nominal typing (DIT, NOC) are computed only
   where the language provides it and marked *not applicable* elsewhere —
   an honest gap, not a zero.
6. **Diagnostic uses.** **Unstable abstractions**: types with high fan-in
   (many `USES_TYPE` in) *and* high signature churn — clients keep being
   broken [D-signals, I-verdict]. **God types**: LCOM4 > 1 plus high WMC
   plus high fan-in → split candidates (LCOM4's components are the natural
   split lines). **Interface bloat / ISP violations**: biclustering the
   client × member usage matrix reveals interfaces whose clients partition
   into disjoint member-usage groups — each bicluster is a candidate
   segregated interface. **Encapsulation leakage**: internal (non-exported)
   types appearing in exported signatures [D — direct and cheap to check].
   **Refused bequest / LSP-violation proxies**: subclasses that override
   methods to throw, or that use ≤ small fraction of inherited members
   [I, conf. L–M — genuine LSP violation is behavioral and not statically
   decidable; these are heuristic flags only].
7. **Example findings.** "`IStorage` has 31 members; its 14 clients
   bicluster into 3 groups using disjoint member sets of size 6/9/4 —
   propose 3 role interfaces." "`core.Order` signature changed 11 times in
   6 months with fan-in 87 — unstable keystone abstraction."
8. **Failure modes.** CK metrics correlate with size (as with cyclomatic —
   normalize or co-report LOC); LCOM variants disagree with each other and
   with human judgment on cohesion [M-claim from literature,
   unverified-memory conf. M]; structural-typing conformance edges at
   confidence < 1 can inflate fan-in; generated types (ORMs, protobuf)
   must be excluded via the F11 exclusion list or they dominate every
   ranking.
9. **Scenario support.** ISP biclusters and LCOM4 components are *direct
   plan inputs*: they parameterize `ExtractInterface` and `SplitModule`
   rewrites (§4.2) with concrete member partitions. Signature-churn ×
   fan-in identifies which contracts a migration plan must freeze first
   (contract tests before movement).
10. **Outputs.** Per-type metric tables; interface-segregation proposals
    (member partitions with client evidence); leakage lists; abstraction-
    instability rankings; inheritance-forest health views.

### F11. Statistical anomaly detection and defect-risk modeling

1. **Name.** Distributional outlier detection and calibrated defect
   prediction.
2. **Mathematical basis.** Heavy-tailed distribution fitting — most
   size/degree metrics in software follow power-law-like distributions
   (Louridas, Spinellis & Vlachos, TOSEM 2008 [unverified-memory, conf. M];
   fitting methodology per Clauset, Shalizi & Newman, SIAM Review 2009
   [unverified-memory, conf. H]); robust statistics (median/MAD, IQR)
   instead of z-scores, which are invalid under heavy tails; Isolation
   Forest (Liu et al. 2008) and Mahalanobis distance for multivariate
   outliers [unverified-memory, conf. M]; supervised defect prediction
   with process metrics, which outperform code metrics (Rahman & Devanbu,
   ICSE 2013, DOI 10.1109/ICSE.2013.6606589 — **✓ verified in-session**;
   corroborated by Majumder et al., EMSE 2022, DOI
   10.1007/s10664-021-10068-4 — **✓ verified in-session**); ownership
   effects — minor-contributor share predicts defects (Bird et al.,
   "Don't Touch My Code", FSE 2011) [unverified-memory, conf. H];
   effort-aware evaluation (Mende & Koschke, ~2010) [unverified-memory,
   conf. M].
3. **Measures.** Which nodes are statistically extreme given the codebase's
   own distributions; and, separately, a calibrated probability-like risk
   score that a node contains latent defects.
4. **Inputs.** Any metric vector from F1–F10 per node; L6 process metrics
   (churn, entropy, contributor counts, minor-contributor share); L7
   historical `FIXES` links (the training labels); exclusion lists for
   generated/vendored code.
5. **Abstraction.** Purely numeric — inherently language-independent —
   **but** distributions differ by language and by artifact kind, so all
   normalization is *within stratum* (per language × node stereotype);
   pooling strata invites Simpson's-paradox reversals [I, conf. M].
6. **Diagnostic uses.** Outlier surfacing as *review queues* (never as
   verdicts); **defect-risk ranking** trained within-project on historical
   fix links — cross-project transfer performs poorly without careful
   adaptation (Zimmermann et al., ESEC/FSE 2009) [unverified-memory,
   conf. H] — evaluated effort-aware (defects found per LOC inspected, not
   raw AUC); minor-contributor concentration as an ownership risk signal.
7. **Example findings.** "Top decile of the risk model contains 9 of the
   last 12 months' defect-fixed files (backtest PPV 0.41 vs. base rate
   0.07)." "`parsers/legacy.x` is a 4-metric outlier (size, churn, fan-in,
   contributor entropy) — queue for inspection."
8. **Failure modes.** The dominant one: **treating rankings as facts**.
   Every output here is [I] by construction. Label leakage in backtests
   (fix commits touching files for incidental reasons); class imbalance;
   concept drift after major refactors (retrain windows); generated files
   dominating outlier lists (mandatory exclusion lists); the model
   explaining *past* attention rather than *future* defects. Mitigations:
   temporal cross-validation only (train on months $1..k$, test on $k{+}1$),
   effort-aware metrics, per-detector precision tracking (§6.8).
9. **Scenario support.** The risk score is a direct input to the risk
   function $\mathrm{risk}(\rho)$ of the scenario engine (§4.4): plan steps touching
   high-risk nodes carry higher failure probability; test-reinforcement
   steps are auto-inserted where risk × impact is high and coverage low.
10. **Outputs.** Stratified outlier queues with the metrics that fired;
    risk-ranked file/function lists with SHAP-style per-feature
    explanations [I]; backtest reports (PPV/recall vs. base rate, by
    month); calibration curves.

### F12. Vector-embedding and similarity analysis

1. **Name.** Embedding-space similarity: clone detection, conceptual
   cohesion, cross-language similarity, naming drift.
2. **Mathematical basis.** Learned code representations — code2vec (Alon et
   al., POPL 2019), CodeBERT (Feng et al., 2020), GraphCodeBERT (Guo et
   al., 2021) [all unverified-memory, conf. H that they exist as cited,
   M on venue details]; graph-node embeddings — DeepWalk (2014), node2vec
   (Grover & Leskovec, 2016), GraphSAGE (Hamilton et al., 2017)
   [unverified-memory, conf. H]; classical clone detection — winnowing
   fingerprints (Schleimer et al., SIGMOD 2003), Deckard (Jiang et al.,
   ICSE 2007), SourcererCC (Sajnani et al., ICSE 2016) [unverified-memory,
   conf. M–H]; cosine similarity + approximate nearest neighbors for
   retrieval; conceptual cohesion via latent-semantic similarity of
   identifiers/comments (Marcus & Poshyvanyk, TSE ~2008)
   [unverified-memory, conf. M].
3. **Measures.** Semantic similarity that structure alone misses: Type-1–3
   clones (textual/near-miss) via fingerprints, Type-4 (semantic) clones
   via embeddings [I — high false-positive class]; whether a module's parts
   "talk about the same thing" (conceptual cohesion); similar code across
   *different languages* (the one capability nothing else in this catalog
   provides); divergence between a node's name and its behavior-derived
   neighborhood (naming drift).
4. **Inputs.** Node text (identifiers, comments, bodies) with L0 spans;
   optionally L1/L2 topology for graph-aware embeddings; a labeled clone
   sample for threshold calibration.
5. **Abstraction.** Token/AST-path/graph-context representations are
   computable for any language with a frontend; embedding models trained
   multi-lingually give a shared space — though quality varies by
   language's training-data share [M-claim, conf. M]. Thresholds are *not*
   transferable across models or corpora; calibrate on a local labeled
   sample (§6.8 phase 4).
6. **Diagnostic uses.** Duplicated-logic maps (merge candidates); "same
   concept, two implementations" across services or languages; conceptual-
   cohesion scoring of proposed F5/F6 module boundaries (do the pieces
   belong together *semantically*, not just structurally?); retrieval for
   "find code like this incident's root cause".
7. **Example findings.** "17 function pairs across the Java and Kotlin
   services exceed 0.92 cosine with matching IO effect profiles — retire
   one implementation per pair behind a shared contract." "Cluster C7's
   conceptual cohesion is 0.31 vs. 0.68 median — structurally clustered,
   semantically incoherent; re-examine the F5 boundary."
8. **Failure modes.** Embedding similarity ≈ *lexical/idiomatic* similarity
   more often than *behavioral* equivalence — boilerplate and framework
   idioms cluster tightly (exclude via idiom lists); benchmark inflation is
   documented — BigCloneBench-based Type-4 results overstate real accuracy
   (Svajlenko et al. 2014 benchmark; criticism by Krinke &
   Ragkhitwetsagul, ~2022) [unverified-memory, conf. M]; embeddings are
   opaque — always attach the concrete text spans as evidence, never the
   score alone.
9. **Scenario support.** Merge scenarios (§5.2) use clone maps to price
   deduplication benefit; cross-language similarity identifies
   consolidation targets in polyglot estates; conceptual cohesion is a
   soft objective term in boundary optimization (§4.3).
10. **Outputs.** Clone-pair/clone-class reports with spans and effect-
    profile agreement; similarity heatmaps; conceptual-cohesion scores per
    proposed module; cross-language consolidation candidate lists.

### F13. Category-theoretic and compositional abstractions

**Honest framing first.** This family's value is mostly *structuring* (H) —
it gives precise language for views, mappings, and rewrites and prevents a
class of silent errors — while its *direct diagnostic yield* is low-to-medium.
Every categorical statement kept below changes some computation or check;
anything that would be decoration has been dropped, per the design rule that
formalism must pay rent.

1. **Name.** Functorial views, conformance as partial functors, rewrites as
   double-pushout (DPO) graph transformations.
2. **Mathematical basis.** The free category over the graph $G$ (objects =
   nodes, morphisms = paths); functors between graph-derived categories;
   pushouts/pullbacks; DPO graph rewriting with the gluing condition (Ehrig,
   Ehrig, Prange & Taentzer, *Fundamentals of Algebraic Graph
   Transformation*, 2006) [unverified-memory, conf. H]; general treatment of
   categories in software engineering (Fiadeiro, *Categories for Software
   Engineering*, 2005) and functorial data migration / ologs (Spivak, ~2012)
   [unverified-memory, conf. M].
3. **Measures / guarantees.**
   - **View consistency.** Each derived view of §1.6 (level projection,
     condensation, slice) is a functor from the L1 category; functoriality
     is the *checkable law* that the view preserves composition — i.e., no
     path exists in the view without a witnessing path below. Violations are
     extractor bugs, and this check finds them [D].
   - **Conformance as a partial functor.** The mapping $m$ from the code
     graph to the reference-architecture category (§1.6) must preserve
     composition; edges whose images are not composable in the model are
     exactly the reflexion-model divergences (Murphy, Notkin & Sullivan,
     FSE 1995 / TSE 2001 — **✓ verified in-session**) restated with a law
     attached. Nothing new is computed vs. F2's reflexion check; what is
     gained is the *totality discipline* — unmapped nodes are first-class
     "unknown" findings rather than silently ignored.
   - **Rewrites as DPO rules.** Each transformation in the scenario engine
     (§4.2) is a span $L \leftarrow K \rightarrow R$; the **gluing
     (dangling-edge) condition** — a rule may not delete a node while
     context edges still attach to it — is a *mechanical safety check* that
     rejects incoherent plan steps ("remove module M" while 14 imports
     still point at M) before costing begins. This is the single most
     operationally useful categorical fact in the catalog [D].
   - **Merges as pushouts.** Merging modules $A, B$ over shared interface
     $I$ is the pushout $A +_I B$; the universal property guarantees no
     client distinguishes the merged object from the pair — *at the
     structural layer only*; behavioral equivalence is out of scope and
     must come from tests [D-structural, I-behavioral].
4. **Inputs.** L1 (+ any layer being viewed/rewritten); the reference model
   $\mathcal{M}$; the rule library.
5. **Abstraction.** Categories are built *from* the normalized graph, so
   language independence is inherited; no additional normalization burden.
6. **Diagnostic uses.** Extractor-consistency CI checks (functor laws);
   conformance divergence lists with unmapped-node inventories; plan-step
   admissibility (gluing condition) as a gate in the scenario engine.
7. **Example findings.** "View `π_module` violates functoriality on 3
   paths — extractor drops aggregate edges for re-exported symbols; bug
   filed." "Plan step 7 (`DeleteModule legacy.auth`) rejected: dangling-
   edge condition fails — 9 `CONSUMES` edges from `mobile-bff` remain."
8. **Failure modes.** The main hazard is *over-formalization* — spending
   modeling effort where a plain graph check suffices; mitigate by the
   pay-rent rule above. Pushout-based merge guarantees are routinely
   over-read as behavioral guarantees; they are not [explicitly I at the
   behavioral level].
9. **Scenario support.** Supplies the rewrite formalism itself (§4.2): rule
   admissibility, inverse rules (reversibility is rule-level, §4.5), and
   composition of steps into plans with well-defined intermediate states.
10. **Outputs.** Law-violation reports; admissibility verdicts per plan
    step; formally specified rule library.

### F14. Optimization and scenario planning

1. **Name.** Combinatorial optimization over target assignments and plan
   sequences.
2. **Mathematical basis.** Mixed-integer programming for module assignment;
   ILP for minimum feedback arc set (Karp 1972 for NP-hardness of FAS —
   **✓ verified in-session** within F3's sources); precedence-constrained
   scheduling (single-machine $1|prec|\sum w_j C_j$ is NP-hard; list
   scheduling as approximation) [unverified-memory, conf. H]; A* search
   with admissible heuristics; beam search fallback; multi-objective
   evolutionary optimization NSGA-II (Deb et al., 2002)
   [unverified-memory, conf. H]; search-based software engineering framing
   (Harman & Jones, 2001; O'Keeffe & Ó Cinnéide, JSS 2008)
   [unverified-memory, conf. M–H].
3. **Measures / computes.** Not a diagnostic: this family *produces plans*.
   (a) **Assignment**: binary $x_{v,c}$ = node $v$ goes to component $c$;
   minimize $\sum_{(u,v) \in E} w(u,v)\,[c(u) {\neq} c(v)] + \sum_v
   \text{move}(v)$ subject to must-link/cannot-link constraints, size
   bounds, and **acyclicity of the induced component graph** via
   topological-order variables $o_c \in \mathbb{R}$ with $o_{c(u)} + 1 \le
   o_{c(v)}$ big-M constraints on cross edges [S — standard formulation;
   solver scaling is the constraint in practice]. (b) **Sequencing**: order
   admissible rewrite steps to minimize risk-exposure integral $\sum_t
   r(t)\,\Delta t$ under precedence from rule dependencies. (c) **Search**:
   A* over graph states with $g$ = accumulated cost and $h$ = admissible
   lower bound = sum over remaining violations of each violation's cheapest
   conceivable repair (never overestimates, since repairs can share work);
   beam search when the state space defeats A* memory.
4. **Inputs.** Current graph; target predicate $\Phi$ and soft objectives;
   rule library with cost/risk estimates (§4.4); constraint set.
5. **Abstraction.** Operates purely on the normalized graph and rule
   algebra; nothing language-specific.
6. **Diagnostic uses.** Indirect: the *gap* between current state and
   optimized assignment is itself a finding ("modularity headroom");
   infeasibility certificates from the MILP (irreducible constraint
   subsets) tell the architect which stated constraints conflict — often
   the most informative single output [D given the model].
7. **Example findings.** "No assignment satisfies both 'auth isolated' and
   'no component > 40 kLOC' — IIS: {auth↔session must-link, session↔user
   40k bound}; relax one." "Best found plan: 23 steps, cost 118 pd, peak
   risk 0.34, 9 steps reversible-cheap."
8. **Failure modes.** Garbage-in — optimization *launders* bad costs into
   confident-looking plans; mandatory **sensitivity analysis**: perturb
   cost/risk inputs ±50% and report plan-ranking stability; if the top plan
   flips under small perturbation, report the ensemble, not a winner [S —
   the ±50% band is a prior]. MILP scaling walls (mitigate: cluster first
   with F5, assign clusters not files); local optima in beam/evolutionary
   modes (report search coverage honestly).
9. **Scenario support.** This *is* the scenario engine's computational
   core; §4 specifies the surrounding contract.
10. **Outputs.** Assignments (proposed module maps), ordered plans with
    per-step cost/risk, Pareto fronts (cost × risk × duration),
    infeasibility certificates, sensitivity/stability reports.

### F15. Risk, cost, and impact modeling

1. **Name.** Probabilistic impact, expected loss, and uncertainty-honest
   aggregation.
2. **Mathematical basis.** Monte Carlo simulation over edge-reliability
   draws; expected-loss decomposition $E[L] = \sum_v P(\text{defect}_v \mid
   \text{features}) \cdot \text{exposure}(v)$; tail risk via CVaR; Bayesian
   networks for dependency-failure propagation [S — structure elicitation
   is the hard part]; bootstrap confidence intervals for every aggregate;
   causal-inference guardrails — observational graph data supports
   *association*; causal claims need quasi-experimental designs
   (difference-in-differences across comparable teams/periods; Pearl 2009
   for the framework) [unverified-memory, conf. H for the framework,
   S for applicability here].
3. **Measures.** Distribution — not point — of blast-radius size for a
   candidate change. Two treatments of edge uncertainty, **never
   composed** (composing them double-counts $c(e)$): *(sampling)* draw
   edge presence with $P(\text{edge}) = c(e)$, then propagate with the
   structural decay $\alpha$ only; or *(analytic)* keep all edges and
   attenuate by $\alpha \cdot c(e)$ as in F7. Record blast size per draw;
   report median / p90 / CVaR$_{0.9}$. Expected loss per module. External-
   dependency risk: CVE feed counts, release-freshness lag, upstream
   bus-factor proxies [M-inputs, I-composite]. **Migration difficulty
   index** per module: $f(\text{cut width}, \text{co-change entanglement},
   \text{boundary test coverage}, \text{dynamic-edge share})$ — a
   monotone composite, reported with its inputs, never alone [S —
   weighting is a prior until calibrated against realized migration
   effort].
4. **Inputs.** Confidence-annotated edges (§1.4); F11 risk scores; F7
   impact machinery; L5 dependency metadata + external feeds; L4 coverage.
5. **Abstraction.** Purely numeric over the normalized graph.
6. **Diagnostic uses.** Ranking modules by expected loss focuses scarce
   review; blast-radius distributions replace single-number impact claims
   (the honest answer to "what does change X affect?" is an interval);
   dependency-risk ranking drives the §5.3/§5.4 scenarios.
7. **Example findings.** "Change to `core.types.Money`: median impact 41
   files, p90 210 — the p90 tail is driven by 3 low-confidence reflective
   edges; verifying those first shrinks p90 to 66." "Removing `libFoo`:
   expected effort 12 pd (p10 7, p90 29); risk concentrated in 2 modules
   with signature-level leakage of `libFoo` types."
8. **Failure modes.** False precision — the reason every output is a
   distribution with its drivers attached; correlated failures that
   independent-edge sampling misses (shared infrastructure — model shared-
   fate groups explicitly where known [S]); CVE counts confound popularity
   with insecurity (normalize by usage base; treat as prior, not verdict);
   causal over-claiming ("modularizing *reduced* defects") from
   before/after comparisons without controls — label such statements [S]
   unless a quasi-experiment backs them.
9. **Scenario support.** Supplies $\mathrm{cost}(\rho)$ and $\mathrm{risk}(\rho)$ estimates with
   uncertainty for every plan step (§4.4); CVaR gives risk-averse plan
   ranking; the migration-difficulty index prices scenario 5.1/5.2 moves.
10. **Outputs.** Blast-radius distributions with driver decomposition;
    expected-loss tables; dependency risk cards; calibrated cost/risk
    estimates per rewrite rule with p10/p90 bands.

---

## 3. Diagnostic Dimensions

Each dimension below is a *composite lens*, not a metric. The table gives:
primary signals, the families that compute them, a composite sketch (always
[S] until calibrated per organization — composites are priors, and the
weights must be fit or at least sensitivity-tested locally), and the main
confounder to disclose alongside any score.

| Dimension | Primary signals | Families | Composite sketch [S] | Conf. | Main confounder |
|---|---|---|---|---|---|
| Correctness risk | defect-risk score, taint paths, FSM gaps, hotspot rank | F8, F9a, F9b, F11 | risk model output × exposure | I | past-attention bias in labels |
| Architectural risk | reflexion divergences, cycle mass, layer violations | F2, F3, F13 | violations weighted by span & churn | D→I | reference model itself may be stale |
| Maintainability | size-normalized complexity, hotspot trend, comment/ID coherence | F8, F9b, F12 | inverse of (hotspot × entropy trend) | I | team familiarity is unmeasured |
| Modularity | Q/CPM score, PC, boundary tax | F2, F5 | Q at calibrated resolution, PC | D | resolution limit; aggregate-edge inflation |
| Coupling/cohesion | Ca/Ce/I, LCOM4, conceptual cohesion, co-change spill | F2, F9b, F10, F12 | per-module vector, not scalar | D→I | generated code inflates coupling |
| Testability | coverage links, mock density, boundary width at seams | F4, F6, L4 | % keystones with COVERS + cut width at test seams | M→D | coverage ≠ assertion strength |
| Dependency risk | freshness lag, CVE prior, bus factor, depth of use | F15 | composite risk card | I | popularity/CVE confound |
| Performance / control-flow risk | loop-carried IO, hot-path centrality × complexity | F4, F8 | flagged paths, not scores | S→I | static flags need runtime confirmation (L5) |
| Security exposure | taint reachability, secret references, exposed surface | F8, L3/L5 | reachable-sink count by severity | D-path, I-verdict | soundiness: reflective paths missed |
| Migration difficulty | cut width, entanglement, coverage at boundary, dynamic share | F6, F9b, F15 | migration difficulty index | S | weights uncalibrated until §6.8 phase 6 |

**Against the single scalar.** A combined "maintainability index" in the
tradition of Oman & Hagemeister (1992) compresses incommensurable signals
and is widely criticized for it [unverified-memory, conf. M on the
literature; the design point stands regardless]: this framework recommends
*dashboards of labeled components* (each with its epistemic tag and
confounder note) and reserves scalar composites for within-stratum ranking
where a human will inspect the top of the list anyway.

---

## 4. Scenario Engine

The scenario engine turns the catalog from descriptive into operational. It
is specified as a contract: states, target predicates, a rule algebra, cost
and risk functions, a planner, and a ranking discipline. Everything numeric
in this section is either [D] (computed from the graph) or [S]/[I]
(engineering priors requiring per-organization calibration) — labeled inline.

### 4.1 States and targets

- **State space.** A state is any schema-valid graph $G \in
  \mathcal{G}_\Sigma$ (satisfying the invariants of §1.1). Plans are paths
  in the (astronomically large) state graph whose arcs are rule
  applications; the planner never materializes this space — it searches it.
- **Source state.** The extracted graph, with its confidence annotations —
  the plan must be robust to the fact that some source edges are
  themselves uncertain (§4.6).
- **Target state.** *Not* a concrete graph (over-specification invites
  false precision) but a pair $(\Phi, \Omega)$:
  - $\Phi$ — hard constraints, expressed as first-order/Datalog predicates
    over the graph. Examples: "no SCC spans two declared layers";
    "the module-level graph is a DAG"; "an admissible mapping $m$ into
    reference model $\mathcal{M}$ exists with zero divergent edges";
    "no L1 path from `domain.*` to `infra.*`".
  - $\Omega$ — soft objectives to optimize once $\Phi$ holds: maximize
    modularity $Q$ at calibrated resolution, minimize propagation cost,
    minimize boundary tax.
- **Distance to target.** Reported as an interval, never a scalar:
  $d(G, \Phi) \in [\text{LB}, \text{UB}]$. For each violation $v_i$ let
  $m_i$ be the cost of its cheapest *single-violation* repair. Then
  $\text{LB} = \max\bigl(\max_i m_i,\ \sum_{i \in S} m_i\bigr)$, where $S$
  is a set of violations no available rule can affect two of (checked
  conservatively via disjointness of the repairs' touched regions). Both
  terms are admissible: any complete plan is, in particular, one way to
  fix each $v_i$, so its cost is $\ge$ every $m_i$; and across $S$ repair
  cost mass cannot be shared, so plan cost is $\ge \sum_S m_i$. A plain
  $\sum_i m_i$ over **all** violations is *not* admissible — one rule may
  fix several violations at once, making true cost smaller than the sum.
  $\text{UB}$ = cost of the best concrete plan found so far. The honest
  statement of "how far is the architecture from the goal" is this
  interval plus its top cost drivers.

### 4.2 Transformation rule library

Each rule is a DPO span $L \leftarrow K \rightarrow R$ (F13) with:
`precondition` (includes the gluing condition), `apply : G → G'`, cost and
risk estimators returning distributions (§4.4), and an `inverse` where one
exists. Core library (extensible; names are canonical for the plans in §5):

| Rule | Effect (informal) | Typical cost drivers | Inverse |
|---|---|---|---|
| `MoveMember(m, T→T')` | relocate field/method | fan-in of `m`, cross-module refs created | `MoveMember` back |
| `MoveType(T, M→M')` | relocate a type | `USES_TYPE` fan-in, signature exposure | itself, reversed |
| `ExtractInterface(T, S⊂members)` | introduce role interface | client rewiring count | `InlineInterface` |
| `IntroducePort(T, dep)` | dependency inversion: callee behind an owned interface | call sites, test doubles needed | remove port |
| `ExtractModule(C⊂V)` | materialize a cut as a module | cut width $w(\partial C)$, leaked internal types | `MergeModules` |
| `MergeModules(A,B over I)` | pushout merge | build/packaging logistics, name collisions | `SplitModule` |
| `SplitModule(M, partition)` | divide along LCOM4/F5 lines | internal edge severing | `MergeModules` |
| `InlineModule(M→host)` | dissolve a boundary | ref rewrites (cheap, mechanical) | `ExtractModule` |
| `DeleteDead(C)` | remove unreachable set | verification effort (roots! §F1) | VCS revert only |
| `ReplaceDependency(d→d')` | adapter swap behind a port | port width, semantic diffs of `d'` | swap back if `d` kept |
| `IntroduceFacade(M)` | narrow an exposed surface | client migration to facade | remove facade |
| `StranglerRoute(entry, old→new)` | percentage routing at a seam | routing infra, dual-run cost | flip routing back |
| `ExtractService(C)` | module → deployable with `CALLS→CONSUMES` conversion | **crossing a process boundary multiplies cost/risk of every converted edge** — latency, partial failure, versioning [S multiplier, calibrate] | rarely cheap; treat as low-reversibility |

Two disciplines: (1) a plan step is *admissible* only if its precondition —
including dangling-edge — holds in the intermediate state where it fires;
(2) every rule application emits a **diff artifact** (nodes/edges
added/removed/retyped) so plans are auditable and partially executable.

### 4.3 Constraints

Hard constraints beyond $\Phi$: must-link/cannot-link node pairs (political
or compliance realities are first-class inputs, not annoyances); size
bounds per component; frozen zones (regulatory code that no plan may
touch); budget/step ceilings; "no step may increase violation count of
constraint class X" (monotone-progress option, which shrinks the search
space at the price of excluding some globally cheaper non-monotone plans —
a disclosed trade).

### 4.4 Cost and risk functions

Per rule application $\rho$ on state $G$:

$$\mathrm{cost}(\rho) = \text{base}(\rho) + \sum_{e \in \text{rewired}(\rho)} \text{unit}(e.\text{kind}) \cdot w(e) \cdot \bigl(1 + \eta\,(1 - c(e))\bigr)$$

(Named function $\mathrm{cost}(\cdot)$, never $c(\cdot)$ — $c$ is reserved
for confidence, §0.3; likewise $\eta$ rather than $\pi$, which is reserved
for level projection, §1.6.)

- `base` — fixed logistics of the rule kind; `unit` — per-edge-kind rewiring
  cost (a `CONSUMES` conversion costs a multiple of a local `CALLS`
  rewire); $w(e)$ — the blended weight of §F6, reused here as a
  *rewiring-effort* proxy under the stated assumption that structurally
  necessary, frequently co-changing edges are the expensive ones to rewire
  [S — replaceable once realized-effort data exists, §6.8 phase 6];
  $\eta$ — the **uncertainty penalty** coefficient: rewiring edges we
  are unsure even exist costs extra investigation. All coefficients [S/I]
  — ship with defaults, calibrate against realized effort (§6.8 phase 6),
  and report cost as p10/median/p90 from the estimator's error
  distribution, not a point.

$$\mathrm{risk}(\rho) = 1 - \prod_{v \in \text{touched}(\rho)} \bigl(1 - p_v\bigr), \qquad p_v = \min\Bigl(1,\ \text{base}_r \cdot \bigl(1 - \beta\,\text{cov}_v\bigr) \cdot \gamma(\text{centrality}_v)\Bigr)$$

- $p_v$ — probability that touching $v$ introduces a regression: scaled up
  by missing coverage and by a centrality factor $\gamma \in
  [1, \gamma_{\max}]$ (structurally critical nodes fail louder). $\beta \in
  [0,1)$ caps the credit coverage can earn (default $0.7$ [S]) — full
  coverage must **not** drive $p_v$ to zero, because coverage is execution,
  not verification (§7.2, item 8); the $\min(1,\cdot)$ clamp keeps $p_v$ a
  probability. $\text{base}_r$ is seeded from the F11 within-project model
  where available, else a prior [I→S]. **Plan-level risk** is computed over
  the *union* of touched nodes per phase — a node touched by several steps
  counts once per phase; multiplying per-step risks as if independent
  over-counts repeated touches and is acceptable only as a disclosed
  conservative bound. Correlation assumptions (independence by default;
  shared-fate groups where known) accompany every estimate.

### 4.5 Reversibility and confidence

- **Reversibility** of step $\rho$ (bounded, **higher = easier to undo**):
  $$\text{rev}(\rho) = \frac{\mathrm{cost}(\rho)}{\mathrm{cost}(\rho) + \mathrm{cost}(\rho^{-1})} \in (0,1)$$
  A free inverse gives $\text{rev} \to 1$; an inverse as costly as the
  forward step gives $0.5$; steps with **no semantic inverse** (data
  migrations, published-API removals) get $\text{rev} = 0$. This bounded
  form replaces a raw cost ratio whose direction was ambiguous; the scale
  now agrees with every use (plan metric $\min_\rho \text{rev}$ = weakest
  link; ties broken toward *higher* values, §4.6).
  **VCS revert is not semantic reversibility** — once clients
  adapt to a new surface, reverting the diff does not revert the world;
  the metric prices the *forward-in-time* undo.
- **Confidence** of a step: the minimum edge/node confidence in the
  subgraph it touches — a plan that rewires reflective edges is a plan
  built on guesses, and its confidence says so. Plan confidence = min over
  steps (weakest-link, deliberately conservative) [S — a mean-based
  alternative is defensible; the conservative choice biases toward
  verification steps, which is the intended behavior].

### 4.6 Planner and ranking

1. Compute violations of $\Phi$; derive LB per §4.1 (max of
   single-violation repair minima, plus the additive term over a
   rule-independent subset). Re-evaluate $\Phi$ **incrementally** against
   each step's diff (delta Datalog / memoized violation counts) — naive
   full re-evaluation per expanded state makes search infeasible
   [implementation note].
2. Search: A* over states with $g$ = accumulated median cost, $h$ = LB.
   Rule instances are grounded from violation witnesses only, pruning the
   otherwise unbounded applicable set. Admissible $h$ ⇒ the first goal
   popped is optimal — **with respect to the median point estimates of
   cost**, which is the disclosed meaning of "optimal" here. Beam search
   with width $k$ when memory/branching defeats A* — *reported* as
   approximate.
3. Evaluate each surviving plan $P$ as a **vector**, never pre-collapsed:
   $\bigl(\textstyle\sum \mathrm{cost},\ \mathrm{risk}(P),\ \text{makespan},\ \min_\rho
   \text{rev}(\rho),\ \text{conf}(P)\bigr)$.
4. Rank by Pareto dominance; present the front. If the user demands a
   scalar, apply *their* stated weights and show rank stability under ±50%
   weight perturbation (F14 discipline). Ties broken toward higher
   reversibility and higher confidence — the anti-fragile default [S,
   design choice].
5. **Local-vs-systemic test** (spec question 9): for each violation class,
   compute the participation/Gini concentration of violations over modules
   and layers. Concentrated (few modules, one layer) ⇒ local defects —
   fix-in-place plans; diffuse (spread across SCCs/layers, high entropy) ⇒
   systemic — boundary-level plans; the threshold between them is [S] and
   should be presented as a spectrum position, not a binary.

---

## 5. Concrete Example Scenarios

Each scenario states: source signature (how the engine recognizes it),
target $\Phi$, the rules it draws on, a plan sketch, dominant cost/risk
drivers, outputs, and an honest confidence note. Costs below are
illustrative shapes, not numbers — numbers come from calibrated estimators.

### 5.1 Monolith → SOLID modular architecture

- **Source signature.** One deployable; giant-SCC share high at module
  level; $Q$ low at calibrated resolution; LCOM4 > 1 common in large types.
- **Target $\Phi$.** Module graph is a DAG; each module maps into a
  reference role; no cross-module SCC; public surfaces are interfaces
  (dependency inversion at seams); size bounds respected.
- **Plan sketch.** (1) F5 consensus clustering (structural + co-change +
  conceptual) → candidate module map; human ratification of the map is a
  *required* step — clustering proposes, architects dispose. (2) F3 minimum
  feedback-set → cycle-breaking sub-plans (`IntroducePort`, `MoveType`)
  applied cheapest-first. (3) F6 min-cut extraction of ratified modules,
  ordered by benefit/cost = (violations retired × exposure) / (cut width);
  each `ExtractModule` preceded by contract tests at the future boundary.
  (4) Conformance CI (F2/F13) turned on at first extraction, ratchet mode —
  divergence count may only fall.
- **Cost/risk drivers.** Cut widths; hidden co-change across proposed
  boundaries (the classic underestimated cost); test debt at seams.
- **Outputs.** Module map with per-module cohesion/confidence; ordered plan
  with per-step diffs; ratchet dashboard.
- **Confidence.** Structure [D]; the *right* boundaries are [I] — the human
  ratification step is where inference is converted to decision.

### 5.2 Hyper-modular system → simpler monolith

- **Source signature.** Median module size tiny; inter-module co-change
  exceeds intra-module co-change (changes routinely cross boundaries);
  **boundary tax** — interface/adapter LOC per logic LOC — high; build/CI
  depth (CCD-style cumulative cost) dominated by orchestration.
- **Target $\Phi$.** Component count ≤ N; every merged unit satisfies
  cohesion floor (mLCOM = 1, §0.5, or conceptual cohesion ≥
  threshold); module DAG preserved.
- **Plan sketch.** Rank module pairs by merge affinity = high mutual
  co-change × high mutual static deps × combined size under bound ×
  **correlated churn profiles**. The churn-profile guard matters: merging a
  volatile module into a stable one destroys the stable one's cheap
  reasoning; volatility mismatch is a veto [S — guard rule, empirically
  motivated]. Execute as `MergeModules` pushouts, leaves-of-the-DAG first;
  collapse now-internal interfaces (`InlineInterface`) where only one
  implementation remains.
- **Cost/risk drivers.** Mostly logistics (build, packaging, ownership
  hand-offs); per-merge semantic risk is low because pushout merges don't
  change behavior — the risk concentrates in the interface-collapse steps.
- **Outputs.** Merge sequence with affinity evidence; boundary-tax
  before/after projection; reversibility note (merges invert as splits at
  moderate cost — this scenario is unusually reversible).
- **Confidence.** Signals [D]; the claim "this system is over-modularized"
  is [I] and should be defended with the boundary-tax and co-change
  evidence, not asserted.

### 5.3 Remove one external dependency

- **Plan sketch.** (1) Usage footprint: reverse reachability from the
  dependency's nodes → every reachable internal node [D]. (2) Depth
  classification: *shallow* use (calls only) vs. *deep* use (dependency
  types leaked into internal signatures — `USES_TYPE` on exported members)
  [D]; deep use multiplies cost because removal changes owned contracts.
  (3) `IntroducePort` around the footprint; golden/contract tests capture
  current behavior *through the port*. (4) Implement replacement behind
  port (`ReplaceDependency`); dual-run or shadow-compare where the
  dependency has observable outputs. (5) Swap, monitor, delete.
- **Cost/risk drivers.** Footprint size; deep-use share; behavioral
  peculiarities of the dependency that tests must pin (timezones, edge-case
  math, locale handling — the graph shows *where* it is used, not *which*
  quirks matter [honest gap — sampling of behavior is [I]]).
- **Outputs.** Footprint subgraph; port specification; parity-test list;
  staged plan with rollback points.

### 5.4 Reduce external dependencies from N to zero

- **Framing.** A portfolio problem: maximize risk retired per unit cost —
  knapsack/Pareto ordering by (risk score from F15) / (removal cost from
  5.3's estimator per dependency). The curve is convex: early removals are
  cheap wins; the tail is superlinear.
- **The honest finding.** There is an **effectively irremovable class** —
  crypto, TLS, database drivers, OS bindings — where in-house replacement
  cost and *risk* (especially for crypto) exceed any plausible benefit;
  the correct output for these is a **do-not-reimplement flag** with
  isolation (`IntroducePort`) as the risk treatment instead of removal.
  "N → 0" is thus usually the wrong target; the engine should return the
  Pareto curve and the knee, and say so [design stance, I].
- **Outputs.** Ranked removal portfolio with cumulative cost/risk curve;
  irremovable list with isolation plans instead.

### 5.5 Isolate an unstable module

- **Source signature.** High signature churn × high fan-in (F10's unstable
  abstraction) or high defect risk (F11) with wide impact (F7).
- **Plan sketch.** `IntroduceFacade` narrowing the exposed surface to what
  clients actually use (bipartite usage analysis, F10); contract tests
  freeze the facade; rewire fan-in to the facade; the volatile interior may
  then churn behind a stable membrane; optionally schedule interior rewrite
  as an independent, now-decoupled plan.
- **Effect on metrics.** Blast radius of interior changes drops to facade
  width [D-predictable]; the *facade itself* becomes a keystone — monitor
  it (F4) [disclosed side effect].
- **Outputs.** Facade spec (member set with client evidence); rewiring
  step list; before/after impact-radius projection.

### 5.6 Break a dependency cycle

- **Plan sketch.** For the target SCC: enumerate simple cycles if small
  (Johnson); compute minimum feedback edge set (ILP if small, heuristic
  else) weighted by per-edge repair cost, *not* raw edge count — the
  cheapest edge to cut is the one whose repair (`IntroducePort`,
  `MoveType`, invert a `USES_TYPE` by extracting a shared kernel type) is
  cheapest, and repair costs differ by orders of magnitude. Apply repairs;
  **verify by recomputing the condensation** — cycle "fixes" that merely
  shuffle the SCC are a known failure; the check is cheap and mandatory.
- **Outputs.** Per-cycle repair menu with costs; post-condition proof
  (condensation diff).

### 5.7 Move domain logic out of infrastructure

- **Source signature.** Reflexion divergences of role class domain→infra;
  domain-stereotype nodes reachable *from* infra namespaces; `USES_TYPE`
  of infra types inside domain signatures.
- **Target $\Phi$.** Hexagonal conformance: no L1 path domain → infra
  except through port interfaces owned by domain; infra implements ports.
- **Plan sketch.** Classify violating edges by kind: data-type leakage →
  `MoveType` of the entity into domain + mapping at the edge; behavioral
  call-outs → `IntroducePort` + adapter in infra; execute leaves-first in
  topological order of the violation subgraph so each step strictly
  reduces the divergence count (ratchet property).
- **Outputs.** Violation inventory by repair kind; ordered ratchet plan;
  conformance CI rule set.

### 5.8 Reduce change blast radius / detect likely defect cluster

- **Blast-radius reduction.** Identify nodes with the worst (impact-radius
  p90 × F11 risk) product; treatments in descending typical value:
  facade isolation (5.5), interface segregation (F10 biclusters — clients
  of a narrow role interface stop being impacted by the rest), cycle
  removal (cycles are impact superconductors — everything in an SCC
  impacts everything), and coverage reinforcement where impact can't be
  narrowed (tests don't shrink the radius; they make traversing it safer —
  distinct effects, both priced) [D mechanics, I on treatment choice].
- **Defect-cluster detection.** Density analysis of elevated F11 risk over
  the graph: a connected subgraph whose risk mass significantly exceeds a
  degree-preserving random relabeling (permutation test; graph scan
  statistics in the spirit of Priebe et al. [unverified-memory, conf. M])
  is a *cluster*, suggesting a shared cause — common author epoch, shared
  hidden dependency (check F9b co-change), shared pattern (check F12
  similarity) [I throughout; the cluster is a hypothesis generator, and
  its output is an inspection queue with the shared-feature evidence
  attached, never a verdict].
- **Outputs.** Ranked (impact × risk) worklist with chosen treatment per
  item; cluster reports with candidate common causes; test-reinforcement
  plan for high-impact/low-coverage intersections.

### 5.9 Decision rules: merge, split, isolate, rewrite, delete

Compact defaults the engine applies when asked "what should happen to
component X?" — all [S] policy defaults, each overridable, each emitted
with the evidence that triggered it:

| Verdict | Trigger sketch |
|---|---|
| **Merge** (into Y) | high mutual co-change ∧ high mutual static deps ∧ combined size under bound ∧ compatible churn profiles |
| **Split** | mLCOM > 1 (§0.5) ∨ (low conceptual cohesion ∧ cheap internal min-cut) |
| **Isolate** | unstable surface (churn × fan-in) ∨ high defect risk with wide impact |
| **Delete** | unreachable from ratified roots ∧ zero coverage ∧ no L5 runtime trace ∧ no recent touches — all four; any single signal is insufficient (§F1 caveat) |
| **Rewrite** | hotspot ∧ defect-dense ∧ low coverage ∧ small enough to re-specify — with the standing warning that rewrites reset team knowledge and historically under-deliver; prefer strangler routing over big-bang [S, experience-based prior] |

---

## 6. Implementation-Ready Outputs

### 6.1 Graph schema (TypeScript)

```typescript
// Epistemic and provenance primitives ------------------------------------

export type EpistemicLabel = "M" | "D" | "I" | "S";       // §0.2
export type Confidence = number;                           // [0,1]

export interface SourceSpan {
  file: string; startLine: number; endLine: number;
  startCol?: number; endCol?: number;
}

export interface Provenance {
  extractor: string;            // e.g. "scip-typescript@0.3.1"
  method:
    | "syntactic" | "resolved-static" | "cha" | "points-to"
    | "duck-structural" | "reflective-heuristic"
    | "vcs" | "runtime-trace" | "manual";
  extractedAt: string;          // ISO 8601
  spans: SourceSpan[];          // evidence; empty only for aggregates
  normalizationGaps?: string[]; // what could NOT be lowered (§1.2)
}

// Node / edge vocabularies ------------------------------------------------

export type NodeType =
  | "Repository" | "Package" | "Module" | "Namespace" | "File"
  | "Class" | "Interface" | "Trait" | "Struct" | "Enum" | "Protocol"
  | "Function" | "Method" | "Constructor" | "Accessor"
  | "Parameter" | "ReturnType" | "Variable" | "Constant" | "Field"
  | "TypeNode" | "GenericParam"
  | "BasicBlock" | "Statement" | "State"
  | "ApiEndpoint" | "Service" | "Port" | "Adapter"
  | "Test" | "Fixture" | "Mock"
  | "ConfigKey" | "EnvVar" | "SecretRef"
  | "ExternalDependency" | "RuntimeService" | "InfraBinding"
  | "Person" | "Team" | "Commit"
  | "Defect" | "Incident" | "Todo" | "LintFinding" | "SecurityFinding";

export type EdgeType =
  | "CONTAINS" | "DECLARES"
  | "IMPORTS" | "EXPORTS" | "DEPENDS_ON" | "BUILDS_WITH"
  | "EXTENDS" | "IMPLEMENTS" | "MIXES_IN" | "COMPOSES"
  | "USES_TYPE" | "CONSTRAINED_BY" | "OVERRIDES"
  | "CALLS" | "READS" | "WRITES" | "ALIASES" | "FLOWS_TO" | "CFG_NEXT"
  | "THROWS" | "HANDLES" | "TRANSITIONS" | "EMITS" | "LISTENS"
  | "EXPOSES" | "CONSUMES"
  | "COVERS" | "ASSERTS_ON" | "MOCKS"
  | "CONFIGURES" | "READS_CONFIG" | "REFERENCES_SECRET"
  | "BINDS_TO" | "DEPLOYS_TO"
  | "AUTHORED" | "TOUCHED" | "REVIEWED" | "OWNS" | "CO_CHANGED" | "FIXES"
  | "LOCATED_IN";

export interface CodeNode {
  id: string;                    // stable content-addressed id
  type: NodeType;
  name: string;
  attrs: Record<string, unknown>;   // open attribute bag (§1.2)
  layer: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
  label: EpistemicLabel;
  confidence: Confidence;
  provenance: Provenance;
}

export interface CodeEdge {
  id: string;
  type: EdgeType;
  source: string; target: string;   // node ids; direction = user → used
  attrs: Record<string, unknown>;   // e.g. dispatch, remote, support/lift
  label: EpistemicLabel;
  confidence: Confidence;           // §1.4 schedule
  provenance: Provenance;
  // The L0–L7 layer of an edge is determined by its type (§1.5); not stored.
  witnesses?: string[];             // for aggregate edges: finer edge ids
}

export interface CodebaseGraph {
  nodes: Map<string, CodeNode>;
  edges: Map<string, CodeEdge>;
  schemaVersion: string;
  invariantsCheckedAt?: string;     // §1.1 invariants I1–I4
}

// Scenario engine ----------------------------------------------------------

export interface Estimate { p10: number; median: number; p90: number; }

export interface Diff {
  addedNodes: CodeNode[]; removedNodeIds: string[];
  addedEdges: CodeEdge[]; removedEdgeIds: string[];
  retyped: Array<{ id: string; from: string; to: string }>;
}

export interface RewriteRule {
  name: string;
  /** includes the DPO gluing / dangling-edge condition (§F13) */
  precondition(g: CodebaseGraph, args: unknown): { ok: boolean; why?: string };
  apply(g: CodebaseGraph, args: unknown): Diff;
  cost(g: CodebaseGraph, args: unknown): Estimate;     // §4.4, calibrated
  risk(g: CodebaseGraph, args: unknown): Estimate;     // §4.4
  inverse?: { name: string; args(diff: Diff): unknown };
}

export interface PlanStep {
  rule: string; args: unknown;
  cost: Estimate; risk: Estimate;
  reversibility: number;            // in [0,1], 1 = free to undo (§4.5)
  confidence: Confidence;           // min over touched subgraph
  diff: Diff;
}

export interface Plan {
  steps: PlanStep[];
  totalCost: Estimate; totalRisk: Estimate;
  makespan: number; minReversibility: number; confidence: Confidence;
  targetSatisfied: boolean;         // Φ check on final state
  sensitivityStable: boolean;       // §F14 ±50% perturbation check
}
```

### 6.2 Metrics catalog

Level: N=node, M=module, G=graph. Label/Conf per §0.2 (Conf. = confidence
in the *method*, not in any single reading).

| Metric | Level | Formula / definition | Label | Conf. |
|---|---|---|---|---|
| LOC (logical) | N | normalized statement count | M | H |
| Cyclomatic $M$ | N | $E - N + 2P$ over CFG | D | H |
| Fan-in / fan-out | N/M | in/out degree by edge kind | D | H |
| Afferent/efferent $C_a, C_e$ | M | external in/out module deps | D | H |
| Instability $I$ | M | $C_e / (C_a + C_e)$ | D | H |
| Abstractness $A$ | M | abstract types / total types | D | M (lang-dependent) |
| Distance $D$ | M | $\lvert A + I - 1 \rvert$ | D | M |
| Propagation cost | G | $\frac{1}{n^2}\sum_{ij} V_{ij}$, $V$=closure | D | H |
| CCD / ACD / NCCD | G | cumulative component dependency (Lakos) | D | M |
| PageRank | N | eigenvector of Google matrix | D | H |
| Betweenness | N | Brandes over chosen layer | D | H |
| Katz influence | N | $((I-\alpha A^\top)^{-1}-I)\mathbf{1}$ | D | H |
| k-core index | N | max k s.t. node in k-core | D | H |
| Modularity $Q$ | G | Newman–Girvan at stated resolution | D | H (given resolution) |
| MoJoFM | G | move-and-join distance to reference decomposition | D | M |
| SCC count / max share | G | Tarjan over module layer | D | H |
| Feedback set size | G | min edge set breaking all cycles | D (bound) | H hard / M heuristic |
| LCOM4 | N | components of member–field graph | D | M |
| CK suite (WMC, DIT, NOC, CBO, RFC) | N | per Chidamber–Kemerer | D | M–H |
| Churn / relative churn | N | lines changed / size (Nagappan & Ball, ICSE 2005) [unverified-memory H] | M | H |
| Change entropy | M/G | $-\sum p_f \log p_f$ per period | D | H |
| Co-change lift | pair | obs. co-freq / expected under independence | D | M (hygiene-dependent) |
| Ownership / minor share | N | contribution concentration | M | H |
| Truck factor | G | min authors covering ≥½ of ownership | D | M |
| Coverage | N | COVERS density; branch % where available | M | H |
| Hotspot score | N | churn × normalized complexity | D | M |
| Defect risk | N | calibrated within-project model output | I | M (backtested) |
| API surface area | M | exported members × signature width | D | H |
| Encapsulation leakage | M | internal types in exported signatures | D | H |
| Clone coverage | G | % LOC in clone classes | D | M |
| Conceptual cohesion | M | mean pairwise semantic similarity | I | M |
| Boundary tax | M/G | interface+adapter LOC / logic LOC | D | M [S threshold] |
| Freshness lag | dep | time behind latest upstream release | M | H |
| CVE exposure | dep | known CVEs weighted by reachability | M-count, I-composite | M |
| Migration difficulty index | M | §F15 composite | S | L until calibrated |

### 6.3 Algorithms catalog

| Task | Algorithm | Complexity | Ref (status) |
|---|---|---|---|
| SCC / condensation | Tarjan | $O(V{+}E)$ | Tarjan 1972 [unverified-memory H] |
| Cycle enumeration | Johnson | $O((V{+}E)(c{+}1))$ | Johnson 1975 [unverified-memory H] |
| Min feedback arc set | ILP exact / Eades–Lin–Smyth | NP-hard / $O(E)$ heur. | Karp 1972 ✓; ELS ~1993 [mem M] |
| Betweenness | Brandes | $O(VE)$ | Brandes 2001 [mem H] |
| PageRank / Katz | power iteration / sparse solve | $O(kE)$ | standard [mem H] |
| Communities | Leiden (CPM) | ~$O(E \log V)$ empir. | Traag et al. 2019 ✓ |
| Consensus clustering | co-association resampling | $k \times$ base cost | Lancichinetti & Fortunato ~2012 [mem M] |
| Max-flow / min-cut | Orlin / push-relabel | $O(VE)$ | Orlin 2013 [mem M] |
| Balanced sparse cut | spectral / METIS-style | heuristic | Shi–Malik 2000 [mem H] |
| k-shortest paths | Yen | $O(kV(E{+}V\log V))$ | Yen 1971 [mem H] |
| Dominators | Lengauer–Tarjan | near-linear | LT 1979 [mem H] |
| Dataflow fixpoint | worklist over lattice | height-bounded | Kildall 1973 [mem H] |
| Slicing | SDG reachability | linear in SDG | Horwitz–Reps–Binkley 1990 [mem H] |
| Points-to | Andersen / Steensgaard | cubic / near-linear | [mem H] |
| Model checking | NuSMV/SPIN-class | state-space bounded | [mem M] |
| Association rules | FP-growth on commits | data-dependent | Zimmermann et al. 2004 [mem H] |
| Outliers | median/MAD, Isolation Forest | $O(n)$ / $O(n\log n)$ | CSN 2009 [mem H]; Liu 2008 [mem M] |
| ANN similarity | HNSW | $O(\log n)$ query empir. | Malkov & Yashunin ~2018 [mem M] |
| Assignment | MILP (branch-and-cut) | exp worst-case | standard [mem H] |
| Plan search | A* admissible / beam | space-bounded | standard [mem H] |
| Multi-objective | NSGA-II | $O(MN^2)$ per gen | Deb 2002 [mem H] |

### 6.4 Pseudocode (four core routines)

```text
# 1. Condensation + declared-layer violation report
#    Note: violations are relative to *declared* layers (an input),
#    not to levels derived from the graph — deriving layers from the
#    same graph you then judge is circular.
function layerViolations(G_module, declaredLayer: Node -> int):
    C := tarjanSCC(G_module)                     # condensation DAG
    viol := []
    for SCC s in C where |s| > 1:
        if |{declaredLayer(v) : v in s}| > 1:
            viol.append(CrossLayerCycle(s))       # severe class
    for edge (u,v) in G_module:
        # Convention: declaredLayer numbers increase upward; 0 = foundation.
        # u -> v means "u uses v"; using something ABOVE you is the violation.
        if declaredLayer(u) < declaredLayer(v):
            viol.append(UpwardDependency(u, v, witness=spans(u,v)))
    return viol, C
```

```text
# 2. Impact radius with attenuation (F7)
#    Impact propagates to *users*: traverse structural edges in reverse.
# Bounded-depth monotone propagation (approximate). Exact max-product
# scores = Dijkstra with edge lengths -log(alpha * e.confidence).
function impactRadius(G, seed, alpha, theta, maxDepth):
    frontier := {(seed, 1.0)}; imp := {seed: 1.0}
    for depth in 1..maxDepth:
        next := {}
        for (v, s) in frontier:
            for e in inEdges(G, v) where structural(e.type):
                u := e.source                     # user of v
                s' := s * alpha * e.confidence
                if s' > theta and s' > imp.get(u, 0):
                    imp[u] := s'; next.add((u, s'))
        frontier := next
    return imp    # node -> score in (theta, 1]; ALWAYS pair with witness paths
```

```text
# 3. Extraction boundary via s-t min-cut (F6)
function extractionBoundary(G_w, seedSet, forbidSet):
    # G_w: module/file graph, blended symmetric weights w(e) (§F6)
    build flow network: superSource S -> each seed (cap ∞)
                        each forbid  -> superSink T (cap ∞)
                        each edge e: cap w(e)
    (cutEdges, S_side) := minCut(S, T)
    return Component(nodes = S_side,
                     interface = cutEdges,        # future module surface
                     width = sum(w(e) for e in cutEdges))
```

```text
# 4. A* plan search with admissible bound (F14/§4.6)
function planSearch(G0, Phi, rules, budget):
    open := priorityQueue()
    open.push(state=G0, g=0, h=LB(G0, Phi), plan=[])
    while open not empty and cost budget not exceeded:
        (G, g, plan) := open.popMin(g + h)
        V := violations(G, Phi)
        if V is empty: yield Plan(plan)           # keep searching for front
        for rule r, args a in applicable(rules, G):   # incl. gluing check
            G' := r.apply(G, a)
            g' := g + r.cost(G, a).median
            open.push(G', g', plan + [(r, a)], h=LB(G', Phi))
    # LB(G, Phi): admissible bound per §4.1 —
    #   max_i m_i   (m_i = cheapest single-violation repair; any complete
    #                plan is one way to fix each v_i, so plan cost >= m_i)
    #   plus, over a subset S no rule can doubly fix, sum_{i in S} m_i.
    # A plain sum over ALL violations is NOT admissible: one rule may fix
    # several violations at once, so true cost can undercut that sum.
```

### 6.5 Example queries

Cypher (property-graph engines):

```cypher
// Upward layer violations with witnesses
// (declaredLayer increases upward; 0 = foundation — see §6.4, routine 1)
MATCH (a:Module)-[d:DEPENDS_ON]->(b:Module)
WHERE a.declaredLayer < b.declaredLayer
RETURN a.name, b.name, d.confidence, d.witnesses;

// Untested keystones: top-centrality nodes with no covering test
MATCH (n) WHERE n.pagerankPct >= 0.95
AND NOT ( (:Test)-[:COVERS]->(n) )
RETURN n.name, n.layer, n.pagerankPct ORDER BY n.pagerankPct DESC;

// Hidden coupling: strong co-change, no nearby structural path
MATCH (a:File)-[c:CO_CHANGED]-(b:File)
WHERE c.lift > 3 AND c.support >= 10
AND NOT ( (a)-[:IMPORTS|CALLS|USES_TYPE*1..2]-(b) )
RETURN a.path, b.path, c.lift, c.support ORDER BY c.lift DESC;

// Domain -> infrastructure role violations (hexagonal check)
MATCH (d)-[e:CALLS|USES_TYPE]->(i)
WHERE d.role = 'domain' AND i.role = 'infrastructure'
AND NOT i.stereotype = 'port'
RETURN d.name, type(e), i.name, e.provenance;
```

Datalog (deductive engines; recursion is the point):

```prolog
reach(X, Y) :- dep(X, Y).
reach(X, Y) :- dep(X, Z), reach(Z, Y).

inCycleWith(X, Y)     :- reach(X, Y), reach(Y, X), X != Y.
cyclicModulePair(A,B) :- inCycleWith(X,Y), moduleOf(X,A), moduleOf(Y,B), A != B.

impacted(T, X) :- changed(X), T = X.
impacted(T, X) :- dep(U, V), impacted(V, X), T = U.      % users of impacted

affectedTest(T, X) :- covers(T, N), impacted(N, X).

deadCandidate(N) :- node(N), not reachFromRoots(N), not covered(N),
                    not runtimeSeen(N), not touchedRecently(N).
                    % all four required — aligned with §5.9 Delete
```

### 6.6 Consolidated scoring formulas

All composites [S] until locally calibrated; report with inputs visible.

- Cycle severity: $\text{sev}(S) = |S| \cdot \text{spread}(S) \cdot
  (1 + \log(1 + \text{churn}(S))) \cdot \bar{w}(S)$
- Hotspot: $\text{churn}_{90d}(v) \times \dfrac{M(v)}{\text{LOC}(v)}$
- Unstable abstraction: $\text{fanIn}(T) \times \text{sigChurn}_{180d}(T)$
- Step cost / step risk: §4.4 formulas
- Reversibility: $\mathrm{cost}(\rho)\,/\,(\mathrm{cost}(\rho) + \mathrm{cost}(\rho^{-1}))$; no semantic inverse ⇒ $0$ (§4.5)
- Plan vector: $(\sum \mathrm{cost},\ \mathrm{risk},\ \text{makespan},\ \min \text{rev},\ \min \text{conf})$ — Pareto-ranked
- Distance to target: interval $[\text{LB}, \text{UB}]$ per §4.1 (LB = max /
  rule-independent-subset bound; **not** the sum over all violations)
- Blast radius: distribution $\{|impactRadius(G_i, x)|\}_{i=1..N}$ over
  Monte Carlo presence draws $G_i \sim \text{Bernoulli}(c(e))$, with decay
  $\alpha$ only inside a draw (§F15 — never also multiply by $c(e)$);
  report median / p90 / CVaR$_{0.9}$

### 6.7 Recommended visualizations

1. **Cluster-ordered DSM heatmap** — modules on both axes, cells =
   dependency weight; off-diagonal-block mass *is* the coupling story.
2. **Condensation DAG, declared-layer-ranked** — SCCs as super-nodes sized by mass;
   upward edges colored as violations.
3. **Martin $I \times A$ scatter** — modules vs. the main sequence; zones
   of pain/uselessness shaded.
4. **Code-city treemap** — containment as layout, height = complexity,
   color = churn (hotspots pop visually).
5. **Chord diagram** of package dependencies (small estates only; DSM
   scales better).
6. **Alluvial: declared vs. detected modules** — where the architecture
   story and the cluster structure disagree.
7. **Centrality × coverage quadrants** — the top-right (high centrality,
   low coverage) is the test-investment shortlist.
8. **Structural vs. co-change overlay diff** — edges in one but not the
   other; the asymmetry is the finding (hidden coupling / dead structure).
9. **Migration Sankey** — nodes flowing from current to target modules
   across plan phases.
10. **Risk burn-down with uncertainty band** — plan progress vs. residual
    risk, p10–p90 shaded; honest plans show the band, not the line.

### 6.8 Roadmap for building the analyzer

Phases gate on exit criteria, several of which are calibration checks —
the anti-hallucination discipline applied to the tool itself.

- **Phase 0 — Extraction & normalization.** Frontends via tree-sitter /
  SCIP / LSIF / CodeQL / Joern adapters into the §1 schema. *Exit:* ≥95%
  node coverage on pilot repos **and** a complete normalization-gap log
  (what was not extracted, why, at what assigned confidence).
- **Phase 1 — Store & query.** Property-graph + Datalog engine; snapshot
  diffing. *Exit:* §6.5 queries run; graph diffs reproducible.
- **Phase 2 — Deterministic analytics.** F1–F8 [D] metrics. *Exit:*
  bit-reproducible outputs on fixed snapshots; §1.1 invariants enforced in
  CI; functor-law checks (F13) green; every approximation in use (e.g.,
  sampled betweenness) recorded with its error bound.
- **Phase 3 — History & statistics.** L6 ingestion, F9b, F11. *Exit:*
  within-repo defect model backtested on held-out months with reported
  PPV/recall vs. base rate — publish the numbers, whatever they are.
- **Phase 4 — Embeddings & clones.** F12. *Exit:* precision on a locally
  labeled clone sample ≥ agreed floor; thresholds documented per model.
- **Phase 5 — Conformance.** Reference-model DSL, reflexion CI (ratchet
  mode). *Exit:* divergence count tracked over ≥1 month on a real repo.
- **Phase 6 — Scenario engine.** Rule library, planner, cost/risk
  estimators. *Exit:* on a pilot migration, predicted-vs-actual effort
  within an agreed band; sensitivity reports generated for every plan.
- **Phase 7 — Continuous calibration.** Treat every analyzer finding as a
  prediction; label outcomes (fixed? confirmed? dismissed?); track PPV per
  detector; retire or retrain detectors below floor. *Exit:* a live
  per-detector precision dashboard — the analyzer earns trust the same way
  this document asks its own claims to be treated: by verification.

### 6.9 Reference substrate mapping

An honest mapping for the common case where the L0/L1 graph *already exists* as
typed RDF — SHACL-validated, one snapshot per commit, with symbol-level
`CALLS`/`EXTENDS`/`OVERRIDES` edges and per-chunk embeddings — i.e. a pipeline
already at roadmap Phase 0–1. This is not a claim that such a pipeline
implements this catalog; it states what the substrate gives for free versus
what remains a build.

| Roadmap phase | Substrate status |
|---|---|
| 0 — extract/normalize | **Done at soundiness ≈S1** across many languages; the normalization-gap log is partial but real — extraction errors and unresolved-reference *reasons* are already captured as data (exactly §1.4's "ship the unsoundness as data") |
| 1 — store & query | **Store + query done.** RDF is the property graph; SPARQL is the Datalog-adjacent engine; **SHACL *is* the §1.1 invariant checker**. Snapshot **diffing not done** — a single snapshot gates every $\{G_t\}$ operation (F9b, F11, drift) |
| 2 — F1–F8 [D] | **F1–F7 are read-only passes** over the stored graph (a standard graph library suffices — no new heavy dependency). **F8 blocked** — no L2 behavior layer (CFG/DFG/PDG) |
| 3 — history/stats | Nearer than it looks for co-change, blocked for ownership — see precision point 2 |
| 4 — embeddings/clones | **Embeddings done**; the F12 clone/similarity layer is a near-term add (an ANN index) — the substrate is *ahead* of the roadmap here |
| 5 — conformance | Reference-model DSL + partial map $m$ absent (F13/F2) |
| 6–7 — scenario engine + calibration | Far; and the planner's real gate is the **calibration program** of §6.8 / F14 (predicted-vs-actual effort bands), not code — a queryable graph does not shorten it |

**The confidence layer (§1.4) is the highest-leverage schema addition.** It is
partially realized: a three-valued `exact / heuristic / ambiguous` label exists
on resolved call/type edges. Generalizing $c(e)\in(0,1]$ to *all* derived edges
and enforcing the invariant $c(e)\le\min(c(s(e)),c(t(e)))$ is what every [D]
operation and the entire scenario engine consume — without it, the dual-run
optimistic/pessimistic bands of §1.4 cannot be computed at all.

**Two precision points the family catalog abstracts away** (both surfaced by
grounding it against a real substrate):

1. **Abstractness has no input until the frontend distinguishes abstract
   types.** F2's $A=\#\text{abstract}/\#\text{types}$ and main-sequence distance
   $D=|A+I-1|$ (and the zone-of-uselessness reading) require a
   Class-vs-Interface/abstract distinction. A substrate that normalizes
   `interface → class` to keep node kinds few — a common and otherwise
   reasonable choice — *erases exactly that distinction*: $C_a, C_e, I$ remain
   computable, but $A$ and $D$ are **not applicable, not merely noisy**. Report
   them N/A per the §1.2 "honest gap, not a zero" rule rather than emitting a
   misleading $0$.
2. **Co-change is an extension of existing history plumbing, not a greenfield
   build.** F9b needs commit → file-set *transactions*; a substrate that already
   invokes `git log --name-only` to compute per-file last-touched times has the
   raw material one aggregation away. By contrast, ownership signals — `Author`
   nodes, `AUTHORED`/`OWNS`, truck factor (F4, F9b) — do require genuinely new
   socio-temporal nodes that such substrates typically lack.

**Keystone.** The one structural addition that turns a queryable extraction
graph into this catalog is an **analysis layer distinct from extraction**: a
read-only pass that consumes the assembled graph and emits confidence-labeled
`Finding` records (the [D]/[I] queues of §7.1). F1–F7 land there first, and it
is the shared prerequisite for Phases 3–6 and for the scenario engine alike —
the same keystone the diagnostic-only treatments of this problem also reduce to.

---

## 7. Epistemics, Limits, and Threats to Validity

### 7.1 Measured / derived / inferred — summary

| Class | Examples | Trust posture |
|---|---|---|
| **[M] Measured** | LOC, churn, coverage links, CVE counts, commit metadata | Trust the value; question the *collection* (rename tracking, bot commits, coverage instrumentation gaps) |
| **[D] Derived** | SCCs, closures, centralities, cuts, $Q$, LCOM4, reflexion divergences | Deterministic given the graph — inherit every input-edge uncertainty; recompute, don't cache across snapshots |
| **[I] Inferred** | defect risk, clone verdicts (Type-4), unstable-abstraction calls, cluster "should-be" boundaries | Hypotheses with error rates; valid only with calibration evidence attached; present as ranked queues, never verdicts |
| **[S] Speculative** | every composite weight, cost/risk coefficients, thresholds, decision-rule defaults | Engineering priors; must be sensitivity-tested and locally calibrated before any consequential use |

### 7.2 Global false-positive registry

Cross-cutting failure modes that recur across families — any consumer of
this framework's outputs should have this list at hand:

1. **Dynamic dispatch / reflection / metaprogramming** — call and type
   edges both missed and over-approximated; run optimistic and pessimistic
   graphs and treat disagreement as the uncertainty band (§1.4).
2. **Code generation** — generated artifacts dominate size, coupling, and
   outlier statistics; exclusion lists are mandatory, and their contents
   are themselves [M] inputs to audit.
3. **Dependency injection & frameworks** — DI-wired implementations look
   unreachable statically (false dead code) and framework callbacks look
   uncalled; root-set curation (§F1) is a human responsibility.
4. **Commit hygiene** — squash merges, bulk reformats, vendoring corrupt
   L6; filters reduce but do not eliminate the damage; co-change findings
   carry residual noise.
5. **Centrality–utility confound** — central ≠ important ≠ risky;
   stratify by stereotype and pair with churn/defect signals (§F4).
6. **Resolution limit & clustering instability** — community findings
   depend on the objective and resolution; consensus + stated parameters
   or the finding is not reportable (§F5).
7. **Simpson's paradox across strata** — pooling languages or artifact
   kinds reverses correlations; all statistics within-stratum (§F11).
8. **Coverage ≠ tested** — a `COVERS` edge without assertions on the
   relevant behavior is execution, not verification; assertion-density is
   a weak but honest co-signal (L4).
9. **The reference model may be wrong** — reflexion divergence means code
   and model disagree; *which* is right is a human call. Convergence-to-
   model is only progress if the model deserves it.
10. **Soundiness everywhere** — per Livshits et al. (CACM 2015)
    [unverified-memory, conf. H], every practical static analysis makes
    unsound choices; this framework's response is to *label* them
    (confidence schedules, dual-run bands) rather than pretend otherwise.

### 7.3 Threats to validity of this document itself

- **Completeness is unprovable.** "The full set of operations" (spec
  wording) cannot be certified; what is offered is a curated, extensible
  catalog organized so gaps are visible (families with explicit inputs
  make missing inputs conspicuous).
- **Reference status.** Five anchor citations were verified in-session
  (marked ✓). All others are from model memory, individually tagged
  [unverified-memory] with confidence H/M/L, and **must be checked before
  external citation** — per the frontmatter disclaimer, any of them may be
  wrong in venue, year, or attribution despite best effort.
- **Numeric defaults are priors.** Every threshold, weight, decay, and
  multiplier marked [S] is a starting point, not a finding. The framework
  is designed so that using it *generates* the calibration data (roadmap
  phases 3, 6, 7) that replaces these priors.
- **Causal humility.** Nothing here demonstrates that any refactoring
  *causes* improved outcomes; the engine estimates structural effects [D]
  and prices risk [I/S]; outcome claims require the quasi-experimental
  designs flagged in F15.
- **Category-theory yield.** Kept only where it changes a computation
  (functor-law checks, gluing condition, pushout merges); readers wanting
  deeper CT treatments should weigh modeling cost against the marginal
  checks gained — the honest assessment is that the marginal yield beyond
  what is included here is low for diagnostic purposes.

### 7.4 Open risks and unresolved assumptions (post-review, v0.2.0)

1. **References.** All [mem-*] entries remain unverified. Highest-priority
   checks before external use: the Bunch/MQ cohesion formula against the
   TSE text (§F5), Hitz–Montazeri LCOM4 details, Eades–Lin–Smyth 1993,
   Mende–Koschke effort-aware evaluation, Priebe et al. scan statistics.
2. **Calibration debt.** Every [S] coefficient ($\eta$, $\beta$,
   $\gamma_{\max}$, decay $\alpha$, thresholds, decision-rule triggers) is
   a prior until roadmap phases 3/6/7 replace it with fitted values. Plans
   produced before calibration are directionally useful, numerically soft.
3. **LB tightness.** The corrected admissible bound (max +
   rule-independent-subset sum) is weaker than the invalid full sum it
   replaced; search remains correct but may expand more states. Tightening
   (e.g., LP relaxations of a repair set-cover) is future work.
4. **Aggregation conservatism.** Plan confidence = min over steps, and
   phase-level risk over touched-node unions, are deliberately
   conservative; large plans will look somewhat worse than they are.
   Alternative aggregations are noted but unvalidated.
5. **Behavioral blindness.** The engine guarantees *structural* effects
   only; behavioral parity rests on the test / dual-run steps the plans
   schedule. Where those cannot be built, the framework's guarantees stop.
6. **Data blind spots.** Coupling mediated by databases, queues, or
   runtime configuration is invisible unless L5 is ingested; the framework
   can flag the absence of L5, not conjure its contents.
7. **Scale walls.** Exact betweenness, Johnson cycle enumeration, and MILP
   assignment become infeasible on very large graphs; the documented
   approximations change results and must be reported with error bounds.
8. **Multi-repository estates** are representable (multiple `Repository`
   roots), but cross-repo edge extraction (service calls, shared schemas)
   is exactly the low-confidence class — expect wider uncertainty bands.

---

## 8. References

Legend: **✓** = verified in-session (bibliographic details confirmed
against sources during preparation). **[mem-H/M/L]** = from model memory
with stated confidence — verify before external use.

**Graph model & program representation**

1. ✓ Yamaguchi, F., Golde, N., Arp, D., Rieck, K. "Modeling and
   Discovering Vulnerabilities with Code Property Graphs." *IEEE S&P*
   2014, pp. 590–604. DOI 10.1109/SP.2014.44.
2. [mem-H] Ferrante, J., Ottenstein, K., Warren, J. "The Program
   Dependence Graph and Its Use in Optimization." *TOPLAS* 9(3), 1987.
3. [mem-H] Horwitz, S., Reps, T., Binkley, D. "Interprocedural Slicing
   Using Dependence Graphs." *TOPLAS* 12(1), 1990.
4. [mem-H] Weiser, M. "Program Slicing." *TSE* SE-10(4), 1984.
5. [mem-H] Kildall, G. "A Unified Approach to Global Program
   Optimization." *POPL* 1973.
6. [mem-H] Lengauer, T., Tarjan, R. "A Fast Algorithm for Finding
   Dominators in a Flowgraph." *TOPLAS* 1(1), 1979.
7. [mem-H] McCabe, T. "A Complexity Measure." *TSE* SE-2(4), 1976.
8. [mem-M] Livshits, B., et al. "In Defense of Soundiness: A Manifesto."
   *CACM* 58(2), 2015.
9. [mem-M] Kivelä, M., et al. "Multilayer Networks." *J. Complex
   Networks* 2(3), 2014.

**Graph algorithms**

10. [mem-H] Tarjan, R. "Depth-First Search and Linear Graph Algorithms."
    *SIAM J. Comput.* 1(2), 1972.
11. [mem-H] Johnson, D. B. "Finding All the Elementary Circuits of a
    Directed Graph." *SIAM J. Comput.* 4(1), 1975.
12. ✓ Karp, R. "Reducibility Among Combinatorial Problems." 1972.
    (Feedback arc set NP-hardness; verified within F3 sourcing.)
13. [mem-M] Eades, P., Lin, X., Smyth, W. F. "A Fast and Effective
    Heuristic for the Feedback Arc Set Problem." *IPL* 47, 1993.
14. [mem-H] Brandes, U. "A Faster Algorithm for Betweenness Centrality."
    *J. Math. Sociol.* 25(2), 2001.
15. [mem-H] Yen, J. Y. "Finding the K Shortest Loopless Paths in a
    Network." *Management Science* 17(11), 1971.
16. [mem-M] Orlin, J. "Max Flows in O(nm) Time, or Better." *STOC* 2013.
17. [mem-H] Shi, J., Malik, J. "Normalized Cuts and Image Segmentation."
    *TPAMI* 22(8), 2000.
18. ✓ Traag, V., Waltman, L., van Eck, N. J. "From Louvain to Leiden:
    Guaranteeing Well-Connected Communities." *Scientific Reports* 9:5233,
    2019. DOI 10.1038/s41598-019-41695-z.
19. [mem-M] Fortunato, S., Barthélemy, M. "Resolution Limit in Community
    Detection." *PNAS* 104(1), 2007.
20. [mem-M] Lancichinetti, A., Fortunato, S. "Consensus Clustering in
    Complex Networks." *Scientific Reports* 2:336, 2012.

**Software architecture & dependency analysis**

21. ✓ Murphy, G., Notkin, D., Sullivan, K. "Software Reflexion Models:
    Bridging the Gap Between Source and High-Level Models." *FSE* 1995,
    pp. 18–28, DOI 10.1145/222124.222136; extended *TSE* 27(4):364–380,
    2001.
22. ✓ MacCormack, A., Rusnak, J., Baldwin, C. "Exploring the Structure of
    Complex Software Designs: An Empirical Study of Open Source and
    Proprietary Code." *Management Science* 52(7):1015–1030, 2006.
    DOI 10.1287/mnsc.1060.0552.
23. [mem-H] Martin, R. C. Stability/abstractness metrics ($C_a$, $C_e$,
    $I$, $A$, $D$); *Agile Software Development*, 2002 (metrics
    originally circulated 1994).
24. [mem-M] Lakos, J. *Large-Scale C++ Software Design.* Addison-Wesley,
    1996 (CCD/ACD/NCCD).
25. [mem-M] Melton, H., Tempero, E. "An Empirical Study of Cycles Among
    Classes in Java." *Empirical Software Engineering*, ~2007.
26. [mem-M] Garcia, J., Ivkovic, I., Medvidović, N. "A Comparative
    Analysis of Software Architecture Recovery Techniques." *ASE* 2013.
27. [mem-M] Bavota, G., et al. Software re-modularization via community
    detection line of work, ~2010–2014.
28. [mem-M] Mancoridis, S., Mitchell, B., et al. Bunch clustering tool /
    MQ objective; *TSE* ~2006 and earlier (formula per §F5 to be verified
    against the TSE text).
29. [mem-M] Tzerpos, V., Holt, R. "MoJo: A Distance Metric for Software
    Clusterings." ~1999; MoJoFM extension ~2004.

**Evolution, defects, ownership**

30. [mem-H] Zimmermann, T., Weißgerber, P., Diehl, S., Zeller, A.
    "Mining Version Histories to Guide Software Changes." *ICSE* 2004;
    *TSE* 31(6), 2005.
31. [mem-H] Hassan, A. E. "Predicting Faults Using the Complexity of Code
    Changes." *ICSE* 2009.
32. ✓ Rahman, F., Devanbu, P. "How, and Why, Process Metrics Are Better."
    *ICSE* 2013, pp. 432–441. DOI 10.1109/ICSE.2013.6606589.
33. ✓ Majumder, S., et al. "Revisiting Process versus Product Metrics: A
    Large-Scale Analysis." *EMSE* 27, 2022. DOI
    10.1007/s10664-021-10068-4.
34. [mem-H] Bird, C., Nagappan, N., Murphy, B., Gall, H., Devanbu, P.
    "Don't Touch My Code! Examining the Effects of Ownership on Software
    Quality." *FSE* 2011.
35. [mem-H] Nagappan, N., Ball, T. "Use of Relative Code Churn Measures
    to Predict System Defect Density." *ICSE* 2005.
36. [mem-H] Zimmermann, T., et al. "Cross-Project Defect Prediction."
    *ESEC/FSE* 2009.
37. [mem-M] Mende, T., Koschke, R. Effort-aware defect prediction
    evaluation, ~2010.
38. [mem-H] Tornhill, A. *Your Code as a Crime Scene.* Pragmatic
    Bookshelf, 2015.
39. [mem-M] Cataldo, M., Herbsleb, J., et al. Socio-technical congruence,
    ~2008; Conway, M. "How Do Committees Invent?" *Datamation*, 1968
    [mem-H].
40. [mem-M] Avelino, G., et al. Truck-factor estimation, ~2016.

**Metrics & type-system analysis**

41. [mem-H] Chidamber, S., Kemerer, C. "A Metrics Suite for Object
    Oriented Design." *TSE* 20(6), 1994.
42. [mem-M] Hitz, M., Montazeri, B. "Measuring Coupling and Cohesion in
    Object-Oriented Systems." 1995 (LCOM4).
43. [mem-M] Oman, P., Hagemeister, J. Maintainability index line of work,
    1992 — cited here *with* its criticisms.
44. [mem-M] Marcus, A., Poshyvanyk, D. Conceptual cohesion of classes.
    *TSE*, ~2008.

**Statistics, ML, embeddings, clones**

45. [mem-M] Louridas, P., Spinellis, D., Vlachos, V. "Power Laws in
    Software." *TOSEM* 18(1), 2008.
46. [mem-H] Clauset, A., Shalizi, C., Newman, M. "Power-Law Distributions
    in Empirical Data." *SIAM Review* 51(4), 2009.
47. [mem-M] Liu, F. T., Ting, K. M., Zhou, Z.-H. "Isolation Forest."
    *ICDM* 2008.
48. [mem-H] Alon, U., et al. "code2vec: Learning Distributed
    Representations of Code." *POPL* 2019.
49. [mem-H] Feng, Z., et al. "CodeBERT." *EMNLP Findings* 2020; Guo, D.,
    et al. "GraphCodeBERT." *ICLR* 2021.
50. [mem-H] Perozzi et al. "DeepWalk" *KDD* 2014; Grover & Leskovec
    "node2vec" *KDD* 2016; Hamilton et al. "GraphSAGE" *NeurIPS* 2017.
51. [mem-M] Schleimer, S., Wilkerson, D., Aiken, A. "Winnowing: Local
    Algorithms for Document Fingerprinting." *SIGMOD* 2003.
52. [mem-M] Jiang, L., et al. "DECKARD." *ICSE* 2007; Sajnani, H., et al.
    "SourcererCC." *ICSE* 2016.
53. [mem-M] Svajlenko, J., et al. BigCloneBench, 2014; criticism: Krinke,
    J., Ragkhitwetsagul, C., ~2022.
54. [mem-M] Malkov, Y., Yashunin, D. "Efficient and Robust Approximate
    Nearest Neighbor Search Using HNSW." *TPAMI* ~2018.

**Category theory & rewriting**

55. [mem-H] Ehrig, H., Ehrig, K., Prange, U., Taentzer, G. *Fundamentals
    of Algebraic Graph Transformation.* Springer, 2006.
56. [mem-M] Fiadeiro, J. *Categories for Software Engineering.* Springer,
    2005.
57. [mem-M] Spivak, D. Ologs / functorial data migration, ~2012;
    *Category Theory for the Sciences*, 2014.

**Optimization, planning, risk**

58. [mem-H] Deb, K., et al. "A Fast and Elitist Multiobjective Genetic
    Algorithm: NSGA-II." *IEEE Trans. Evol. Comput.* 6(2), 2002.
59. [mem-M] Harman, M., Jones, B. "Search-Based Software Engineering."
    *IST* 43(14), 2001; O'Keeffe, M., Ó Cinnéide, M. "Search-Based
    Refactoring." *JSS* 81(4), 2008.
60. [mem-H] Pearl, J. *Causality*, 2nd ed., 2009.
61. [mem-M] Priebe, C., et al. Scan statistics on graphs, ~2005.
62. [mem-H] Clarke, E., Emerson, E. A. Temporal-logic model checking
    origins, 1981; NuSMV/SPIN tool literature [mem-M].
63. [mem-M] Rothermel, G., Harrold, M. J. "A Safe, Efficient Regression
    Test Selection Technique." *TOSEM* 6(2), 1997.

*End of document. Per the frontmatter disclaimer: treat every
[unverified-memory] entry and every [S]/[I] figure as requiring
verification or calibration before consequential use.*
---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-08"
title: "prompt-gen-abox — Reusable ABox generation for Scope-A dimension analysis"
version: "1.0.0"
---

# prompt-gen-abox

Reusable command for classifying **any** codebase-mapper instance against the
twenty core dimensions and one assessment overlay defined by the Scope-A
framework. The framework's authoritative forms are:

- **TBox + SHACL** — [static/schemas/software_architecture_dimensions.ttl](../static/schemas/software_architecture_dimensions.ttl) (`owl:versionInfo 2.0.5-scope-a`)
- **Prose** — [static/refs/orthogonal-dimensions-framework.md](../static/refs/orthogonal-dimensions-framework.md) (v2.0.2)

This document produces the **ABox** (instance data): a set of
`arch:DimensionApplication` assertions that classify one analysed system, each
anchored to evidence and tagged with confidence, validated against the TBox's
SHACL shapes.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## ARCHITECTURAL REQUIREMENT (PALS's LAW)

> LLMs will always produce some form of error. Absence of output verification is
> a design defect, not a runtime bug. All LLM output must be treated as untrusted
> and validated explicitly.

The generation step below is LLM output. It is **unverified by default**. The
`Step 2 — Validate` command is **not optional post-processing**; it is the
verification layer that makes the ABox trustworthy. An ABox that has not passed
`Step 2` must be treated as a draft, never as a result.

---

## The reusable command

### Step 0 — Produce or connect the input (deterministic)

`codebase-mapper` maps a repository into an RDF/JSON bundle of mechanically
derived facts. First produce the bundle:

```bash
make analyze REPO=<url|path> OUT=_tmp/<system>-map
# → _tmp/<system>-map/ contains the bundle (graph.ttl + JSON layers + run_manifest.json)
```

Then consume it **through the read-only `cbm` MCP server** — the intended agent
interface — rather than eyeballing files. Register it once (see
[docs/mcp-install.md](./mcp-install.md)); the minimal Claude Code entry:

```json
{ "mcpServers": { "cbm": {
  "command": "/abs/path/.venv/bin/python", "args": ["-m", "frontend.mcp_server"],
  "cwd": "/abs/path/to/code-base-mapper",
  "env": { "CBM_BUNDLES_ROOT": "/abs/path/to/_tmp" } } } }
```

Workflow: call **`orient_bundle`** first (metadata + layer/namespace cheat sheet
+ suggested probes), then `list_bundles` → `select_bundle` if more than one is
mounted, then the per-dimension tools below.

The bundle — surfaced through these tools — **is the primary evidence source,
not your impression of the repo.** Cite tool calls or artifact paths in every
`evidenceSummary` (e.g. `per imports_of(path="src/api/http.py")`).

#### cbm MCP tools → dimensions they primarily feed

| Tool | Feeds (primary) |
|---|---|
| `orient_bundle` · `repository_summary` · `bundle_summary` | orientation + provenance; D01, D02, D08, D14; SHACL-conformance/backend for the provenance note |
| `list_files` (language/type/prefix, import-degree) | D08 physical org · D14 paradigm & language mix · D01 |
| `file_detail` (imports both ways, tests, concepts) | D02 · D04 contracts · D13 tests · D01 |
| `imports_of` · `imported_by` · `file_impact` | D02 dependency topology · change blast radius |
| `concept_detail` · `concept_neighborhood` (SKOS) | D01 domain partitioning / bounded contexts |
| `list_chunks` · `chunk_detail` · `chunk_blob` | source-level evidence for D03, D05, D06, D09, D10, D16, D17, D19 |
| `semantic_neighbors` (NL query) | targeted hunts: "auth middleware"→D19, "retry/circuit breaker"→D16, "queue consumer"→D03/D06, "feature flag"→D12, "logging/tracing"→D18, "CI/build"→D20 |
| `items_by_attribute` (Rust) | D14/D15 idiom · D17 type discipline |
| `sparql` (**gated by `CBM_ENABLE_SPARQL=1`**, read-only, 10 s/1000 rows) | precise graph evidence for any dimension — last resort; prefer the specialized tools |

Resources also help: `cbm://bundle/{b}/shapes.shacl.ttl` and
`cbm://bundle/{b}/ontology-mapping.ttl` show how the graph is structured;
`cbm://bundle/{b}/manifest` and `/summary` carry provenance.

### Step 1 — Generate the ABox (LLM; unverified)

Copy the prompt below verbatim, replace the `{{PLACEHOLDERS}}`, and run it in a
context that can read the bundle and the source tree (e.g. `claude -p`, an agent
session, or the API). The model's only output is a single Turtle file.

````text
You are an architecture analyst applying the Scope-A Orthogonal Dimensions
framework (static/schemas/software_architecture_dimensions.ttl, owl:versionInfo
2.0.5-scope-a; prose: static/refs/orthogonal-dimensions-framework.md v2.0.2).
Read both before you begin.

INPUTS
- cbm MCP server:          connected (bundle mounted); call orient_bundle first,
                           then select_bundle "{{BUNDLE_NAME}}" if more than one.
                           Gather evidence via the cbm tools (imports_of,
                           file_detail, concept_neighborhood, semantic_neighbors,
                           list_files, file_impact, chunk_detail, sparql if enabled).
- fallback (no MCP):       read the bundle dir {{BUNDLE_DIR}} + source tree {{REPO_DIR}} directly.
- system label:            "{{SYSTEM_LABEL}}"
- instance namespace:      {{INSTANCE_IRI_BASE}}   e.g. https://example.org/abox/<system>#

TASK
Emit ONE Turtle document — a valid ABox — that classifies the system along all
twenty core dimensions (D01–D20) and states the O01 overlay reading. Emit nothing
but the Turtle. Do not restate the TBox; reference its IRIs.

FOR EACH of D01..D20, emit at least one arch:DimensionApplication with:
  - arch:classifiesSystem   → the single system node (typed arch:ImplementedSoftwareSystem)  [exactly 1]
  - arch:appliesDimension   → the arch:Dxx_... IRI of that dimension                          [exactly 1]
  - arch:atScope            → exactly one of "system" "subsystem" "module"
                              "artifact" "language-region" (closed set,
                              SHACL-enforced; new altitudes need a TBox change)  [exactly 1]
  - arch:usesClassificationValue → one or more values (see VALUES)  [required UNLESS confidence is "Unknown"]
  - arch:supportedByEvidence → one or more arch:EvidenceRecord nodes                          [>=1]
  - arch:confidenceLevel     → exactly one of "High" "Medium" "Low" "Unknown"                 [exactly 1]

HETEROGENEOUS / LAYERED SYSTEMS (the intended representation)
Emit multiple applications of the SAME dimension to the SAME system, each with a
different arch:inRegion "<module path | subsystem | language region>", PLUS one
whole-scope application that names arch:dominantValue → the prevailing value. The
dominant MUST be one of that application's own arch:usesClassificationValue
values (SHACL-enforced by DominantValueCoherenceShape). An application with no
arch:inRegion covers the whole stated scope. Never force a single value onto a
genuinely heterogeneous system.

EVIDENCE
Every arch:EvidenceRecord carries arch:evidenceSummary (≥10 chars) that CITES a
concrete cbm tool call or source path — e.g. "package-per-feature: list_files
prefix=src/features shows 1 dir per feature" or "cyclic: imports_of + imported_by
on src/util.py form a cycle". Documentation and diagrams are CLAIMS to verify
against code, never High-confidence evidence on their own.

CONFIDENCE (apply the rubric, do not inflate)
  High    = directly observed in enforced or executable artifacts you examined
            (compiler visibility, CI gates, arch tests, IAM, DB constraints, code,
            tests, manifests). Documents alone can never yield High.
  Medium  = strongly inferred from ≥2 independent executable/structural sources.
  Low     = single source, docs/testimony only, or unresolved counter-evidence.
  Unknown = insufficient evidence. Unknown is a first-class value. PREFER Unknown
            over a guess. When confidence is "Unknown" you MAY omit
            arch:usesClassificationValue entirely; the arch:evidenceSummary must
            then state what evidence would settle the classification.

VALUES
Prefer the canonical arch:ClassificationValue individuals already in the TBox
(reference table below). Every used value MUST belong to the applied dimension's
declared value set or validation fails (ValueBelongsToDimensionShape). Canonical
values are already registered; if none fits, MINT one AND register it on the
dimension in the same document:

    ex:MyValue a arch:ClassificationValue ; skos:prefLabel "..."@en .
    arch:D0X_... arch:hasClassificationValue ex:MyValue .   # REQUIRED registration

Never use an untyped node, never register a value on a dimension it does not
describe, and never let a value be a judgement word ("clean", "modern", "good") —
values are neutral descriptors.

RISK (optional)
Where a classification reveals a hazard, attach arch:revealsRisk → an
ex:Risk_... node typed arch:RiskFinding with an rdfs:comment. Cross-dimensional
hazards (e.g. shared-store multi-writer ownership under independently deployed
units) are the highest-value findings.

O01 OVERLAY
The overlay is NOT a DimensionApplication (its target is a core dimension only).
Emit the revealed priority ordering as an ex:OverlayReading_<system> node with an
rdfs:comment giving the ranked profile and its explicitness (ADR-stated /
SLO-implied / emergent-only), plus a arch:RiskFinding if stated priorities and
structural reality diverge. (The TBox has no overlay-application class as of
2.0.5; represent it descriptively and flag it as a candidate TBox extension.)

ACTUAL OVER INTENDED
Classify the system as implemented. Where documentation and code diverge,
classify the code and record the delta as an arch:RiskFinding.

SELF-CHECK BEFORE YOU EMIT (PALS's Law — you will still be validated downstream)
  □ the document declares an owl:Ontology header with dcterms:creator naming
    the authoring model + tool (SHACL-enforced by AnalysisProvenanceShape)
  □ every dimension D01..D20 has >=1 application (more when layered by region)
  □ each application has classifiesSystem, appliesDimension, exactly one
    atScope from the closed set, supportedByEvidence (>=1), and exactly one
    confidenceLevel
  □ every arch:dominantValue is one of its application's own used values
  □ usesClassificationValue present on every application whose confidence is not
    "Unknown"; each value is a typed arch:ClassificationValue
  □ every used value is registered on its dimension via arch:hasClassificationValue
    (canonical values already are; minted values you must register)
  □ at most one arch:dominantValue per application; layered variants use arch:inRegion
  □ every confidenceLevel is one of "High" "Medium" "Low" "Unknown"
  □ every evidenceSummary (>=10 chars) cites a bundle fact or a real path
  □ the system node is typed arch:ImplementedSoftwareSystem
Output the Turtle only.
````

### Step 2 — Validate (deterministic; MANDATORY)

```bash
uv run scripts/setup_and_validate_ontology.py \
  --tbox static/schemas/software_architecture_dimensions.ttl \
  {{OUT}}-abox.ttl
```

`--tbox` loads the ABox against the TBox's SHACL shapes (`DimensionApplicationShape`,
`EvidenceRecordShape`, the confidence enum, the required-dimension shapes). Exit
`0` = conformant; any violation prints the offending focus node and exits `1`.
**Do not accept an ABox that has not exited `0`.** Feed the violation report back
to Step 1 and regenerate.

---

## ABox schema (what a conforming instance looks like)

Minimal skeleton (prefixes required):

```turtle
@prefix arch: <https://w3id.org/arc4d3/software-architecture-dimensions#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix ex:   <https://example.org/abox/acme#> .

# Ontology header — dcterms:creator is SHACL-required (AnalysisProvenanceShape):
# the authoring model identity must be a queryable triple, not a comment.
ex:Ontology a owl:Ontology ;
    dcterms:creator "<model/tool identifier> (extraction: codebase-mapper vX.Y.Z)" .

ex:System_acme a arch:ImplementedSoftwareSystem ;
    skos:prefLabel "ACME billing platform"@en .

# whole-system classification with a canonical value
ex:App_D01 a arch:DimensionApplication ;
    arch:classifiesSystem        ex:System_acme ;
    arch:appliesDimension        arch:D01_DecompositionModel ;
    arch:atScope                 "system" ;
    arch:usesClassificationValue arch:ByFeature ;
    arch:supportedByEvidence     ex:Ev_D01 ;
    arch:confidenceLevel         "High" .
ex:Ev_D01 a arch:EvidenceRecord ;
    arch:evidenceSummary "Top-level packages map 1:1 to product features under src/features/*; per bundle L1 package nodes." .

# minted value — MUST be registered on the dimension it describes
ex:CapabilityPartitioned a arch:ClassificationValue ; skos:prefLabel "capability-partitioned"@en .
arch:D01_DecompositionModel arch:hasClassificationValue ex:CapabilityPartitioned .

# layered dimension: per-region variants + a whole-scope dominant
ex:App_D14_core a arch:DimensionApplication ;
    arch:classifiesSystem ex:System_acme ; arch:appliesDimension arch:D14_ProgrammingParadigmModel ;
    arch:atScope "module" ; arch:inRegion "billing/core" ;
    arch:usesClassificationValue arch:FunctionalParadigm ;
    arch:supportedByEvidence ex:Ev_D14a ; arch:confidenceLevel "Medium" .
ex:App_D14 a arch:DimensionApplication ;
    arch:classifiesSystem ex:System_acme ; arch:appliesDimension arch:D14_ProgrammingParadigmModel ;
    arch:atScope "system" ; arch:dominantValue arch:ObjectOrientedParadigm ;
    arch:usesClassificationValue arch:ObjectOrientedParadigm ;
    arch:supportedByEvidence ex:Ev_D14b ; arch:confidenceLevel "Medium" .
ex:Ev_D14a a arch:EvidenceRecord ; arch:evidenceSummary "billing/core is pure functions over immutable records; per bundle L2 chunk facts." .
ex:Ev_D14b a arch:EvidenceRecord ; arch:evidenceSummary "class-based services dominate outside billing/core; per bundle L1 class counts." .

# Unknown: value omitted, evidence states what would settle it
ex:App_D19 a arch:DimensionApplication ;
    arch:classifiesSystem ex:System_acme ; arch:appliesDimension arch:D19_SecurityAndTrustBoundaryModel ;
    arch:atScope "system" ; arch:confidenceLevel "Unknown" ;
    arch:supportedByEvidence ex:Ev_D19 .
ex:Ev_D19 a arch:EvidenceRecord ; arch:evidenceSummary "No authz middleware or IAM config in the bundle; would be settled by security-policy artifacts or gateway config." .

# O01 overlay reading (descriptive: the TBox has no overlay-application class as of 2.0.5)
ex:OverlayReading_acme rdfs:comment
    "O01 overlay: delivery-cadence > modifiability > latency; SLO-implied only." .
```

### Shapes the ABox must satisfy (from the TBox)

| Node kind | Property | Cardinality / constraint |
|---|---|---|
| `arch:DimensionApplication` | `arch:classifiesSystem` | exactly 1, class `arch:SoftwareSystem` |
| | `arch:appliesDimension` | exactly 1, class `arch:CoreSystemDimension` |
| | `arch:atScope` | exactly 1 of `"system"`/`"subsystem"`/`"module"`/`"artifact"`/`"language-region"` (closed set, SHACL-enforced) |
| | `arch:inRegion` | optional; names the region for layered per-region variants |
| | `arch:usesClassificationValue` | class `arch:ClassificationValue`; ≥ 1 **unless** confidence is `Unknown` |
| | `arch:dominantValue` | ≤ 1, class `arch:ClassificationValue` (whole-scope dominant) |
| | `arch:supportedByEvidence` | ≥ 1, class `arch:EvidenceRecord` |
| | `arch:confidenceLevel` | exactly 1 ∈ {`High`,`Medium`,`Low`,`Unknown`} |
| *(cross-cutting SPARQL)* | `ValueBelongsToDimensionShape` | every used value must be registered on its dimension via `arch:hasClassificationValue` |
| *(cross-cutting SPARQL)* | `DominantValueCoherenceShape` | every `arch:dominantValue` must be one of the application's own used values |
| *(cross-cutting SPARQL)* | `AnalysisProvenanceShape` | the ABox `owl:Ontology` header must carry `dcterms:creator` naming the authoring model/tool |
| `arch:EvidenceRecord` | `arch:evidenceSummary` | ≥ 1, string, min length 10 |
| `arch:RiskFinding` | — | unconstrained (free-form findings) |

`arch:measuredBy` is the declared inverse of `arch:appliesDimension`; you may
assert either direction. Multiple applications of one dimension to one system,
differing by `arch:atScope`/`arch:inRegion`, are the intended representation of
layered systems.

---

## Canonical classification values (prefer these; mint typed values only if none fit)

| Dim | `arch:ClassificationValue` individuals |
|---|---|
| D01 Decomposition | `ByLayer` · `ByFeature` · `ByBoundedContext` |
| D02 Dependency topology | `StrictLayeredDependencies` · `DAGDependencies` · `CyclicDependencies` |
| D03 Connector & interaction | `DirectCallConnector` · `MessageQueueConnector` · `PubSubConnector` · `StreamConnector` · `ActorMailboxConnector` |
| D04 Interface & contract | `SchemaFirstContract` · `CodeFirstContract` · `ImplicitContract` |
| D05 State & data ownership | `SharedDatabaseOwnership` · `SingleWriterOwnership` · `EventSourcedOwnership` |
| D06 Runtime execution | `SingleThreadedRuntime` · `ThreadPerRequestRuntime` · `EventLoopRuntime` · `ActorRuntime` · `BatchRuntime` · `AgenticLoopRuntime` |
| D07 Deployment & distribution | `SingleNodeDeployment` · `ContainerOrchestratedDeployment` · `ServerlessDeployment` · `PackageRegistryDistribution` |
| D08 Codebase physical org | `MonorepoOrganization` · `PolyrepoOrganization` · `PackageByFeatureLayout` |
| D09 Abstraction & encapsulation | `OpaqueModuleEncapsulation` · `ConcretionExposedEncapsulation` |
| D10 Composition & reuse | `PluginComposition` · `LibraryComposition` · `GeneratedCodeComposition` |
| D11 Change & evolution | `SemanticVersionEvolution` · `ExpandContractMigration` |
| D12 Variability & config-space | `FeatureFlagVariability` · `ProductLineVariability` · `TenantSpecificVariability` |
| D13 Verification & assurance | `UnitIntegrationE2EPortfolio` · `FormalVerificationAssurance` · `RuntimeInvariantAssurance` |
| D14 Programming paradigm | `ObjectOrientedParadigm` · `FunctionalParadigm` · `ProceduralParadigm` · `DeclarativeParadigm` · `AgenticLoopParadigm` |
| D15 Implementation idiom & style | `IdiomaticImplementation` · `UnidiomaticImplementation` |
| D16 Error-handling & recovery | `ExceptionErrorModel` · `ResultErrorModel` · `SupervisorRecoveryModel` |
| D17 Type & data discipline | `StaticTypeDiscipline` · `RuntimeSchemaDiscipline` · `StringlyTypedDiscipline` |
| D18 Operational observability | `LogsMetricsTracesObservability` · `FeatureFlagControl` |
| D19 Security & trust boundary | `PerimeterSecurityModel` · `ZeroTrustModel` · `TenantIsolationModel` |
| D20 Build & delivery | `HermeticBuildModel` · `ManualReleaseModel` · `TrunkBasedDeliveryModel` |

The enumerations are **exemplary, not closed** (they satisfy the "≥ 2 values"
shape). Mint additional typed `arch:ClassificationValue` individuals in the
instance namespace whenever the system's actual value is not listed.

---

## Provenance separation

Keep the three provenance tiers distinct, per the project's epistemic commitments:

- **Mechanically derived facts** — the codebase-mapper bundle (Step 0). Deterministic.
- **Generated inferences** — the classifications, confidences, and risk findings
  in the ABox (Step 1). LLM-authored, **unverified until Step 2 passes**.
- **Human judgement** — recommendations and priority calls belong in the scoring
  layers (framework §8), never as raw `DimensionApplication` values.

---

## Related

- Framework prose and rubric: [static/refs/orthogonal-dimensions-framework.md](../static/refs/orthogonal-dimensions-framework.md)
- Machine-readable schema: [static/schemas/software_architecture_dimensions.ttl](../static/schemas/software_architecture_dimensions.ttl)
- Validator: [scripts/setup_and_validate_ontology.py](../scripts/setup_and_validate_ontology.py) (`--tbox` mode)
- cbm MCP install + registration: [docs/mcp-install.md](./mcp-install.md)
- cbm MCP tool/resource/prompt surface: [frontend/mcp_server/README.md](../frontend/mcp_server/README.md)
- Project root: [README.md](../README.md)

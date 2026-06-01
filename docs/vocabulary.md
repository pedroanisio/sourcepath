---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "GPT-5 Codex"
  date: "2026-05-22"
---

# L3 Controlled Vocabulary

> User-facing overview (run the pipeline, opt out, override): [README.md
> § Controlled vocabulary](../README.md#controlled-vocabulary). This
> document is the maintainer's reference — read it when extending the
> bundled vocabulary or writing typed SPARQL against the L3 graph.

The L3 concept graph blends two sources:

1. **Unsupervised concepts** derived from identifier splitting +
   cooccurrence (the long-standing behavior).
2. **Curated concepts** — a small, named set of domain primitives that
   the host *knows about* and tags with a semantic `kind`. This layer
   is the controlled vocabulary.

Every bundle built since vocabulary v1 carries both. The unsupervised
half lights up arbitrary identifiers; the curated half lights up
intent-first domain terms (intent, behavior, contract, effect,
expression, …), universal code-structure terms (module, class, method,
function, …), and edge/relation terms (block_edge, data_flow_edge, …).

## Layers and predicates

The vocabulary slots into the existing L3 SKOS layout. Two new
predicates and one new node type appear when at least one curated
concept lights up:

```turtle
cbmi:concept/behavior a skos:Concept ;
    skos:prefLabel "behavior"@en ;
    skos:altLabel "Behavior"@en, "behaviors"@en, …, "behaviour"@en ;
    cbml3:occurrenceCount 129 ;
    cbml3:fileCount 10 ;
    cbml3:embeddingRow 57 ;
    # ---- new in vocabulary v1 ----
    cbml3:conceptKind "domain-primitive" ;
    cbml3:broaderCollection cbmi:collection/intent_first_ontology .

cbmi:collection/intent_first_ontology a skos:Collection ;
    skos:prefLabel "intent-first ontology"@en ;
    cbml3:conceptKindBacking "domain-primitive" ;
    skos:member cbmi:concept/behavior, cbmi:concept/intent, … .
```

| Predicate | Domain | Range / values | Cardinality |
|---|---|---|---|
| `cbml3:conceptKind` | `skos:Concept` | `"domain-primitive"` \| `"structural-primitive"` \| `"relational-primitive"` | 0..1 |
| `cbml3:broaderCollection` | `skos:Concept` | `skos:Collection` IRI | 0..1 |
| `cbml3:conceptKindBacking` | `skos:Collection` | one of the three literals above | 1..1 |

All three are SHACL-validated. The two predicates on `skos:Concept` are
optional (a concept that didn't match the curated set has neither);
`cbml3:conceptKindBacking` is required on every emitted collection. The
shape definitions live in
[`plugins/concept_graph/graph_writer.py`](../plugins/concept_graph/graph_writer.py)
(`ConceptShapes.contribute`).

## Source of truth

The bundled vocabulary is a single YAML file:
[`codebase_mapper/emission/infrastructure/vocab/software_primitives.yaml`](../codebase_mapper/emission/infrastructure/vocab/software_primitives.yaml).
Shape:

```yaml
version: 1

kinds:
  domain-primitive:     [intent, behavior, contract, effect, …]
  structural-primitive: [application, module, class, method, function, …]
  relational-primitive: [edge, block_edge, data_flow_edge, …]

aliases:
  behavior: [behaviour, behaviors, behaviours]
  function: [func, funcs, functions]
  parameter: [params, param, parameters, method_parameter]
  # …

broader:
  domain-primitive:     intent_first_ontology
  structural-primitive: code_structure
  relational-primitive: code_relations
```

The loader is in
[`codebase_mapper/emission/infrastructure/vocab/loader.py`](../codebase_mapper/emission/infrastructure/vocab/loader.py).
`load_vocabulary(path)` validates the doc and returns a `Vocabulary`
with two indices: `terms: dict[str, VocabTerm]` keyed by canonical
name, and `by_alias: dict[str, str]` mapping every alias and canonical
form to the canonical name. Validation rejects:

- unknown concept kinds (anything outside the three literals)
- aliases pointing at canonical names that aren't in `kinds`
- the same alias mapping to two different canonicals
- a `version` other than the schema version

## How curated terms join the L3 graph

`ConceptAggregator` resolves a vocabulary at run time
([`plugins/concept_graph/concepts.py`](../plugins/concept_graph/concepts.py)).
Resolution order:

1. `ctx.scratch["host:concept_vocab_disabled"] == True` → `None` (no
   curated tagging).
2. `ctx.indices["host:concept_vocab"]` (an explicit `Vocabulary`
   instance) → use it.
3. The constructor-supplied vocab, or the `USE_BUILTIN` sentinel
   (default for `register_all`) → load the bundled YAML.
4. None → pre-vocab behavior (bundles look identical to pre-v1).

Two integration points:

- **Canonicalization** (`canonicalize(token, vocab)`): the vocab takes
  precedence over both stopwords and plural-stripping. `func`
  (normally a stopword) becomes `function`; `behaviour` becomes
  `behavior`; `params` becomes `parameter`. Alias collapse runs twice
  — on the lowercased raw token, and again on the plural-stripped
  stem — so curated terms are caught from either entry path.
- **Tagging**: after the per-concept record is built, the aggregator
  looks up the canonical name in `vocab.terms` and attaches `kind` /
  `broader` to the record. Compound concepts (`user_service`) are
  never tagged — the bundled vocabulary only declares atomic
  primitives.

The resolved vocabulary is stashed on
`ctx.scratch["l3:resolved_vocab"]` for downstream contributors. The
graph writer's L2-chunk anchoring reads the same vocab so file-anchored
and chunk-anchored concepts agree on alias collapses.

## Determinism

The bundled YAML is part of the cbm distribution; loading it has no
side effects. Two consecutive runs over the same commit produce
byte-identical `inventory.ttl` (the existing determinism guarantee
extends to the new predicates). `Vocabulary` is itself a frozen
dataclass; `ConceptAggregator` caches the parsed default at class
level so repeated runs don't re-parse the YAML.

## CLI flags

`scripts/run_l3.py` and `scripts/run_xrefs.py` expose two
mutually-exclusive flags:

| Flag | Effect |
|---|---|
| (default) | Use the bundled `software_primitives.yaml`. |
| `--concept-vocab PATH` | Load and apply a custom YAML at `PATH`. |
| `--no-builtin-vocab` | Disable curated tagging entirely; bundles look like pre-v1. |

`--concept-vocab` and `--no-builtin-vocab` are mutually exclusive; the
script rejects both at the same time.

## MCP / API surface

The MCP server surfaces the curated typing through two tools:

- **`concept_detail`** — the response's `concept` object includes
  `kind` and `broader` when the concept is curated; both are absent
  otherwise.
- **`concept_neighborhood`** — accepts an optional `kind` argument
  (one of the three literals). When supplied, only neighbors whose
  underlying concept carries that `kind` are returned, and the
  response includes a `kind_filter` echo. Each emitted neighbor row
  carries its own `kind` / `broader` when known, regardless of the
  filter — so clients can do further filtering without re-fetching.

The frontend backend (`frontend/backend/app.py`) returns the concept
record as-is and so passes the fields through transparently; the React
UI ([`frontend/ui/src/views/ConceptDetail.tsx`](../frontend/ui/src/views/ConceptDetail.tsx))
renders a `KindBadge` next to the concept label and surfaces `kind` /
`broader` rows in the metadata block when present.

## Extending the vocabulary

Adding a term is a one-line YAML edit:

```yaml
kinds:
  structural-primitive:
    - trait        # new
```

Aliases are similarly additive. After editing, re-run `verify_vocab.py`
+ `verify_vocab_emission.py` + `verify_vocab_wiring.py` + `verify_l3.py`
to confirm the YAML still loads, SHACL still conforms, and the new term
flows through the pipeline.

### Stability rules

- **Removing a term** is a breaking change for any consumer SPARQL —
  avoid. If a term has truly become wrong, deprecate by removing the
  alias entries while keeping the canonical (the canonical will still
  emit if seen).
- **Renaming a term** requires adding the old name as an alias of the
  new canonical, so existing chunks/files still resolve to the term.
- **Adding a new `kind` literal** is a coordinated change: extend the
  closed set in `codebase_mapper/emission/infrastructure/vocab/loader.py:_CONCEPT_KINDS`,
  `plugins/concept_graph/graph_writer.py:CONCEPT_KIND_LITERALS`, and
  the SHACL `sh:in` constraint. A verifier
  (`tests/verify_vocab_emission.py::test_kind_literal_set_matches_loader`)
  enforces the lockstep.

## Verifiers

| Script | Scope |
|---|---|
| [`tests/verify_vocab.py`](../tests/verify_vocab.py) | YAML loader: schema, alias resolution, validation errors |
| [`tests/verify_vocab_emission.py`](../tests/verify_vocab_emission.py) | RDF emission + SHACL shape (synthetic concepts) |
| [`tests/verify_vocab_wiring.py`](../tests/verify_vocab_wiring.py) | aggregator integration: canonicalize, ctx resolution, sidecar |
| [`tests/verify_vocab_pipeline.py`](../tests/verify_vocab_pipeline.py) | end-to-end: real `scripts/run_l3.py` × {default, `--no-builtin-vocab`, `--concept-vocab`}, alias-collapse equivalence, RDF↔JSON parity |
| [`tests/verify_l3.py`](../tests/verify_l3.py) | full pipeline + SHACL + mutation tests (vocab live by default) |

Add a new verifier when a behavior crosses a layer boundary; extend an
existing one when the behavior stays within a layer.

## File map

| Path | Role |
|---|---|
| `codebase_mapper/emission/infrastructure/vocab/software_primitives.yaml` | bundled vocabulary |
| `codebase_mapper/emission/infrastructure/vocab/loader.py` | `Vocabulary`, `VocabTerm`, `load_vocabulary`, `builtin_vocabulary` |
| `plugins/concept_graph/concepts.py` | `ConceptAggregator`, vocab resolution, `canonicalize(token, vocab)` |
| `plugins/concept_graph/graph_writer.py` | new triples + SHACL shapes |
| `plugins/concept_graph/artifact.py` | `concepts.json` sidecar carries `kind`/`broader` |
| `frontend/mcp_server/handlers.py` | `concept_detail`/`concept_neighborhood` typed surface |
| `frontend/mcp_server/schemas.py` | input + output JSON Schema for the new fields |
| `frontend/ui/src/views/ConceptDetail.tsx` | `KindBadge` + metadata rows |

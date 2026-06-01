---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "GPT-5 Codex"
  date: "2026-05-22"
---

# L4 LLM enrichment

> User-facing overview (install Ollama, run the pipeline, opt out):
> [README.md § LLM enrichment](../README.md#llm-enrichment). This
> document is the maintainer's reference — read it when extending the
> enrichment kinds, writing typed SPARQL against the L4 graph, or
> debugging a SHACL failure.
>
> See also: [docs/archive/llm-enrich-plan.md](archive/llm-enrich-plan.md) — the
> 10-step implementation plan (now shipped, kept as the
> architectural-commitment record), and
> [docs/llm-baseline-results.md](llm-baseline-results.md) — the
> 5-model benchmark that selected the default.

The L4 enrichment layer calls a local Ollama server to attach
LLM-authored annotations to existing L1/L2/L3 entities. Every triple
carries provenance (model name, prompt SHA, generation timestamp), and
every output is content-addressed in a disk cache so re-emits over an
unchanged repo are byte-identical (the warm-cache determinism
guarantee). The layer is opt-in via CLI flag — bundles built without
``--llm-enrich`` look exactly like pre-L4 bundles.

## Layer position and design commitments

| Layer | Source of truth | Determinism |
|---|---|---|
| L1 host | git tree + AST | mechanical |
| L2 chunks_embeddings | L1 + embedding model | mechanical (deterministic backends) |
| L3 concept_graph | L1 identifier splitting + cooccurrence | mechanical |
| **L4 llm_enrich** | **L1/L3 + Ollama chat completions** | **mechanical *given a populated cache*** |

The plan locked ten architectural commitments at Step 1; all hold in
the shipped code. The two that are most surprising to consumers:

- **Determinism is warm-cache only.** The model's cold output may
  drift; the cache turns it into a deterministic re-emit. See
  [tests/verify_llm_enrich_determinism.py](../tests/verify_llm_enrich_determinism.py).
- **Failure mode is degradation, not breakage.** Ollama unreachable →
  log + skip → SHACL stays green → bundle is bit-for-bit identical to
  a no-L4 run. See
  [tests/verify_llm_enrich_offline.py](../tests/verify_llm_enrich_offline.py).

## RDF surface

Three enrichment kinds, each emitted as four predicates on the
relevant subject:

| Kind | Target class | Predicates |
|---|---|---|
| ``file_summary`` | ``cbm:File`` (source code) | ``cbml4:fileSummary``, ``cbml4:fileSummaryModel``, ``cbml4:fileSummaryPromptSha``, ``cbml4:fileSummaryGeneratedAt`` |
| ``concept_description`` | ``skos:Concept`` (curated only) | ``cbml4:conceptDescription``, ``cbml4:conceptDescriptionModel``, ``cbml4:conceptDescriptionPromptSha``, ``cbml4:conceptDescriptionGeneratedAt`` |
| ``schema_purpose`` | ``cbm:File`` (under ``static/schemas/``) | ``cbml4:schemaPurpose``, ``cbml4:schemaPurposeModel``, ``cbml4:schemaPurposePromptSha``, ``cbml4:schemaPurposeGeneratedAt`` |

Example turtle (an enriched source file):

```turtle
cbmi:file/auth.py
    cbml4:fileSummary "auth.py provides a class for token-based authentication with a method to check tokens." ;
    cbml4:fileSummaryModel "qwen2.5-coder:7b" ;
    cbml4:fileSummaryPromptSha "882a84cb6e165f9cb1ed83c0a019843d4fae94fe9f3b5744dba67136009f9cd4" ;
    cbml4:fileSummaryGeneratedAt "2026-05-14T06:34:09+00:00"^^xsd:dateTime .
```

Every predicate is **optional** in SHACL (``maxCount 1``, no
``minCount``). A bundle built without ``--llm-enrich``, or one whose
Ollama daemon was down at emit time, carries none of these triples and
still SHACL-validates. The shapes themselves are always declared (so a
consumer SPARQLing ``shapes.shacl.ttl`` can discover the L4 contract
regardless of whether the data graph populated it).

SHACL constraints worth noting:

- ``cbml4:*PromptSha`` is constrained by ``sh:pattern "^[a-f0-9]{64}$"``
  — a bogus SHA fails validation, not just a missing one.
- ``cbml4:*GeneratedAt`` is typed ``xsd:dateTime``.

## Sidecar: ``enrichments.jsonl``

The full enrichment set also ships as a line-delimited JSON sidecar:

```jsonl
{"generated_at":"2026-05-14T06:34:09Z","kind":"file_summary","model":"qwen2.5-coder:7b","prompt_sha":"882a84cb…","target":"auth.py","target_sha":"5084f711…","text":"auth.py provides …"}
```

Rows are sorted by ``(kind, target)`` and serialized with
``sort_keys=True`` so two runs over the same enrichment set produce
byte-identical sidecar files — the warm-cache determinism guarantee
extends to this artifact.

Consumers that can't speak RDF read the sidecar directly. The MCP
server's backend ([frontend/backend/app.py](../frontend/backend/app.py))
loads it into per-kind dicts on bundle open.

## Sources of truth

The bundled prompts live under
[plugins/llm_enrich/prompts/](../plugins/llm_enrich/prompts/):

- ``file_summary.v1.txt``        — one declarative sentence, under 30 words
- ``concept_description.v1.txt`` — anchored paragraph, 3-5 sentences
- ``schema_purpose.v1.txt``      — schema definition + notable elements, 2-3 sentences

Each file's SHA-256 (over its raw bytes) is part of the cache key —
editing the file invalidates every cache entry built against the
previous bytes by design. The registry in
[plugins/llm_enrich/prompts.py](../plugins/llm_enrich/prompts.py) holds
the active version of each prompt; bumping a prompt requires both
copying ``kind.vN.txt`` → ``kind.v(N+1).txt`` and updating the
``_load("kind", N+1)`` call in ``PROMPT_REGISTRY``. The verifier
[tests/verify_llm_enrich_prompts.py](../tests/verify_llm_enrich_prompts.py)
catches the mismatch loudly.

## Cache

Default location ``~/.cache/cbm-llm/`` (override via
``$CBM_LLM_CACHE``). Flat ``<sha256>.json`` files. Key composition:

```
sha256( kind || \x1f || model || \x1f || prompt_sha || \x1f || target_sha )
```

where ``\x1f`` is the ASCII unit-separator (reserved across every
field — adding a new field to the key in a future version won't
collide with existing values).

The cache:

- **Lives outside the bundle.** Bundles ship the *outputs*
  (``enrichments.jsonl`` + the RDF triples), not the cache. A cache
  miss is regenerated; cache hits are skipped at the model layer.
- **Atomic writes** via ``tmp + os.replace``.
- **Forward-compatible schema** — every cache file carries ``"v": 1``;
  a future bump to ``CACHE_SCHEMA_VERSION`` invalidates older entries
  cleanly without confusing readers.

## CLI flags

Three entry points all gate on the same L4 plugin. The flags are
identical in behavior across them.

| Flag | run_l4.py | run_l3.py | run_xrefs.py |
|---|---|---|---|
| ``--llm-enrich`` (short-form, defaults) | — | yes | yes |
| ``--llm-model MODEL`` | yes | (use run_l4) | (use run_l4) |
| ``--llm-host URL`` | yes | (via $OLLAMA_HOST) | (via $OLLAMA_HOST) |
| ``--llm-scope CSV`` | yes | (use run_l4) | (use run_l4) |
| ``--llm-cache-dir PATH`` | yes | (via $CBM_LLM_CACHE) | (via $CBM_LLM_CACHE) |
| ``--llm-no-cache`` | yes | — | — |
| ``--no-llm`` | yes | — | — |

For fine-grained control use [scripts/run_l4.py](../scripts/run_l4.py).
The ``--llm-enrich`` flag on ``run_l3.py``/``run_xrefs.py`` is the
"just give me the defaults" shorthand: it registers the plugin with
``model=qwen2.5-coder:7b``, ``scopes=("files", "concepts", "schemas")``,
and the default cache directory.

``--llm-enrich`` on ``run_xrefs.py`` implies ``--concepts`` —
concept_description needs L3's typed-concept index to exist.

## MCP / API / UI surface

The MCP server exposes L4 fields on three tools:

- **``file_detail``** returns optional ``llm_summary`` (any source
  file enriched with ``file_summary``) and ``llm_schema_purpose``
  (files under ``static/schemas/`` enriched with ``schema_purpose``).
  Each is ``{text, provenance: {model, prompt_sha, target_sha,
  generated_at}}``.
- **``concept_detail``** returns optional ``llm_description`` for
  curated concepts.
- **``repository_summary``** attaches short-form ``llm_summary``
  strings to ``central_files`` entries and short-form
  ``llm_description`` strings to ``key_concepts`` entries. Full
  provenance is omitted here (deliberately — it's an executive read,
  not a deep call); follow up with ``file_detail`` /
  ``concept_detail`` for the SHAs.

The React UI ([frontend/ui/src/components/LlmEnrichmentCard.tsx](../frontend/ui/src/components/LlmEnrichmentCard.tsx))
renders L4 fields as a labelled card with an *"AI-enriched"* badge,
the LLM text, and provenance under a collapsible ``<details>``.

## Extending the layer

### Adding a fourth enrichment kind

The naming convention is uniform across all kinds — adding a fourth
follows the same shape as the existing three:

1. Add the prompt file: ``plugins/llm_enrich/prompts/<new_kind>.v1.txt``.
2. Register it in ``PROMPT_REGISTRY`` in
   [plugins/llm_enrich/prompts.py](../plugins/llm_enrich/prompts.py).
   Document the expected placeholders inline.
3. Add a scope literal in
   [plugins/llm_enrich/aggregator.py](../plugins/llm_enrich/aggregator.py)
   (``SCOPE_<X>``) and extend ``ALL_SCOPES``.
4. Implement the producer:
    - **Per-record** kind → add the body to ``LlmEnricher.enrich``
      ([plugins/llm_enrich/enricher.py](../plugins/llm_enrich/enricher.py)).
    - **Aggregator** kind → add the body to ``LlmAggregator.run``
      (current pattern in ``aggregator.py``).
5. Add the four predicates to
   ``_<NEW_KIND>_PREDICATES`` in
   [plugins/llm_enrich/graph_writer.py](../plugins/llm_enrich/graph_writer.py)
   and add a writer-loop entry that iterates
   ``ctx.scratch["llm:<new_kind>"]`` and emits via the existing
   ``_add_triples()`` helper.
6. Extend ``LlmShapes.contribute`` with four matching
   ``sh:property`` blocks (the existing helpers
   ``_add_optional_string`` / ``_add_optional_datetime`` handle the
   common cases).
7. Extend ``_iter_records`` in
   [plugins/llm_enrich/artifact.py](../plugins/llm_enrich/artifact.py)
   to include the new bucket in the sidecar.
8. (Optional) extend the MCP handlers in
   [frontend/mcp_server/handlers.py](../frontend/mcp_server/handlers.py)
   to surface the kind on the relevant detail endpoint, and add the
   field to ``frontend/mcp_server/schemas.py``.
9. Update the verifiers — at minimum
   ``tests/verify_llm_enrich_prompts.py`` (add placeholder coverage)
   and ``tests/verify_llm_enrich_aggregator.py`` (or
   ``verify_llm_enrich_file_summary.py`` for per-record kinds).

### Stability rules

- **Adding a kind** is additive — clients that don't know about it
  ignore the new predicates.
- **Removing a kind** is a breaking change for any consumer SPARQL.
  Don't.
- **Renaming a kind** is breaking — the predicate names appear in the
  RDF surface. Don't.
- **Bumping a prompt version** (``kind.v1.txt`` → ``kind.v2.txt``)
  invalidates every cache entry built against ``v1``. Existing
  bundles still contain the ``v1`` outputs in their sidecars + RDF;
  *new* emits will re-call the model. The
  ``cbml4:*PromptSha`` field tracks which version was used.
- **Changing the model default** (``qwen2.5-coder:7b`` → something
  else) is reportable but not breaking: the ``cbml4:*Model`` field
  tracks which model produced each enrichment. Different bundles can
  carry enrichments from different models.

## Verifier matrix

| Script | Scope | Tests |
|---|---|---|
| [`verify_llm_enrich.py`](../tests/verify_llm_enrich.py) | Step 1: back-compat anchor (with-L4 vs without-L4 byte equality, modulo the L4 SHACL shape contribution) | 18 |
| [`verify_llm_enrich_cache.py`](../tests/verify_llm_enrich_cache.py) | Step 2: cache key stability, atomic writes, OllamaClient | 29 |
| [`verify_llm_enrich_prompts.py`](../tests/verify_llm_enrich_prompts.py) | Step 3: prompt file SHA matches registered version | 19 |
| [`verify_llm_enrich_file_summary.py`](../tests/verify_llm_enrich_file_summary.py) | Step 3: enricher end-to-end against live Ollama | 12 |
| [`verify_llm_enrich_rdf.py`](../tests/verify_llm_enrich_rdf.py) | Step 4: RDF triples + SHACL + sidecar parity | 17 |
| [`verify_llm_enrich_aggregator.py`](../tests/verify_llm_enrich_aggregator.py) | Step 5: concept_description + schema_purpose, per-scope independence | 18 |
| [`verify_llm_enrich_determinism.py`](../tests/verify_llm_enrich_determinism.py) | Step 6: warm-cache determinism (run 2 ≡ run 3 byte-identical) | 23 |
| [`verify_llm_enrich_offline.py`](../tests/verify_llm_enrich_offline.py) | Step 6: degradation when Ollama is unreachable | 20 |
| [`verify_llm_enrich_cli.py`](../tests/verify_llm_enrich_cli.py) | Step 8: subprocess-driven script tests | 27 |
| [`verify_llm_enrich_ci_determinism.py`](../tests/verify_llm_enrich_ci_determinism.py) | Step 10: warm-cache determinism via committed fixture, no Ollama | 25 |
| **Total** | | **208** |

The MCP server side has additional coverage in
[frontend/mcp_server/tests/test_llm_enrich_surface.py](../frontend/mcp_server/tests/test_llm_enrich_surface.py)
(5 end-to-end tests against a freshly-emitted enriched bundle).

The UI side has coverage in
[frontend/ui/src/__tests__/empty-states.test.tsx](../frontend/ui/src/__tests__/empty-states.test.tsx)
(5 tests on ``LlmEnrichmentCard`` visibility + provenance details).

## CI determinism harness

A committed cache fixture under
[tests/fixtures/llm_cache/](../tests/fixtures/llm_cache/) lets the
warm-cache determinism guarantee run in CI without a live Ollama.
The fixture is small (~32 KB across 7 cache files for 7 enrichments
covering all three kinds) and contains:

- ``cache/<sha256>.json`` × N — pre-populated cache entries
- ``repo/*`` — the source files those entries were generated from
- ``manifest.json`` — expected n_enrichments / by_kind / SHACL conforms

The CI verifier [tests/verify_llm_enrich_ci_determinism.py](../tests/verify_llm_enrich_ci_determinism.py)
materializes the repo, copies the cache to a temp dir, and runs the
pipeline twice with a *stub* OllamaClient that raises ``CacheMiss``
on any chat. The two outputs must be byte-identical and every record
must report ``was_cache_hit=True``.

### When to regenerate the fixture

Run
[`tests/fixtures/llm_cache/regenerate.py`](../tests/fixtures/llm_cache/regenerate.py)
(requires a live Ollama) whenever one of these triggers fires:

- A prompt file's bytes change (``file_summary.v1.txt`` edit, or a
  ``v1 → v2`` bump anywhere in ``PROMPT_REGISTRY``).
- The default model changes (``qwen2.5-coder:7b`` → something else).
- The cache schema version bumps
  (``CACHE_SCHEMA_VERSION 1 → 2`` in
  [plugins/llm_enrich/cache.py](../plugins/llm_enrich/cache.py)).
- A new enrichment kind is added that should be exercised by the
  fixture.

The verifier fails loudly if any of these change without a
corresponding fixture regeneration. The error message points at the
regeneration script.

## File map

| Path | Role |
|---|---|
| `plugins/llm_enrich/__init__.py` | `register_all(client, cache, model, scopes)` |
| `plugins/llm_enrich/client.py` | `OllamaClient`, `OllamaUnreachable`, `OllamaModelMissing` |
| `plugins/llm_enrich/cache.py` | `Cache`, key composition, `get_or_compute` |
| `plugins/llm_enrich/enricher.py` | `LlmEnricher` — per-file `file_summary` |
| `plugins/llm_enrich/aggregator.py` | `LlmAggregator` — `concept_description` + `schema_purpose` |
| `plugins/llm_enrich/graph_writer.py` | `LlmGraphWriter`, `LlmShapes` |
| `plugins/llm_enrich/artifact.py` | `LlmArtifact` — emits `enrichments.jsonl` |
| `plugins/llm_enrich/prompts.py` | `PROMPT_REGISTRY`, `PromptTemplate`, `verify_registry()` |
| `plugins/llm_enrich/prompts/*.v1.txt` | versioned prompts |
| `codebase_mapper/shared_kernel/constants.py` | `CBML4`, `CBML4_NS` |
| `scripts/run_l4.py` | full L1+L2+L3+L4 entry point |
| `scripts/run_l3.py` | `--llm-enrich` short-form opt-in |
| `scripts/run_xrefs.py` | `--llm-enrich` (implies `--concepts`) |
| `frontend/backend/app.py` | `Bundle.enrichment_*`, `_load_enrichments()` |
| `frontend/mcp_server/handlers.py` | `file_detail`/`concept_detail`/`repository_summary` projections |
| `frontend/mcp_server/schemas.py` | `_LLM_ENRICHMENT` building block |
| `frontend/ui/src/api.ts` | `LlmEnrichment` TypeScript type |
| `frontend/ui/src/components/LlmEnrichmentCard.tsx` | shared UI card |
| `tests/fixtures/llm_cache/` | committed cache fixture for CI determinism |
| `tests/fixtures/llm_cache/regenerate.py` | rebuilds the fixture (requires Ollama) |
| `tests/verify_llm_enrich_ci_determinism.py` | CI-runnable determinism verifier |
| `docs/archive/llm-enrich-plan.md` | the 10-step plan (now shipped) |
| `docs/llm-baseline-results.md` | the 5-model benchmark + recommendation |

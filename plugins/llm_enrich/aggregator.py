"""LlmAggregator — concept and schema enrichments that need post-L3 context.

Step 5 fills in the body. The aggregator runs *after* every
RecordEnricher (including L3's ConceptAggregator), so it has access
to the curated-vocab concept index that Step 5's concept_description
prompt needs.

Two kinds live here:

  - ``concept_description``: per *typed* concept (those carrying
    ``cbml3:conceptKind`` from the controlled vocabulary). Reads from
    ``ctx.indices["l3_20_concepts"]`` and stashes onto
    ``ctx.scratch["llm:concept_description"]``.

  - ``schema_purpose``: per source schema file (XSDs, JSON Schemas,
    Protobufs under ``static/schemas/``). Reads from ``ctx.records``
    and stashes onto ``ctx.scratch["llm:schema_purpose"]``.

The aggregator returns an index payload with both bucket
dictionaries; the graph writer + artifact emitter (Step 4) read from
``ctx.scratch`` directly. The index return is kept for diagnostics
and for the manifest reporter.

Gating:
  ``scopes=None`` or empty       → no-op (back-compat anchor)
  ``("files",)``                 → no concept/schema work
  ``("concepts",)``              → concept_description only
  ``("schemas",)``               → schema_purpose only
  ``("files", "concepts", ...)`` → opt in to each kind independently
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

from codebase_mapper.shared_kernel.progress import ProgressReporter

from .cache import Cache, hash_text
from .client import OllamaClient, OllamaModelMissing, OllamaUnreachable
from .model_resolver import DEFAULT_MODEL

#: Corpus-tier size: top-N concepts by (frequency, file spread) described in
#: addition to every curated-vocab match (error-free-mapping E6 wave 2).
#: 0 disables the corpus tier. Env: CBM_CONCEPT_TOP_N.
DEFAULT_CONCEPT_TOP_N = 200


def _concept_top_n() -> int:
    raw = os.environ.get("CBM_CONCEPT_TOP_N", "").strip()
    if not raw:
        return DEFAULT_CONCEPT_TOP_N
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_CONCEPT_TOP_N


def select_concepts_for_description(
    concepts: dict, top_n: int,
) -> list[tuple[str, str]]:
    """Two deliberate description tiers, each tagged with its provenance.

    Tier 1 ("vocab"): every concept carrying a curated cbml3:conceptKind —
    the typed backbone, always described. Tier 2 ("corpus_top"): the top-N
    remaining concepts by occurrence count then file spread — the corpus's
    own dominant language, so a domain the vocabulary barely covers (24 of
    776,716 on the kernel, flaw F6) still gets meaningful descriptions.
    Deterministic order: vocab tier sorted by name, then corpus tier by
    (-frequency, -file_count, name).
    """
    vocab = sorted(n for n, meta in concepts.items() if "kind" in meta)
    selected: list[tuple[str, str]] = [(n, "vocab") for n in vocab]
    if top_n > 0:
        rest = sorted(
            (n for n, meta in concepts.items() if "kind" not in meta),
            key=lambda n: (-int(concepts[n].get("frequency", 0)),
                           -int(concepts[n].get("file_count", 0)), n))
        selected += [(n, "corpus_top") for n in rest[:top_n]]
    return selected
from .prompts import PROMPT_REGISTRY

if TYPE_CHECKING:
    from codebase_mapper.shared_kernel.extensions import PipelineCtx


AGGREGATOR_NAME = "l4_20_enrich"

# Scope literals (mirrored in the CLI flag --llm-scope and in the
# enricher for files).
SCOPE_FILES = "files"
SCOPE_CONCEPTS = "concepts"
SCOPE_SCHEMAS = "schemas"
ALL_SCOPES = (SCOPE_FILES, SCOPE_CONCEPTS, SCOPE_SCHEMAS)

# Content budget for the schema_purpose prompt. Schemas can be huge
# (the cbm-vendored XSDs run 100KB+); we truncate to keep the request
# inside an 8B-class model's context window. The hash of the truncated
# content drives the cache key, so re-summarizing a different prefix
# is treated as a different input — correct behavior.
SCHEMA_CONTENT_BUDGET = 8000

# Sub-directories whose source files we treat as schemas for the
# schema_purpose kind. The plugin doesn't currently introspect file
# *content* to classify schemas — it goes by path. This matches the
# vendored fixture layout (static/schemas/).
SCHEMA_PATH_PREFIXES: tuple[str, ...] = (
    "static/schemas/",
)
SCHEMA_EXTENSIONS: tuple[str, ...] = (
    ".xsd", ".xml",
    ".json", ".yaml", ".yml",
    ".proto",
)


_log = logging.getLogger("cbm.llm_enrich")


@dataclass
class LlmAggregator:
    """Step-5 aggregator that produces concept and schema enrichments."""

    client: OllamaClient | None = None
    cache: Cache | None = None
    model: str = DEFAULT_MODEL
    scopes: tuple[str, ...] | None = None

    name: str = AGGREGATOR_NAME

    # Per-run kill switch — flips True on first OllamaUnreachable /
    # OllamaModelMissing, then every subsequent attempt is skipped.
    _disabled: bool = field(default=False, init=False, repr=False)

    # ----------------------------------------------------------------

    def _enabled(self, scope: str) -> bool:
        return (
            self.client is not None
            and self.scopes is not None
            and scope in self.scopes
            and not self._disabled
        )

    def run(self, ctx: "PipelineCtx") -> dict:
        # Default index payload — empty buckets so the writer + artifact
        # have a stable shape to consume from.
        bucket_concepts: dict[str, dict] = {}
        bucket_schemas: dict[str, dict] = {}

        if self._enabled(SCOPE_CONCEPTS):
            self._do_concept_descriptions(ctx, bucket_concepts)

        if self._enabled(SCOPE_SCHEMAS):
            self._do_schema_purposes(ctx, bucket_schemas)

        # Stash on scratch for the writer + artifact emitter.
        if bucket_concepts:
            ctx.scratch["llm:concept_description"] = bucket_concepts
        if bucket_schemas:
            ctx.scratch["llm:schema_purpose"] = bucket_schemas

        # Index payload — the manifest reporter consumes the counts.
        return {
            "concept_description": bucket_concepts,
            "schema_purpose": bucket_schemas,
        }

    # ----------------------------------------------------------------

    def _disable_with_disclosure(  # noqa: D401 — see module docstring
        self, ctx: "PipelineCtx", kind: str, error: str, skipped: int,
    ) -> None:
        """Self-disable and register the degradation on ctx.scratch.

        Mirrors LlmEnricher._disable: a layer that stops producing must
        leave a machine-readable record of its blast radius — the manifest
        emitter surfaces ctx.scratch["degradations"] (PALS's Law). The
        aggregator runs single-threaded, so no lock is needed here.
        """
        from plugins.llm_enrich.enricher import ERROR_EXCERPT_CHARS

        self._disabled = True
        ctx.scratch.setdefault("degradations", []).append({
            "component": "llm_enrich",
            "reason": "client_failure_self_disabled",
            "kind": kind,
            "skipped": skipped,
            "error": error[:ERROR_EXCERPT_CHARS],
        })

    def _do_concept_descriptions(
        self, ctx: "PipelineCtx", out: dict[str, dict],
    ) -> None:
        """Generate concept_description for every concept carrying a
        cbml3:conceptKind (i.e. matched against the curated vocab)."""
        l3 = cast(dict, ctx.indices.get("l3_20_concepts") or {})
        concepts = cast(dict, l3.get("concepts") or {})
        cooccurrence = cast(list, l3.get("cooccurrence") or [])
        per_path = cast(dict, l3.get("per_path_concepts") or {})

        # Pre-compute per-concept cooccurrence lists (highest weight first)
        # and per-concept file lists. Iterating once over cooccurrence /
        # per_path is cheaper than re-walking them per concept.
        by_name: dict[str, list[tuple[str, int]]] = {}
        for a, b, w in cooccurrence:
            by_name.setdefault(a, []).append((b, int(w)))
            by_name.setdefault(b, []).append((a, int(w)))
        for name in by_name:
            by_name[name].sort(key=lambda x: -x[1])

        files_for: dict[str, list[str]] = {}
        for path, names in per_path.items():
            for n in names:
                files_for.setdefault(n, []).append(path)
        for n in files_for:
            files_for[n].sort()

        # Deterministic two-tier selection (E6 wave 2): every vocab match
        # plus the corpus's own top concepts, each tagged with provenance.
        typed = select_concepts_for_description(concepts, _concept_top_n())

        cache = self.cache or Cache()
        tmpl = PROMPT_REGISTRY["concept_description"]

        reporter = ProgressReporter("[L4] concept_description",
                                    total=len(typed))
        for idx, (name, selection) in enumerate(typed):
            if self._disabled:
                return
            meta = concepts[name]
            cooc = by_name.get(name, [])[:5]
            files = files_for.get(name, [])[:3]
            alt = meta.get("alt_labels", [])[:6]

            cooc_str = ", ".join(f"{n} ({w})" for n, w in cooc) or "(none)"
            files_str = ", ".join(files) or "(none)"
            alt_str = ", ".join(alt) or "(none)"

            system, user = tmpl.render(
                name=name,
                kind=meta.get("kind", ""),
                frequency=int(meta.get("frequency", 0)),
                alt_labels=alt_str,
                cooccurring=cooc_str,
                files=files_str,
            )

            # Cache key target_sha hashes the *rendered user prompt* so
            # any change to the bundled context (different cooccurring
            # neighbors, additional files) invalidates the entry. That
            # is the correct invalidation policy: if the codebase
            # changed enough to shift this concept's neighbors, the
            # description should be regenerated.
            target_sha = hash_text(user)

            def compute(_sys=system, _user=user) -> dict:
                assert self.client is not None
                text, _dt = self.client.chat(
                    model=self.model, system=_sys, user=_user, seed=42,
                )
                return {"text": text.strip(),
                        "generated_at": _iso_now()}

            try:
                record, was_hit = cache.get_or_compute(
                    kind="concept_description",
                    model=self.model,
                    prompt_sha=tmpl.sha256,
                    target_sha=target_sha,
                    compute=compute,
                )
            except OllamaUnreachable as e:
                _log.warning(
                    "llm_enrich: Ollama unreachable, disabling "
                    "concept_description for the rest of this run: %s",
                    e,
                )
                # The failed concept plus every one not yet attempted.
                self._disable_with_disclosure(
                    ctx, "concept_description", str(e), len(typed) - idx)
                return
            except OllamaModelMissing as e:
                _log.warning(
                    "llm_enrich: model %r not available, disabling "
                    "concept_description for the rest of this run: %s",
                    self.model, e,
                )
                self._disable_with_disclosure(
                    ctx, "concept_description", str(e), len(typed) - idx)
                return

            reporter.update(name, cached=was_hit)
            out[name] = {**record, "was_cache_hit": was_hit,
                         "selection": selection}

    # ----------------------------------------------------------------

    def _do_schema_purposes(
        self, ctx: "PipelineCtx", out: dict[str, dict],
    ) -> None:
        """Generate schema_purpose for every file under SCHEMA_PATH_PREFIXES
        with a known schema-like extension."""
        records = [r for r in sorted(ctx.records, key=lambda r: r.path)
                   if _is_schema_file(r.path)]
        cache = self.cache or Cache()
        tmpl = PROMPT_REGISTRY["schema_purpose"]

        reporter = ProgressReporter("[L4] schema_purpose",
                                    total=len(records))
        for idx, record in enumerate(records):
            if self._disabled:
                return

            try:
                raw = ctx.read_path(record.path)
            except Exception as e:
                _log.warning(
                    "llm_enrich: could not read %r for schema_purpose: %s",
                    record.path, e,
                )
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary schema file (e.g., compiled proto). Skip.
                continue

            truncated = text[:SCHEMA_CONTENT_BUDGET]
            target_sha = hash_text(truncated)

            system, user = tmpl.render(
                path=record.path,
                filename=PurePosixPath(record.path).name,
                content=truncated,
            )

            def compute(_sys=system, _user=user) -> dict:
                assert self.client is not None
                text, _dt = self.client.chat(
                    model=self.model, system=_sys, user=_user, seed=42,
                )
                return {"text": text.strip(),
                        "generated_at": _iso_now()}

            try:
                record_d, was_hit = cache.get_or_compute(
                    kind="schema_purpose",
                    model=self.model,
                    prompt_sha=tmpl.sha256,
                    target_sha=target_sha,
                    compute=compute,
                )
            except OllamaUnreachable as e:
                _log.warning(
                    "llm_enrich: Ollama unreachable, disabling "
                    "schema_purpose for the rest of this run: %s", e,
                )
                self._disable_with_disclosure(
                    ctx, "schema_purpose", str(e), len(records) - idx)
                return
            except OllamaModelMissing as e:
                _log.warning(
                    "llm_enrich: model %r not available, disabling "
                    "schema_purpose for the rest of this run: %s",
                    self.model, e,
                )
                self._disable_with_disclosure(
                    ctx, "schema_purpose", str(e), len(records) - idx)
                return

            reporter.update(record.path, cached=was_hit)
            out[record.path] = {**record_d, "was_cache_hit": was_hit}


# ----------------------------------------------------------------------


def _is_schema_file(path: str) -> bool:
    """Path-based classifier. The aggregator currently does no content
    inspection — that's a future iteration if false positives appear."""
    if not any(path.startswith(prefix) for prefix in SCHEMA_PATH_PREFIXES):
        return False
    ext = "".join(PurePosixPath(path).suffixes[-1:]).lower()
    return ext in SCHEMA_EXTENSIONS


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

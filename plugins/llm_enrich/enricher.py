"""LlmEnricher — per-file LLM summaries.

Step 3 fills in the body. The enricher runs once per FileRecord
*after* AST extraction (RecordEnricher contract) and produces a
``file_summary`` enrichment for every source-code file when the
caller opted into the ``"files"`` scope.

Wiring:
  - On miss, calls ``OllamaClient.chat`` and writes the record to the
    cache via ``Cache.get_or_compute``.
  - On hit, no model call.
  - On Ollama failure (unreachable or model missing), logs the error
    once and disables further calls for the rest of the run — the
    failure mode the plan calls "degradation, not breakage"
    (Commitment #7). The degradation is disclosed, not silent
    (PALS's LAW): one entry is registered on
    ``ctx.scratch["degradations"]`` counting every eligible record
    left unenriched from the failing call onward, so the manifest
    can carry a machine-readable record that the layer degraded.
  - Result lands on ``ctx.scratch["llm:file_summary"][record.path]``
    as a dict ``{text, model, prompt_sha, target_sha, generated_at,
    was_cache_hit}``. Step 4 reads from there to emit triples; the
    artifact emitter reads from there to write the sidecar.

Scope discipline:
  - ``scopes=None`` (default for ``register_all()``)  → no-op
  - ``scopes=()``                                      → no-op
  - ``scopes=("files",)``                              → file_summary fires
  - ``scopes=("files", "concepts")``                   → file_summary fires;
                                                        concept_description
                                                        (Step 5) fires from
                                                        the aggregator

The no-op default preserves Step 1's back-compat anchor —
``register_all()`` with no args remains byte-identical to no
registration at all.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, ClassVar

from codebase_mapper.shared_kernel.progress import ProgressReporter

from .cache import Cache, hash_text
from .client import OllamaClient, OllamaModelMissing, OllamaUnreachable
from .model_resolver import DEFAULT_MODEL
from .prompts import PROMPT_REGISTRY

if TYPE_CHECKING:
    from codebase_mapper.shared_kernel.extensions import PipelineCtx
    from codebase_mapper.inspection.models import FileRecord


# Plugin-name prefix follows the project convention: `l<layer>_<step>_<purpose>`.
ENRICHER_NAME = "l4_10_enrich"

# Max characters of file content we send to the model. Tuned during
# the POC: 4000 chars covers ~95% of source files in the
# code-mapper bundle without truncation, and stays well inside an
# 8B-class model's typical 8K-token context window even with the
# system + user template overhead.
CONTENT_BUDGET_CHARS = 4000

# Scope literal used to opt in to file-level summaries. Mirrored in
# the CLI flag (`--llm-scope files,...`) and in the docs.
SCOPE_FILES = "files"

# Budget for the error excerpt mirrored into the degradation entry
# (ctx.scratch["degradations"]). Mirrored by the assertion in
# tests/verify_llm_enrich_degradation.py.
ERROR_EXCERPT_CHARS = 200

# Languages we attempt to summarize. Anything outside this set is
# skipped (the enricher is opt-in by language). The set deliberately
# matches the well-supported AST languages so we never send a binary
# blob to the model by accident; rare languages can be added later.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset((
    "python",
    "typescript", "javascript",
    "rust", "go",
    "ruby", "kotlin", "swift", "dart",
    # C family (error-free-mapping E6): cpp and objective-c were excluded
    # while c was included — genuine C++/ObjC projects silently got no
    # file summaries.
    "c", "cpp", "objective-c",
    "cfml",
    "sql",
    "html",
    "css", "scss",
    "json", "yaml",
    "shell",
))


_log = logging.getLogger("cbm.llm_enrich")


@dataclass
class LlmEnricher:
    """RecordEnricher that produces per-file LLM summaries.

    Constructor params:
      ``client``:  OllamaClient (real or None). None → no-op even if
                   scopes is set, so the registry can be wired in
                   environments that don't have Ollama.
      ``cache``:   Cache. None → an enabled default cache at the
                   conventional path. Pass ``Cache(enabled=False)`` to
                   disable caching entirely (verifier path).
      ``model``:   Ollama model tag. Defaults to qwen2.5-coder:7b per the
                   benchmark (docs/llm-baseline-results.md); ``register_all``
                   may auto-resolve it to an installed same-family tag when
                   the default is not pulled (see model_resolver.py).
      ``scopes``:  Tuple of scope names to opt in to. ``None`` or empty
                   tuple = no-op (Step 1 back-compat anchor).
    """

    client: OllamaClient | None = None
    cache: Cache | None = None
    model: str = DEFAULT_MODEL
    scopes: tuple[str, ...] | None = None

    name: str = ENRICHER_NAME

    # Concurrency contract with the host pipeline: enrich() may be called
    # from multiple threads on distinct records. Shared state is either
    # guarded (_reporter, below) or benign under the GIL (_disabled is a
    # latch that only ever flips False→True; ctx.scratch writes are
    # per-record keys inside a dict obtained via atomic setdefault).
    parallel_safe: ClassVar[bool] = True

    # Set to True if Ollama fails mid-run; subsequent records are
    # skipped without further attempts. Reset on next process.
    _disabled: bool = field(default=False, init=False, repr=False)

    # Degradation disclosure (PALS's LAW): self-disabling silently
    # would let the run "complete normally" with no machine-readable
    # record that the enrichment layer degraded. On disable, exactly
    # one entry is appended to ctx.scratch["degradations"] and kept
    # here by reference; its "skipped" count is then incremented in
    # place for every later eligible record, so the end of the run
    # sees one entry carrying the final tally. The manifest emitter
    # reads ctx.scratch["degradations"] to surface it in the bundle.
    _degradation: dict | None = field(default=None, init=False, repr=False)

    _reporter_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False)

    # Throttled progress heartbeat. Created lazily on the first file we
    # actually summarize; its total is the count of *eligible* records
    # (this enricher's own _should_summarize gate applied to ctx.records),
    # so file_summary shows a real "i/total (pct%)" + ETA rather than a
    # bare running count. The streaming RecordEnricher loop doesn't hand
    # the plugin a total, but ctx.records is available, so the plugin
    # derives its own — honestly, from the same gate it enriches by.
    _reporter: ProgressReporter | None = field(
        default=None, init=False, repr=False)

    # ----------------------------------------------------------------

    def _file_summary_enabled(self) -> bool:
        return (
            self.client is not None
            and self.scopes is not None
            and SCOPE_FILES in self.scopes
            and not self._disabled
        )

    def _should_summarize(self, record: "FileRecord") -> bool:
        """Per-record gate. Returns True iff this file qualifies for
        file_summary enrichment under the current scope."""
        if record.type_ != "source_code":
            return False
        if record.language not in SUPPORTED_LANGUAGES:
            return False
        return True

    # ----------------------------------------------------------------

    def _disable(self, ctx: "PipelineCtx", error: str) -> None:
        """Self-disable and register the degradation disclosure.

        Called from the except handlers below, i.e. only after the
        scope gates passed and a real client call failed — so every
        record counted from here on truly lost its enrichment to the
        failure. The record whose call just failed counts as the
        first skip: it produced no enrichment either.

        Thread-safe: enrichment may run on a worker pool, so two
        records can fail concurrently before either marks the
        enricher disabled. The lock guarantees exactly one entry is
        registered; the loser's failed record folds into the tally.
        """
        with self._reporter_lock:
            self._disabled = True
            if self._degradation is not None:
                self._degradation["skipped"] += 1
                return
            entry = {
                "component": "llm_enrich",
                "reason": "client_failure_self_disabled",
                "kind": "file_summary",
                "skipped": 1,  # the record whose call just failed
                "error": error[:ERROR_EXCERPT_CHARS],
            }
            ctx.scratch.setdefault("degradations", []).append(entry)
            self._degradation = entry

    def enrich(self, record: "FileRecord", content: bytes,
               ctx: "PipelineCtx") -> None:
        if self._disabled:
            # Degraded: count every record that would have been
            # summarized so the run's single degradation entry
            # discloses the true blast radius instead of the run
            # silently "completing normally".
            if self._degradation is not None and \
                    self._should_summarize(record):
                with self._reporter_lock:
                    self._degradation["skipped"] += 1
            return
        if not self._file_summary_enabled():
            return
        if not self._should_summarize(record):
            return

        # Decode content; truncate to the prompt budget. We hash the
        # *truncated* content (post-budget) so the cache key reflects
        # what the model actually saw. If the budget changes in a
        # later version, that becomes a cache-invalidating event —
        # which is correct (different prompt input → different output).
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            # Source files with bad encodings are exotic. Skip rather
            # than guess.
            return
        truncated = text[:CONTENT_BUDGET_CHARS]
        target_sha = hash_text(truncated)

        cache = self.cache or Cache()
        tmpl = PROMPT_REGISTRY["file_summary"]
        system, user = tmpl.render(
            path=record.path,
            language=record.language or "",
            content=truncated,
        )

        def compute() -> dict:
            assert self.client is not None  # gated by _file_summary_enabled
            text, _dt = self.client.chat(
                model=self.model, system=system, user=user, seed=42,
            )
            return {
                "text": text.strip(),
                "generated_at": _iso_now(),
            }

        try:
            record_dict, was_hit = cache.get_or_compute(
                kind="file_summary",
                model=self.model,
                prompt_sha=tmpl.sha256,
                target_sha=target_sha,
                compute=compute,
            )
        except OllamaUnreachable as e:
            _log.warning(
                "llm_enrich: Ollama unreachable, disabling file_summary "
                "for the rest of this run: %s", e,
            )
            self._disable(ctx, str(e))
            return
        except OllamaModelMissing as e:
            _log.warning(
                "llm_enrich: model %r not available, disabling "
                "file_summary for the rest of this run: %s",
                self.model, e,
            )
            self._disable(ctx, str(e))
            return

        with self._reporter_lock:
            if self._reporter is None:
                eligible = sum(1 for rec in ctx.records
                               if self._should_summarize(rec))
                self._reporter = ProgressReporter(
                    "[L4] file_summary", total=eligible)
            self._reporter.update(record.path, cached=was_hit)

        # Stash on ctx.scratch under a documented key. Step 4 reads
        # this in LlmGraphWriter.contribute; the artifact emitter
        # mirrors it into enrichments.jsonl.
        bucket: dict = ctx.scratch.setdefault("llm:file_summary", {})
        bucket[record.path] = {
            **record_dict,  # carries v, kind, model, prompt_sha, target_sha, text, generated_at
            "was_cache_hit": was_hit,
        }


def _iso_now() -> str:
    """UTC ISO-8601 with seconds resolution. Stable per record but not
    cross-run — that's why we cache by content, not by timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

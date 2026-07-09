#!/usr/bin/env python3
"""verify_llm_enrich_degradation.py — degradation disclosure contract.

When the Ollama client fails mid-run the enricher self-disables
(log once + skip the rest) — that is Commitment #7's "degradation,
not breakage". But a run that completes normally while silently
dropping an enrichment layer violates PALS's LAW: the bundle must
carry a machine-readable record that enrichment degraded. This
verifier enforces the disclosure contract shared with the manifest
emitter:

    ctx.scratch["degradations"] == [{
        "component": "llm_enrich",
        "reason": "client_failure_self_disabled",
        "kind": "file_summary",
        "skipped": <eligible records left unenriched by the failure>,
        "error": <first error string, truncated>,
    }]

What's checked:

  1. Client fails on call N: exactly one degradation entry appears,
     with skipped == count of eligible records from the failing one
     onward (the failing record got no enrichment, so it counts) —
     and the records enriched before the failure keep their summaries.
  2. Ineligible records (wrong type_ / unsupported language) never
     inflate the count — the scope filter applies while disabled too.
  3. Healthy client: the enricher adds no "degradations" key at all.
  4. No-op registration (empty scopes): no "degradations" key either.
  5. Client dead from call 1: entry present with the FULL eligible
     count (Ollama never reachable at all must still be disclosed).
  6. OllamaModelMissing takes the same disclosure path as
     OllamaUnreachable.
  7. Log-once behavior preserved: exactly one warning per run, and no
     further client calls after the enricher disables itself.
  8. The error excerpt is truncated to the documented budget
     (200 chars — mirrors ERROR_EXCERPT_CHARS in enricher.py).

All tests run offline against a stub client; no Ollama required.
Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.shared_kernel.extensions import PipelineCtx
from plugins.llm_enrich.cache import Cache
from plugins.llm_enrich.client import OllamaModelMissing, OllamaUnreachable
from plugins.llm_enrich.enricher import LlmEnricher


PASS = 0
FAIL = 0

# Mirrors ERROR_EXCERPT_CHARS in plugins/llm_enrich/enricher.py — the
# budget for the error excerpt mirrored into the degradation entry.
ERROR_EXCERPT_BUDGET = 200


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:6]:
                print(f"        {line}")
        FAIL += 1


# ----------------------------------------------------------------------
# Fixtures — stub client, in-memory ctx, and the pipeline's own loop
# shape (mirrors codebase_mapper/inspection/pipeline.py:318-326).
# ----------------------------------------------------------------------


class StubClient:
    """Stub OllamaClient: succeeds for ``healthy_calls`` chats, then
    raises ``exc`` on every later call. ``exc=None`` = always healthy."""

    def __init__(self, healthy_calls: int, exc: Exception | None = None):
        self.healthy_calls = healthy_calls
        self.exc = exc
        self.calls = 0

    def chat(self, *, model: str, system: str, user: str,
             seed: int) -> tuple[str, float]:
        self.calls += 1
        if self.exc is not None and self.calls > self.healthy_calls:
            raise self.exc
        return f"summary #{self.calls}", 0.01


def rec(path: str, language: str | None = "python",
        type_: str = "source_code") -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=16, language=language, type_=type_, phases=[],
    )


def make_ctx(records: list[FileRecord]) -> PipelineCtx:
    return PipelineCtx(
        repo=Path("/nonexistent"),
        commit="deadbeef",
        records=records,
        blob_by_path={},
        mode_by_path={},
        paths_set={r.path for r in records},
        read_path=lambda p: b"",
    )


def drive(enricher: LlmEnricher, records: list[FileRecord],
          ctx: PipelineCtx) -> None:
    """Run the RecordEnricher loop exactly as the pipeline does."""
    for r in records:
        if r.type_ == "binary":
            continue
        enricher.enrich(r, b'print("fixture")\n', ctx)


def fixture_records() -> list[FileRecord]:
    """5 eligible source files + 3 records the scope filter rejects."""
    return [
        rec("a.py"),
        rec("b.py"),
        rec("README.md", language="markdown", type_="documentation"),
        rec("c.py"),
        rec("query.sql", language="sql"),          # unsupported language
        rec("d.py"),
        rec("logo.bin", language=None, type_="binary"),
        rec("e.py"),
    ]


N_ELIGIBLE = 5


def make_enricher(client: StubClient,
                  scopes: tuple[str, ...] | None = ("files",),
                  ) -> LlmEnricher:
    return LlmEnricher(
        client=client,                      # type: ignore[arg-type]
        cache=Cache(enabled=False),         # no disk I/O, compute always
        model="stub-model:1b",
        scopes=scopes,
    )


class _WarningCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def run_with_warning_capture(
    enricher: LlmEnricher, records: list[FileRecord], ctx: PipelineCtx,
) -> list[logging.LogRecord]:
    log = logging.getLogger("cbm.llm_enrich")
    handler = _WarningCounter()
    log.addHandler(handler)
    try:
        drive(enricher, records, ctx)
    finally:
        log.removeHandler(handler)
    return handler.records


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_failure_mid_run_registers_degradation() -> None:
    # Client survives 2 chats, dies on the 3rd (c.py). Eligible order:
    # a.py, b.py enriched; c.py fails; d.py, e.py skipped → skipped=3.
    client = StubClient(
        healthy_calls=2,
        exc=OllamaUnreachable("connect: connection refused (boom)"),
    )
    records = fixture_records()
    ctx = make_ctx(records)
    warnings = run_with_warning_capture(make_enricher(client), records, ctx)

    summaries = ctx.scratch.get("llm:file_summary", {})
    check("records before the failure keep their summaries",
          sorted(summaries) == ["a.py", "b.py"],
          f"got {sorted(summaries)}")

    degradations = ctx.scratch.get("degradations")
    check("degradations key present after mid-run failure",
          isinstance(degradations, list),
          f"got {degradations!r}")
    if not isinstance(degradations, list):
        return
    check("exactly one degradation entry",
          len(degradations) == 1, f"got {len(degradations)}")
    if len(degradations) != 1:
        return
    entry = degradations[0]
    check('entry["component"] == "llm_enrich"',
          entry.get("component") == "llm_enrich",
          f"got {entry.get('component')!r}")
    check('entry["reason"] == "client_failure_self_disabled"',
          entry.get("reason") == "client_failure_self_disabled",
          f"got {entry.get('reason')!r}")
    check('entry["kind"] == "file_summary"',
          entry.get("kind") == "file_summary",
          f"got {entry.get('kind')!r}")
    check("skipped == remaining eligible count (3: c.py, d.py, e.py)",
          entry.get("skipped") == 3, f"got {entry.get('skipped')}")
    check("error carries the first failure string",
          isinstance(entry.get("error"), str)
          and entry["error"].startswith("connect: connection refused"),
          f"got {entry.get('error')!r}")

    check("no client calls after self-disable (log-once + skip stays)",
          client.calls == 3, f"got {client.calls}")
    check("exactly one warning logged",
          len(warnings) == 1, f"got {len(warnings)}")


def test_healthy_client_adds_no_degradations_key() -> None:
    client = StubClient(healthy_calls=10 ** 6)
    records = fixture_records()
    ctx = make_ctx(records)
    warnings = run_with_warning_capture(make_enricher(client), records, ctx)

    check("healthy run: all eligible files summarized",
          len(ctx.scratch.get("llm:file_summary", {})) == N_ELIGIBLE,
          f"got {len(ctx.scratch.get('llm:file_summary', {}))}")
    check("healthy run: no degradations key added",
          "degradations" not in ctx.scratch,
          f"got {ctx.scratch.get('degradations')!r}")
    check("healthy run: no warnings logged",
          len(warnings) == 0, f"got {len(warnings)}")


def test_noop_scopes_add_no_degradations_key() -> None:
    # Empty scopes → the enricher is a no-op; even a dead client must
    # leave scratch untouched because chat() is never attempted.
    client = StubClient(healthy_calls=0, exc=OllamaUnreachable("dead"))
    records = fixture_records()
    ctx = make_ctx(records)
    drive(make_enricher(client, scopes=()), records, ctx)

    check("no-op scopes: client never called", client.calls == 0,
          f"got {client.calls}")
    check("no-op scopes: no degradations key added",
          "degradations" not in ctx.scratch,
          f"got {ctx.scratch.get('degradations')!r}")


def test_dead_from_first_call_registers_full_count() -> None:
    client = StubClient(
        healthy_calls=0,
        exc=OllamaUnreachable("connect: no route to host"),
    )
    records = fixture_records()
    ctx = make_ctx(records)
    warnings = run_with_warning_capture(make_enricher(client), records, ctx)

    check("dead client: no summaries written",
          "llm:file_summary" not in ctx.scratch,
          f"got {sorted(ctx.scratch.get('llm:file_summary', {}))}")
    degradations = ctx.scratch.get("degradations")
    check("dead client: exactly one degradation entry",
          isinstance(degradations, list) and len(degradations) == 1,
          f"got {degradations!r}")
    if not (isinstance(degradations, list) and len(degradations) == 1):
        return
    entry = degradations[0]
    check("dead client: skipped == full eligible count",
          entry.get("skipped") == N_ELIGIBLE,
          f"got {entry.get('skipped')}")
    check("dead client: reason is client_failure_self_disabled",
          entry.get("reason") == "client_failure_self_disabled",
          f"got {entry.get('reason')!r}")
    check("dead client: exactly one warning logged",
          len(warnings) == 1, f"got {len(warnings)}")
    check("dead client: only the first call was attempted",
          client.calls == 1, f"got {client.calls}")


def test_model_missing_takes_same_path() -> None:
    client = StubClient(
        healthy_calls=1,
        exc=OllamaModelMissing("model 'stub-model:1b' not found"),
    )
    records = fixture_records()
    ctx = make_ctx(records)
    drive(make_enricher(client), records, ctx)

    degradations = ctx.scratch.get("degradations")
    check("model-missing: exactly one degradation entry",
          isinstance(degradations, list) and len(degradations) == 1,
          f"got {degradations!r}")
    if not (isinstance(degradations, list) and len(degradations) == 1):
        return
    entry = degradations[0]
    check("model-missing: same reason literal",
          entry.get("reason") == "client_failure_self_disabled",
          f"got {entry.get('reason')!r}")
    check("model-missing: skipped == 4 (b..e minus the 1 healthy)",
          entry.get("skipped") == 4, f"got {entry.get('skipped')}")
    check("model-missing: error mentions the model",
          isinstance(entry.get("error"), str)
          and "stub-model:1b" in entry["error"],
          f"got {entry.get('error')!r}")


def test_error_string_is_truncated() -> None:
    long_msg = "x" * 1000
    client = StubClient(healthy_calls=0, exc=OllamaUnreachable(long_msg))
    records = fixture_records()
    ctx = make_ctx(records)
    drive(make_enricher(client), records, ctx)

    degradations = ctx.scratch.get("degradations")
    if not (isinstance(degradations, list) and len(degradations) == 1):
        check("truncation: degradation entry present", False,
              f"got {degradations!r}")
        return
    err = degradations[0].get("error")
    check("truncation: error capped at the excerpt budget",
          isinstance(err, str) and len(err) == ERROR_EXCERPT_BUDGET
          and set(err) == {"x"},
          f"got len={len(err) if isinstance(err, str) else 'N/A'}")


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------


def main() -> int:
    global FAIL
    tests = [
        test_failure_mid_run_registers_degradation,
        test_healthy_client_adds_no_degradations_key,
        test_noop_scopes_add_no_degradations_key,
        test_dead_from_first_call_registers_full_count,
        test_model_missing_takes_same_path,
        test_error_string_is_truncated,
    ]
    for t in tests:
        print(f"--- {t.__name__} ---")
        try:
            t()
        except Exception:
            FAIL += 1
            print(f"  FAIL  {t.__name__} (unexpected exception)")
            traceback.print_exc()

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

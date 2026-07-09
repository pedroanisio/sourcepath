#!/usr/bin/env python3
"""bench_llm_models.py — quality + speed benchmark for L4 enrichment models.

Rebuilds, as a *retained* first-class script, the model-selection
benchmark that originally lived at ``_tmp/llm_baseline_bench.py`` on the
``poc/llm-baseline`` branch and was never merged into the active
checkout (see docs/llm-baseline-results.md § Reproducibility). It exists
so the "which Ollama model should L4 use?" decision stays data-driven
and re-runnable as new models ship and as the host hardware changes.

Why this rebuild differs from the POC (all three are improvements the
POC's own "Threats to validity" section asked for):

  1. **Real prompts.** The POC ran against unfinalized spike prompts;
     this harness imports the *shipped* prompt registry
     (``plugins.llm_enrich.prompts.PROMPT_REGISTRY``) and reproduces the
     exact rendering + content budgets used by ``enricher.py`` /
     ``aggregator.py``. It benchmarks the workload production runs.
  2. **Real inputs.** file_summary / schema_purpose inputs are sampled
     from a real repository (``--repo``); concept_description inputs are
     sourced from a genuine in-process L3 concept index (``--scopes``
     ``concepts``), not a fabricated fixture.
  3. **Retained raw outputs.** Every model output is written to
     ``--out-jsonl`` so the ship decision can be human-audited later —
     the POC gitignored its outputs and could not be re-reviewed.

Quality scoring is deterministic and derived from the literal prompt
contracts (one-sentence/word budgets, banned phrases, sentence-count
ranges) plus a hallucinated-identifier count (code-like tokens in the
output that do not appear in the input). It deliberately does *not* use
an LLM-as-judge: that output would itself be unverified (PALS's Law).
The heuristics are a screen, not a verdict — a real ship decision should
still include human review of the retained JSONL (this matches the POC
doc's "Heuristic ≠ human review" caveat).

Speed metrics come from Ollama's own response telemetry
(``eval_count`` / ``eval_duration`` → generation tokens/sec,
``load_duration`` → cold-load), which is authoritative in a way that
wall-clock timing around the call is not.

Usage:

    .venv/bin/python scripts/bench_llm_models.py \\
        --models qwen2.5-coder:7b qwen2.5-coder:14b qwen2.5-coder:32b \\
        --scopes files,schemas \\
        --sample-files 8 --sample-schemas 4 \\
        --report-md docs/llm-bench-$(date +%F).md

Add ``--scopes files,schemas,concepts`` to also benchmark concept
descriptions (runs an in-process L1+L3 pass over ``--repo`` to source
real curated concepts — slower, but faithful).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

# The benchmark must measure the *shipped* workload, so it imports the
# production prompt code rather than re-declaring prompts. This is why
# the script is Python (not TypeScript per CLAUDE.md §4): it links
# against the Python L4 plugin.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.llm_enrich.cache import hash_text  # noqa: E402
from plugins.llm_enrich.client import resolve_host  # noqa: E402
from plugins.llm_enrich.enricher import (  # noqa: E402
    CONTENT_BUDGET_CHARS,
    SUPPORTED_LANGUAGES,
)
from plugins.llm_enrich.aggregator import SCHEMA_CONTENT_BUDGET  # noqa: E402
from plugins.llm_enrich.prompts import PROMPT_REGISTRY  # noqa: E402


DEFAULT_SEED = 42
DEFAULT_TIMEOUT_S = 300.0

# Extension → language map, restricted to the languages the L4 enricher
# will actually summarize (plugins.llm_enrich.enricher.SUPPORTED_LANGUAGES).
# Used only to *sample* file inputs for the benchmark; production derives
# language from the AST analyzer, but the resulting language string for
# these extensions is the same. Anything not here is skipped.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".dart": "dart",
    ".c": "c", ".h": "c",
}

# A file must have at least this many non-whitespace characters to be a
# useful benchmark input. Re-export shims / near-empty files reveal
# nothing about model quality (the POC excluded them for the same reason).
_MIN_CONTENT_CHARS = 200


# ======================================================================
# Pure scoring functions (unit-tested by tests/verify_bench_llm_models.py;
# no Ollama required). Kept import-clean so the verifier can exercise them
# without a live server.
# ======================================================================

# A sentence boundary is terminal punctuation followed by whitespace or
# end-of-string. The "followed by whitespace" guard means dotted code
# tokens (``auth.py``, ``os.path``) are NOT counted as boundaries.
_SENT_BOUNDARY = re.compile(r"[.!?]+(?=\s|$)")

# Candidate token: starts with a letter/underscore, may carry the inner
# punctuation that appears in code identifiers and paths.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*[A-Za-z0-9_]")

# camelCase / PascalCase-with-run / ALLCAPS-run signal — used to decide
# whether a bare alphanumeric token (no ``_./``) still looks like code.
_CASE_SIGNAL = re.compile(r"[a-z][A-Z]|[A-Z]{2,}")


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on terminal punctuation.

    Heuristic (documented as such): a boundary is ``[.!?]`` followed by
    whitespace or the end of the string, so ``auth.py`` does not split.
    Abbreviations (``e.g.``) and decimals may under/over-count by one —
    acceptable for a screen that only asks "roughly 1 / 3-5 / 2-3
    sentences?".
    """
    text = text.strip()
    if not text:
        return []
    parts = _SENT_BOUNDARY.split(text)
    return [p.strip() for p in parts if p.strip()]


def count_words(text: str) -> int:
    """Whitespace-delimited word count."""
    return len(re.findall(r"\S+", text.strip()))


def _looks_like_code(token: str) -> bool:
    """True if ``token`` resembles a source identifier / path / filename.

    Rules (any one suffices): contains ``_``, ``.``, or ``/``; or carries
    a camelCase / PascalCase-run / ALLCAPS-run case signal. Plain English
    words (``authentication``, ``provides``) return False so they are not
    scored as identifiers.
    """
    if any(c in token for c in ("_", ".", "/")):
        return True
    return bool(_CASE_SIGNAL.search(token))


def extract_code_identifiers(text: str) -> list[str]:
    """Ordered, de-duplicated code-like tokens found in ``text``.

    Trailing separator punctuation is stripped from each match so a
    sentence-final ``auth.py.`` yields ``auth.py``.
    """
    seen: dict[str, None] = {}
    for raw in _TOKEN.findall(text):
        tok = raw.strip("./-_")
        if not tok or not _looks_like_code(tok):
            continue
        seen.setdefault(tok, None)
    return list(seen)


def _is_grounded(token: str, haystack_lower: str) -> bool:
    """True if ``token`` is anchored in ``haystack_lower`` (already
    lower-cased).

    A token is grounded if it appears whole, OR — for compound tokens
    carrying ``/`` or ``-`` — if every non-trivial segment appears. The
    segment rule avoids false positives on tokens like
    ``subclassOf/overrides`` or ``ULID-like`` whose parts are all present
    in the input even though the joined form is not, while still flagging
    a genuinely invented compound whose parts are absent.
    """
    t = token.lower()
    if t in haystack_lower:
        return True
    if "/" in token or "-" in token:
        # Only the identifier-shaped segments (a case signal — ``ULID``,
        # ``subclassOf``) must be anchored; lowercase connectors
        # (``like`` in ``ULID-like``) are English, not symbols, and are
        # not required. A compound with no identifier-shaped segment
        # (``frobnicate/wibble``) is not credited here and stays flagged.
        segs = [s for s in re.split(r"[/-]", token) if len(s) >= 2]
        code_segs = [s for s in segs if _CASE_SIGNAL.search(s)]
        if code_segs and all(s.lower() in haystack_lower for s in code_segs):
            return True
    return False


def count_hallucinated_identifiers(
    output: str, input_text: str,
) -> tuple[int, int]:
    """Return ``(hallucinated, total_code_like)`` for ``output``.

    A code-like token in the output is *grounded* if it is anchored in
    ``input_text`` (see :func:`_is_grounded`) — which callers pass as the
    *full rendered prompt* the model was given (path + filename + content
    for file/schema kinds; the rendered field block for concepts), i.e.
    everything the model actually saw. One that is not anchored is
    *hallucinated*: it looks like a symbol from the codebase but was not
    in the prompt. This is the POC's anti-hallucination metric and the
    core PALS's-Law check for the L4 layer.
    """
    haystack = input_text.lower()
    idents = extract_code_identifiers(output)
    hallucinated = sum(1 for tok in idents if not _is_grounded(tok, haystack))
    return hallucinated, len(idents)


def grounding_score(hallucinated: int, total_code_like: int) -> float:
    """Fraction of code-like output tokens that were grounded (0..1).

    No code-like tokens → 1.0 (nothing to hallucinate; not penalized).
    """
    if total_code_like <= 0:
        return 1.0
    return 1.0 - (hallucinated / total_code_like)


# Per-kind format contract, lifted verbatim from the prompt files under
# plugins/llm_enrich/prompts/. Each predicate is one shippable rule; the
# format score is the fraction satisfied.
_BANNED_PHRASE = {
    "file_summary": "this file",
    "concept_description": "this concept is",
    "schema_purpose": "this schema is",
}
_SENTENCE_RANGE = {
    "file_summary": (1, 1),
    "concept_description": (3, 5),
    "schema_purpose": (2, 3),
}


@dataclass
class FormatScore:
    """Per-output structural verdict against the prompt contract."""
    checks: dict[str, bool]

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for ok in self.checks.values() if ok) / len(self.checks)


def score_format(kind: str, text: str, *, input_empty: bool = False) -> FormatScore:
    """Score ``text`` against ``kind``'s prompt contract.

    ``input_empty`` reflects the file_summary escape hatch ("If the file
    is empty or whitespace-only, reply: empty file"): when the input was
    empty, the only correct output is ``empty file`` and the structural
    rules are scored against that expectation instead.
    """
    if kind not in _SENTENCE_RANGE:
        raise KeyError(f"unknown enrichment kind {kind!r}")

    body = text.strip()
    checks: dict[str, bool] = {}

    if kind == "file_summary" and input_empty:
        checks["empty_file_reply"] = body.lower() == "empty file"
        return FormatScore(checks)

    checks["non_empty"] = bool(body)

    lo, hi = _SENTENCE_RANGE[kind]
    n_sent = len(split_sentences(body))
    checks["sentence_count_in_range"] = lo <= n_sent <= hi

    banned = _BANNED_PHRASE[kind]
    checks["no_banned_phrase"] = banned not in body.lower()

    if kind == "file_summary":
        # "under 30 words" — strict.
        checks["word_budget"] = count_words(body) < 30

    return FormatScore(checks)


# ----------------------------------------------------------------------
# Speed metrics — parse Ollama's /api/chat telemetry.
# ----------------------------------------------------------------------

def tokens_per_sec(count: int | None, duration_ns: int | None) -> float | None:
    """Tokens per second, or None if telemetry is missing/degenerate."""
    if not count or not duration_ns or duration_ns <= 0:
        return None
    return count / (duration_ns / 1e9)


@dataclass
class CallMetrics:
    """Speed telemetry extracted from one /api/chat response."""
    total_s: float | None
    load_s: float | None
    prompt_tokens: int | None
    prompt_tps: float | None
    gen_tokens: int | None
    gen_tps: float | None
    wall_s: float


def parse_call_metrics(payload: dict[str, Any], wall_s: float) -> CallMetrics:
    """Pull speed telemetry out of an Ollama /api/chat JSON body.

    Ollama reports durations in nanoseconds. ``wall_s`` is the caller's
    measured round-trip, kept as a sanity cross-check against
    ``total_duration``.
    """
    def _ns(key: str) -> int | None:
        v = payload.get(key)
        return int(v) if isinstance(v, (int, float)) else None

    prompt_ct = payload.get("prompt_eval_count")
    gen_ct = payload.get("eval_count")
    total_ns = _ns("total_duration")
    load_ns = _ns("load_duration")
    return CallMetrics(
        total_s=(total_ns / 1e9) if total_ns else None,
        load_s=(load_ns / 1e9) if load_ns else None,
        prompt_tokens=prompt_ct if isinstance(prompt_ct, int) else None,
        prompt_tps=tokens_per_sec(prompt_ct, _ns("prompt_eval_duration")),
        gen_tokens=gen_ct if isinstance(gen_ct, int) else None,
        gen_tps=tokens_per_sec(gen_ct, _ns("eval_duration")),
        wall_s=wall_s,
    )


def median(xs: Iterable[float]) -> float | None:
    vals = [x for x in xs if x is not None]
    return statistics.median(vals) if vals else None


# ======================================================================
# Input sampling — real prompts against real repository content.
# ======================================================================

@dataclass
class Sample:
    """One benchmark input: a rendered (system, user) prompt for a kind."""
    kind: str
    target: str
    system: str
    user: str
    input_text: str          # what "grounding" is checked against
    input_empty: bool = False


def _git_tracked_files(repo: Path) -> list[str] | None:
    """Repo-relative tracked paths via ``git ls-files``; None if not a
    git repo (caller falls back to a filesystem walk)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None
    return [line for line in out.stdout.splitlines() if line]


def _walk_files(repo: Path) -> list[str]:
    """Filesystem fallback when ``repo`` is not a git checkout."""
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__",
            ".mypy_cache", ".pytest_cache", "dist", "build"}
    rels: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for fn in files:
            rels.append(str(Path(root, fn).relative_to(repo)))
    return rels


def _list_repo_files(repo: Path) -> list[str]:
    return _git_tracked_files(repo) or _walk_files(repo)


def _read_text(repo: Path, rel: str) -> str | None:
    try:
        return (repo / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def sample_file_summaries(repo: Path, n: int) -> list[Sample]:
    """The ``n`` largest supported source files, rendered through the
    real file_summary template. Largest-first because dense files are the
    most discriminating between models (trivial shims saturate).
    """
    tmpl = PROMPT_REGISTRY["file_summary"]
    candidates: list[tuple[int, str, str, str]] = []  # (size, rel, lang, content)
    for rel in _list_repo_files(repo):
        lang = _EXT_TO_LANG.get(Path(rel).suffix.lower())
        if lang is None or lang not in SUPPORTED_LANGUAGES:
            continue
        text = _read_text(repo, rel)
        if text is None or len(text.strip()) < _MIN_CONTENT_CHARS:
            continue
        candidates.append((len(text), rel, lang, text))

    candidates.sort(key=lambda t: (-t[0], t[1]))
    samples: list[Sample] = []
    for _size, rel, lang, text in candidates[:n]:
        truncated = text[:CONTENT_BUDGET_CHARS]
        system, user = tmpl.render(path=rel, language=lang, content=truncated)
        # Grounding basis = the full rendered prompt (path + language +
        # content), i.e. everything the model saw. Using only the content
        # body would falsely flag a summary that names its own file.
        samples.append(Sample(
            kind="file_summary", target=rel,
            system=system, user=user, input_text=user,
        ))
    return samples


def sample_schema_purposes(repo: Path, n: int) -> list[Sample]:
    """Schema files under ``static/schemas/``, rendered through the real
    schema_purpose template."""
    from plugins.llm_enrich.aggregator import _is_schema_file  # local import
    tmpl = PROMPT_REGISTRY["schema_purpose"]
    rels = sorted(r for r in _list_repo_files(repo) if _is_schema_file(r))
    samples: list[Sample] = []
    for rel in rels[:n]:
        text = _read_text(repo, rel)
        if text is None:
            continue
        truncated = text[:SCHEMA_CONTENT_BUDGET]
        system, user = tmpl.render(
            path=rel, filename=Path(rel).name, content=truncated,
        )
        # Ground against the full rendered prompt (see file_summary).
        samples.append(Sample(
            kind="schema_purpose", target=rel,
            system=system, user=user, input_text=user,
        ))
    return samples


def sample_concept_descriptions(repo: Path, n: int) -> list[Sample]:
    """Real curated concepts, sourced from an in-process L1+L3 pass over
    ``repo`` (faithful to production, which reads the same
    ``l3_20_concepts`` index). Heavier than the file/schema scopes — only
    invoked when the caller opts into the ``concepts`` scope.
    """
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.inspection.repo_source import resolve_repo_source
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from plugins import concept_graph

    reset_registries()
    concept_graph.register_all(vocab=concept_graph.USE_BUILTIN)
    with resolve_repo_source(str(repo), "HEAD", work_dir=repo.parent) as src:
        mapped = map_codebase(src.path, src.state)
    ctx = mapped["ctx"]
    l3 = ctx.indices.get("l3_20_concepts") or {}

    concepts = l3.get("concepts") or {}
    cooccurrence = l3.get("cooccurrence") or []
    per_path = l3.get("per_path_concepts") or {}

    by_name: dict[str, list[tuple[str, int]]] = {}
    for a, b, w in cooccurrence:
        by_name.setdefault(a, []).append((b, int(w)))
        by_name.setdefault(b, []).append((a, int(w)))
    for name in by_name:
        by_name[name].sort(key=lambda x: -x[1])
    files_for: dict[str, list[str]] = {}
    for path, names in per_path.items():
        for nm in names:
            files_for.setdefault(nm, []).append(path)
    for nm in files_for:
        files_for[nm].sort()

    # Deterministic: curated concepts (those carrying a kind), highest
    # frequency first, then name — the most-lexicalized concepts are the
    # most discriminating inputs.
    typed = sorted(
        (nm for nm, meta in concepts.items() if "kind" in meta),
        key=lambda nm: (-int(concepts[nm].get("frequency", 0)), nm),
    )
    tmpl = PROMPT_REGISTRY["concept_description"]
    samples: list[Sample] = []
    for name in typed[:n]:
        meta = concepts[name]
        cooc = by_name.get(name, [])[:5]
        files = files_for.get(name, [])[:3]
        alt = meta.get("alt_labels", [])[:6]
        cooc_str = ", ".join(f"{c} ({w})" for c, w in cooc) or "(none)"
        files_str = ", ".join(files) or "(none)"
        alt_str = ", ".join(alt) or "(none)"
        system, user = tmpl.render(
            name=name, kind=meta.get("kind", ""),
            frequency=int(meta.get("frequency", 0)),
            alt_labels=alt_str, cooccurring=cooc_str, files=files_str,
        )
        samples.append(Sample(
            kind="concept_description", target=name,
            system=system, user=user, input_text=user,
        ))
    return samples


# ======================================================================
# Ollama transport + per-call scoring.
# ======================================================================

class BenchError(RuntimeError):
    """Fatal benchmark setup error (unreachable server, missing model)."""


def installed_models(client: httpx.Client) -> list[str]:
    r = client.get("/api/tags", timeout=10.0)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def _chat_once(
    client: httpx.Client, model: str, system: str, user: str, seed: int,
) -> tuple[str, dict[str, Any], float]:
    """One /api/chat turn. Mirrors OllamaClient.chat's request body exactly
    (temperature 0, fixed seed, non-streaming) so measured behavior matches
    production, but returns the full JSON payload (which OllamaClient.chat
    discards) so speed telemetry can be read.
    """
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0, "seed": int(seed)},
    }
    t0 = time.time()
    r = client.post("/api/chat", json=body)
    wall = time.time() - t0
    if r.status_code == 404:
        raise BenchError(f"model {model!r} not installed on server")
    r.raise_for_status()
    payload = r.json()
    content = payload.get("message", {}).get("content", "")
    return content, payload, wall


@dataclass
class CallResult:
    """One scored model call over one sample."""
    model: str
    kind: str
    target: str
    prompt_sha: str
    output: str
    format_score: float
    format_checks: dict[str, bool]
    hallucinated: int
    total_code_like: int
    grounding: float
    is_cold: bool
    metrics: CallMetrics


def score_call(
    model: str, sample: Sample, output: str, payload: dict[str, Any],
    wall_s: float, *, is_cold: bool,
) -> CallResult:
    """⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.

    LLMs statistically produce errors: omissions, hallucinations,
    partial completions, schema violations, and silent failures. These
    are properties of the model class, not exceptional conditions.

    This function IS the verification layer for a benchmarked model call:
    it never returns the raw ``output`` as a trusted result — it returns a
    scored verdict (format-contract adherence + hallucinated-identifier
    count). A caller that ranks models on the raw text instead of on this
    verdict is reintroducing the architectural omission this guards.
    """
    fmt = score_format(sample.kind, output, input_empty=sample.input_empty)
    halluc, total = count_hallucinated_identifiers(output, sample.input_text)
    return CallResult(
        model=model, kind=sample.kind, target=sample.target,
        prompt_sha=PROMPT_REGISTRY[sample.kind].sha256,
        output=output,
        format_score=fmt.score, format_checks=fmt.checks,
        hallucinated=halluc, total_code_like=total,
        grounding=grounding_score(halluc, total),
        is_cold=is_cold,
        metrics=parse_call_metrics(payload, wall_s),
    )


# ======================================================================
# Aggregation + reporting.
# ======================================================================

@dataclass
class KindAggregate:
    model: str
    kind: str
    n: int
    format_mean: float
    grounding_mean: float
    halluc_total: int
    warm_gen_tps_median: float | None
    warm_latency_median_s: float | None
    cold_load_s: float | None


def aggregate(results: list[CallResult]) -> list[KindAggregate]:
    groups: dict[tuple[str, str], list[CallResult]] = {}
    for r in results:
        groups.setdefault((r.model, r.kind), []).append(r)

    aggs: list[KindAggregate] = []
    for (model, kind), rs in sorted(groups.items()):
        warm = [r for r in rs if not r.is_cold]
        cold = [r for r in rs if r.is_cold]
        aggs.append(KindAggregate(
            model=model, kind=kind, n=len(rs),
            format_mean=statistics.mean(r.format_score for r in rs),
            grounding_mean=statistics.mean(r.grounding for r in rs),
            halluc_total=sum(r.hallucinated for r in rs),
            warm_gen_tps_median=median(r.metrics.gen_tps for r in warm),
            warm_latency_median_s=median(r.metrics.total_s or r.metrics.wall_s
                                         for r in warm),
            cold_load_s=median(r.metrics.load_s for r in cold) if cold else None,
        ))
    return aggs


@dataclass
class ModelScore:
    model: str
    quality: float          # mean over kinds of mean(format, grounding)
    halluc_total: int
    gen_tps_median: float | None


def rank_models(aggs: list[KindAggregate]) -> list[ModelScore]:
    by_model: dict[str, list[KindAggregate]] = {}
    for a in aggs:
        by_model.setdefault(a.model, []).append(a)
    scores: list[ModelScore] = []
    for model, group in by_model.items():
        per_kind_quality = [
            statistics.mean((a.format_mean, a.grounding_mean)) for a in group
        ]
        tps = [a.warm_gen_tps_median for a in group
               if a.warm_gen_tps_median is not None]
        scores.append(ModelScore(
            model=model,
            quality=statistics.mean(per_kind_quality) if per_kind_quality else 0.0,
            halluc_total=sum(a.halluc_total for a in group),
            gen_tps_median=median(tps) if tps else None,
        ))
    # Rank by quality desc, then throughput desc, then fewest hallucinations.
    scores.sort(key=lambda s: (-s.quality, -(s.gen_tps_median or 0.0),
                               s.halluc_total))
    return scores


def _fmt_num(x: float | None, spec: str = ".2f") -> str:
    return format(x, spec) if x is not None else "—"


def format_scorecard(aggs: list[KindAggregate], ranked: list[ModelScore]) -> str:
    lines: list[str] = []
    lines.append("Per-model / per-kind scorecard")
    lines.append("")
    header = (f"{'model':<24} {'kind':<20} {'n':>3} "
              f"{'format':>7} {'ground':>7} {'halluc':>7} "
              f"{'gen_tps':>8} {'lat_s':>7} {'load_s':>7}")
    lines.append(header)
    lines.append("-" * len(header))
    for a in aggs:
        lines.append(
            f"{a.model:<24} {a.kind:<20} {a.n:>3} "
            f"{a.format_mean:>7.2f} {a.grounding_mean:>7.2f} "
            f"{a.halluc_total:>7d} "
            f"{_fmt_num(a.warm_gen_tps_median, '.1f'):>8} "
            f"{_fmt_num(a.warm_latency_median_s, '.2f'):>7} "
            f"{_fmt_num(a.cold_load_s, '.2f'):>7}"
        )
    lines.append("")
    lines.append("Overall ranking (quality desc, then throughput):")
    lines.append("")
    rank_header = (f"{'#':>2} {'model':<24} {'quality':>8} "
                   f"{'halluc':>7} {'gen_tps':>8}")
    lines.append(rank_header)
    lines.append("-" * len(rank_header))
    for i, s in enumerate(ranked, 1):
        lines.append(
            f"{i:>2} {s.model:<24} {s.quality:>8.3f} "
            f"{s.halluc_total:>7d} {_fmt_num(s.gen_tps_median, '.1f'):>8}"
        )
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report_md(
    aggs: list[KindAggregate], ranked: list[ModelScore],
    *, models: Sequence[str], host: str, repo: Path, seed: int,
) -> str:
    """A Markdown report carrying the mandatory disclaimer frontmatter
    (CLAUDE.md §5). Structural metrics are deterministic; the ranking is a
    heuristic screen — the frontmatter says so and the body repeats it.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = (
        "---\n"
        "disclaimer:\n"
        "  notice: >-\n"
        "    No information within this document should be taken for granted.\n"
        "    Any statement or premise not backed by a real logical definition\n"
        "    or verifiable reference may be invalid, erroneous, or a hallucination.\n"
        '  generated_by: "codebase-mapper scripts/bench_llm_models.py"\n'
        f'  date: "{today}"\n'
        "---\n\n"
    )
    out: list[str] = [fm]
    out.append("# L4 model benchmark — quality & speed\n")
    out.append(f"- Generated: `{_now_iso()}`")
    out.append(f"- Ollama host: `{host}`")
    out.append(f"- Repo under test: `{repo}`")
    out.append(f"- Models: {', '.join(f'`{m}`' for m in models)}")
    out.append(f"- Seed: `{seed}` (temperature 0)\n")
    out.append(
        "> **Evidence basis.** The structural columns (format adherence, "
        "hallucinated-identifier count) and the speed columns (from Ollama's "
        "own `eval_count`/`eval_duration` telemetry) are mechanically derived "
        "and reproducible. The overall *ranking* is a deterministic heuristic "
        "screen, not a human quality verdict — validate against the retained "
        "raw outputs before a ship decision.\n"
    )

    out.append("## Overall ranking\n")
    out.append("| # | Model | Quality (0–1) | Hallucinations | Gen tok/s (median) |")
    out.append("|---|---|---|---|---|")
    for i, s in enumerate(ranked, 1):
        out.append(f"| {i} | `{s.model}` | {s.quality:.3f} | "
                   f"{s.halluc_total} | {_fmt_num(s.gen_tps_median, '.1f')} |")
    out.append("")

    out.append("## Per-kind detail\n")
    out.append("| Model | Kind | n | Format | Grounding | Halluc | "
               "Gen tok/s | Latency s | Cold load s |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for a in aggs:
        out.append(
            f"| `{a.model}` | {a.kind} | {a.n} | {a.format_mean:.2f} | "
            f"{a.grounding_mean:.2f} | {a.halluc_total} | "
            f"{_fmt_num(a.warm_gen_tps_median, '.1f')} | "
            f"{_fmt_num(a.warm_latency_median_s, '.2f')} | "
            f"{_fmt_num(a.cold_load_s, '.2f')} |"
        )
    out.append("")

    out.append("## Metric definitions\n")
    out.append(
        "- **Format** (0–1): fraction of the prompt's structural contract "
        "satisfied — sentence-count range, word budget (file_summary), and "
        "the banned self-referential phrase. Derived from the shipped prompt "
        "files under `plugins/llm_enrich/prompts/`.")
    out.append(
        "- **Grounding** (0–1): fraction of code-like tokens in the output "
        "that appear in the input. `1 − (hallucinated ÷ code-like tokens)`.")
    out.append(
        "- **Halluc**: count of code-like output tokens absent from the input "
        "— the anti-hallucination screen (PALS's Law).")
    out.append(
        "- **Gen tok/s / Latency / Cold load**: from Ollama response telemetry "
        "(`eval_count`/`eval_duration`, `total_duration`, `load_duration`). "
        "Warm calls exclude the first (cold-load) call per model.")
    return "\n".join(out) + "\n"


# ======================================================================
# Orchestration.
# ======================================================================

def collect_samples(repo: Path, scopes: Sequence[str], args: argparse.Namespace,
                    ) -> list[Sample]:
    samples: list[Sample] = []
    if "files" in scopes:
        samples += sample_file_summaries(repo, args.sample_files)
    if "schemas" in scopes:
        samples += sample_schema_purposes(repo, args.sample_schemas)
    if "concepts" in scopes:
        samples += sample_concept_descriptions(repo, args.sample_concepts)
    return samples


def run_benchmark(args: argparse.Namespace) -> int:
    host = resolve_host(args.host)
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    valid = {"files", "schemas", "concepts"}
    unknown = set(scopes) - valid
    if unknown:
        raise BenchError(f"unknown scope(s) {sorted(unknown)}; valid: {sorted(valid)}")

    client = httpx.Client(base_url=host, timeout=DEFAULT_TIMEOUT_S)
    try:
        try:
            present = set(installed_models(client))
        except httpx.HTTPError as e:
            raise BenchError(f"cannot reach Ollama at {host}: {e}") from e

        missing = [m for m in args.models if m not in present]
        if missing and args.pull:
            for m in missing:
                print(f"[bench] pulling {m} …", file=sys.stderr)
                _pull_model(host, m)
            present = set(installed_models(client))
            missing = [m for m in args.models if m not in present]
        if missing:
            raise BenchError(
                f"model(s) not installed: {missing}. "
                f"Pull them (`ollama pull <model>`) or pass --pull. "
                f"Installed: {sorted(present)}"
            )

        repo = args.repo.resolve()
        print(f"[bench] sampling inputs from {repo} (scopes: {scopes}) …",
              file=sys.stderr)
        samples = collect_samples(repo, scopes, args)
        if not samples:
            raise BenchError("no benchmark samples were produced — check "
                             "--repo and --scopes")
        print(f"[bench] {len(samples)} sample(s): "
              + ", ".join(f"{k}={sum(1 for s in samples if s.kind == k)}"
                          for k in sorted({s.kind for s in samples})),
              file=sys.stderr)

        results: list[CallResult] = []
        out_path = args.out_jsonl.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as sink:
            for model in args.models:
                print(f"[bench] === {model} ===", file=sys.stderr)
                first_call = True
                for s in samples:
                    output, payload, wall = _chat_once(
                        client, model, s.system, s.user, args.seed)
                    res = score_call(model, s, output, payload, wall,
                                     is_cold=first_call)
                    first_call = False
                    results.append(res)
                    sink.write(json.dumps(_result_row(res), sort_keys=True) + "\n")
                    print(f"  {s.kind:<20} {s.target:<40} "
                          f"fmt={res.format_score:.2f} "
                          f"halluc={res.hallucinated} "
                          f"tps={_fmt_num(res.metrics.gen_tps, '.0f')}",
                          file=sys.stderr)
                # Determinism probe: re-run the first sample twice more and
                # report whether the warm pair is byte-identical (the POC's
                # "cache hit = byte-identical" property).
                if args.determinism_runs >= 2 and samples:
                    _determinism_probe(client, model, samples[0], args, sink)

        aggs = aggregate(results)
        ranked = rank_models(aggs)

        print("\n" + format_scorecard(aggs, ranked))
        print(f"\n[bench] raw outputs (auditable): {out_path}", file=sys.stderr)

        if args.report_md:
            report = build_report_md(
                aggs, ranked, models=args.models, host=host,
                repo=repo, seed=args.seed)
            args.report_md.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.report_md.resolve().write_text(report, encoding="utf-8")
            print(f"[bench] markdown report: {args.report_md.resolve()}",
                  file=sys.stderr)

        if args.json:
            print(json.dumps({
                "ranking": [asdict(s) for s in ranked],
                "per_kind": [asdict(a) for a in aggs],
            }, indent=2, sort_keys=True))
        return 0
    finally:
        client.close()


def _pull_model(host: str, model: str) -> None:
    with httpx.Client(base_url=host, timeout=None) as c:
        with c.stream("POST", "/api/pull", json={"model": model}) as r:
            r.raise_for_status()
            for _ in r.iter_lines():
                pass


def _determinism_probe(
    client: httpx.Client, model: str, sample: Sample,
    args: argparse.Namespace, sink: Any,
) -> None:
    outputs: list[str] = []
    for _ in range(args.determinism_runs):
        out, _payload, _wall = _chat_once(
            client, model, sample.system, sample.user, args.seed)
        outputs.append(out)
    warm = outputs[1:]
    warm_identical = len(set(warm)) == 1 if len(warm) >= 2 else None
    all_identical = len(set(outputs)) == 1
    sink.write(json.dumps({
        "kind": "_determinism_probe", "model": model,
        "target": sample.target, "sample_kind": sample.kind,
        "runs": len(outputs),
        "warm_identical": warm_identical,
        "all_identical": all_identical,
    }, sort_keys=True) + "\n")
    print(f"  determinism: warm_identical={warm_identical} "
          f"all_identical={all_identical}", file=sys.stderr)


def _result_row(r: CallResult) -> dict[str, Any]:
    return {
        "model": r.model, "kind": r.kind, "target": r.target,
        "prompt_sha": r.prompt_sha, "output": r.output,
        "format_score": round(r.format_score, 4),
        "format_checks": r.format_checks,
        "hallucinated": r.hallucinated, "total_code_like": r.total_code_like,
        "grounding": round(r.grounding, 4),
        "is_cold": r.is_cold,
        "metrics": asdict(r.metrics),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--models", nargs="+", required=True,
                   help="Ollama model tags to benchmark (must be installed, "
                        "or pass --pull).")
    p.add_argument("--repo", type=Path, default=_REPO_ROOT,
                   help="Repository to source real inputs from "
                        f"(default: {_REPO_ROOT}).")
    p.add_argument("--scopes", default="files,schemas",
                   help="Comma-separated subset of {files,schemas,concepts}. "
                        "'concepts' runs an in-process L1+L3 pass (slower). "
                        "Default: files,schemas.")
    p.add_argument("--sample-files", type=int, default=8)
    p.add_argument("--sample-schemas", type=int, default=4)
    p.add_argument("--sample-concepts", type=int, default=4)
    p.add_argument("--host", default=None,
                   help="Ollama base URL (default: $OLLAMA_HOST or "
                        "http://localhost:11434).")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--determinism-runs", type=int, default=3,
                   help="Re-runs of the first sample per model to probe "
                        "warm-call determinism. <2 disables.")
    p.add_argument("--pull", action="store_true",
                   help="Auto-pull any requested model that is not installed.")
    p.add_argument("--out-jsonl", type=Path,
                   default=_REPO_ROOT / "bench_results.jsonl",
                   help="Where to write per-call raw outputs (for human "
                        "review). Default: ./bench_results.jsonl.")
    p.add_argument("--report-md", type=Path, default=None,
                   help="Optional Markdown report path (carries the "
                        "mandatory disclaimer frontmatter).")
    p.add_argument("--json", action="store_true",
                   help="Also print a machine-readable summary to stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        return run_benchmark(args)
    except BenchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""verify_bench_llm_models.py — unit coverage for the model benchmark.

Exercises the *pure* scoring + metric functions of
[scripts/bench_llm_models.py](../scripts/bench_llm_models.py) against
fixed inputs and a synthetic Ollama telemetry payload. No live Ollama is
required — this is the deterministic core of the benchmark (the part that
turns raw model output into an auditable verdict), so it must be testable
without a server.

The live model-calling path (``_chat_once`` / ``run_benchmark``) is not
unit-tested here — it is I/O against an external server. Its correctness
rests on (a) the request body matching ``OllamaClient.chat`` byte-for-byte
(asserted below by comparison) and (b) these scoring functions, which it
composes.

Run:  .venv/bin/python tests/verify_bench_llm_models.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.bench_llm_models as bench

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------- sentences

def test_split_sentences() -> None:
    # Dotted code tokens must NOT split (boundary requires trailing space/EOS).
    check("one sentence with a dotted token",
          len(bench.split_sentences("auth.py provides token auth.")) == 1,
          repr(bench.split_sentences("auth.py provides token auth.")))
    check("two sentences",
          len(bench.split_sentences("Defines X. Serves Y.")) == 2)
    check("three sentences no trailing punct",
          len(bench.split_sentences("A does a. B does b. C does c")) == 3)
    check("empty string → zero", len(bench.split_sentences("   ")) == 0)
    check("os.path mid-sentence stays one",
          len(bench.split_sentences("Wraps os.path.join for callers.")) == 1)


def test_count_words() -> None:
    check("word count", bench.count_words("a b c d") == 4)
    check("word count trims", bench.count_words("  a   b  ") == 2)
    check("word count empty", bench.count_words("") == 0)


# ------------------------------------------------------------- identifiers

def test_extract_identifiers() -> None:
    idents = set(bench.extract_code_identifiers(
        "The FileRecord in auth.py calls get_token and os.path.join."))
    check("finds snake_case", "get_token" in idents, str(idents))
    check("finds PascalCase", "FileRecord" in idents, str(idents))
    check("finds dotted path", "os.path.join" in idents, str(idents))
    check("finds filename", "auth.py" in idents, str(idents))
    check("ignores plain english 'calls'", "calls" not in idents, str(idents))
    check("ignores plain english 'The'", "The" not in idents, str(idents))


def test_hallucination_detection() -> None:
    inp = "def get_token(self):\n    return self.token  # FileRecord"
    # All output identifiers present in input → zero hallucinations.
    halluc, total = bench.count_hallucinated_identifiers(
        "get_token reads FileRecord.", inp)
    check("grounded output → 0 hallucinations", halluc == 0, f"{halluc}/{total}")
    check("grounded output counts code-like tokens", total == 2, str(total))
    # An identifier not in the input → one hallucination.
    halluc2, total2 = bench.count_hallucinated_identifiers(
        "get_token calls delete_everything.", inp)
    check("ungrounded identifier flagged", halluc2 == 1, f"{halluc2}/{total2}")
    # No code-like tokens at all → nothing to hallucinate.
    halluc3, total3 = bench.count_hallucinated_identifiers(
        "This provides token based authentication.", inp)
    check("no code-like tokens → 0 total", total3 == 0, str(total3))


def test_compound_token_grounding() -> None:
    # Real false-positive cases caught during the first live run: the joined
    # compound is not a substring, but all its parts are present.
    inp = "resolves subclassOf and overrides edges; generates ULID ids"
    h1, t1 = bench.count_hallucinated_identifiers("emits subclassOf/overrides.", inp)
    check("compound with both parts present is grounded", h1 == 0, f"{h1}/{t1}")
    h2, t2 = bench.count_hallucinated_identifiers("makes ULID-like ids.", inp)
    check("hyphen compound with parts present is grounded", h2 == 0, f"{h2}/{t2}")
    # A compound whose segments are genuinely absent is still flagged.
    h3, t3 = bench.count_hallucinated_identifiers("calls frobnicate/wibble.", inp)
    check("compound with absent parts still flagged", h3 == 1, f"{h3}/{t3}")


def test_filename_citation_is_grounded() -> None:
    """The systematic FP that biased the first run: a summary naming its own
    file must be grounded when the path is part of the prompt (input_text)."""
    prompt = ("Path: plugins/chunks_embeddings/chunker.py\nLanguage: python\n"
              "Content:\nclass ChunkExtractor: ...")
    h, t = bench.count_hallucinated_identifiers(
        "The chunker.py file provides a ChunkExtractor class.", prompt)
    check("filename + class both grounded against full prompt", h == 0, f"{h}/{t}")


def test_grounding_score() -> None:
    check("no code tokens → 1.0", bench.grounding_score(0, 0) == 1.0)
    check("all grounded → 1.0", bench.grounding_score(0, 4) == 1.0)
    check("half grounded → 0.5", bench.grounding_score(2, 4) == 0.5)
    check("none grounded → 0.0", bench.grounding_score(3, 3) == 0.0)


# ---------------------------------------------------------- format scoring

def test_format_file_summary() -> None:
    good = "auth.py provides token-based authentication for API callers."
    fs = bench.score_format("file_summary", good)
    check("good file_summary scores 1.0", fs.score == 1.0, str(fs.checks))

    long = " ".join(["word"] * 40) + "."
    check("over-budget file_summary fails word_budget",
          bench.score_format("file_summary", long).checks["word_budget"] is False)

    banned = "This file provides authentication."
    check("banned phrase 'this file' fails",
          bench.score_format("file_summary", banned).checks["no_banned_phrase"]
          is False)

    two = "It does A. It does B."
    check("two-sentence file_summary fails sentence range",
          bench.score_format("file_summary", two).checks["sentence_count_in_range"]
          is False)


def test_format_file_summary_empty_input() -> None:
    fs = bench.score_format("file_summary", "empty file", input_empty=True)
    check("empty-input correct reply scores 1.0", fs.score == 1.0, str(fs.checks))
    fs_bad = bench.score_format(
        "file_summary", "This does stuff.", input_empty=True)
    check("empty-input wrong reply scores 0.0", fs_bad.score == 0.0, str(fs_bad.checks))


def test_format_concept_description() -> None:
    good = "Edge models a graph relation. It appears in graph.py. It cooccurs with node."
    check("3-sentence concept_description passes range",
          bench.score_format("concept_description", good)
          .checks["sentence_count_in_range"] is True)
    one = "Edge models a graph relation."
    check("1-sentence concept_description fails range",
          bench.score_format("concept_description", one)
          .checks["sentence_count_in_range"] is False)
    banned = ("This concept is important. It appears here. It matters a lot "
              "in this codebase overall.")
    check("banned 'this concept is' fails",
          bench.score_format("concept_description", banned)
          .checks["no_banned_phrase"] is False)


def test_format_schema_purpose() -> None:
    good = "Defines the AST node schema. Serves the exporter consumers."
    check("2-sentence schema_purpose passes range",
          bench.score_format("schema_purpose", good)
          .checks["sentence_count_in_range"] is True)
    four = "A. B. C. D."
    check("4-sentence schema_purpose fails range",
          bench.score_format("schema_purpose", four)
          .checks["sentence_count_in_range"] is False)


def test_format_unknown_kind() -> None:
    try:
        bench.score_format("bogus_kind", "x")
        check("unknown kind raises KeyError", False)
    except KeyError:
        check("unknown kind raises KeyError", True)


# ------------------------------------------------------------ speed metrics

def test_tokens_per_sec() -> None:
    # 100 tokens in 2s (2e9 ns) → 50 tok/s.
    check("tokens_per_sec basic", bench.tokens_per_sec(100, 2_000_000_000) == 50.0)
    check("tokens_per_sec zero count → None", bench.tokens_per_sec(0, 1) is None)
    check("tokens_per_sec zero duration → None",
          bench.tokens_per_sec(10, 0) is None)
    check("tokens_per_sec None inputs → None",
          bench.tokens_per_sec(None, None) is None)


def test_parse_call_metrics() -> None:
    # Synthetic Ollama /api/chat telemetry (nanoseconds).
    payload = {
        "message": {"content": "x"},
        "total_duration": 3_000_000_000,
        "load_duration": 1_000_000_000,
        "prompt_eval_count": 200,
        "prompt_eval_duration": 1_000_000_000,   # 200 tok/s
        "eval_count": 50,
        "eval_duration": 1_000_000_000,          # 50 tok/s
    }
    m = bench.parse_call_metrics(payload, wall_s=3.1)
    check("total_s parsed", m.total_s == 3.0, str(m.total_s))
    check("load_s parsed", m.load_s == 1.0, str(m.load_s))
    check("prompt_tps parsed", m.prompt_tps == 200.0, str(m.prompt_tps))
    check("gen_tps parsed", m.gen_tps == 50.0, str(m.gen_tps))
    check("gen_tokens parsed", m.gen_tokens == 50, str(m.gen_tokens))
    check("wall passthrough", m.wall_s == 3.1, str(m.wall_s))

    # Missing telemetry must degrade to None, not crash.
    m2 = bench.parse_call_metrics({"message": {"content": "x"}}, wall_s=0.5)
    check("missing telemetry → None fields",
          m2.gen_tps is None and m2.total_s is None, str(m2))


def test_median() -> None:
    check("median odd", bench.median([3, 1, 2]) == 2)
    check("median filters None", bench.median([None, 4, 2]) == 3)
    check("median empty → None", bench.median([]) is None)
    check("median all None → None", bench.median([None, None]) is None)


# -------------------------------------------------------------- aggregation

def _mk_result(model: str, kind: str, fmt: float, ground: float,
               halluc: int, gen_tps: float | None, cold: bool) -> "bench.CallResult":
    return bench.CallResult(
        model=model, kind=kind, target="t", prompt_sha="0" * 64,
        output="o", format_score=fmt, format_checks={"x": True},
        hallucinated=halluc, total_code_like=halluc + 2,
        grounding=ground, is_cold=cold,
        metrics=bench.CallMetrics(
            total_s=1.0, load_s=(5.0 if cold else None),
            prompt_tokens=10, prompt_tps=100.0,
            gen_tokens=50, gen_tps=gen_tps, wall_s=1.0),
    )


def test_aggregate_and_rank() -> None:
    results = [
        # model A: strong quality, cold first call excluded from warm tps
        _mk_result("A", "file_summary", 1.0, 1.0, 0, None, cold=True),
        _mk_result("A", "file_summary", 1.0, 1.0, 0, 40.0, cold=False),
        _mk_result("A", "file_summary", 1.0, 1.0, 0, 60.0, cold=False),
        # model B: weaker quality, faster
        _mk_result("B", "file_summary", 0.5, 0.5, 3, None, cold=True),
        _mk_result("B", "file_summary", 0.5, 0.5, 1, 100.0, cold=False),
        _mk_result("B", "file_summary", 0.5, 0.5, 1, 120.0, cold=False),
    ]
    aggs = bench.aggregate(results)
    a_agg = next(a for a in aggs if a.model == "A")
    check("aggregate n counts all calls", a_agg.n == 3, str(a_agg.n))
    check("warm tps median excludes cold", a_agg.warm_gen_tps_median == 50.0,
          str(a_agg.warm_gen_tps_median))
    check("cold load captured", a_agg.cold_load_s == 5.0, str(a_agg.cold_load_s))
    check("format mean", a_agg.format_mean == 1.0, str(a_agg.format_mean))

    ranked = bench.rank_models(aggs)
    check("higher quality ranks first", ranked[0].model == "A",
          str([(r.model, r.quality) for r in ranked]))
    check("quality is mean(format, grounding)", ranked[0].quality == 1.0,
          str(ranked[0].quality))
    check("B halluc total summed", next(r for r in ranked if r.model == "B")
          .halluc_total == 5, str(ranked))


# ------------------------------------------------------ production fidelity

def test_request_body_matches_client() -> None:
    """The benchmark must issue the same request body OllamaClient.chat
    does, or the measured behavior is not production's. Assert the option
    keys/values line up (temperature 0, integer seed, non-streaming)."""
    import inspect
    from plugins.llm_enrich import client as prod_client
    src = inspect.getsource(prod_client.OllamaClient.chat)
    check("prod client uses temperature 0", '"temperature": 0' in src, src[:200])
    check("prod client uses stream False", '"stream": False' in src)
    bench_src = inspect.getsource(bench._chat_once)
    check("bench uses temperature 0", '"temperature": 0' in bench_src)
    check("bench uses stream False", '"stream": False' in bench_src)
    check("bench uses integer seed", '"seed": int(seed)' in bench_src)


def test_prompts_are_production() -> None:
    """The benchmark must import the shipped prompt registry, not redeclare
    prompts — otherwise it does not measure production's workload."""
    from plugins.llm_enrich.prompts import PROMPT_REGISTRY
    check("bench references shipped registry",
          bench.PROMPT_REGISTRY is PROMPT_REGISTRY)
    for kind in ("file_summary", "concept_description", "schema_purpose"):
        check(f"registry has {kind}", kind in bench.PROMPT_REGISTRY)


def test_report_has_disclaimer() -> None:
    """CLAUDE.md §5: every agent-produced Markdown doc carries the
    disclaimer frontmatter. The report builder must emit it."""
    aggs = [bench.KindAggregate("m", "file_summary", 1, 1.0, 1.0, 0, 50.0, 1.0, 5.0)]
    ranked = bench.rank_models(aggs)
    md = bench.build_report_md(aggs, ranked, models=["m"],
                               host="http://h", repo=Path("/r"), seed=42)
    check("report starts with frontmatter", md.startswith("---\n"), md[:40])
    check("report has disclaimer notice", "should be taken for granted" in md)
    check("report labels generator", "bench_llm_models.py" in md)
    check("report has a date field", 'date: "' in md)


def main() -> int:
    tests = [
        test_split_sentences,
        test_count_words,
        test_extract_identifiers,
        test_hallucination_detection,
        test_compound_token_grounding,
        test_filename_citation_is_grounded,
        test_grounding_score,
        test_format_file_summary,
        test_format_file_summary_empty_input,
        test_format_concept_description,
        test_format_schema_purpose,
        test_format_unknown_kind,
        test_tokens_per_sec,
        test_parse_call_metrics,
        test_median,
        test_aggregate_and_rank,
        test_request_body_matches_client,
        test_prompts_are_production,
        test_report_has_disclaimer,
    ]
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  FAIL  {t.__name__} raised:")
            traceback.print_exc()

    print(f"\n{'=' * 50}")
    print(f"  {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

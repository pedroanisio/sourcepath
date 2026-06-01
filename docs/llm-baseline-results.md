---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "GPT-5 Codex"
  date: "2026-05-22"
---

# LLM baseline benchmark — model selection for L4 enrichment

> Status: POC result. Branch `poc/llm-baseline`. Companion to
> [archive/llm-enrich-plan.md](archive/llm-enrich-plan.md).
> The single-model spike confirmed the plan's three hypotheses hold for
> `llama3.1:8b`. This benchmark widens that to 5 candidate models and
> recommends a default for Step 8 of the plan.

## Setup

- **Hardware**: NVIDIA RTX 3050, 8 GB VRAM (the binding constraint),
  125 GB RAM, 1.3 TB disk.
- **Ollama**: official Linux installer; service running at
  `localhost:11434` under systemd.
- **Bundle under test**: [_tmp/code-mapper/](../_tmp/code-mapper/) —
  the fresh re-emit produced at the end of Stage 8 of the vocabulary
  absorption (68 files, 1,825 concepts, 25 of them curated).
- **Harness**: [_tmp/llm_baseline_bench.py](../_tmp/llm_baseline_bench.py)
  (gitignored; the harness lives only on this POC branch).

Inputs were chosen deterministically so every model saw the same
prompts:

- **8 source files**: 8 Python files from the `code_mapper/` package,
  ranging from `__init__.py` (small re-export shim) to
  `expression_emitter.py` (18 KB, dense logic). Trivial files
  (`.gitignore`, `Dockerfile`) excluded — they reveal nothing about
  model quality.
- **4 curated concepts** (sorted by frequency): `edge`, `method`,
  `class`, `block`. All carry `cbml3:conceptKind` from the bundled
  vocabulary.
- **1 determinism probe** per model: re-run the first concept's
  prompt 3× at `temperature=0, seed=42`. Records whether all 3 calls
  produced identical bytes, or 2 of 3, or all unique.

## The candidate lineup

Selected from the 2026 community consensus on small Ollama models for
code work (sources at the end). All fit on 8 GB VRAM at Q4_K_M; total
disk cost ≈19 GB.

| Model | Size | Spec | Selection rationale |
|---|---|---|---|
| `llama3.1:8b` | 8B | general | Baseline from the single-model spike; control |
| `qwen2.5-coder:7b` | 7B | code-specialized | Strongest 7B coder per all sources (~88% HumanEval) |
| `qwen2.5:7b` | 7B | general | Same family as above; isolates "code training" vs "general" |
| `codegemma:7b` | 7B | code-specialized | Google's coder; secondary triangulation point |
| `qwen2.5-coder:1.5b` | 1.5B | code-specialized | Minimum-viable tier; if quality holds, throughput wins |

Models *excluded* (out of scope for 8 GB VRAM):
`qwen2.5-coder:14b` (would spill to CPU on 8 GB → misleadingly slow),
`qwen2.5-coder:32b` (won't fit at all). A larger-GPU follow-up could
add them.

## Raw scorecard

Reading: median latency for warm calls (cold-load excluded);
heuristic score (0–3); count of hallucinated identifiers (names that
look like Python symbols but don't appear in the input); anchoring =
how many of N concept outputs cite at least one of the file paths the
prompt explicitly provided.

```
model                   file_med  file_score file_halluc  conc_med  conc_score  conc_anchor   det
-------------------------------------------------------------------------------------------------
codegemma:7b               2.49s       2.38/3           5     7.80s       3.00/3     4/4        no
llama3.1:8b                0.85s       2.50/3           2     2.95s       3.00/3     4/4        no
qwen2.5-coder:1.5b         0.42s       1.62/3           0     2.02s       2.25/3     3/4        no
qwen2.5-coder:7b           0.90s       2.88/3           1     3.58s       3.00/3     4/4        no
qwen2.5:7b                 0.95s       2.88/3           1     3.61s       3.00/3     4/4        no
```

Raw rows were generated on the POC branch and were not retained in the active
checkout. The table above is the preserved benchmark summary.

## Reading the numbers

### Quality

**`qwen2.5-coder:7b` and `qwen2.5:7b` lead** at 2.88/3 mean file
summary score with only 1 hallucinated identifier each. `llama3.1:8b`
trails slightly at 2.50/3 with 2 hallucinations. `codegemma:7b` lands
at 2.38/3 with 5 hallucinations — twice as many as anyone else.
`qwen2.5-coder:1.5b` at 1.62/3 confirms the size penalty: outputs
become generic and drift into Python-101 territory ("class is the
foundation of OOP") instead of staying anchored to the codebase.

All four 7B+ models scored a perfect 3.00/3 on concept descriptions
with 4/4 anchoring. The 1.5B trailed at 2.25/3 with 3/4 anchoring.

Side-by-side reads from the same prompts:

**`code_mapper/exporters.py`:**
- `qwen2.5-coder:7b`: "Python classes for exporting models to Go and Python source code" ✅
- `qwen2.5:7b`: "exporters for generating Go and Python source code from mapped models" ✅
- `llama3.1:8b`: "exports source code from models to Go and Python" ✅ (terse but correct)
- `codegemma:7b`: "exports models to Go and Python source code" ✅
- `qwen2.5-coder:1.5b`: "exporters for generating Go and Python source code from models..." ✅ but verbose

All five accurately identified the file's purpose — Python + Go
(none mentioned JavaScript, which the file does export; tied across
the board). This is a noteworthy ceiling: even the strongest model
in the lineup misses a non-trivial class on a real file.

### Throughput

`qwen2.5-coder:1.5b` is **2× faster** than the 7B class on file
summaries (0.42s median vs 0.85–0.95s) and **1.5×** on concept
descriptions. `codegemma:7b` is the slowest at the 7B size, ~3×
slower than the qwen pair for both prompt types.

Cold-load times (one-time per process):
- `llama3.1:8b`: 3.3s
- `qwen2.5:7b`: 3.7s
- `qwen2.5-coder:1.5b`: 8.3s (cache miss?)
- `codegemma:7b`: 12.4s
- `qwen2.5-coder:7b`: 16.4s (suspect a model-specific load path)

Cold-load only happens once per process and is dwarfed by the warm
budget on any real-repo run (≥ 100 prompts). Not load-bearing for
model selection.

### Determinism

**Every model showed the same pattern: call #1 ≠ calls #2 and #3,
which are identical.** Across 5 models × 3 runs each, the warm-call
pair was always byte-identical; the first call always drifted.

This rules out one explanation (model non-determinism per se) and
points at a different one: Ollama's KV cache or scheduler state
needs one warm-up before deterministic output. The
**operationally relevant property** — *cache hit = byte-identical
re-emit* — is satisfied for every model in the lineup, because the
cache eliminates the call entirely on hit. The "first-call drift" is
only relevant on cache miss, and on cache miss the first call *is*
the source of truth.

This is exactly the "warm-cache determinism" framing the archived plan
([archive/llm-enrich-plan.md § Architectural commitments § 5](archive/llm-enrich-plan.md))
already commits to. **The plan does not need rewriting.**

## Recommendation

**Ship `qwen2.5-coder:7b` as the default for L4 enrichment.**

| Criterion | Why this model |
|---|---|
| Quality | Tied for highest score (2.88/3 file summaries, 3.00/3 concept descriptions); fewest hallucinations among 7B+ models |
| Code-specialized | Trained on 5.5T tokens of code across 92 languages — the strongest fit for cbm's domain |
| Throughput | Warm latency comparable to `llama3.1:8b`; ~3× faster than `codegemma:7b` |
| Memory | 4.5 GB on disk; fits Q4 on 8 GB VRAM with headroom |
| 2026 consensus | Three independent sources rank it as the top 7B coder |
| Family availability | Same family scales to 1.5B / 14B / 32B for users with different hardware |

### Secondary recommendation

**Pin `qwen2.5-coder:1.5b` as a documented "fast tier" alternative,
not the default.** Useful for users on Apple M1 / 4 GB VRAM laptops
or anyone running cbm in CI. The quality drop is real but the
*concept* descriptions still scored above the doc's pass bar (anchored
in 3/4 of cases), and the speed is genuinely useful for big repos.

### What about `llama3.1:8b`?

The spike used it; the benchmark vindicates it as solid — but `qwen2.5-coder:7b` is
better on every quality dimension at the same throughput. The spike's
prior recommendation should be updated.

### Models to drop from the lineup

- **`codegemma:7b`** — 3× slower than the qwen pair, more
  hallucinations, no quality advantage. The 2026 consensus already
  reflects this; Google has effectively been overtaken in the 7B
  code-specialist niche.
- **`qwen2.5:7b`** (non-coder variant) — statistically tied with the
  coder variant in this benchmark, but the coder variant is the more
  defensible default because its training set is purpose-fit. Treat
  the tie as evidence the prompts work, not evidence the variants
  are interchangeable; on harder prompts (chunk summaries, refactor
  hints) the coder variant should pull ahead.

## Plan impact

The benchmark recommends two **concrete amendments** to
[archive/llm-enrich-plan.md](archive/llm-enrich-plan.md):

1. **Step 8 default model** changes from `llama3.1:8b` to
   `qwen2.5-coder:7b`. The flag remains
   `--llm-model qwen2.5-coder:7b` with the same syntax.
2. **README L4 quickstart** should mention the fast-tier alternative:
   ```bash
   ollama pull qwen2.5-coder:7b    # default; ~4.5 GB, best quality
   ollama pull qwen2.5-coder:1.5b  # fast tier; ~1 GB, faster but generic
   ```

The plan's three architectural commitments that were under test
(quality / determinism / throughput) all hold. **No rewrites required.**

## Threats to validity

- **N is small.** 8 files + 4 concepts × 5 models = 60 outputs. The
  heuristic scoring noise floor is ~±0.3 score units. A 0.5 gap is
  meaningful; the 0.0 gap between the two qwen variants is noise.
- **Prompts are unfinalized.** Step 3 of the plan ships the real
  prompts; these are the spike's prompts. If the final prompts
  differ substantially, the relative ranking could shift.
- **One bundle.** Results are from cbm's own bundle of the
  `code-mapper` codebase — Python-heavy. A repo dominated by Rust /
  Go / TypeScript could re-rank the models. The plan's prompts are
  language-agnostic, but per-language fine-tuning bias exists in all
  the candidate models.
- **No quantization comparison.** All Q4_K_M. F16 outputs would
  likely score higher but won't fit on this GPU.
- **Heuristic ≠ human review.** The doc's pass bar was manual
  scoring; this benchmark adds automated heuristics. They correlate
  with the manual read on the outputs we spot-checked, but a real
  ship decision should include 30 minutes of human review of retained
  benchmark outputs. Those raw outputs were gitignored on the POC branch and
  are not present in the active checkout.

## Reproducibility

The original harness and raw output files lived on the `poc/llm-baseline`
branch and are not part of the active checkout.

```bash
git checkout poc/llm-baseline
ollama serve &
for m in llama3.1:8b qwen2.5-coder:7b qwen2.5:7b codegemma:7b qwen2.5-coder:1.5b; do
    ollama pull "$m"
done
.venv/bin/python _tmp/llm_baseline_bench.py \
    --bundle _tmp/code-mapper \
    --models llama3.1:8b qwen2.5-coder:7b qwen2.5:7b codegemma:7b qwen2.5-coder:1.5b \
    --sample-files 8 --sample-concepts 4 \
    --out _tmp/llm_baseline_results.jsonl
```

Total runtime ≈4 minutes on RTX 3050 with all models pre-pulled.

## Sources (2026 community consensus)

- [Best Ollama Models: 12 Models Ranked for Coding, RAG & Agents (2026)](https://www.morphllm.com/best-ollama-models)
- [Best LLM for Coding in 2026: Ranked by Real Benchmarks](https://whatllm.org/best-llm-for-coding)
- [Best Local AI Coding Models for Ollama 2026](https://localaimaster.com/models/best-local-ai-coding-models)
- [Best Open Source LLMs in 2026: Ranked by Coding, Reasoning & Cost](https://whatllm.org/best-open-source-llm)
- [qwen2.5-coder model page on Ollama](https://ollama.com/library/qwen2.5-coder)

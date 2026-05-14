# LLM enrichment — 1-day proof of concept

> Status: spike. Run this **before** committing to
> [docs/llm-enrich-plan.md](llm-enrich-plan.md). The full plan is
> ~6-10 days of work; this POC takes a day and answers the three
> questions whose answers would change the plan.

The full plan assumes things we haven't measured: that a local 8B
model produces useful summaries on this codebase's vocabulary, that
warm-cache determinism is achievable in practice, and that the
per-file budget (~1s) holds up at real-repo scale. This document is
the cheapest experiment that answers all three before we build the
plumbing.

## Hypotheses under test

1. **Quality.** A local `llama3.1:8b` (or comparable) running on
   `temperature=0` produces `file_summary` and `concept_description`
   outputs *good enough to be visible in the MCP / UI without
   embarrassment*. Falsifies if 30%+ of outputs are wrong, generic,
   or hallucinated.
2. **Determinism.** Two consecutive calls with identical
   `(model, prompt, content, temperature=0, seed=N)` produce
   identical bytes. Falsifies if Ollama returns drift even with
   `temperature=0` and a fixed seed — which would force us into a
   "the cache is the only source of truth" stance from day 1 instead
   of relying on the cold path.
3. **Throughput.** Per-file latency at our typical content sizes
   (2-50KB) on a local 8B is in the 0.5–2s range, making a 1k-file
   repo a 15-30 minute job. Falsifies if median latency exceeds 5s
   (a 1k-file repo becomes a multi-hour job and the UX changes).

If all three pass, proceed to Step 1 of the plan as written. If any
fails, the plan needs a redesign — see the "Decision rules" section.

## What this POC is not

- Not wired into the cbm pipeline. The whole point is to stay
  outside the host so we can iterate fast.
- Not a finished prompt set. We're testing *that prompts work*, not
  finalizing them. Final prompts come in Step 3 of the plan.
- Not a cache implementation. We use a one-line `functools.lru_cache`
  for the duration of the spike; the real cache lives in Step 2.
- Not architected. Single file, ~150 lines. We delete most of it
  when Step 1 lands.

## Setup (15 minutes)

```bash
# 1. Install Ollama and pull a model.
#    macOS: brew install ollama && ollama serve &
#    Linux: curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b

# 2. Confirm reachable.
curl -s http://localhost:11434/api/tags | jq '.models[].name'

# 3. Use the cbm venv we already have.
.venv/bin/python -c "import httpx; print('ok')"
```

If Ollama isn't installable on this machine, the POC blocks. The
plan blocks too — Ollama is the entire transport. Resolve before
proceeding.

## The spike script

Single file, dropped at `_tmp/poc_llm.py`. Not committed; delete
when done.

```python
"""POC: probe Ollama for cbm enrichment quality + determinism + throughput.

Usage:
  ollama serve &
  ollama pull llama3.1:8b
  .venv/bin/python _tmp/poc_llm.py --bundle _tmp/code-mapper \
      --sample-files 10 --sample-concepts 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

# A small prompt set. Final versions live in plugins/llm_enrich/prompts/
# in Step 3 of the plan; this is just *enough* to test the hypotheses.
FILE_SUMMARY_SYSTEM = (
    "You write one declarative sentence summarizing the purpose of a "
    "source file. Under 30 words. No marketing language, no "
    "'this file', no speculation about what it 'might' do."
)
FILE_SUMMARY_USER = (
    "Path: {path}\nLanguage: {language}\n\nContent:\n```\n{content}\n```"
)

CONCEPT_DESC_SYSTEM = (
    "You write a single paragraph (3-5 sentences) explaining what a "
    "named concept means *in the specific codebase you're shown*. "
    "Anchor every claim to identifiers or file paths from the data. "
    "If the data is too sparse to draw a conclusion, say so."
)
CONCEPT_DESC_USER = (
    "Concept: {name}\nKind: {kind}\nAlt labels: {alt_labels}\n"
    "Top cooccurring: {cooccurring}\n"
    "Files lexicalizing it: {files}\n"
)


def ollama_chat(
    client: httpx.Client, model: str, system: str, user: str,
    seed: int = 0,
) -> tuple[str, float]:
    """Single chat call. Returns (content, wall_seconds)."""
    t0 = time.time()
    r = client.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0, "seed": seed},
        },
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()["message"]["content"], time.time() - t0


def hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def probe_file_summary(client, model, bundle: Path, n: int) -> list[dict]:
    """Pick n source files from concepts.json's per_path_concepts, summarize."""
    concepts = json.loads((bundle / "concepts.json").read_text())
    paths = sorted(concepts.get("per_path_concepts", {}))[:n]
    out = []
    for p in paths:
        # Find the file's blob; bundle layout is content-addressed.
        # If blobs/ exists, prefer the blob; otherwise read the working
        # tree (the POC isn't strict about source).
        content = (bundle / "blobs" / _find_blob_for(bundle, p)).read_text(
            errors="replace"
        )[:4000]
        user = FILE_SUMMARY_USER.format(
            path=p, language="python", content=content,
        )
        summary, dt = ollama_chat(client, model, FILE_SUMMARY_SYSTEM, user)
        out.append({
            "kind": "file_summary",
            "target": p, "summary": summary.strip(),
            "wall_seconds": round(dt, 2),
            "content_sha": hash_text(content),
            "summary_sha": hash_text(summary),
        })
    return out


def probe_concept_description(client, model, bundle: Path, n: int) -> list[dict]:
    """Pick n curated concepts from concepts.json."""
    concepts = json.loads((bundle / "concepts.json").read_text())["concepts"]
    typed = [
        (name, meta) for name, meta in concepts.items() if "kind" in meta
    ]
    cooccur = json.loads((bundle / "concepts.json").read_text()).get(
        "cooccurrence", []
    )
    by_name: dict[str, list[str]] = {}
    for a, b, _w in cooccur:
        by_name.setdefault(a, []).append(b)
        by_name.setdefault(b, []).append(a)

    per_path = json.loads(
        (bundle / "concepts.json").read_text()
    ).get("per_path_concepts", {})
    files_for: dict[str, list[str]] = {}
    for path, names in per_path.items():
        for n_ in names:
            files_for.setdefault(n_, []).append(path)

    out = []
    for name, meta in typed[:n]:
        user = CONCEPT_DESC_USER.format(
            name=name, kind=meta["kind"],
            alt_labels=", ".join(meta.get("alt_labels", [])[:6]),
            cooccurring=", ".join(by_name.get(name, [])[:5]),
            files=", ".join(files_for.get(name, [])[:3]),
        )
        desc, dt = ollama_chat(client, model, CONCEPT_DESC_SYSTEM, user)
        out.append({
            "kind": "concept_description",
            "target": name, "description": desc.strip(),
            "wall_seconds": round(dt, 2),
            "summary_sha": hash_text(desc),
        })
    return out


def check_determinism(client, model, bundle: Path) -> dict:
    """Run the same prompt twice; compare bytes."""
    concepts = json.loads((bundle / "concepts.json").read_text())["concepts"]
    # Pick the first curated concept for the test.
    typed = [name for name, m in concepts.items() if "kind" in m]
    name = typed[0]
    user = CONCEPT_DESC_USER.format(
        name=name, kind=concepts[name]["kind"],
        alt_labels="", cooccurring="", files="",
    )
    a, _ = ollama_chat(client, model, CONCEPT_DESC_SYSTEM, user, seed=42)
    b, _ = ollama_chat(client, model, CONCEPT_DESC_SYSTEM, user, seed=42)
    return {
        "kind": "determinism_check",
        "target": name,
        "byte_identical": a == b,
        "hash_a": hash_text(a),
        "hash_b": hash_text(b),
    }


def _find_blob_for(bundle: Path, path: str) -> str:
    """Look up the contentSha256 for a path from inventory.ttl. POC
    quality: scrape the turtle. Real implementation uses rdflib."""
    inv = (bundle / "inventory.ttl").read_text()
    needle = f'cbm:path "{path}"'
    idx = inv.find(needle)
    if idx < 0:
        raise FileNotFoundError(path)
    sha_marker = "cbm:contentSha256 \""
    s = inv.find(sha_marker, idx) + len(sha_marker)
    e = inv.find('"', s)
    return inv[s:e]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--sample-files", type=int, default=10)
    ap.add_argument("--sample-concepts", type=int, default=5)
    ap.add_argument("--out", default="_tmp/poc_llm_results.jsonl")
    args = ap.parse_args()

    with httpx.Client() as c:
        results = []
        print(f"[1/3] file_summary × {args.sample_files} …")
        results += probe_file_summary(c, args.model, args.bundle, args.sample_files)
        print(f"[2/3] concept_description × {args.sample_concepts} …")
        results += probe_concept_description(
            c, args.model, args.bundle, args.sample_concepts
        )
        print("[3/3] determinism check …")
        results.append(check_determinism(c, args.model, args.bundle))

    Path(args.out).write_text(
        "\n".join(json.dumps(r) for r in results) + "\n"
    )

    # Tiny summary on stdout so you don't have to open the file.
    file_lat = [r["wall_seconds"] for r in results if r["kind"] == "file_summary"]
    conc_lat = [r["wall_seconds"] for r in results
                if r["kind"] == "concept_description"]
    det = next(r for r in results if r["kind"] == "determinism_check")
    print()
    print(f"file_summary    n={len(file_lat)}  median={sorted(file_lat)[len(file_lat)//2]:.2f}s  max={max(file_lat):.2f}s")
    print(f"concept_desc    n={len(conc_lat)}  median={sorted(conc_lat)[len(conc_lat)//2]:.2f}s  max={max(conc_lat):.2f}s")
    print(f"determinism     byte_identical={det['byte_identical']}  hashes=({det['hash_a']}, {det['hash_b']})")
    print(f"\nresults: {args.out}")


if __name__ == "__main__":
    main()
```

## What to look at after running

```bash
.venv/bin/python _tmp/poc_llm.py --bundle _tmp/code-mapper \
    --sample-files 10 --sample-concepts 5
```

The script prints a one-line summary covering all three hypotheses,
and writes `_tmp/poc_llm_results.jsonl` with every output for manual
read-through.

### Quality (manual, 20 minutes)

Open `_tmp/poc_llm_results.jsonl` in your editor. For each
`file_summary` row, ask:

- Is the sentence specific to this file, or a generic
  "this file contains code"?
- Does it mention anything that isn't in the file?
- Could you delete the rest of the file and reconstruct its purpose
  from this sentence alone? (If yes — score it useful.)

For each `concept_description` row, ask:

- Does it reference at least one file path or alt-label from the
  data we provided?
- Does it explain the concept in this codebase's terms, or recite a
  textbook definition?
- Would showing this in `concept_detail` make the tool more useful
  to an LLM client?

**Pass bar**: ≥ 7/10 file summaries useful, ≥ 4/5 concept
descriptions useful. **Fail bar**: < 5/10 file summaries useful, or
any hallucinated content (mentions a file/class that doesn't exist).

### Determinism (automated)

The script's `byte_identical` line is the answer. **Pass**: True.
**Fail**: False — proceed to the determinism fallback below.

### Throughput (automated)

Median wall time per call. **Pass**: file_summary median < 2.0s,
concept_description median < 3.0s. **Fail**: either median > 5s.

## Decision rules

After running the POC:

| Quality | Determinism | Throughput | Action |
|---|---|---|---|
| Pass | Pass | Pass | Proceed to Step 1 of the plan as written. |
| Pass | Pass | Fail | Plan still good, but Step 4's content-addressed cache becomes load-bearing — assume cold runs take hours and design the UX around "kick off and come back tomorrow." Add `--llm-progress` flag. |
| Pass | Fail | Pass | **Rewrite Step 1**. Drop the "deterministic provenance" framing. Cache becomes the *only* source of truth for re-emit determinism; cold-cache runs are explicitly non-reproducible. Doc rewrite is small; verifier rewrite is larger. |
| Fail | * | * | **Don't proceed**. Try a larger model (`qwen2.5:14b` or `mistral-small:24b`) and re-run the quality probe. If 24B still fails, the local-first hypothesis is wrong and the plan needs a cloud-API path. |

The interesting failure mode is "quality good, determinism bad" —
that's the one that changes the architectural commitments. Cold-cache
determinism is the one promise we make in the plan that could turn
out to be untrue.

## Cleanup

```bash
rm _tmp/poc_llm.py _tmp/poc_llm_results.jsonl
```

The cache directory (if you implement one during the spike) lives at
`~/.cache/cbm-llm-poc/`. Delete it too if you want a clean slate.

## What the POC explicitly defers

- **The cache layer.** Real cache lives in Step 2 of the plan. The
  POC re-calls every prompt every run. That's fine for 15 prompts.
- **The plugin shape.** The POC is a script. The plugin shape is
  Step 1 of the plan.
- **RDF emission, SHACL shapes, MCP surface, UI badge.** All Steps
  4-7.
- **Multiple models.** The POC tests one model at the size we
  expect users to actually run. If you want to compare 8B vs. 32B,
  run the script twice with different `--model` values.

## Expected duration

- Setup: 15 min.
- Write script: already done above.
- Run: 5 min (15 prompts × 2-3s).
- Read outputs and score quality: 20 min.
- Decision: 10 min.
- **Total: ~1 hour of attention spread over an afternoon.**

If this POC exceeds half a day, something is wrong with the
environment (Ollama not installable, model not pulling, network in
the way). Stop and fix that before scaling up.

## What "POC succeeded" looks like

You can paste this into the plan-review meeting:

> Ran the LLM enrichment POC against `_tmp/code-mapper`. Model:
> `llama3.1:8b`. 10 file summaries, 5 concept descriptions, 1
> determinism check. **Quality**: 8/10 file summaries useful, 5/5
> concept descriptions anchored to real files. No hallucinations.
> **Determinism**: byte-identical across two calls at
> `temperature=0, seed=42`. **Throughput**: file_summary median
> 1.4s, concept_description median 2.1s. Recommendation: proceed to
> Step 1 of `docs/llm-enrich-plan.md` as written.

If you can write that paragraph honestly, the plan is sound and the
work is worth doing.

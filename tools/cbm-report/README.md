---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-09"
---

# cbm-report

Rust crate that processes a codebase-mapper bundle — including the multi-GB
`inventory.jsonld` — and emits a polished, self-contained PDF report about the
data. Back to the root [README.md](../../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Usage

```bash
cargo build --release
./target/release/cbm-report <bundle-or-parent-dir> [-o output.pdf]

# example: the Linux kernel sandbox bundle
./target/release/cbm-report _tmp/linux-sandbox
# → _tmp/linux-sandbox/linux-bundle-report.pdf
```

The positional argument may be the bundle directory itself (containing
`run_manifest.json`) or its parent (the `<out>/<repo_name>/` layout is
auto-detected). The PDF defaults to `<repo_name>-bundle-report.pdf` inside the
given directory.

Fonts: DejaVu Sans is loaded from `/usr/share/fonts/truetype/dejavu` and
embedded into the PDF; override the location with `CBM_REPORT_FONT_DIR`.

## What it reads

| Artifact | How | Feeds |
|---|---|---|
| `run_manifest.json` | serde | provenance, per-language AST coverage, artifact sizes |
| `inventory.jsonld` (5 GB+) | streaming splitter + rayon | chunk/file/concept stats, directory bytes, size histograms, git-time cardinality |
| `enrichments.jsonl` | line scan | L4 kinds, models, throughput timeline, text lengths |
| `rust_items.jsonl` | line scan | Rust item kinds, visibility, directories |

The inventory is never parsed whole: a byte-level state machine
(`ingest/splitter.rs`) extracts complete `@graph` objects from sequential
64 MB reads, and batches are folded into mergeable statistics on the rayon
pool. The 5.2 GB Linux-kernel inventory scans in ~7 s on 24 cores with a
memory ceiling of one block plus per-batch accumulators.

## Report contents (8 pages, A4)

1. Cover — provenance, headline tiles, artifact sizes
2. Language landscape — files/bytes by language, files by type
3. Extraction health — symbols, imports, parse-quality rates
4. Repository structure — bytes by top-level directory, file-size histogram
5. Rust in the tree — item kinds, visibility, directories
6. Chunks & embeddings — kinds, size distribution, embedding artifact
7. Concepts & LLM enrichment — top concepts, L4 throughput timeline
8. Data quality & epistemics — mechanical anomaly flags, including an
   independent recount of the inventory cross-checked against the manifest

All charts are drawn as PDF vector paths (no raster, no external chart
library) following a fixed design method: thin marks with rounded data ends,
hairline recessive grids, ink-colored direct labels, a CVD-validated
categorical palette, and status colors reserved for state.

## Epistemics

Every figure in the report is mechanically derived from bundle artifacts and
can be recomputed from them. The report's own footer and its final page state
the split required by PALS's LAW: the *text* of L4 enrichments is LLM-authored
and unverified by default; this crate aggregates only its metadata (counts,
kinds, timing) and never quotes enrichment text as fact.

## Tests

```bash
cargo test
```

Unit tests cover the graph splitter (including object boundaries at every
byte offset), the statistics fold/merge, ISO-8601 parsing verified against
`date -u`, number formatting, histogram bucketing, and axis tick generation.

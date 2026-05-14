"""LlmArtifact — ArtifactEmitter that writes the enrichments sidecar.

Output (relative to out_dir, only when there are enrichments):
  - enrichments.jsonl  : one record per (target_iri, kind), sorted.

Step 1 status: skeleton. Emits no file and returns an empty manifest
fragment whose presence is itself the back-compat anchor — comparing
``run_manifest.json["extensions"]`` between with-L4 and without-L4 runs
on a fresh repo, the only difference must be this entry's empty
metrics.

Sidecar shape (Step 4+):

    {"target": "code_mapper/__init__.py", "kind": "file_summary",
     "text": "…", "model": "qwen2.5-coder:7b",
     "prompt_sha": "<hex>",
     "generated_at": "2026-05-14T03:42:11Z"}

One line per enrichment, sorted by (target, kind). No timestamps in the
filename so re-emits over a warm cache stay byte-identical to the
previous run.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codebase_mapper.extensions import PipelineCtx


ARTIFACT_NAME = "l4_50_artifact"
SIDECAR_FILENAME = "enrichments.jsonl"


class LlmArtifact:
    """Step-1 skeleton. ``emit`` returns an empty manifest fragment and
    writes no file. Step 4 makes the sidecar conditional on the
    aggregator's index containing at least one enrichment."""

    name = ARTIFACT_NAME

    def emit(self, out_dir: Path, ctx: "PipelineCtx") -> dict:
        # Step 4: read ctx.indices[AGGREGATOR_NAME], serialize to
        # SIDECAR_FILENAME, return counts + sha. Step 1 emits nothing.
        return {
            "n_enrichments": 0,
            "by_kind": {},
            "files": {},
        }

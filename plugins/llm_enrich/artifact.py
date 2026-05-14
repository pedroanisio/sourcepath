"""LlmArtifact — ArtifactEmitter that writes the enrichments sidecar.

Output (relative to out_dir, only when there are enrichments):
  - enrichments.jsonl  : one record per (target_iri, kind), sorted.

Step 4 fills in the body. On a run with no enrichments (no scope
opted in, or Ollama unreachable), the emitter writes NO file and
returns an empty manifest fragment — preserving Step 1's invariant
that the on-disk artifact set is identical to a no-L4 run.

Sidecar shape (one line per enrichment):

    {"target": "code_mapper/__init__.py",
     "kind": "file_summary",
     "text": "…",
     "model": "qwen2.5-coder:7b",
     "prompt_sha": "<hex>",
     "target_sha": "<hex>",
     "generated_at": "2026-05-14T03:42:11Z"}

Records are sorted by ``(target, kind)`` and emitted with stable
JSON keys (sort_keys=True, no spaces) so two runs over the same
records produce byte-identical files. ``was_cache_hit`` is NOT
serialized — it's a per-run diagnostic, not part of the bundle's
content.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from codebase_mapper.shared_kernel.extensions import PipelineCtx


ARTIFACT_NAME = "l4_50_artifact"
SIDECAR_FILENAME = "enrichments.jsonl"


# Fields that land in the sidecar, in the order the JSON serializer
# will produce them (sort_keys=True imposes the actual order, but
# documenting them here pins the contract).
_SERIALIZED_FIELDS: tuple[str, ...] = (
    "generated_at",
    "kind",
    "model",
    "prompt_sha",
    "target",         # the cbm:path the enrichment is attached to
    "target_sha",
    "text",
)


class LlmArtifact:
    """ArtifactEmitter for cbml4 enrichments.

    Reads ``ctx.scratch["llm:file_summary"]`` (and Step 5 will add
    ``llm:concept_description``, ``llm:schema_purpose``); flattens to
    one record per (target, kind) sorted line; returns metrics that
    land in ``run_manifest.json["extensions"]["l4_50_artifact"]``."""

    name = ARTIFACT_NAME

    def emit(self, out_dir: Path, ctx: "PipelineCtx") -> dict:
        records = list(_iter_records(ctx))

        if not records:
            # No file on disk — preserves Step 1's "default run is
            # byte-identical to no-plugin run" anchor. The manifest
            # fragment is still present (zero counts), so consumers
            # can detect "L4 ran but produced nothing" vs. "L4 wasn't
            # registered at all".
            return {
                "n_enrichments": 0,
                "by_kind": {},
                "files": {},
            }

        sidecar = out_dir / SIDECAR_FILENAME
        sidecar.write_text(
            "".join(_serialize(r) + "\n" for r in records),
            encoding="utf-8",
        )
        sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()

        by_kind: dict[str, int] = {}
        for r in records:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

        return {
            "n_enrichments": len(records),
            "by_kind": dict(sorted(by_kind.items())),
            "files": {
                SIDECAR_FILENAME: {
                    "path": SIDECAR_FILENAME,
                    "sha256": sha,
                    "size_bytes": sidecar.stat().st_size,
                },
            },
        }


def _iter_records(ctx: "PipelineCtx"):
    """Yield flattened sidecar rows from all enrichment buckets.

    Each bucket holds ``{target_key: {text, model, prompt_sha,
    target_sha, generated_at, was_cache_hit, …}}``. For file-targeted
    kinds the target_key is a bundle-relative path; for
    concept_description it's the concept's canonical name. Sort keys
    are (kind, target) — kind first so all concept rows group
    together, then alphabetical within each kind."""
    buckets = (
        ("llm:file_summary", "file_summary"),
        ("llm:concept_description", "concept_description"),
        ("llm:schema_purpose", "schema_purpose"),
    )
    rows: list[dict[str, Any]] = []
    for scratch_key, kind_label in buckets:
        bucket = cast(dict, ctx.scratch.get(scratch_key, {}))
        for target, rec in bucket.items():
            text = rec.get("text")
            if not text:
                continue
            rows.append({
                "target": target,
                "kind": kind_label,
                "text": text,
                "model": rec.get("model", ""),
                "prompt_sha": rec.get("prompt_sha", ""),
                "target_sha": rec.get("target_sha", ""),
                "generated_at": rec.get("generated_at", ""),
            })
    rows.sort(key=lambda r: (r["kind"], r["target"]))
    return rows


def _serialize(rec: dict) -> str:
    """One line of JSONL. sort_keys + compact separators → byte-stable."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)

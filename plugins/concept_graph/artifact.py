"""ConceptsArtifact — sidecar JSON + optional npz.

Emits:
  - concepts.json         : structured concept index (label, alt labels,
                            frequency, file count, components, embedding row)
  - concepts_embeddings.npz : per-concept centroid vectors, only if L2
                              embeddings were available and centroids were
                              computed by the aggregator
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import numpy as np

from codebase_mapper.shared_kernel.extensions import PipelineCtx


class ConceptsArtifact:
    name = "l3_40_concepts_artifact"

    def emit(self, out_dir: Path, ctx: PipelineCtx) -> dict:
        idx = cast(dict, ctx.indices.get("l3_20_concepts") or {})
        concepts = idx.get("concepts", {})
        per_path = idx.get("per_path_concepts", {})
        cooccurrence = idx.get("cooccurrence", [])
        cembs = idx.get("concept_embeddings")
        cemb_ids = idx.get("concept_embedding_ids")

        # ---- concepts.json ----
        # `kind` and `broader` are only present for concepts that
        # matched the curated vocab; absent for compound terms and for
        # any atomic term not in the YAML. We emit them only when
        # present so untyped runs produce JSON identical to pre-vocab
        # bundles (back-compat for any consumer reading concepts.json).
        json_path = out_dir / "concepts.json"
        payload = {
            "concepts": {
                k: _concept_payload(v)
                for k, v in sorted(concepts.items())
            },
            "per_path_concepts": {
                k: list(v) for k, v in sorted(per_path.items())
            },
            "cooccurrence": [list(t) for t in cooccurrence],
            "concept_embeddings_artifact": (
                "concepts_embeddings.npz" if cembs is not None else None
            ),
            "concept_embedding_ids": list(cemb_ids) if cemb_ids else None,
        }
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        json_sha = hashlib.sha256(json_path.read_bytes()).hexdigest()

        out_files = {
            "concepts.json": {
                "path": "concepts.json",
                "sha256": json_sha,
                "size_bytes": json_path.stat().st_size,
            }
        }

        # ---- concepts_embeddings.npz (optional) ----
        if cembs is not None and cemb_ids:
            npz_path = out_dir / "concepts_embeddings.npz"
            np.savez(
                str(npz_path),
                vectors=cembs.astype(np.float32, copy=False),
                ids=np.array(cemb_ids, dtype=object),
            )
            out_files["concepts_embeddings.npz"] = {
                "path": "concepts_embeddings.npz",
                "sha256": hashlib.sha256(npz_path.read_bytes()).hexdigest(),
                "size_bytes": npz_path.stat().st_size,
            }

        # A concept only gets a centroid when at least one of its
        # lexicalizing files contributed an embedded chunk row; the rest
        # have no vector source. Legitimate, but it must be visible —
        # on the Linux bundle 7,418 of 776,716 concepts had no vector and
        # nothing disclosed it (flaw map F14).
        n_with_embedding = int(len(cemb_ids)) if cemb_ids else 0
        return {
            "n_concepts": int(len(concepts)),
            "n_cooccurrence": int(len(cooccurrence)),
            "concept_centroids_available": cembs is not None,
            "n_concepts_with_embedding": n_with_embedding,
            "n_concepts_without_embedding": int(len(concepts)) - n_with_embedding,
            "files": out_files,
        }


def _concept_payload(v: dict) -> dict:
    """Project a concept record onto its on-disk JSON shape.

    Required keys are always emitted; `kind` / `broader` are emitted
    iff the aggregator attached them (curated-vocab match).
    """
    out: dict = {
        "label": v["label"],
        "alt_labels": v["alt_labels"],
        "components": v["components"],
        "frequency": v["frequency"],
        "file_count": v["file_count"],
        "embedding_row": v.get("embedding_row"),
    }
    if "kind" in v:
        out["kind"] = v["kind"]
    if "broader" in v:
        out["broader"] = v["broader"]
    return out

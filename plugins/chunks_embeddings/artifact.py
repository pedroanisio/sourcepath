"""EmbeddingsArtifact — ArtifactEmitter that writes the embeddings binary
plus a metadata JSON sidecar.

Output files (relative to out_dir):
  - embeddings.npz   : NumPy .npz archive with two arrays:
        vectors : float32 (N, D), L2-normalized
        ids     : <U-string array, len N, parallel to vectors rows
  - embeddings_meta.json : human-readable summary
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import cast

import numpy as np

from codebase_mapper.extensions import PipelineCtx


class EmbeddingsArtifact:
    name = "l2_40_embeddings_artifact"

    def emit(self, out_dir: Path, ctx: PipelineCtx) -> dict:
        index = cast(dict, ctx.indices.get("l2_20_embeddings", {}))
        vectors = index.get("vectors")
        ids = index.get("row_to_chunk_id", [])
        backend = index.get("backend", {})

        if vectors is None or len(ids) == 0:
            return {
                "n_chunks": 0,
                "dimension": int(backend.get("dimension", 0)),
                "backend": backend,
                "files": {},
            }

        npz_path = out_dir / "embeddings.npz"
        meta_path = out_dir / "embeddings_meta.json"

        # Use savez (not savez_compressed) so byte output is stable across
        # numpy versions; the file is small enough that compression isn't
        # critical for a prototype. We also fix the in-archive order by
        # passing as kwargs.
        np.savez(
            str(npz_path),
            vectors=vectors.astype(np.float32, copy=False),
            ids=np.array(ids, dtype=object),
        )

        npz_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()

        meta = {
            "n_chunks": int(len(ids)),
            "dimension": int(vectors.shape[1]),
            "backend": dict(backend),
            "normalized": bool(backend.get("normalized", False)),
            "vector_dtype": "float32",
            "ids_field": "ids",
            "vectors_field": "vectors",
            "artifact_sha256": npz_sha,
        }
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

        return {
            "n_chunks": int(len(ids)),
            "dimension": int(vectors.shape[1]),
            "backend": dict(backend),
            "files": {
                "embeddings.npz": {
                    "path": "embeddings.npz",
                    "sha256": npz_sha,
                    "size_bytes": npz_path.stat().st_size,
                },
                "embeddings_meta.json": {
                    "path": "embeddings_meta.json",
                    # meta sha excludes itself; we compute it after write.
                    "sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                    "size_bytes": meta_path.stat().st_size,
                },
            },
        }

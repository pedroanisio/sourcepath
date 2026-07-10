"""E7 (error-free-mapping plan) — every concept gets a vector.

linux-v23 evidence: 7,394 of 793,210 concepts had no centroid because none
of their lexicalizing files contributed an embedded chunk row. The fallback
embeds the concept's own label text (prefLabel + alt labels) through the
same backend, tagged ``embedding_source: label`` so consumers can filter by
provenance; centroid-backed concepts carry ``embedding_source: centroid``.

Run from the repo root:  python -m pytest tests/test_concept_label_embeddings.py
"""
from __future__ import annotations

import numpy as np

from plugins.concept_graph.concepts import compute_concept_embeddings


def _encode(texts):
    """Deterministic fake backend: unit vector keyed by text hash."""
    out = np.zeros((len(texts), 4), dtype=np.float32)
    for i, t in enumerate(texts):
        out[i, hash(t) % 4] = 1.0
    return out


def _fixture():
    concepts = {
        "covered": {"label": "covered", "alt_labels": [], "components": ["covered"],
                    "frequency": 3, "file_count": 1, "embedding_row": None},
        "orphan": {"label": "orphan", "alt_labels": ["orphans"],
                   "components": ["orphan"], "frequency": 2, "file_count": 1,
                   "embedding_row": None},
    }
    per_path = {"src/a.c": ["covered"], "docs/readme.txt": ["orphan"]}
    l2_idx = {
        "vectors": np.eye(4, dtype=np.float32),
        "row_to_chunk_id": ["c0", "c1", "c2", "c3"],
        "encode_texts": _encode,
    }
    l2_chunks = [{"row": 0, "path": "src/a.c"},
                 {"row": 1, "path": "src/a.c"}]
    return concepts, per_path, l2_idx, l2_chunks


def test_orphan_concepts_get_label_embeddings():
    concepts, per_path, l2_idx, l2_chunks = _fixture()
    matrix, ids, sources = compute_concept_embeddings(
        concepts, per_path, l2_idx, l2_chunks)
    assert set(ids) == {"covered", "orphan"}
    assert sources["covered"] == "centroid"
    assert sources["orphan"] == "label"
    assert matrix.shape == (2, 4)
    # every concept has its row recorded and a unit-norm vector
    for name in ids:
        row = concepts[name]["embedding_row"]
        assert row is not None
        assert abs(float(np.linalg.norm(matrix[row])) - 1.0) < 1e-5
        assert concepts[name]["embedding_source"] == sources[name]


def test_without_encoder_only_centroids_exist():
    concepts, per_path, l2_idx, l2_chunks = _fixture()
    del l2_idx["encode_texts"]
    matrix, ids, sources = compute_concept_embeddings(
        concepts, per_path, l2_idx, l2_chunks)
    assert ids == ["covered"]
    assert concepts["orphan"]["embedding_row"] is None


def test_no_l2_vectors_means_no_embeddings():
    concepts, per_path, _, _ = _fixture()
    matrix, ids, sources = compute_concept_embeddings(
        concepts, per_path, {}, [])
    assert matrix is None and ids is None and sources == {}

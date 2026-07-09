"""F14 — the concept-embedding shortfall must be disclosed in the manifest.

On the Linux bundle, concepts_embeddings.npz held 769,298 vectors against
776,716 concepts: 7,418 concepts (0.95%) have no centroid because none of
their lexicalizing files contributed an embedded chunk row. That is a
legitimate outcome (no vector source exists), but it was silent — nothing
in the manifest let a consumer see the gap. The artifact fragment now
discloses both sides of the count.

Run from the repo root:  python -m pytest tests/test_concept_embedding_disclosure.py
"""
from __future__ import annotations

import numpy as np

from plugins.concept_graph.artifact import ConceptsArtifact


class _Ctx:
    def __init__(self, idx):
        self.indices = {"l3_20_concepts": idx}


def _concept(row=None):
    return {
        "label": "x", "alt_labels": [], "components": ["x"],
        "frequency": 3, "file_count": 1, "embedding_row": row,
    }


def test_fragment_discloses_embedding_gap(tmp_path):
    idx = {
        "concepts": {"a": _concept(0), "b": _concept(1), "c": _concept()},
        "per_path_concepts": {},
        "cooccurrence": [],
        "concept_embeddings": np.zeros((2, 4), dtype=np.float32),
        "concept_embedding_ids": ["a", "b"],
    }
    fragment = ConceptsArtifact().emit(tmp_path, _Ctx(idx))
    assert fragment["n_concepts"] == 3
    assert fragment["n_concepts_with_embedding"] == 2
    assert fragment["n_concepts_without_embedding"] == 1


def test_fragment_discloses_total_gap_when_no_centroids(tmp_path):
    idx = {
        "concepts": {"a": _concept(), "b": _concept()},
        "per_path_concepts": {},
        "cooccurrence": [],
        "concept_embeddings": None,
        "concept_embedding_ids": None,
    }
    fragment = ConceptsArtifact().emit(tmp_path, _Ctx(idx))
    assert fragment["concept_centroids_available"] is False
    assert fragment["n_concepts_with_embedding"] == 0
    assert fragment["n_concepts_without_embedding"] == 2

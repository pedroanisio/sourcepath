"""E6 wave-2 (error-free-mapping plan) — concept-description scope is policy.

59 vocab-matched descriptions against 793,210 kernel concepts is a scope so
narrow it reads as an error. The selection now has two deliberate tiers:
every curated-vocab match ("vocab") plus the top-N corpus concepts by
occurrence and file spread ("corpus_top"), each record tagged with its
selection provenance so the epistemics stay separated.

Run from the repo root:  python -m pytest tests/test_concept_selection.py
"""
from __future__ import annotations

from plugins.llm_enrich.aggregator import select_concepts_for_description


def _c(freq, files, kind=None):
    meta = {"frequency": freq, "file_count": files}
    if kind:
        meta["kind"] = kind
    return meta


CONCEPTS = {
    "vocab_rare": _c(2, 1, kind="domain-primitive"),
    "hot_a": _c(900, 120),
    "hot_b": _c(800, 100),
    "mid": _c(50, 10),
    "cold": _c(2, 1),
}


def test_vocab_tier_always_selected_and_first():
    sel = select_concepts_for_description(CONCEPTS, top_n=2)
    assert sel[0] == ("vocab_rare", "vocab")
    names = [n for n, _ in sel]
    assert names == sorted(names[:1]) + ["hot_a", "hot_b"]


def test_corpus_tier_is_top_n_by_occurrence_then_spread():
    sel = dict(select_concepts_for_description(CONCEPTS, top_n=3))
    assert sel == {"vocab_rare": "vocab", "hot_a": "corpus_top",
                   "hot_b": "corpus_top", "mid": "corpus_top"}


def test_top_n_zero_means_vocab_only():
    sel = select_concepts_for_description(CONCEPTS, top_n=0)
    assert sel == [("vocab_rare", "vocab")]


def test_deterministic_tiebreak():
    tied = {"b": _c(5, 5), "a": _c(5, 5)}
    sel = select_concepts_for_description(tied, top_n=2)
    assert [n for n, _ in sel] == ["a", "b"]

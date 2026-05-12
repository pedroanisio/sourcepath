"""ConceptAggregator — builds the canonical concept set.

Reads from:
  - ctx.scratch["raw_terms"]              (populated by IdentifierSplitter)
  - ctx.indices["l2_20_embeddings"]       (optional; populated by L2's EmbeddingComputer)
  - ctx.indices["l2_10_chunks"]           (optional; chunk metadata for centroids)

Writes to:
  - ctx.indices["l3_concepts"] = {
        "concepts": {
            "user": {
                "label": "user",
                "alt_labels": ["User", "users"],
                "components": [],              # canonical_form of composing atomic concepts
                "frequency": 14,               # total occurrences across raw_terms
                "file_count": 7,               # distinct files this concept appears in
                "embedding_row": 4 | None,     # row in concepts_embeddings if computed
            },
            "user_service": {                  # compound concept (composedOf atomic)
                "label": "user service",
                "alt_labels": ["UserService"],
                "components": ["user", "service"],
                "frequency": 3, "file_count": 2,
                "embedding_row": ...,
            },
            ...
        },
        "per_path_concepts": {
            "src/user_service.py": ["service", "user", "user_service"],
            ...
        },
        "cooccurrence": [
            (concept_a, concept_b, count), ...        # count >= MIN_COOCCURRENCE
        ],
        "concept_embeddings": np.ndarray | None,      # (M, D) if L2 vectors present
        "concept_embedding_ids": list[str] | None,    # parallel to rows
    }

Canonicalization: lowercase + naive suffix-strip (plurals + common verb suffixes).
This is the prototype-grade default and is documented as such; production
should plug in a real lemmatizer (spaCy / NLTK / wordnet).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import cast

import numpy as np

from codebase_mapper.extensions import PipelineCtx


# Programming-context stopwords. Conservative: anything in this set is
# dropped before concept identity is computed. Tunable.
STOPWORDS = {
    # generic English filler
    "a", "an", "the", "of", "for", "in", "on", "at", "to", "from", "with",
    "by", "as", "or", "and", "if", "then", "else", "not", "no",
    "is", "be", "do", "have", "has", "had", "this", "that", "it", "its",
    # programming filler
    "get", "set", "fn", "func", "method", "var", "val", "init", "self",
    "this_", "tmp", "temp", "ret", "res",
    # truthy literals
    "true", "false", "none", "null", "nil",
    # very-common short tokens
    "x", "y", "z", "n", "i", "j", "k", "v",
}

MIN_TOKEN_LEN = 2
MIN_COOCCURRENCE = 2     # drop singletons; tune for noisier codebases
MIN_FREQUENCY = 1        # keep all concepts that appear at least once


def canonicalize(token: str) -> str | None:
    """Lower-case + trailing-'s' plural strip. Returns None to drop.

    Deliberately minimal. Real lemmatization (Porter stemmer, spaCy,
    NLTK WordNet) belongs in a future iteration; the previous naive
    stripper over-stripped ('mapper' -> 'mapp', 'languages' -> 'languag').
    Under-stemming is the lesser evil for a concept graph.
    """
    if not token:
        return None
    t = token.lower()
    if len(t) < MIN_TOKEN_LEN:
        return None
    if t in STOPWORDS:
        return None
    if t.isdigit():
        return None
    # Only one rule: simple plural strip. Skip if it would produce a stem
    # shorter than MIN_TOKEN_LEN+1 (avoids 'us' -> 'u') and skip 'ss'
    # endings ('class' should not become 'clas').
    if len(t) >= MIN_TOKEN_LEN + 2 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


class ConceptAggregator:
    name = "l3_20_concepts"

    def run(self, ctx: PipelineCtx) -> dict:
        raw_map = cast(dict, ctx.scratch.get("raw_terms", {}))

        # Augment raw_terms with method-level identifiers from L2 chunks if
        # present. L2's chunker exposes 'symbol' and 'parent_symbol' for
        # functions, methods, and classes — including methods that the host's
        # current ast_summary doesn't surface (which only has top_level_*).
        from .splitter import split_identifier
        l2_chunks = cast(list, ctx.indices.get("l2_10_chunks") or [])
        if l2_chunks:
            for c in l2_chunks:
                path = c.get("path")
                if not path:
                    continue
                entries = raw_map.setdefault(path, [])
                for key in ("symbol", "parent_symbol"):
                    name = c.get(key)
                    if not name or name == "<file>":
                        continue
                    toks = split_identifier(name)
                    if not toks:
                        continue
                    entries.append({
                        "source": f"chunk_{key}", "owner": name,
                        "raw": name, "tokens": toks,
                    })

        # ----- Pass 1: build concept frequencies and per-file term lists -----
        atomic_freq: Counter[str] = Counter()
        atomic_alt_labels: dict[str, set[str]] = defaultdict(set)
        atomic_file_count: dict[str, set[str]] = defaultdict(set)
        compound_freq: Counter[tuple[str, ...]] = Counter()
        compound_alt_labels: dict[tuple[str, ...], set[str]] = defaultdict(set)
        compound_file_count: dict[tuple[str, ...], set[str]] = defaultdict(set)
        per_path_atomic: dict[str, set[str]] = defaultdict(set)
        per_path_compound: dict[str, set[tuple[str, ...]]] = defaultdict(set)

        for path in sorted(raw_map.keys()):
            for entry in raw_map[path]:
                tokens = entry["tokens"]
                raw = entry["raw"]
                canon = [c for c in (canonicalize(t) for t in tokens) if c is not None]
                if not canon:
                    continue
                # Atomic concepts: each canonicalized token.
                for c in canon:
                    atomic_freq[c] += 1
                    atomic_alt_labels[c].add(_alt_form(c, tokens, raw))
                    atomic_file_count[c].add(path)
                    per_path_atomic[path].add(c)
                # Compound concept: the full canonical tuple. Only meaningful
                # if it has 2+ atomic parts (else it equals the atomic).
                if len(canon) >= 2:
                    key = tuple(canon)
                    compound_freq[key] += 1
                    compound_alt_labels[key].add(raw)
                    compound_file_count[key].add(path)
                    per_path_compound[path].add(key)

        # ----- Build concept records -----
        concepts: dict[str, dict] = {}
        # atomic
        for c in sorted(atomic_freq):
            if atomic_freq[c] < MIN_FREQUENCY:
                continue
            concepts[c] = {
                "label": c,
                "alt_labels": sorted(x for x in atomic_alt_labels[c] if x != c),
                "components": [],
                "frequency": int(atomic_freq[c]),
                "file_count": len(atomic_file_count[c]),
                "embedding_row": None,
            }
        # compound — synthesize canonical_form by joining with underscore
        for key in sorted(compound_freq, key=lambda k: ("_".join(k), k)):
            if compound_freq[key] < MIN_FREQUENCY:
                continue
            canon_form = "_".join(key)
            # Avoid collision with atomic concepts.
            if canon_form in concepts:
                # Rename: add a suffix to disambiguate.
                canon_form = canon_form + "_compound"
            concepts[canon_form] = {
                "label": " ".join(key),
                "alt_labels": sorted(compound_alt_labels[key]),
                "components": list(key),
                "frequency": int(compound_freq[key]),
                "file_count": len(compound_file_count[key]),
                "embedding_row": None,
            }

        # ----- per-path canonical concept lists (atomic + compound) -----
        per_path_concepts: dict[str, list[str]] = {}
        for path in sorted(per_path_atomic.keys() | per_path_compound.keys()):
            names = set(per_path_atomic.get(path, set()))
            for key in per_path_compound.get(path, set()):
                cf = "_".join(key)
                if cf not in concepts and cf + "_compound" in concepts:
                    cf = cf + "_compound"
                if cf in concepts:
                    names.add(cf)
            per_path_concepts[path] = sorted(names)

        # ----- Co-occurrence (concept_a, concept_b, count) -----
        pair_counts: Counter[tuple[str, str]] = Counter()
        for path, names in per_path_concepts.items():
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    pair_counts[(a, b)] += 1
        cooccurrence = [
            (a, b, int(c))
            for (a, b), c in sorted(pair_counts.items())
            if c >= MIN_COOCCURRENCE
        ]

        # ----- Optional: per-concept embedding centroids from L2 vectors -----
        concept_embeddings: np.ndarray | None = None
        concept_embedding_ids: list[str] | None = None
        l2_idx = cast(dict, ctx.indices.get("l2_20_embeddings") or {})
        l2_chunks = cast(list, ctx.indices.get("l2_10_chunks") or [])
        if l2_idx.get("vectors") is not None and len(l2_idx.get("row_to_chunk_id", [])) > 0:
            vecs = cast(np.ndarray, l2_idx["vectors"])
            dim = int(vecs.shape[1])
            # Map chunk row -> set of concept canonical_forms via the chunk's
            # path. Compute centroids.
            concept_rows: dict[str, list[int]] = defaultdict(list)
            for c in l2_chunks:
                row = c.get("row")
                path = c.get("path")
                if row is None or path is None:
                    continue
                for cn in per_path_concepts.get(path, []):
                    concept_rows[cn].append(int(row))

            concept_embedding_ids = sorted(cn for cn in concepts if concept_rows.get(cn))
            if concept_embedding_ids:
                centroids = np.zeros(
                    (len(concept_embedding_ids), dim), dtype=np.float32
                )
                for i, cn in enumerate(concept_embedding_ids):
                    rows = concept_rows[cn]
                    centroid = vecs[rows].mean(axis=0)
                    norm = np.linalg.norm(centroid)
                    if norm > 0:
                        centroid = centroid / norm
                    centroids[i] = centroid
                    concepts[cn]["embedding_row"] = i
                concept_embeddings = centroids
            else:
                concept_embedding_ids = None

        return {
            "concepts": concepts,
            "per_path_concepts": per_path_concepts,
            "cooccurrence": cooccurrence,
            "concept_embeddings": concept_embeddings,
            "concept_embedding_ids": concept_embedding_ids,
        }


def _alt_form(canon: str, tokens: list[str], raw: str) -> str:
    """Pick an alternate label that hints at the canonical's surface form.

    Prefer the raw identifier if short; otherwise pick the token that
    canonicalizes to `canon`.
    """
    if len(raw) <= 30:
        return raw
    for tok in tokens:
        if canonicalize(tok) == canon:
            return tok
    return raw

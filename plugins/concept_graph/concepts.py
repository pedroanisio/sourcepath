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

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.infrastructure.vocab import Vocabulary, builtin_vocabulary


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


class _UseBuiltin:
    """Sentinel type for the constructor's USE_BUILTIN flag."""
    __slots__ = ()
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<USE_BUILTIN>"

USE_BUILTIN = _UseBuiltin()


def canonicalize(token: str, vocab: Vocabulary | None = None) -> str | None:
    """Lower-case + trailing-'s' plural strip + optional vocab override.

    Deliberately minimal. Real lemmatization (Porter stemmer, spaCy,
    NLTK WordNet) belongs in a future iteration; the previous naive
    stripper over-stripped ('mapper' -> 'mapp', 'languages' -> 'languag').
    Under-stemming is the lesser evil for a concept graph.

    Vocab pass: when a `vocab` is given, the lowercased token (and its
    plural-stripped form) is consulted against the alias table first.
    A hit returns the canonical name directly, which means curated terms
    survive even when they'd otherwise be dropped (e.g. "func" is a
    stopword by default but maps to "function" under the vocab).
    """
    if not token:
        return None
    t = token.lower()
    # Vocab takes precedence over both stopwords and plural-stripping.
    if vocab is not None:
        hit = vocab.by_alias.get(t)
        if hit is not None:
            return hit
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
        stem = t[:-1]
        # Give the vocab one more shot on the stripped form — handles
        # ("behaviors" -> "behavior") when "behaviors" wasn't an alias.
        if vocab is not None:
            hit = vocab.by_alias.get(stem)
            if hit is not None:
                return hit
        return stem
    return t


class ConceptAggregator:
    name = "l3_20_concepts"

    # Cached default. Shared across instances because the YAML doesn't
    # change between calls in a single process.
    _cached_builtin_vocab: Vocabulary | None = None

    def __init__(self, vocab: Vocabulary | None | _UseBuiltin = None) -> None:
        """Construct with a specific vocabulary, or with the sentinel
        `USE_BUILTIN` to defer-load the bundled one, or with `None` to
        disable typed concepts entirely.

        Default (`vocab=None`) preserves the pre-vocab behavior so the
        aggregator stays drop-in compatible. `register_all(vocab=...)`
        is the entry point that opts into the bundled vocab by default.
        """
        self._configured_vocab: Vocabulary | None | _UseBuiltin = vocab

    @classmethod
    def _builtin(cls) -> Vocabulary:
        if cls._cached_builtin_vocab is None:
            cls._cached_builtin_vocab = builtin_vocabulary()
        return cls._cached_builtin_vocab

    def _resolve_vocab(self, ctx: PipelineCtx) -> Vocabulary | None:
        """Returns the vocabulary to use, or None if disabled.

        Resolution order:
          1. ctx.scratch["host:concept_vocab_disabled"] == True  -> None
          2. ctx.indices["host:concept_vocab"] (Vocabulary)       -> override
          3. constructor-supplied vocab or USE_BUILTIN sentinel
          4. None (pre-vocab default, full back-compat)
        """
        if ctx.scratch.get("host:concept_vocab_disabled"):
            return None
        override = ctx.indices.get("host:concept_vocab")
        if isinstance(override, Vocabulary):
            return override
        if self._configured_vocab is USE_BUILTIN:
            return self._builtin()
        return cast("Vocabulary | None", self._configured_vocab)

    def run(self, ctx: PipelineCtx) -> dict:
        raw_map = cast(dict, ctx.scratch.get("raw_terms", {}))
        vocab = self._resolve_vocab(ctx)
        # Stash the resolved vocab so downstream contributors
        # (notably ConceptGraphWriter's L2 chunk-anchoring) agree on
        # alias collapses. Aggregators always run before writers.
        ctx.scratch["l3:resolved_vocab"] = vocab

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
                canon = [c for c in (canonicalize(t, vocab) for t in tokens)
                         if c is not None]
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
                    ckey = tuple(canon)
                    compound_freq[ckey] += 1
                    compound_alt_labels[ckey].add(raw)
                    compound_file_count[ckey].add(path)
                    per_path_compound[path].add(ckey)

        # ----- Build concept records -----
        concepts: dict[str, dict] = {}
        # atomic
        for c in sorted(atomic_freq):
            if atomic_freq[c] < MIN_FREQUENCY:
                continue
            record: dict = {
                "label": c,
                "alt_labels": sorted(x for x in atomic_alt_labels[c] if x != c),
                "components": [],
                "frequency": int(atomic_freq[c]),
                "file_count": len(atomic_file_count[c]),
                "embedding_row": None,
            }
            # Vocab typing: if this canonical form is a curated term,
            # attach `kind` and `broader` so the writer + sidecar can
            # emit cbml3:conceptKind / cbml3:broaderCollection. Compound
            # concepts (joined with '_') are intentionally not typed —
            # the curated vocab only declares atomic primitives.
            if vocab is not None:
                term = vocab.terms.get(c)
                if term is not None:
                    record["kind"] = term.kind
                    if term.broader is not None:
                        record["broader"] = term.broader
            concepts[c] = record
        # compound — synthesize canonical_form by joining with underscore
        for ckey in sorted(compound_freq, key=lambda k: ("_".join(k), k)):
            if compound_freq[ckey] < MIN_FREQUENCY:
                continue
            canon_form = "_".join(ckey)
            # Avoid collision with atomic concepts.
            if canon_form in concepts:
                # Rename: add a suffix to disambiguate.
                canon_form = canon_form + "_compound"
            concepts[canon_form] = {
                "label": " ".join(ckey),
                "alt_labels": sorted(compound_alt_labels[ckey]),
                "components": list(ckey),
                "frequency": int(compound_freq[ckey]),
                "file_count": len(compound_file_count[ckey]),
                "embedding_row": None,
            }

        # ----- per-path canonical concept lists (atomic + compound) -----
        per_path_concepts: dict[str, list[str]] = {}
        for path in sorted(per_path_atomic.keys() | per_path_compound.keys()):
            names = set(per_path_atomic.get(path, set()))
            for ckey in per_path_compound.get(path, set()):
                cf = "_".join(ckey)
                if cf not in concepts and cf + "_compound" in concepts:
                    cf = cf + "_compound"
                if cf in concepts:
                    names.add(cf)
            per_path_concepts[path] = sorted(names)

        # ----- Co-occurrence (concept_a, concept_b, count) -----
        pair_counts: Counter[tuple[str, str]] = Counter()
        for path, path_names in per_path_concepts.items():
            for i, a in enumerate(path_names):
                for b in path_names[i + 1:]:
                    pair_counts[(a, b)] += 1
        cooccurrence = [
            (a, b, int(c))
            for (a, b), c in sorted(pair_counts.items())
            if c >= MIN_COOCCURRENCE
        ]

        # ----- Optional: per-concept embedding vectors from L2 -----
        l2_idx = cast(dict, ctx.indices.get("l2_20_embeddings") or {})
        l2_chunks = cast(list, ctx.indices.get("l2_10_chunks") or [])
        concept_embeddings, concept_embedding_ids, _sources = (
            compute_concept_embeddings(concepts, per_path_concepts,
                                       l2_idx, l2_chunks))

        return {
            "concepts": concepts,
            "per_path_concepts": per_path_concepts,
            "cooccurrence": cooccurrence,
            "concept_embeddings": concept_embeddings,
            "concept_embedding_ids": concept_embedding_ids,
        }


def compute_concept_embeddings(
    concepts: dict,
    per_path_concepts: dict[str, list[str]],
    l2_idx: dict,
    l2_chunks: list,
) -> tuple["np.ndarray | None", "list[str] | None", dict[str, str]]:
    """Per-concept vectors: chunk centroids, with a label-embedding fallback.

    A concept whose lexicalizing files contributed embedded chunk rows gets
    the mean of those rows (``embedding_source: centroid``). A concept with
    no vector source — 7,394 on linux-v23, previously a silent gap (plan
    E7) — gets its own label text (prefLabel + alt labels) embedded through
    the same backend (``embedding_source: label``) when the L2 index exposes
    ``encode_texts``. Every returned row is L2-normalized; each concept's
    ``embedding_row`` / ``embedding_source`` fields are updated in place.

    Returns ``(matrix, ids, sources)``; ``(None, None, {})`` when L2
    embeddings are absent entirely.
    """
    if l2_idx.get("vectors") is None or not len(l2_idx.get("row_to_chunk_id", [])):
        return None, None, {}
    vecs = cast(np.ndarray, l2_idx["vectors"])
    dim = int(vecs.shape[1])

    concept_rows: dict[str, list[int]] = defaultdict(list)
    for c in l2_chunks:
        row = c.get("row")
        path = c.get("path")
        if row is None or path is None:
            continue
        for cn in per_path_concepts.get(path, []):
            concept_rows[cn].append(int(row))

    centroid_ids = sorted(cn for cn in concepts if concept_rows.get(cn))
    encode = l2_idx.get("encode_texts")
    orphan_ids = (sorted(cn for cn in concepts if not concept_rows.get(cn))
                  if callable(encode) else [])

    ids = centroid_ids + orphan_ids
    if not ids:
        return None, None, {}

    matrix = np.zeros((len(ids), dim), dtype=np.float32)
    sources: dict[str, str] = {}

    for i, cn in enumerate(centroid_ids):
        centroid = vecs[concept_rows[cn]].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        matrix[i] = centroid
        sources[cn] = "centroid"

    if orphan_ids:
        # orphan_ids is non-empty only when callable(encode) held above
        assert callable(encode)
        texts = []
        for cn in orphan_ids:
            meta = concepts[cn]
            texts.append(" ".join(
                [str(meta.get("label", cn))] + list(meta.get("alt_labels", []))))
        label_vecs = np.asarray(encode(texts), dtype=np.float32)
        for j, cn in enumerate(orphan_ids):
            v = label_vecs[j]
            norm = np.linalg.norm(v)
            if norm > 0:
                v = v / norm
            matrix[len(centroid_ids) + j] = v
            sources[cn] = "label"

    for i, cn in enumerate(ids):
        concepts[cn]["embedding_row"] = i
        concepts[cn]["embedding_source"] = sources[cn]
    return matrix, ids, sources


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

#!/usr/bin/env python3
"""verify_vocab.py — Stage 1 acceptance test for the L3 controlled vocabulary.

Exercises:
  1. The bundled YAML loads without error and produces a Vocabulary
     with the expected schema version.
  2. The loader rejects unknown concept kinds.
  3. The loader rejects aliases that point at unknown canonical terms.
  4. The loader rejects aliases that collide across canonical terms.
  5. A well-formed in-memory document round-trips: terms, kinds,
     aliases, and `broader` are surfaced correctly, and `resolve()`
     normalizes case + maps aliases to canonical.
  6. The empty Stage-1 YAML produces a Vocabulary of length 0 (no
     callers yet — emptiness is intentional).

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys
import tempfile
import traceback

from pathlib import Path

from codebase_mapper.vocab import (
    VOCAB_SCHEMA_VERSION,
    Vocabulary,
    builtin_vocabulary,
    builtin_vocabulary_path,
    load_vocabulary,
)
from codebase_mapper.vocab.loader import VOCAB_SCHEMA_VERSION as _SCHEMA  # noqa: F401


def _write_tmp(text: str) -> Path:
    fp = tempfile.NamedTemporaryFile(
        prefix="vocab_", suffix=".yaml", delete=False, mode="w",
        encoding="utf-8",
    )
    fp.write(text)
    fp.close()
    return Path(fp.name)


def test_builtin_loads() -> None:
    vocab = builtin_vocabulary()
    assert isinstance(vocab, Vocabulary)
    assert vocab.version == 1
    assert builtin_vocabulary_path().is_file()
    # Stage 2 populates the scaffold. Guard the invariants rather than
    # an exact count so adding a term in a future stage doesn't churn
    # this test.
    assert len(vocab) > 0, "builtin vocab is unexpectedly empty"
    kinds = {t.kind for t in vocab.terms.values()}
    assert kinds == {"domain-primitive", "structural-primitive",
                     "relational-primitive"}, (
        f"all three kinds should appear in the builtin vocab; got {kinds}"
    )
    # Every term must have a broader (Stage 2 sets one per kind).
    missing_broader = [t.name for t in vocab.terms.values()
                       if t.broader is None]
    assert not missing_broader, (
        f"terms missing `broader`: {missing_broader}"
    )
    # Every alias must resolve to a known canonical, and the canonical's
    # alias tuple must contain it. (Guards against loader regressions.)
    for alias, canon in vocab.by_alias.items():
        assert canon in vocab.terms, (
            f"alias {alias!r} maps to unknown canonical {canon!r}"
        )
        if alias != canon:
            assert alias in vocab.terms[canon].aliases, (
                f"alias {alias!r} missing from {canon!r}.aliases tuple"
            )
    # Spot-check a representative absorption: `behaviour` (British)
    # must collapse to `behavior` and be tagged a domain primitive.
    b = vocab.resolve("behaviour")
    assert b is not None and b.name == "behavior"
    assert b.kind == "domain-primitive"


def test_unknown_kind_rejected() -> None:
    p = _write_tmp(
        "version: 1\n"
        "kinds:\n"
        "  bogus-kind: [foo]\n"
    )
    try:
        load_vocabulary(p)
    except ValueError as e:
        assert "unknown concept kind" in str(e), str(e)
        return
    raise AssertionError("expected ValueError for unknown kind")


def test_alias_to_unknown_rejected() -> None:
    p = _write_tmp(
        "version: 1\n"
        "kinds:\n"
        "  domain-primitive: [behavior]\n"
        "aliases:\n"
        "  ghost: [behaviour]\n"
    )
    try:
        load_vocabulary(p)
    except ValueError as e:
        assert "unknown" in str(e), str(e)
        return
    raise AssertionError("expected ValueError for alias to unknown term")


def test_alias_collision_rejected() -> None:
    p = _write_tmp(
        "version: 1\n"
        "kinds:\n"
        "  domain-primitive: [behavior, contract]\n"
        "aliases:\n"
        "  behavior: [shared]\n"
        "  contract: [shared]\n"
    )
    try:
        load_vocabulary(p)
    except ValueError as e:
        assert "already maps to" in str(e), str(e)
        return
    raise AssertionError("expected ValueError for alias collision")


def test_roundtrip_resolve() -> None:
    p = _write_tmp(
        "version: 1\n"
        "kinds:\n"
        "  domain-primitive: [behavior, intent]\n"
        "  structural-primitive: [module]\n"
        "aliases:\n"
        "  behavior: [behaviour, behaviors, behaviours]\n"
        "broader:\n"
        "  domain-primitive: intent_first_ontology\n"
    )
    v = load_vocabulary(p)

    assert len(v) == 3, len(v)
    b = v.terms["behavior"]
    assert b.kind == "domain-primitive"
    assert set(b.aliases) == {"behaviour", "behaviors", "behaviours"}
    assert b.broader == "intent_first_ontology"

    # Case-insensitive alias resolution; canonical also resolves to itself.
    assert v.resolve("Behaviour").name == "behavior"
    assert v.resolve("BEHAVIOR").name == "behavior"
    assert v.resolve("module").name == "module"
    assert v.resolve("unknown_token") is None

    # `module` has no broader entry (only domain-primitive does in this fixture).
    assert v.terms["module"].broader is None


def main() -> int:
    tests = [
        test_builtin_loads,
        test_unknown_kind_rejected,
        test_alias_to_unknown_rejected,
        test_alias_collision_rejected,
        test_roundtrip_resolve,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    if failures:
        print(f"\n{failures}/{len(tests)} test(s) failed")
        return 1
    print(f"\n{len(tests)} test(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

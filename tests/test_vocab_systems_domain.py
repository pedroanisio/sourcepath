"""F6 — the curated vocabulary must cover systems-programming domain nouns.

On the Linux bundle exactly 24 of 776,716 concepts matched the vocabulary
(<0.1%), so L4 concept descriptions — which by design cover only
vocab-typed concepts — described almost nothing. The intent-first and
structural buckets are language-shaped; a kernel's domain language
(device, interrupt, scheduler, socket…) had no representation. The terms
added here are additive per the vocabulary's own editing rules: no kind
schema change, no removals, no renames.

Run from the repo root:  python -m pytest tests/test_vocab_systems_domain.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.emission.infrastructure.vocab.loader import (
    builtin_vocabulary,
)

SYSTEMS_TERMS = [
    "device", "driver", "interrupt", "lock", "mutex", "semaphore",
    "spinlock", "buffer", "queue", "cache", "page", "socket", "packet",
    "protocol", "filesystem", "inode", "scheduler", "thread", "process",
    "signal", "timer", "clock", "dma", "firmware", "probe", "callback",
    "handler", "descriptor", "namespace", "channel", "session",
    "transaction", "endpoint", "configuration", "workqueue",
]


def test_systems_terms_are_curated_domain_primitives():
    vocab = builtin_vocabulary()
    missing = [t for t in SYSTEMS_TERMS if t not in vocab.terms]
    assert not missing, f"missing systems terms: {missing}"
    wrong_kind = [t for t in SYSTEMS_TERMS
                  if vocab.terms[t].kind != "domain-primitive"]
    assert not wrong_kind


@pytest.mark.parametrize("alias,canonical", [
    ("irq", "interrupt"),
    ("irqs", "interrupt"),
    ("devices", "device"),
    ("buf", "buffer"),
    ("sched", "scheduler"),
    ("config", "configuration"),
    ("filesystems", "filesystem"),
    ("callbacks", "callback"),
])
def test_kernel_spellings_resolve_to_canonical(alias, canonical):
    vocab = builtin_vocabulary()
    term = vocab.resolve(alias)
    assert term is not None, alias
    assert term.name == canonical


def test_existing_terms_survive_the_extension():
    vocab = builtin_vocabulary()
    for name, kind in [("intent", "domain-primitive"),
                       ("module", "structural-primitive"),
                       ("edge", "relational-primitive")]:
        assert vocab.terms[name].kind == kind
    # a canonical name must never be shadowed by another term's alias
    assert vocab.resolve("module").name == "module"

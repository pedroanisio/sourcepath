"""Content-addressed cache — Step 1 stub. Real implementation lands in Step 2.

The real cache is a flat ``<sha256>.json`` directory under
``$CBM_LLM_CACHE`` (default ``~/.cache/cbm-llm/``). Keys derive from
``(model, prompt_template_sha, target_content_sha, enrichment_kind)``.
The stub mirrors the API shape (``get``/``put``/``key_for``) so the
plugin can register and tests can instantiate the wiring, but every
``get`` misses and every ``put`` is a no-op.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CACHE_DIR = Path.home() / ".cache" / "cbm-llm"


def default_cache_dir() -> Path:
    """Honor ``$CBM_LLM_CACHE`` if set; otherwise ``~/.cache/cbm-llm/``."""
    env = os.environ.get("CBM_LLM_CACHE")
    return Path(env) if env else DEFAULT_CACHE_DIR


@dataclass
class Cache:
    """Step-1 stub. Constructor signature is final; semantics fill in
    Step 2. Stub semantics: every ``get`` is a miss, every ``put`` is a
    no-op, every ``key_for`` returns a deterministic placeholder."""

    cache_dir: Path = field(default_factory=default_cache_dir)
    enabled: bool = True

    def key_for(self, *, model: str, prompt_sha: str,
                target_sha: str, kind: str) -> str:  # pragma: no cover
        # Stable shape: the real key in Step 2 is sha256(...) but the
        # composition is the same. Stub returns the inputs joined so
        # equality testing in the verifier is meaningful without
        # depending on the real cache layer.
        return f"{kind}:{model}:{prompt_sha}:{target_sha}"

    def get(self, key: str) -> dict | None:  # pragma: no cover
        return None

    def put(self, key: str, value: dict) -> None:  # pragma: no cover
        return None

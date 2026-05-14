"""Content-addressed cache for LLM enrichments.

Step 2 fills in the stub from Step 1. The real cache is a flat
directory of ``<sha256>.json`` files. Each file holds one enrichment
record:

    {
        "v": 1,
        "kind": "file_summary",
        "model": "qwen2.5-coder:7b",
        "prompt_sha": "<hex>",
        "target_sha": "<hex>",
        "text": "…",
        "generated_at": "2026-05-14T03:42:11Z"
    }

Key composition (locked):

    sha256( kind || \\x1f || model || \\x1f || prompt_sha || \\x1f || target_sha )

The ``\\x1f`` separator (ASCII unit-separator) is reserved so we can
add fields to the key later without silently colliding with existing
entries. Bumping any input bytes changes the key — by design, that's
how prompt-template edits invalidate prior cache entries (plan
Commitment #9).

Default location ``~/.cache/cbm-llm/``; override via the ``$CBM_LLM_CACHE``
environment variable.

Atomic writes: write to ``<key>.tmp``, then ``os.replace`` onto
``<key>.json``. ``os.replace`` is atomic on POSIX and Windows.

Failure modes:
  - Cache dir doesn't exist → created on first ``put``.
  - Disk full → ``put`` raises (caller decides). Subsequent ``get``
    misses, so the system still works, just without acceleration.
  - Corrupt JSON → ``get`` returns None (treat as miss); the entry
    gets rewritten on the next ``put`` for the same key.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = 1
_KEY_SEPARATOR = "\x1f"  # ASCII unit-separator; reserved across all key fields.


DEFAULT_CACHE_DIR = Path.home() / ".cache" / "cbm-llm"


def default_cache_dir() -> Path:
    """Honor ``$CBM_LLM_CACHE`` if set; otherwise ``~/.cache/cbm-llm/``."""
    env = os.environ.get("CBM_LLM_CACHE")
    return Path(env) if env else DEFAULT_CACHE_DIR


def hash_text(s: str | bytes) -> str:
    """Hex sha256 of a string or bytes input. Top-level helper because
    Step 3 (prompt registry) needs to hash prompt-file bytes and the
    enrichment target's content with the same function."""
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


@dataclass
class Cache:
    """Flat content-addressed file cache.

    Construction is cheap (no I/O); the cache dir is created on first
    write. Set ``enabled=False`` to disable both reads and writes
    without changing call sites — convenient for tests and for the
    ``--llm-no-cache`` verifier path (plan Step 8)."""

    cache_dir: Path = field(default_factory=default_cache_dir)
    enabled: bool = True

    # --- key composition ------------------------------------------------

    @staticmethod
    def compose_key(*, kind: str, model: str, prompt_sha: str,
                    target_sha: str) -> str:
        """Stable cache key for an (enrichment_kind, model, prompt,
        target-content) tuple. Returns a 64-char hex sha256."""
        raw = _KEY_SEPARATOR.join((kind, model, prompt_sha, target_sha))
        return hash_text(raw)

    # --- I/O ------------------------------------------------------------

    def _path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        """Return the cached record for ``key``, or None on miss /
        corrupt entry / disabled cache. Never raises."""
        if not self.enabled:
            return None
        p = self._path_for(key)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            # Corrupt entry — treat as miss. The next put() will
            # overwrite it cleanly.
            return None
        if not isinstance(data, dict) or data.get("v") != CACHE_SCHEMA_VERSION:
            # Unknown schema version — treat as miss. Future cache-format
            # changes can migrate or invalidate by bumping CACHE_SCHEMA_VERSION.
            return None
        return data

    def put(self, key: str, value: dict) -> None:
        """Write ``value`` (a record dict) atomically. The ``v`` field
        is set automatically if absent. Caller must not pre-populate
        ``v`` with a wrong value — that would be silently overwritten."""
        if not self.enabled:
            return
        if "v" not in value:
            value = dict(value, v=CACHE_SCHEMA_VERSION)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self._path_for(key)
        tmp = p.with_suffix(p.suffix + ".tmp")
        # sort_keys=True so on-disk bytes are stable per (key, value).
        # That property doesn't matter for correctness but it makes
        # cache fingerprinting (Step 10's CI determinism check) trivial.
        tmp.write_text(
            json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, p)

    # --- convenience ----------------------------------------------------

    def get_or_compute(
        self, *, kind: str, model: str, prompt_sha: str, target_sha: str,
        compute: "Any",  # callable returning {"text": str, "generated_at": str, ...}
    ) -> tuple[dict, bool]:
        """Return (record, was_hit). On miss, calls ``compute`` to
        produce the record, fills in standard fields, and writes it.

        Step 3 calls this from the enricher. ``compute`` is the
        Ollama call; the cache is the thing that lets the rest of the
        pipeline be deterministic across re-emits."""
        key = self.compose_key(
            kind=kind, model=model,
            prompt_sha=prompt_sha, target_sha=target_sha,
        )
        cached = self.get(key)
        if cached is not None:
            return cached, True
        produced = compute()
        record = {
            "v": CACHE_SCHEMA_VERSION,
            "kind": kind,
            "model": model,
            "prompt_sha": prompt_sha,
            "target_sha": target_sha,
            **produced,
        }
        self.put(key, record)
        return record, False

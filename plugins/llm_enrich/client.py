"""OllamaClient — Step 1 stub. Real implementation lands in Step 2.

The real client wraps ``POST /api/chat`` on a local Ollama server and
returns ``(content, wall_seconds)`` for a single ``temperature=0`` chat
turn. The stub is constructible (so the plugin can register and tests
can instantiate the wiring) but raises if ``.chat()`` is called — the
Step-1 skeleton has nothing that should reach the model.
"""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class OllamaClient:
    """Step-1 stub. ``host`` and ``timeout`` are accepted so callers in
    Steps 2-10 can construct the real client with the same constructor
    signature. ``chat`` raises until Step 2 fills it in."""

    host: str = DEFAULT_HOST
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def chat(self, model: str, system: str, user: str,
             *, seed: int = 0) -> tuple[str, float]:  # pragma: no cover
        raise NotImplementedError(
            "OllamaClient.chat is a Step-1 stub. Step 2 implements it."
        )

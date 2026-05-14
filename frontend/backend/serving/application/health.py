"""Health endpoint application logic."""
from __future__ import annotations


def health_response() -> dict[str, str]:
    return {"status": "ok"}

"""Bundle listing use case."""
from __future__ import annotations

from typing import Any

from .bundle_data import _bundles_root, _default_bundle_name, list_bundles


def list_bundles_response() -> dict[str, Any]:
    return {
        "bundles": list_bundles(),
        "selected": _default_bundle_name(),
        "bundles_root": str(_bundles_root()),
    }

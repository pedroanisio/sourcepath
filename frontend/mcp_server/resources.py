"""MCP resources (Phase 4).

URI scheme: ``cbm://...`` over the templates locked in Phase 1.

* ``resources/list`` enumerates static, addressable resources: the bundles
  index plus per-bundle manifest / summary / shapes / ontology mapping.
* ``resources/templates/list`` advertises the parametric URIs (file,
  chunk, concept) so a client knows the shape without iterating the
  whole codebase.
* ``resources/read`` dispatches to the underlying bundle data.

Every URI is parsed by ``parse_uri()``, which rejects alien schemes,
empty bundle names, and ``..`` segments before any handler runs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource, ResourceTemplate

from frontend.backend.serving.application import bundle_data as backend_bundle_data

from . import handlers as _h  # for dispatch() into the Phase 2 surface
from .schemas import RESOURCE_URI_TEMPLATES
from .validators import (
    INVALID_ARGUMENT,
    NOT_FOUND,
    ToolError,
    validate_bundle_name,
    validate_relative_path,
)

CBM_SCHEME = "cbm://"

# Pattern table — order matters, more-specific entries first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^cbm://bundles$"), "bundles_index"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/manifest$"), "bundle_manifest"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/summary$"), "bundle_summary"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/shapes\.shacl\.ttl$"), "bundle_shacl"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/ontology-mapping\.ttl$"), "bundle_ontology"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/file/(?P<path>.+)$"), "file"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/chunk/(?P<idx>\d+)$"), "chunk"),
    (re.compile(r"^cbm://bundle/(?P<bundle>[^/]+)/concept/(?P<name>.+)$"), "concept"),
]


@dataclass(frozen=True)
class ParsedUri:
    kind: str
    params: dict[str, str]


# --------------------------------------------------------------------------
# URI parsing + validation
# --------------------------------------------------------------------------


def parse_uri(uri: str) -> ParsedUri:
    """Parse a ``cbm://`` URI into a (kind, params) descriptor.

    Raises ToolError(INVALID_ARGUMENT) on alien schemes, empty bundle
    names, or path traversal. The handler can trust the returned params
    without further string checks (except for downstream existence).
    """
    if not isinstance(uri, str) or not uri.startswith(CBM_SCHEME):
        raise ToolError(INVALID_ARGUMENT, f"unsupported URI scheme: {uri!r}")
    for pat, kind in _PATTERNS:
        m = pat.match(uri)
        if not m:
            continue
        params = m.groupdict()
        # Always validate the bundle component when present.
        if "bundle" in params and params["bundle"] is not None:
            validate_bundle_name(params["bundle"])
        # File paths get the same defence-in-depth as the file_detail tool.
        if "path" in params and params["path"] is not None:
            params["path"] = validate_relative_path(params["path"])
        # Concept names must not contain '/' (regex captured up to end, but
        # could include path separators if a malicious caller is creative).
        if "name" in params and params["name"] is not None:
            if "/" in params["name"] or ".." in params["name"]:
                raise ToolError(INVALID_ARGUMENT, f"invalid concept name in URI: {uri!r}")
        return ParsedUri(kind=kind, params=params)
    raise ToolError(INVALID_ARGUMENT, f"unrecognized cbm:// URI: {uri!r}")


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def list_static_resources(bundle_default: str | None = None) -> list[Resource]:
    """Return one Resource per static (non-parametric) URI.

    For every known bundle, emit four resources: manifest, summary,
    shapes, ontology. Plus the always-present bundles index. Per-file
    / per-chunk / per-concept URIs are *templated* (see
    ``list_resource_templates``) and not enumerated to keep the response
    bounded for large codebases.
    """
    out: list[Resource] = []

    idx = RESOURCE_URI_TEMPLATES["bundles_index"]
    out.append(Resource(
        uri=idx["uri"],
        name=idx["name"],
        description=idx["description"],
        mimeType=idx["mimeType"],
    ))

    # Bundles available right now
    try:
        bundles = backend_bundle_data.list_bundles()
    except Exception:  # pragma: no cover — defensive
        bundles = []

    static_kinds = ("bundle_manifest", "bundle_summary", "bundle_shacl", "bundle_ontology")
    for b in bundles:
        name = b["name"]
        for kind in static_kinds:
            spec = RESOURCE_URI_TEMPLATES[kind]
            out.append(Resource(
                uri=spec["uri"].format(bundle=name),
                name=f"{name}: {spec['name']}",
                description=spec["description"],
                mimeType=spec["mimeType"],
            ))
    return out


def list_resource_templates() -> list[ResourceTemplate]:
    """Return the parametric URI shapes (file, chunk, concept).

    Clients use these to construct read URIs without having to know the
    full set of paths/chunks/concepts up front.
    """
    out: list[ResourceTemplate] = []
    for kind in ("file", "chunk", "concept"):
        spec = RESOURCE_URI_TEMPLATES[kind]
        out.append(ResourceTemplate(
            uriTemplate=spec["uri"],
            name=spec["name"],
            description=spec["description"],
            mimeType=spec["mimeType"],
        ))
    return out


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _json_contents(payload: Any) -> ReadResourceContents:
    return ReadResourceContents(
        content=json.dumps(payload, indent=2, sort_keys=True),
        mime_type="application/json",
    )


def _ttl_contents(path) -> ReadResourceContents:
    return ReadResourceContents(
        content=path.read_text(errors="replace"),
        mime_type="text/turtle",
    )


def read_resource(uri: str, bundle_default: str | None = None) -> list[ReadResourceContents]:
    """Resolve ``uri`` and return its contents.

    Raises ToolError(INVALID_ARGUMENT) on malformed URIs,
    ToolError(NOT_FOUND) on missing bundles/files/chunks/concepts.
    """
    parsed = parse_uri(uri)
    bundle_name = parsed.params.get("bundle") or bundle_default

    if parsed.kind == "bundles_index":
        return [_json_contents(_h.dispatch("list_bundles", {}, bundle_default=bundle_default))]

    if parsed.kind == "bundle_manifest":
        b = _h._get_bundle(bundle_name)
        return [_json_contents(b.manifest)]

    if parsed.kind == "bundle_summary":
        return [_json_contents(_h.dispatch("bundle_summary", {"bundle": bundle_name}))]

    if parsed.kind == "bundle_shacl":
        b = _h._get_bundle(bundle_name)
        p = b.output_dir / "shapes.shacl.ttl"
        if not p.exists():
            raise ToolError(NOT_FOUND, f"shapes.shacl.ttl missing from bundle {bundle_name!r}")
        return [_ttl_contents(p)]

    if parsed.kind == "bundle_ontology":
        b = _h._get_bundle(bundle_name)
        p = b.output_dir / "ontology-mapping.ttl"
        if not p.exists():
            raise ToolError(NOT_FOUND, f"ontology-mapping.ttl missing from bundle {bundle_name!r}")
        return [_ttl_contents(p)]

    if parsed.kind == "file":
        payload = _h.dispatch("file_detail", {
            "bundle": bundle_name,
            "path": parsed.params["path"],
        })
        return [_json_contents(payload)]

    if parsed.kind == "chunk":
        payload = _h.dispatch("chunk_detail", {
            "bundle": bundle_name,
            "idx": int(parsed.params["idx"]),
        })
        return [_json_contents(payload)]

    if parsed.kind == "concept":
        payload = _h.dispatch("concept_detail", {
            "bundle": bundle_name,
            "name": parsed.params["name"],
        })
        return [_json_contents(payload)]

    # The pattern table covers every kind above; reaching here means a
    # bug in parse_uri.
    raise ToolError(INVALID_ARGUMENT, f"no reader for resource kind: {parsed.kind!r}")  # pragma: no cover

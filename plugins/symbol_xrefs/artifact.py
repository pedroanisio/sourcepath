"""XrefsArtifact — ArtifactEmitter that writes the symbol-xref sidecar.

Output (relative to out_dir):
  - xrefs.jsonl  : one edge per line, sorted by (src, dst, kind, resolver).
                   Empty when no resolvers are registered.

The manifest fragment lands under
``run_manifest.json["extensions"]["l3_50_xrefs_artifact"]`` and carries
per-kind, per-resolution, per-language counts plus the file's sha256.

Cost is bounded by the number of call sites, not symbols. Each line is a
flat JSON object (no nested structures) so streaming readers can parse
without an indexer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import SymbolXrefEdge, UnresolvedSymbolRef

from .aggregator import XREF_INDEX_KEY


SIDECAR_FILENAME = "xrefs.jsonl"


class XrefsArtifact:
    name = "l3_50_xrefs_artifact"

    def emit(self, out_dir: Path, ctx: PipelineCtx) -> dict:
        index = cast(dict, ctx.indices.get(XREF_INDEX_KEY, {}))
        edges: list[SymbolXrefEdge] = list(index.get("edges", []))
        unresolved: list[UnresolvedSymbolRef] = list(index.get("unresolved", []))

        sidecar = out_dir / SIDECAR_FILENAME
        # Always write the file so its presence is part of the contract;
        # empty edges produce a zero-byte file (clean signal for the
        # backend's "no xref data" branch).
        sidecar.write_text("".join(_serialize_edge(e) + "\n" for e in edges))
        sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()

        return {
            "n_edges": len(edges),
            "n_unresolved": len(unresolved),
            "by_kind": dict(index.get("by_kind", {})),
            "by_resolution": dict(index.get("by_resolution", {})),
            "by_language": dict(index.get("by_language", {})),
            "files": {
                SIDECAR_FILENAME: {
                    "path": SIDECAR_FILENAME,
                    "sha256": sha,
                    "size_bytes": sidecar.stat().st_size,
                },
            },
        }


def _serialize_edge(edge: SymbolXrefEdge) -> str:
    # sort_keys keeps the line byte-stable across Python versions.
    return json.dumps(asdict(edge), sort_keys=True, separators=(",", ":"))

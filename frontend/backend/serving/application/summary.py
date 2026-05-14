"""Summary use case."""
from __future__ import annotations

from .bundle_data import get_bundle


def build_summary_response(bundle: str | None = None) -> dict[str, object]:
    b = get_bundle(bundle)
    manifest = b.manifest
    return {
        "repo_name": manifest.get("repo_name"),
        "commit_sha": manifest.get("commit_sha"),
        "generated_at": manifest.get("generated_at"),
        "tool_version": manifest.get("tool_version"),
        "counts": manifest.get("counts", {}),
        "files_by_language": manifest.get("files_by_language", {}),
        "files_by_type": manifest.get("files_by_type", {}),
        "embeddings_backend": (b.embeddings_meta.get("backend") or {}).get("name"),
        "embeddings_dimension": b.embeddings_meta.get("dimension"),
        "n_chunks": b.embeddings_meta.get("n_chunks", 0),
        "n_concepts": len(b.concepts.get("concepts", {})),
        "shacl_conforms": (manifest.get("shacl_self_check") or {}).get("conforms"),
        "output_dir": str(b.output_dir),
    }

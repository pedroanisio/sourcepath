"""LlmEnricher — RecordEnricher that produces per-file LLM summaries.

Step 1 status: skeleton. The class satisfies the RecordEnricher
protocol and is registered by ``register_all``, but ``enrich`` is a
no-op — it never reads content, never calls the model, never writes
to ctx. The point is to prove the plugin can be wired in without
perturbing the bundle.

Step 3 fills this in: gated by ``scope == "files"``, reads
``record.content`` (or re-reads via ``ctx.read_path``), assembles a
prompt with the path + language + truncated content, consults the
cache, falls through to the OllamaClient on miss, and stashes the
result on ``ctx.scratch["llm:file_summary"][record.path]``. Step 4
emits those into ``cbml4:fileSummary`` triples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codebase_mapper.extensions import PipelineCtx
    from codebase_mapper.models import FileRecord
    from .cache import Cache
    from .client import OllamaClient


# Plugin-name prefix follows the project convention from L2/L3:
# `l<layer>_<step>_<purpose>`. Sorting keeps the enricher before the
# aggregator and the writer.
ENRICHER_NAME = "l4_10_enrich"


@dataclass
class LlmEnricher:
    """Step-1 skeleton. Constructor signature is final; ``enrich`` body
    fills in Step 3."""

    client: "OllamaClient | None" = None
    cache: "Cache | None" = None
    model: str = "qwen2.5-coder:7b"
    scopes: tuple[str, ...] | None = None

    name: str = ENRICHER_NAME

    def enrich(self, record: "FileRecord", content: bytes,
               ctx: "PipelineCtx") -> None:
        # Step 1: deliberate no-op. The verifier asserts a bundle built
        # with this enricher registered is byte-identical to a bundle
        # built without it.
        return None

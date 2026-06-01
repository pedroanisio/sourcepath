---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "GPT-5 Codex"
  date: "2026-05-22"
---

# PURPOSE.md - codebase-mapper

## Why This Project Exists

`codebase-mapper` exists to turn source repositories into inspectable,
queryable knowledge bundles. Its core job is to preserve code structure,
relationships, provenance, and derived analysis in a form that humans,
automation, frontends, and MCP clients can inspect consistently.

The project is built around a verification-first premise: generated facts,
LLM-authored annotations, and inferred graph edges are useful only when their
source, scope, and failure modes are explicit. Bundle outputs should therefore
separate mechanically derived data from advisory or stochastic enrichment, and
they should expose enough metadata for downstream consumers to audit claims.

## What It Does

- Maps repositories into RDF and JSON sidecars with file, import, dependency,
  test, AST, chunk, concept, xref, and optional LLM-enrichment data.
- Provides CLI entry points for producing bundles from local paths and cloneable
  Git URLs.
- Provides a FastAPI backend, React UI, and MCP server for exploring generated
  bundles.
- Ships verifiers that keep documentation, schemas, emitted artifacts, and
  plugin contracts aligned with the live codebase.

## Who It Is For

This repository is for engineers and agents that need a grounded view of a
codebase before acting on it: refactoring tools, code reviewers, documentation
auditors, architecture inspectors, and MCP clients that need read-only
repository intelligence.

## Non-Goals

- It is not a replacement for source control or a byte-for-byte repository
  archive unless a run explicitly emits and retains blobs.
- It is not a source of unquestioned truth about code intent; inferred and
  LLM-authored outputs remain derived data.
- It is not a write-capable remote code execution service. The exposed MCP
  surface is intentionally read-only.

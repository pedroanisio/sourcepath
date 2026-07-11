# Backlog

## Disclaimer

This work is subject to the methodological caveats and commitments described in DISCLAIMER.md.
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Deferred work captured so it is not lost. Nothing here blocks current use unless a current task explicitly promotes it.

**System of record:** `docs/backlog.yml`, structured by `docs/schema/backlog.schema.json`. The YAML is canonical; this table is a rendered view. Effort is expressed as **complexity** (["XS","S","M","L","XL"]), never time. `Owner` records the provenance boundary so concurrent work stays attributable.

Validate the backlog:

```bash
node scripts/check-backlog-governance.mjs
```

## Items

| ID | Item | Category | Type | Status | Cx | Priority | Owner |
|----|------|----------|------|--------|----|----------|-------|
| BL-001 | Govern the backlog with schema and drift checks | docs | infra | ready | S | high | shared |
| BL-002 | Member containment edges and qualified chunk identity | feature | feature | done | S | high | mine |
| BL-003 | Extract class fields and attributes with declared types | feature | feature | ready | M | critical | unassigned |
| BL-004 | Resolve type literals into references xref edges | feature | feature | ready | M | high | unassigned |
| BL-005 | Preserve extends vs implements through the chunk contract | feature | tech-debt | ready | S | high | unassigned |
| BL-006 | Emit abstract, static, and final modifiers as chunk facts | feature | feature | ready | S | medium | unassigned |
| BL-007 | Preserve declaration-kind stereotypes in chunk kinds | feature | tech-debt | ready | M | medium | unassigned |
| BL-008 | Close the xref resolver registry gap for six languages | feature | feature | ready | L | medium | unassigned |
| BL-009 | Record call-site position and order on calls edges | feature | feature | ready | L | medium | unassigned |
| BL-010 | Receiver and dispatch resolution for sequence lifelines | feature | research | parked | XL | low | unassigned |
| BL-011 | Rewire dossier UML views to consume the bundle graph | tooling | tech-debt | ready | M | high | unassigned |
| BL-012 | Chunk nested and inner declarations beyond one level | feature | feature | ready | M | low | unassigned |
| BL-013 | Widen Python xref binding scope | feature | feature | ready | M | medium | unassigned |

Items BL-002..BL-013 form the **UML reconstruction cluster**: the facts the
bundle must additionally capture (or stop dropping) before correct, full
class and sequence diagrams can be derived from a bundle by query alone.
Grounding: UML gap analysis of the extraction pipeline (2026-07-11); each
item cites file-level evidence in its `references`. Package diagrams need no
new data — `cbm:imports` / `cbm:importsExternal` suffice today.

See `backlog.yml` for each item's full description, rationale, acceptance criteria, dependencies, related decisions, and references.

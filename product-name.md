---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-09"
---

# SourcePath — Product Name Concept

**SourcePath** is the product name for the system this repository implements:
`codebase-mapper` — repository → RDF/JSON knowledge bundle → CLI / API / UI /
MCP inspection surface. The name refers to the whole system, never to a single
layer.

Canonical written forms (defined once, here):

- **SourcePath** — prose and titles. One word, camel-cased. Never "Source
  Path", never pluralized.
- **`sourcepath`** — packages, commands, URLs, handles. All lowercase.
- In titles and headings, pair the name with a qualifier — "SourcePath
  bundle", "SourcePath MCP" — to separate it from the compiler-flag noun (see
  [Verification and risks](#verification-and-risks)).

---

## Why the name fits

Each half of the name maps to something the product verifiably does.

1. **Source is the input — and the boundary.** The product ingests source
   repositories from a local path or a cloneable URL. It reads source; it does
   not execute it, archive it, or replace its history (`PURPOSE.md`,
   non-goals). The name states both the raw material and the limit.

2. **Path is the query primitive.** Bundles are RDF graphs, and the native way
   to interrogate an RDF graph is to walk a *path* through it — SPARQL 1.1
   property paths are the formal mechanism
   ([W3C SPARQL 1.1 Query Language, §9](https://www.w3.org/TR/sparql11-query/#propertypaths)).
   "Who imports this file?", "which tests cover this chunk?", "what concepts
   touch this symbol?" — each is a path traversal. The name encodes the query
   model.

3. **Path is the provenance model.** The project's verification-first premise
   (`PURPOSE.md`) requires every claim to be walkable back to its origin —
   mechanically derived fact, inferred edge, or LLM-authored enrichment. A
   path is exactly that: a route where every hop is explicit and auditable.
   The name encodes the epistemics.

---

## The narrative ladder

A map earns its keep by giving you routes through the territory — and the four
concept lines trace one continuous route, the motion of a camera over a
landscape. Each rung is backed by a shipped capability, not an aspiration.

| # | Line | What it means | Backed by |
|---|------|---------------|-----------|
| 1 | **Unfold your codebase.** | A repo you can only read file-by-file is a folded map. Extraction unfolds it into files, imports, dependencies, tests, AST, chunks, concepts, and xrefs. | Inspection pipeline + emission (L1–L3, mechanically derived) |
| 2 | **Take the high ground.** | The vantage point over the whole territory at once: layout, dependency shape, concept landscape. | Repository/bundle summaries, orientation tools, UI overview |
| 3 | **Zoom in on any detail.** | From the aerial view down to one file, one chunk, one symbol edge — without losing the surrounding context. | File/chunk/concept detail views, symbol xrefs, SPARQL endpoint |
| 4 | **Your source knowledge, unleashed.** | The unfolded map leaves the repo and goes to work — read-only, for humans and agents alike. | MCP server, FastAPI backend, opt-in LLM enrichment (L4, disclosed and confidence-tagged) |

**Epistemic boundary.** Rungs 1–3 describe mechanically derived output. Rung 4
is the only rung that includes stochastic enrichment, and the product
discloses that boundary (L4 is opt-in and labeled). Marketing copy may not
blur rung 4 into the first three.

## Taglines

Rung 1 doubles as the primary tagline; the others derive from the ladder.

- **Primary:** *Unfold your codebase.*
- **Full ladder:** *Unfold your codebase — take the high ground, zoom to any
  detail, and put the knowledge to work.*
- **Technical audiences:** *Know every path through your code.*

## Positioning — where the name earns its keep

SourcePath is built for the codebases where no unaided head can hold the map:

- **Large codebases.** Too many files, imports, and cross-references for
  anyone to survey from ground level. The high ground is the only place the
  whole territory is visible at once.
- **Ancient codebases.** The original authors are gone and the design lives
  nowhere but the source. SourcePath re-derives the structural map
  mechanically from the code itself — at any time, on demand.
- **Teams held hostage to departed knowledge.** When "how this works" exists
  only in the memory of people who left, every change is a negotiation with
  ghosts. Audience-facing line: *No longer hostage to knowledge that walked
  out the door.*

**Epistemic bound on the promise.** What SourcePath recovers mechanically is
*structure* — files, imports, dependencies, tests, symbols, concepts. It does
not recover *intent*; interpretive claims remain derived data (rung 4,
disclosed). The copy may promise freedom from remembered structure, never
freedom from human judgment.

---

## Name system

The product name is the outward face; internal identifiers keep their
stability. Adopting the brand requires no rename.

| Layer | Identifier | Status |
|---|---|---|
| Product / brand | SourcePath | this document |
| Repository | `code-base-mapper` | unchanged (rename optional, out of scope) |
| Python import package | `codebase_mapper` | unchanged — import stability |
| PyPI distribution name | `sourcepath` | available as of 2026-07-09 (see verification) |
| CLI | `cbm` | unchanged; `sourcepath` may be added as an alias |
| MCP server | `cbm` | presented as "SourcePath MCP" |
| Bundle artifact | "SourcePath bundle" | naming in docs/UI copy |

## Boilerplate

Copy-paste description for READMEs, listings, and announcements:

> **SourcePath** unfolds a source repository into a knowledge bundle you can
> actually interrogate — an RDF graph plus JSON sidecars covering files,
> imports, dependencies, tests, AST structure, chunks, concepts, and symbol
> cross-references. Survey the whole codebase from above, zoom to any detail,
> and expose the result — read-only — to humans, frontends, and AI agents over
> API and MCP. Mechanically derived facts, inferred edges, and LLM-authored
> annotations stay explicitly separated, so every claim can be walked back to
> its source. Built for codebases too large to hold in one head — and old
> enough that nobody's head still holds them.

---

## Verification and risks

Stated per the project's epistemic commitments; unverified items are marked.
All availability checks are point-in-time (2026-07-09) — re-verify before
publishing or purchasing.

**Verified available (HTTP 404 from the authoritative registry):**

- PyPI package `sourcepath` (`https://pypi.org/pypi/sourcepath/json`)
- npm package `sourcepath` (`https://registry.npmjs.org/sourcepath`)
- GitHub handle `github.com/sourcepath` (404 may also mean reserved or
  suspended; confirm at signup)
- Domains `sourcepath.dev` and `sourcepath.io` — no registration record via
  RDAP (`https://rdap.org/domain/…`); confirm with a registrar

**Verified taken:**

- **`sourcepath.com` is registered** (RDAP record exists). The `.com` is not
  on the table; the product would live on `.dev` or `.io`. If `.com`
  ownership matters commercially, that is a real cost of this name.

**Known term collision (accepted):**

- `sourcepath` is an established common noun in build toolchains — the
  `javac -sourcepath` option
  ([Oracle javac reference](https://docs.oracle.com/en/java/javase/21/docs/specs/man/javac.html))
  and equivalent Ant/Gradle attributes and debugger settings. Bare-word search
  results will collide with compiler-flag documentation. Mitigation is the
  qualifier rule in the identity block above.

**Not verified:**

- **Trademark clearance.** A web search on 2026-07-09 surfaced no prominent
  developer-tool product named "SourcePath", but that is not a trademark
  search. Legal clearance is required before commercial use; this document
  explicitly does not claim it.

**Accepted weakness:**

- "Source" and "path" are two of the most common words in software;
  distinctiveness is moderate at best. The name earns meaning from the ladder
  above, not from inherent uniqueness — the copy has to do the differentiating
  work.

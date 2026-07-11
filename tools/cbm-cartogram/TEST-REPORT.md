---
disclaimer:
  notice: >-
    No information within this document should be taken for granted. Any statement
    or premise not backed by a real logical definition or verifiable reference may
    be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# Test Report

**Build:** Cartogram 1.0.0
**Inventory (snapshot):** FastAPI repository inventory at commit `7cb06f360dd44efac059848df1a9beee7643b018`

This is a point-in-time snapshot. The authoritative gate is `make test-cartogram`
(re-run on every change); this file records a representative pass.

## Automated model tests

- Tests executed: 10
- Passed: 10
- Failed: 0

Validated properties:

1. metadata counts match normalized arrays;
2. all canonical relation endpoints are valid;
3. every internal import is retained and reversed only in the Imports projection;
4. primary-parent selection is unique and acyclic;
5. every explicit test mapping is retained and reversed only in the Tests projection;
6. external imports become external-import edges without changing the canonical fact;
7. the directory hierarchy contains every file exactly once;
8. suite aggregates preserve complete test membership without duplicates;
9. all chunks remain traceable to valid files;
10. file, package, concept, and chunk identifiers remain unique.

## Browser smoke test

The standalone HTML was loaded in headless Chromium at 1600×1000. The following
operations completed without console or page errors:

- initial full-map render;
- projection switching (Combined / Imports / Tests);
- search for `fastapi/routing.py`;
- artifact selection and details rendering;
- animated focus transition;
- close-zoom symbol rendering;
- level-of-detail switching;
- canvas export control availability.

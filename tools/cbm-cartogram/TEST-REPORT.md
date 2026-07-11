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

- Tests executed: 22
- Passed: 22
- Failed: 0

Validated properties (model):

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

Validated properties (theme system, `src/themes.js`):

11. the registry exposes its API and the required-token contract;
12. all five studies plus the default register in both dark and light modes;
13. every preset resolves a complete token set (no missing tokens);
14. `canvasColors` / `cssVars` project every renderer color key and `:root` variable;
15. the historical CSS-vs-canvas palette drift is unified to one value per concept;
16. `register()` accepts a complete custom theme and rejects an incomplete one (guardrail);
17. `inheritDefaults` layers brand overrides over the default palette without dropping neutrals;
18. an unknown theme or mode fails loudly;
19. `swatch()` returns a preview strip drawn only from the resolved palette;
20. `swatch()` is mode-sensitive (dark and light previews differ);
21. `swatch()` carries the projection identity (import + test hues).

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

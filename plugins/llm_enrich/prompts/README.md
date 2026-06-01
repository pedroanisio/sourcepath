# L4 prompt registry

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Each enrichment kind has a versioned prompt file:

```
<kind>.v<N>.txt
```

The file's SHA-256 (over its raw bytes) is part of the cache key. Editing
the file invalidates every cache entry built against the previous bytes —
by design. To keep an old prompt working alongside a new one, *bump the
version number*: copy `kind.v1.txt` to `kind.v2.txt`, edit `v2`, and
update `PROMPT_REGISTRY` in `plugins/llm_enrich/prompts.py` to
point at v2.

File format:

```
SYSTEM:
<system message — terse, role-oriented>

USER:
<user message with {placeholders}>
```

The `{placeholders}` are filled by `render(target, context)` in the
registry. Each kind documents its own placeholders in
`plugins/llm_enrich/prompts.py` (Step 3+).

Current prompt files:

- `file_summary.v1.txt`
- `concept_description.v1.txt`
- `schema_purpose.v1.txt`

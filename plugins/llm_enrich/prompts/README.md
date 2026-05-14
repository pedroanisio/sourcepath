# L4 prompt registry

Each enrichment kind has a versioned prompt file:

```
<kind>.v<N>.txt
```

The file's SHA-256 (over its raw bytes) is part of the cache key. Editing
the file invalidates every cache entry built against the previous bytes —
by design. To keep an old prompt working alongside a new one, *bump the
version number*: copy `kind.v1.txt` to `kind.v2.txt`, edit `v2`, and
update `PROMPT_REGISTRY` in `plugins/llm_enrich/prompts.py` (Step 3+) to
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

Step 1 status: directory empty. Step 3 lands the first prompt
(`file_summary.v1.txt`). Step 5 adds `concept_description.v1.txt` and
`schema_purpose.v1.txt`.

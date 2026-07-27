# Analyze a Codebase

`codebase-mapper` can analyze either a local Git checkout or a cloneable Git
URL. For a full bundle with all current layers, use `scripts/run_xrefs.py`
with `--concepts`.

## Full Analysis

```bash
python scripts/run_xrefs.py \
  --repo /path/to/repo \
  --out _tmp/repo-map \
  --backend hash \
  --concepts
```

This runs:

| Layer | Output |
|---|---|
| L1 host | file inventory, AST summaries, imports, dependency/test edges |
| L2 chunks | source chunks and embedding vectors |
| L3 concepts | canonical concepts, co-occurrence graph, concept centroids |
| xrefs | symbol-level call, subclass, and override edges |

The output directory is a bundle containing `run_manifest.json`,
`inventory.ttl`, `inventory.jsonld`, `shapes.shacl.ttl`, chunk embedding
sidecars, concept sidecars, `xrefs.jsonl`, `ast_coverage.json`
(extraction-coverage honesty table — see
[ast-coverage.md](ast-coverage.md)), and optional `blobs/`.

Import semantics (Python): imports are extracted from **every scope**, and
each extracted record carries a `scope` tag — `module` (unconditional
top-level statement), `guarded` (module level inside `if TYPE_CHECKING:`,
`try/except`, or a loop), or `nested` (inside a function/method/class, i.e.
a lazy dependency). All three feed `cbm:imports` / `cbm:importsExternal`
edges: a lazy import is still a real file-to-file dependency. When an
unresolved top-level name is also a declared dependency, the external
classification wins over the internal suffix heuristic (name-shadowing
guard); an exact internal module-path match still wins over both.

`inventory.ttl` is typed by the SHACL shapes shipped alongside it as
`shapes.shacl.ttl`; the canonical Pydantic mirror for Python consumers
is
[`codebase_mapper/emission/domain/inventory_schema.py`](../codebase_mapper/emission/domain/inventory_schema.py)
(see [README § Inventory schema](../README.md#inventory-schema)).

## Local Repositories

The `--repo` value can be any local Git worktree:

```bash
python scripts/run_xrefs.py \
  --repo ~/src/my-project \
  --out _tmp/my-project-map \
  --backend hash \
  --concepts
```

Use `--state` to analyze a specific commit, tag, or branch:

```bash
python scripts/run_xrefs.py \
  --repo ~/src/my-project \
  --state v1.2.0 \
  --out _tmp/my-project-v1.2.0-map \
  --backend hash \
  --concepts
```

## GitHub URLs

The same `--repo` option accepts cloneable Git URLs. Remote repositories are
cloned into a temporary directory, checked out, analyzed, and removed when the
process exits.

Supported forms:

```bash
https://github.com/OWNER/REPO.git
git@github.com:OWNER/REPO.git
ssh://git@github.com/OWNER/REPO.git
github.com/OWNER/REPO
```

Examples:

```bash
python scripts/run_xrefs.py \
  --repo https://github.com/OWNER/REPO.git \
  --out _tmp/repo-map \
  --backend hash \
  --concepts

python scripts/run_xrefs.py \
  --repo git@github.com:OWNER/PRIVATE_REPO.git \
  --out _tmp/private-map \
  --backend hash \
  --concepts
```

For a remote branch or tag:

```bash
python scripts/run_xrefs.py \
  --repo https://github.com/OWNER/REPO.git \
  --state feature-branch \
  --out _tmp/repo-feature-map \
  --backend hash \
  --concepts
```

When a branch name is not available as a local ref after cloning, the CLI also
tries `origin/<branch>`.

## Embedding Backends

Three choices, all writing their identity into `embeddings_meta.json` so
downstream consumers know what produced the vectors.

| `--backend` | Vectors | Needs | Use when |
|---|---|---|---|
| `hash` | SHA-256 pseudo-vectors, **no semantics** | nothing | fast, fully deterministic runs and verifiers |
| `sbert` | real, in-process | `sentence-transformers` (+ torch) | semantic search without a server |
| `ollama` | real, over HTTP | a running Ollama server | semantic search without the torch stack, or to use a model Ollama hosts |

Use `hash` for fast, deterministic output:

```bash
--backend hash
```

Use `sbert` for semantic chunk search:

```bash
--backend sbert
```

`sbert` uses `sentence-transformers/all-MiniLM-L6-v2` by default and may
download model files on first use. Override the model with:

```bash
--sbert-model sentence-transformers/all-MiniLM-L6-v2
```

Use `ollama` to embed through a running Ollama server instead:

```bash
--backend ollama
--backend ollama --ollama-embed-model mxbai-embed-large
```

The default tag is `nomic-embed-text` (768-dim). The server address comes
from `$OLLAMA_HOST` (default `http://localhost:11434`), the same variable
the L4 enrichment layer uses. Pull the model first — `ollama pull
nomic-embed-text` — and pick a tag the server reports as
embedding-capable: a generation-only tag answers `/api/embed` with an
error (observed on Ollama 0.32.1: HTTP 501, *"This server does not
support embeddings"*), which the backend surfaces verbatim rather than
guessing.

The backend name recorded in the bundle is `ollama:<model>`. The FastAPI
search endpoint, the MCP `semantic_neighbors` tool, and the walkthrough
question panel all re-embed queries through that same model, and each
degrades to lexical matching when the server is unreachable.

Determinism honesty: `hash` is byte-identical everywhere. `sbert` and
`ollama` are deterministic forward passes, so repeated runs against the
same warm model reproduce, but bit-identity across machines or GPU
builds is not guaranteed for either.

## Excluding Files

Exclude generated or vendored paths per invocation:

```bash
python scripts/run_xrefs.py \
  --repo https://github.com/OWNER/REPO.git \
  --out _tmp/repo-map \
  --backend hash \
  --concepts \
  --exclude 'node_modules/**' \
  --exclude 'vendor/**' \
  --exclude 'dist/**'
```

Quote glob patterns in shells such as zsh so the shell does not expand them
before Python receives the argument.

You can also add a `.cbmignore` file at the target repo root:

```gitignore
.git
node_modules/**
vendor/**
dist/**
build/**
```

Patterns from `--exclude` and `.cbmignore` are merged and recorded in
`run_manifest.json`.

## Docker

The root `Dockerfile` builds an isolated analyzer image with Git, SSH client
support, Python dependencies, and `codebase-mapper` installed.

```bash
docker build -t codebase-mapper .
```

Run all layers against a GitHub URL and write the bundle to a mounted output
directory:

```bash
mkdir -p _tmp
docker run --rm -v "$PWD/_tmp:/work" codebase-mapper \
  https://github.com/OWNER/REPO.git --out /work/repo-map
```

The image defaults to:

```bash
python /opt/codebase-mapper/scripts/run_xrefs.py --concepts
```

You can pass the explicit `--repo` form if preferred:

```bash
docker run --rm -v "$PWD/_tmp:/work" codebase-mapper \
  --repo https://github.com/OWNER/REPO.git \
  --out /work/repo-map \
  --backend hash
```

Analyze a local checkout through Docker:

```bash
docker run --rm \
  -v "$PWD:/src:ro" \
  -v "$PWD/_tmp:/work" \
  codebase-mapper \
  --repo /src \
  --out /work/current-map \
  --backend hash \
  --exclude '_tmp/**'
```

For private GitHub repositories over SSH, mount your SSH config read-only:

```bash
docker run --rm \
  -v "$PWD/_tmp:/work" \
  -v "$HOME/.ssh:/root/.ssh:ro" \
  codebase-mapper \
  git@github.com:OWNER/PRIVATE_REPO.git --out /work/private-map
```

The default Docker image is optimized for `--backend hash` and does not install
the semantic embedding stack. Build a semantic image when you need `sbert`:

```bash
docker build --build-arg WITH_SBERT=1 -t codebase-mapper:sbert .

docker run --rm -v "$PWD/_tmp:/work" codebase-mapper:sbert \
  https://github.com/OWNER/REPO.git \
  --out /work/repo-map \
  --backend sbert
```

## Inspect the Bundle

Start the FastAPI backend against the generated bundle:

```bash
CBM_OUTPUT_DIR=_tmp/repo-map \
  uv run uvicorn frontend.backend.app:app --port 8000 --reload
```

Useful endpoints:

```bash
curl http://localhost:8000/api/summary
curl 'http://localhost:8000/api/file-graph?limit=100'
curl 'http://localhost:8000/api/concept-graph?limit=100'
curl 'http://localhost:8000/api/chunks?limit=20'
```

For the React UI, run the backend on port `8765`, then:

```bash
cd frontend/ui
npm install
npm run dev
```

The Vite dev server prints the local UI URL.

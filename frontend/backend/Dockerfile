FROM python:3.12-slim AS base

ARG WITH_SBERT=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Layer 1: backend's own runtime deps (FastAPI, uvicorn, rdflib, …).
# Caches whenever requirements*.txt is unchanged, independent of the
# codebase_mapper source tree.
COPY frontend/backend/requirements.txt frontend/backend/requirements-sbert.txt ./
RUN pip install -r requirements.txt && \
    if [ "$WITH_SBERT" = "1" ]; then pip install -r requirements-sbert.txt; fi

# ── Layer 2: install the in-repo `codebase_mapper` package.
# We install with --no-deps because the backend only imports
# `codebase_mapper.constants`, whose sole transitive dep (`rdflib`) is
# already pinned in requirements.txt. Pulling the package's full
# dependency closure (tree-sitter-*, sentence-transformers, …) would
# bloat this image by >2 GB for a single TOOL_VERSION constant.
# Build context is the REPO ROOT (see docker-compose.yml `context: ..`),
# so pyproject.toml and the package source resolve from there.
COPY pyproject.toml ./pyproject.toml
COPY codebase_mapper ./codebase_mapper
RUN pip install --no-deps .

# ── Layer 3: the backend app itself.
# `app.py` imports the `serving` package (application/, domain/, …) on nearly
# every route. Copying only app.py produced an image that died at import with
# `ModuleNotFoundError: No module named 'serving'` (BL-029) — and no CI job
# built the image, so nothing observed it. tests/verify_backend_image.py now
# derives this from app.py's imports: any first-party module the app imports
# must be COPYed here, or the check fails on the commit that adds it.
COPY frontend/backend/app.py ./app.py
COPY frontend/backend/serving ./serving

ENV CBM_OUTPUT_DIR=/data \
    PORT=8765

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD python -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:8765/api/healthz').read();" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]

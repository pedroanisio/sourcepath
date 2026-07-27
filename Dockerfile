FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*'

WORKDIR /opt/codebase-mapper

ARG WITH_SBERT=0

COPY pyproject.toml README.md ./
COPY codebase_mapper ./codebase_mapper
COPY plugins ./plugins
COPY scripts ./scripts
COPY docker/cbm-analyze /usr/local/bin/cbm-analyze

# Resolve dependencies from pyproject.toml — never a hand-maintained list.
#
# This previously installed an explicit package subset and then ran
# `pip install --no-deps .`, which suppressed the real dependency set. The
# list drifted from pyproject and the image shipped without `pydantic`,
# `pyoxigraph`, and the cpp/java/objc/cfml grammars: every invocation died
# with `ModuleNotFoundError: No module named 'pydantic'` while the build
# itself stayed green. `tests/verify_docker_deps.py` now fails if `--no-deps`
# comes back or a hand-maintained list reappears.
#
# The image stays lean because `sentence-transformers` (torch) is the
# `[sbert]` extra rather than a base dependency — opt in with
# `--build-arg WITH_SBERT=1`.
RUN pip install --upgrade pip \
    && if [ "$WITH_SBERT" = "1" ]; then pip install ".[sbert]"; else pip install .; fi \
    && chmod +x /usr/local/bin/cbm-analyze

WORKDIR /work

ENTRYPOINT ["cbm-analyze"]
CMD ["--help"]

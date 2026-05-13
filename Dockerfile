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

RUN pip install --upgrade pip \
    && pip install \
        rdflib \
        pyshacl \
        tree-sitter \
        tree-sitter-c \
        tree-sitter-go \
        tree-sitter-javascript \
        tree-sitter-kotlin \
        tree-sitter-ruby \
        tree-sitter-rust \
        tree-sitter-swift \
        tree-sitter-typescript \
        numpy \
        pyyaml \
    && pip install --no-deps . \
    && if [ "$WITH_SBERT" = "1" ]; then pip install sentence-transformers; fi \
    && chmod +x /usr/local/bin/cbm-analyze

WORKDIR /work

ENTRYPOINT ["cbm-analyze"]
CMD ["--help"]

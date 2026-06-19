# Makefile — code-base-mapper
#
# Single entry point for routine dev workflows. Targets are grouped to match
# the verifier layout in tests/ and the docker/frontend topology.
#
# Conventions:
#   - One canonical command per workflow (no silent divergence with README).
#   - Targets are .PHONY unless they materialise a file.
#   - `make help` lists every target annotated with `## description`.
#   - Variables (PYTHON, REPO, OUT, ...) are overridable on the command line.

PYTHON         ?= python
PIP            ?= $(PYTHON) -m pip
PYTEST         ?= $(PYTHON) -m pytest
DOCKER         ?= docker
IMAGE          ?= codebase-mapper
IMAGE_SBERT    ?= $(IMAGE):sbert

# Used by the `analyze` target. Override on the CLI:
#   make analyze REPO=https://github.com/foo/bar.git OUT=_tmp/bar-map
REPO           ?=
OUT            ?=

TESTS_DIR      := tests
FRONTEND_DIR   := frontend
UI_DIR         := $(FRONTEND_DIR)/ui

# Verifier groups — keep in lockstep with tests/verify_*.py and README.md.
DRIFT_VERIFIERS := \
	$(TESTS_DIR)/verify_drift_p1.py \
	$(TESTS_DIR)/verify_drift_p2.py \
	$(TESTS_DIR)/verify_drift_p3.py \
	$(TESTS_DIR)/verify_shape_coverage.py \
	$(TESTS_DIR)/verify_dependency_hygiene.py

CORE_VERIFIERS := \
	$(TESTS_DIR)/verify_roundtrip.py \
	$(TESTS_DIR)/verify_regenerate.py \
	$(TESTS_DIR)/verify_excludes.py \
	$(TESTS_DIR)/verify_repo_source.py \
	$(TESTS_DIR)/verify_timestamps.py \
	$(TESTS_DIR)/verify_l2.py \
	$(TESTS_DIR)/verify_l3.py \
	$(TESTS_DIR)/verify_xrefs.py \
	$(TESTS_DIR)/verify_repository_summary.py

VOCAB_VERIFIERS := \
	$(TESTS_DIR)/verify_vocab.py \
	$(TESTS_DIR)/verify_vocab_emission.py \
	$(TESTS_DIR)/verify_vocab_wiring.py \
	$(TESTS_DIR)/verify_vocab_pipeline.py

LANG_VERIFIERS := \
	$(TESTS_DIR)/verify_cpp.py \
	$(TESTS_DIR)/verify_dart.py \
	$(TESTS_DIR)/verify_java.py \
	$(TESTS_DIR)/verify_go.py \
	$(TESTS_DIR)/verify_clojure.py \
	$(TESTS_DIR)/verify_objc.py \
	$(TESTS_DIR)/verify_xsd_fixture.py \
	$(TESTS_DIR)/verify_proto_fixture.py

RUST_VERIFIERS := \
	$(TESTS_DIR)/verify_rust_ast.py \
	$(TESTS_DIR)/verify_rust_xrefs.py \
	$(TESTS_DIR)/verify_rust_tests_edges.py \
	$(TESTS_DIR)/verify_rust_attribute_query.py \
	$(TESTS_DIR)/verify_rust_super_self.py \
	$(TESTS_DIR)/verify_rust_regenerate.py \
	$(TESTS_DIR)/verify_rust_ast_body_count.py

# L4 LLM enrich — split by whether they require a live Ollama backend.
LLM_OFFLINE_VERIFIERS := \
	$(TESTS_DIR)/verify_llm_enrich.py \
	$(TESTS_DIR)/verify_llm_enrich_cache.py \
	$(TESTS_DIR)/verify_llm_enrich_prompts.py \
	$(TESTS_DIR)/verify_llm_enrich_offline.py \
	$(TESTS_DIR)/verify_llm_enrich_cli.py \
	$(TESTS_DIR)/verify_llm_enrich_ci_determinism.py

LLM_ONLINE_VERIFIERS := \
	$(TESTS_DIR)/verify_llm_enrich_file_summary.py \
	$(TESTS_DIR)/verify_llm_enrich_rdf.py \
	$(TESTS_DIR)/verify_llm_enrich_aggregator.py \
	$(TESTS_DIR)/verify_llm_enrich_determinism.py

.DEFAULT_GOAL := help

# ---------------------------------------------------------------- help / meta

.PHONY: help
help: ## Show this help (auto-generated from `##` annotations).
	@awk 'BEGIN {FS = ":.*?## "; printf "Usage: make <target>\n\nTargets:\n"} \
		/^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[1m%-22s\033[0m %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST)

# ----------------------------------------------------------- install / lint

.PHONY: install
install: ## Install package + dev extras editable (matches CI).
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: lint
lint: ## Enforce import boundaries (mirrors .github/workflows/lint.yml).
	lint-imports

# ----------------------------------------------------------------- tests

.PHONY: test
test: test-core test-vocab test-langs test-rust test-llm-offline test-drift ## Run the full offline test surface (skips Ollama-dependent verifiers).

.PHONY: test-core
test-core: ## Core round-trip, L2/L3, xrefs, repo summary.
	@for v in $(CORE_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-vocab
test-vocab: ## Controlled-vocabulary suite (loader, emission, wiring, pipeline).
	@for v in $(VOCAB_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-langs
test-langs: ## Per-language verifiers (C++, Dart, Java, Obj-C, XSD, proto).
	@for v in $(LANG_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-rust
test-rust: ## Rust 7-stage AST/xref/regenerate suite.
	@for v in $(RUST_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-llm-offline
test-llm-offline: ## L4 verifiers that do NOT require a live Ollama instance.
	@for v in $(LLM_OFFLINE_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-llm-online
test-llm-online: ## L4 verifiers that REQUIRE a reachable Ollama instance.
	@for v in $(LLM_ONLINE_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-llm
test-llm: test-llm-offline test-llm-online ## All L4 verifiers (offline + online).

.PHONY: test-drift
test-drift: ## Drift-risk checks (P1/P2/P3) + shape coverage + dep hygiene.
	@for v in $(DRIFT_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-ui
test-ui: ## Frontend (vitest) test suite.
	cd $(UI_DIR) && npm test

# ----------------------------------------------------------------- pipelines

.PHONY: analyze
analyze: ## Run end-to-end analysis. Requires REPO=<url|path> OUT=<dir>.
	@if [ -z "$(REPO)" ] || [ -z "$(OUT)" ]; then \
		echo "usage: make analyze REPO=<url|path> OUT=<dir>"; exit 2; \
	fi
	$(PYTHON) -m codebase_mapper --repo $(REPO) --out $(OUT)

.PHONY: run-l2 run-l3 run-l4 run-xrefs

run-l2: ## scripts/run_l2.py — chunks + embeddings layer.
	$(PYTHON) scripts/run_l2.py $(ARGS)

run-l3: ## scripts/run_l3.py — concept graph layer.
	$(PYTHON) scripts/run_l3.py $(ARGS)

run-l4: ## scripts/run_l4.py — LLM enrich layer (needs Ollama).
	$(PYTHON) scripts/run_l4.py $(ARGS)

run-xrefs: ## scripts/run_xrefs.py — symbol cross-references.
	$(PYTHON) scripts/run_xrefs.py $(ARGS)

# ----------------------------------------------------------------- docker

.PHONY: docker-build
docker-build: ## Build the CLI image (hash backend only).
	$(DOCKER) build -t $(IMAGE) .

.PHONY: docker-build-sbert
docker-build-sbert: ## Build the CLI image with sentence-transformers.
	$(DOCKER) build --build-arg WITH_SBERT=1 -t $(IMAGE_SBERT) .

.PHONY: docker-run
docker-run: ## Run the CLI image. Forward args via ARGS="...".
	$(DOCKER) run --rm -v "$(CURDIR)/_tmp:/work" $(IMAGE) $(ARGS)

.PHONY: frontend-up
frontend-up: ## Bring up backend + UI via frontend/docker-compose.yml.
	cd $(FRONTEND_DIR) && $(DOCKER) compose up -d --build

.PHONY: frontend-down
frontend-down: ## Stop the frontend stack.
	cd $(FRONTEND_DIR) && $(DOCKER) compose down

.PHONY: frontend-logs
frontend-logs: ## Tail logs from the frontend stack.
	cd $(FRONTEND_DIR) && $(DOCKER) compose logs -f

# ----------------------------------------------------------------- clean

.PHONY: clean
clean: ## Remove caches, coverage, and build artefacts (keeps _tmp/).
	rm -rf .pytest_cache .import_linter_cache .coverage \
		codebase_mapper.egg-info build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-tmp
clean-tmp: ## Remove _tmp/ (generated analysis bundles). Destructive.
	rm -rf _tmp

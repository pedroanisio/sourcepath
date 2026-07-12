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

# `dist-zip` base name; the target appends a UTC timestamp + `.zip`.
DIST_NAME      ?= code-base-mapper

# Verifier groups — keep in lockstep with tests/verify_*.py and README.md.
DRIFT_VERIFIERS := \
	$(TESTS_DIR)/verify_drift_p1.py \
	$(TESTS_DIR)/verify_drift_p2.py \
	$(TESTS_DIR)/verify_drift_p3.py \
	$(TESTS_DIR)/verify_shape_coverage.py \
	$(TESTS_DIR)/verify_dependency_hygiene.py \
	$(TESTS_DIR)/verify_report_spec.py \
	$(TESTS_DIR)/verify_api_field_parity.py \
	$(TESTS_DIR)/verify_report_predicates.py \
	$(TESTS_DIR)/verify_report_rs_contract.py \
	$(TESTS_DIR)/verify_requirements_mirror.py \
	$(TESTS_DIR)/verify_readme_coverage.py \
	$(TESTS_DIR)/verify_ci_live_bundle.py \
	$(TESTS_DIR)/verify_backend_image.py \
	$(TESTS_DIR)/verify_make_wiring.py

CORE_VERIFIERS := \
	$(TESTS_DIR)/verify_roundtrip.py \
	$(TESTS_DIR)/verify_regenerate.py \
	$(TESTS_DIR)/verify_excludes.py \
	$(TESTS_DIR)/verify_repo_source.py \
	$(TESTS_DIR)/verify_timestamps.py \
	$(TESTS_DIR)/verify_l2.py \
	$(TESTS_DIR)/verify_l3.py \
	$(TESTS_DIR)/verify_xrefs.py \
	$(TESTS_DIR)/verify_repository_summary.py \
	$(TESTS_DIR)/verify_ast_coverage.py \
	$(TESTS_DIR)/verify_grammar_disclosure.py \
	$(TESTS_DIR)/verify_progress.py \
	$(TESTS_DIR)/verify_golden_repo.py \
	$(TESTS_DIR)/verify_dimension_shapes.py

VOCAB_VERIFIERS := \
	$(TESTS_DIR)/verify_vocab.py \
	$(TESTS_DIR)/verify_vocab_emission.py \
	$(TESTS_DIR)/verify_vocab_wiring.py \
	$(TESTS_DIR)/verify_vocab_pipeline.py

LANG_VERIFIERS := \
	$(TESTS_DIR)/verify_cpp.py \
	$(TESTS_DIR)/verify_dart.py \
	$(TESTS_DIR)/verify_sql.py \
	$(TESTS_DIR)/verify_html.py \
	$(TESTS_DIR)/verify_css.py \
	$(TESTS_DIR)/verify_json.py \
	$(TESTS_DIR)/verify_yaml.py \
	$(TESTS_DIR)/verify_shell.py \
	$(TESTS_DIR)/verify_php.py \
	$(TESTS_DIR)/verify_java.py \
	$(TESTS_DIR)/verify_go.py \
	$(TESTS_DIR)/verify_clojure.py \
	$(TESTS_DIR)/verify_cobol.py \
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
	$(TESTS_DIR)/verify_llm_enrich_degradation.py \
	$(TESTS_DIR)/verify_llm_enrich_cli.py \
	$(TESTS_DIR)/verify_llm_enrich_ci_determinism.py \
	$(TESTS_DIR)/verify_bench_llm_models.py

LLM_ONLINE_VERIFIERS := \
	$(TESTS_DIR)/verify_llm_enrich_file_summary.py \
	$(TESTS_DIR)/verify_llm_enrich_rdf.py \
	$(TESTS_DIR)/verify_llm_enrich_aggregator.py \
	$(TESTS_DIR)/verify_llm_enrich_determinism.py

# Reporting surface — pytest suites for the cbm.py tools (dispatcher, X-ray
# caveats, evidence banner, authored-report PDF pipeline, dossier, static
# site). The Rust crate's own tests run via `test-report-rs`.
REPORTING_TESTS := \
	$(TESTS_DIR)/test_cbm_cli.py \
	$(TESTS_DIR)/test_cbm_walkthrough.py \
	$(TESTS_DIR)/test_report_caveats.py \
	$(TESTS_DIR)/test_evidence_banner.py \
	$(TESTS_DIR)/test_report_to_pdf.py \
	$(TESTS_DIR)/test_cbm_dossier.py \
	$(TESTS_DIR)/test_static_site.py \
	$(TESTS_DIR)/test_cbm_cartogram.py \
	$(TESTS_DIR)/test_python_import_scopes.py \
	$(TESTS_DIR)/test_env_settings.py

CBM_REPORT_MANIFEST := tools/cbm-report/Cargo.toml
CBM_CARTOGRAM_DIR   := tools/cbm-cartogram

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
test: test-core test-vocab test-langs test-rust test-llm-offline test-drift test-units test-backend test-docs test-report-rs test-cartogram test-backlog-governance ## Run the full offline test surface (skips Ollama-dependent verifiers).

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

.PHONY: check
check: ## Goal + drift gate: TIOBE-50 language-support ledger, then the drift verifiers.
	@echo "== tests/verify_language_goal.py =="
	@$(PYTHON) $(TESTS_DIR)/verify_language_goal.py || exit $$?
	@for v in $(DRIFT_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-drift
test-drift: ## Drift-risk checks (P1/P2/P3) + shape coverage + dep hygiene.
	@for v in $(DRIFT_VERIFIERS); do echo "== $$v =="; $(PYTHON) $$v || exit $$?; done

.PHONY: test-units
test-units: ## Whole pytest tree under tests/ (unit suites incl. decomposer + recomposer).
	$(PYTEST) $(TESTS_DIR) -q

.PHONY: regen-shacl-golden
regen-shacl-golden: ## Regenerate the SHACL golden after an INTENTIONAL shape change (tests/test_shacl_spec.py pins against it).
	$(PYTHON) -c "import sys; sys.path.insert(0, '.'); \
	from tests.test_shacl_spec import build_full_spec_graph, GOLDEN; \
	build_full_spec_graph().serialize(destination=str(GOLDEN), format='turtle'); \
	print(f'regenerated {GOLDEN}')"

.PHONY: test-backend
test-backend: ## Frontend service pytest suites (FastAPI backend + MCP server).
	$(PYTEST) $(FRONTEND_DIR)/backend/tests $(FRONTEND_DIR)/mcp_server/tests -q --no-cov

.PHONY: test-docs
test-docs: ## Documentation hygiene (README disclaimers, local links, stale docs).
	$(PYTHON) $(TESTS_DIR)/verify_doc_hygiene.py

.PHONY: test-reporting
test-reporting: ## Focused reporting pytest subset (superset runs via test-units).
	$(PYTEST) $(REPORTING_TESTS) -q

.PHONY: test-report-rs
test-report-rs: ## Rust cbm-report crate unit tests (disclosed skip when cargo is absent).
	@if command -v cargo >/dev/null 2>&1; then \
		cargo test --manifest-path $(CBM_REPORT_MANIFEST) --quiet; \
	else \
		echo "test-report-rs: cargo not found — Rust crate tests SKIPPED (disclosed, not silent)"; \
	fi

.PHONY: test-ui
test-ui: ## Frontend (vitest) test suite.
	cd $(UI_DIR) && npm test

.PHONY: test-backlog-governance
test-backlog-governance: ## Unit tests for the backlog governance/stats script (Node); disclosed skip when node is absent.
	@if command -v node >/dev/null 2>&1; then \
		node --test scripts/tests/*.test.mjs; \
	else \
		echo "test-backlog-governance: node not found — backlog governance tests SKIPPED (disclosed, not silent)"; \
	fi

.PHONY: test-cartogram
test-cartogram: ## Cartogram model tests (Node); disclosed skip when node is absent.
	@if command -v node >/dev/null 2>&1; then \
		cd $(CBM_CARTOGRAM_DIR) && node --test tests/*.test.mjs; \
	else \
		echo "test-cartogram: node not found — Cartogram tests SKIPPED (disclosed, not silent)"; \
	fi

.PHONY: lint-cartogram
lint-cartogram: ## Parse-check Cartogram JS (node --check); disclosed skip when node is absent.
	@if command -v node >/dev/null 2>&1; then \
		for f in $(CBM_CARTOGRAM_DIR)/src/*.js $(CBM_CARTOGRAM_DIR)/tools/*.mjs; do node --check "$$f" || exit 1; done; \
		echo "lint-cartogram: all Cartogram JS parses"; \
	else \
		echo "lint-cartogram: node not found — SKIPPED (disclosed, not silent)"; \
	fi

.PHONY: build-cartogram
build-cartogram: ## Build the standalone Cartogram HTML from a cbm bundle. Requires INVENTORY=<path/to/inventory.jsonld> (produced by scripts/run_l3.py or run_l4.py).
	@command -v node >/dev/null 2>&1 || { echo "build-cartogram: node not found — cannot build"; exit 1; }
	@test -n "$(INVENTORY)" || { echo "build-cartogram: set INVENTORY=<inventory.jsonld> from a run_l3.py/run_l4.py bundle (a bare codebase-mapper L1 bundle has no concepts/chunks and is rejected)"; exit 2; }
	node $(CBM_CARTOGRAM_DIR)/tools/normalize-inventory.mjs "$(INVENTORY)" $(CBM_CARTOGRAM_DIR)/data/atlas-data.js
	node $(CBM_CARTOGRAM_DIR)/tools/build-standalone.mjs

# ----------------------------------------------------------------- pipelines

.PHONY: validate-ontology
validate-ontology: ## Install RDF/SHACL toolchain (idempotent) and validate the ontology TTL(s).
	uv run scripts/setup_and_validate_ontology.py $(ARGS)

.PHONY: validate-abox
validate-abox: ## Validate a generated ABox against the TBox shapes. Requires ABOX=<file.ttl>.
	@if [ -z "$(ABOX)" ]; then echo "usage: make validate-abox ABOX=<file.ttl>"; exit 2; fi
	uv run scripts/setup_and_validate_ontology.py --tbox static/schemas/software_architecture_dimensions.ttl $(ABOX)

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

.PHONY: build-report-rs
build-report-rs: ## Compile the Rust cbm-report PDF renderer (needed by `cbm.py report-rs`).
	cargo build --release --manifest-path $(CBM_REPORT_MANIFEST)

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

# ----------------------------------------------------------------- package

.PHONY: dist-zip
dist-zip: ## Clean, timestamped source zip (git-tracked + new files, honors .gitignore).
	@TS=$$(date -u +%Y%m%d-%H%M%S); \
	OUT="$(DIST_NAME)-$$TS.zip"; \
	git ls-files --cached --others --exclude-standard -z \
		| $(PYTHON) scripts/pack_clean_zip.py "$$OUT" "$(DIST_NAME)"

# ----------------------------------------------------------------- clean

.PHONY: clean
clean: ## Remove caches, coverage, and build artefacts (keeps _tmp/).
	rm -rf .pytest_cache .import_linter_cache .coverage \
		codebase_mapper.egg-info build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-tmp
clean-tmp: ## Remove _tmp/ (generated analysis bundles). Destructive.
	rm -rf _tmp

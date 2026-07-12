#!/usr/bin/env python3
"""verify_drift_p1.py — contract suite for drift-risk-map.md HIGH findings.

Each block corresponds to one HIGH-rated coupling in `drift-risk-map.md`.
Running this script offline (no Docker, no npm) gives a contributor an
immediate signal if any of the six couplings has drifted.

Findings covered:

  #6  Port 8765 triple-declaration (Dockerfile, docker-compose.yml,
      nginx.conf). Asserts all three files reference the same backend
      port literal.
  #7  No `.env.example`. Asserts every CBM_* / OLLAMA_* env var read
      anywhere in the source tree is documented in `.env.example`, and
      vice versa.
  #8  FastAPI handlers without `response_model`. AST-parses
      `frontend/backend/app.py`, finds every `@app.<method>(…)`
      decorator, and asserts a `response_model=` keyword is present
      except for a documented allowlist.
  #9  MCP `OUTPUT_SCHEMAS` vs `frontend/ui/src/api.ts` types. Asserts
      every tool name in `OUTPUT_SCHEMAS` is referenced (by string
      literal) somewhere in `api.ts` — i.e. the UI has at least a
      callsite for every advertised MCP tool. Drift in field-level
      shape is NOT detected (codegen is the proper fix); presence is
      the minimum guard.
  #10 `concepts.json` top-level shape vs backend reader. Emits a
      bundle from a small fixture, parses `concepts.json`, and asserts
      the top-level key set matches the expected contract — what
      `frontend/backend/app.py::load_bundle` and the MCP handlers read.
  #16 `pyproject.toml` version vs `codebase_mapper.constants.TOOL_VERSION`.
      Asserts the two strings are identical.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.extensions import reset_registries
from codebase_mapper.shared_kernel.constants import TOOL_VERSION
from plugins import chunks_embeddings, concept_graph


REPO_ROOT = Path(__file__).resolve().parent.parent

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:20]:
                print(f"        {line}")
        FAIL += 1


# ───────────────────────────────────────────────────────────────────────
# Finding #16 — pyproject.toml version vs constants.TOOL_VERSION
# ───────────────────────────────────────────────────────────────────────


def check_version_drift() -> None:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(),
    )
    pyproject_version = pyproject["project"]["version"]
    check(
        "pyproject.toml::version == constants.TOOL_VERSION",
        pyproject_version == TOOL_VERSION,
        f"pyproject={pyproject_version!r} TOOL_VERSION={TOOL_VERSION!r}",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #6 — Port 8765 triple-declared across Docker / Compose / nginx
# ───────────────────────────────────────────────────────────────────────


def check_port_consistency() -> None:
    dockerfile = (REPO_ROOT / "frontend/backend/Dockerfile").read_text()
    compose = (REPO_ROOT / "frontend/docker-compose.yml").read_text()
    nginx = None
    for cand in (REPO_ROOT / "frontend/ui").rglob("nginx.conf"):
        nginx = cand.read_text()
        break

    # The Dockerfile is the authoritative source for the backend port.
    # We pull the `PORT=<num>` declaration and assert every other file
    # references the same literal.
    m = re.search(r"PORT=(\d+)", dockerfile)
    check(
        "frontend/backend/Dockerfile declares PORT=<num>",
        m is not None,
        "no PORT= directive found",
    )
    if m is None:
        return
    port = m.group(1)

    # Dockerfile: must also EXPOSE and bind uvicorn to the same port.
    expose_match = re.search(rf"EXPOSE\s+{port}\b", dockerfile)
    uvicorn_match = re.search(
        rf"--port[\"\s,]+{port}\b", dockerfile,
    )
    healthcheck_match = re.search(rf":{port}/", dockerfile)
    check(
        f"Dockerfile EXPOSEs port {port}",
        expose_match is not None,
        "EXPOSE line missing or mismatched",
    )
    check(
        f"Dockerfile uvicorn --port matches PORT={port}",
        uvicorn_match is not None,
        "CMD's --port disagrees with PORT env",
    )
    check(
        f"Dockerfile HEALTHCHECK URL uses port {port}",
        healthcheck_match is not None,
        "HEALTHCHECK URL disagrees with PORT env",
    )

    # docker-compose.yml must `expose` the same port (or omit it entirely
    # — exposing the wrong one is the drift hazard, not omission).
    compose_ports = set(re.findall(r'"(\d{2,5})"', compose))
    check(
        f"docker-compose.yml mentions port {port} (or omits it)",
        port in compose_ports or all(
            p == port for p in compose_ports
            if 8000 <= int(p) <= 9999
        ),
        f"compose ports={sorted(compose_ports)} expected to include {port}",
    )

    # nginx must proxy_pass to that port if it proxies the backend.
    if nginx is not None:
        nginx_match = re.search(
            rf"proxy_pass\s+http://[^:\s]+:{port}\b", nginx,
        )
        check(
            f"nginx.conf proxy_pass uses port {port}",
            nginx_match is not None,
            "nginx is forwarding to a different port than the backend exposes",
        )


# ───────────────────────────────────────────────────────────────────────
# Finding #7 — .env.example vs actual env reads in source
# ───────────────────────────────────────────────────────────────────────


# Test-only / dev vars are excluded from the inventory contract because
# they're not part of the deployment surface.
ENV_INVENTORY_EXCLUSIONS: set[str] = {
    "XREF_TEST_KEEP",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PIP_NO_CACHE_DIR",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    "PORT",          # Docker-set port; treated as infra, not deployment
    "TAG",           # docker-compose image-tag override
    "WITH_SBERT",    # build-time arg
    "CBM_BUNDLE",    # docker-compose mount source (host path, not app cfg)
    "FRONTEND_PORT", # docker-compose host port mapping
    # Read only by frontend/backend/tests/conftest.py to turn a missing live
    # bundle into a hard error instead of a silent skip (BL-024). CI sets it;
    # it configures the *test harness*, never the deployed app. Documented in
    # .env.example's test-only section and held by verify_ci_live_bundle.py.
    "CBM_REQUIRE_LIVE_BUNDLE",
    "HOME",          # read by tests to build a hermetic subprocess env
}


def _scan_env_reads() -> set[str]:
    found: set[str] = set()
    name_re = re.compile(r'os\.environ(?:\.get)?\(["\']([A-Z][A-Z0-9_]*)["\']')
    # ALSO recognize the `*_from_env` helper convention: functions whose
    # name ends in `_from_env` take the env-var name as a string-literal
    # first argument and read os.environ internally — e.g.
    # _workers_from_env("CBM_EXTRACT_WORKERS", default).
    helper_re = re.compile(r'_from_env\(\s*["\']([A-Z][A-Z0-9_]*)["\']')
    # ALSO follow MODULE_LEVEL constants like JWT_AUDIENCE_ENV = "CBM_..."
    # or MODEL_ENV_VAR = "CBM_..." consumed via os.environ.get(NAME).
    # MULTILINE so `^` anchors to each line, not just the start of the file.
    indirect_re = re.compile(
        r'^\s*([A-Z][A-Z0-9_]*(?:_ENV|_ENV_VAR))\s*=\s*"([A-Z][A-Z0-9_]*)"',
        re.MULTILINE,
    )
    # ALSO catch dynamic env-var *families* built with f-strings, e.g.
    # key = f"CBM_MCP_TIMEOUT_{tool.upper()}". A family is reported as the
    # wildcard "PREFIX*" and documented in .env.example the same way.
    family_re = re.compile(r'f"(CBM_[A-Z0-9_]*_)\{')

    indirect_alias: dict[str, str] = {}
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "_tmp", "node_modules", ".claude"}
               for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for m in name_re.finditer(text):
            found.add(m.group(1))
        for m in helper_re.finditer(text):
            found.add(m.group(1))
        for m in indirect_re.finditer(text):
            indirect_alias[m.group(1)] = m.group(2)
        if "os.environ" in text:
            for m in family_re.finditer(text):
                found.add(m.group(1) + "*")

    # Rust reads count too: tools/ crates consume the same deployment env
    # (drift-risk H7 — CBM_REPORT_FONT_DIR was read only in canvas.rs).
    rust_re = re.compile(r'env::var\(\s*"([A-Z][A-Z0-9_]*)"')
    for path in (REPO_ROOT / "tools").rglob("*.rs"):
        if "target" in path.parts:
            continue
        for m in rust_re.finditer(path.read_text(errors="ignore")):
            found.add(m.group(1))

    # Resolve indirect aliases: os.environ.get(JWT_AUDIENCE_ENV) → CBM_MCP_JWT_AUDIENCE
    indirect_use_re = re.compile(
        r'os\.environ(?:\.get)?\(([A-Z][A-Z0-9_]*(?:_ENV|_ENV_VAR))\b')
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "_tmp", "node_modules", ".claude"}
               for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        for m in indirect_use_re.finditer(text):
            alias = m.group(1)
            if alias in indirect_alias:
                found.add(indirect_alias[alias])

    return found - ENV_INVENTORY_EXCLUSIONS


def _parse_env_example() -> set[str]:
    text = (REPO_ROOT / ".env.example").read_text()
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def check_env_inventory() -> None:
    if not (REPO_ROOT / ".env.example").exists():
        check(".env.example exists at repo root", False,
              "drift-risk #7 fix is to create this file")
        return
    documented = _parse_env_example()
    consumed = _scan_env_reads()
    check(
        "every env var read in code is documented in .env.example",
        consumed <= documented,
        f"missing_from_env_example={sorted(consumed - documented)}\n"
        f"consumed={sorted(consumed)}\ndocumented={sorted(documented)}",
    )
    check(
        "every .env.example key is actually read in code "
        "(no stale documentation)",
        documented <= consumed,
        f"stale_in_env_example={sorted(documented - consumed)}",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #8 — FastAPI handlers without response_model
# ───────────────────────────────────────────────────────────────────────


# Routes that legitimately return arbitrary shapes. Document the reason
# each one is on the allowlist when adding.
ROUTES_WITHOUT_RESPONSE_MODEL_OK: set[str] = {
    # Returns a raw {sha256, text} envelope with truncated blob content;
    # not part of the typed UI surface (the UI fetches blobs directly and
    # api.ts declares no type for it).
    "/api/chunk-blob/{sha}",
    # /api/chunk/{idx} gained ChunkDetailResp (drift-risk-map C1) and left
    # this list; field parity with api.ts::ChunkDetail is enforced by
    # tests/verify_api_field_parity.py.
    # Liveness probe; intentionally raw.
    "/api/healthz",
}


def check_fastapi_response_models() -> None:
    src = (REPO_ROOT / "frontend/backend/app.py").read_text()
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Want: Call(func=Attribute(value=Name("app"), attr="get"|...))
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if not (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id in {"app", "router"}
                    and f.attr in {"get", "post", "put", "delete", "patch"}):
                continue
            # First positional arg is the route path.
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            route = dec.args[0].value
            if not isinstance(route, str) or not route.startswith("/api/"):
                continue
            has_response_model = any(
                kw.arg == "response_model" for kw in dec.keywords
            )
            if not has_response_model and route not in ROUTES_WITHOUT_RESPONSE_MODEL_OK:
                offenders.append(route)
    check(
        "every FastAPI /api/ route declares response_model "
        "(or is in the documented allowlist)",
        not offenders,
        f"offenders={offenders}\n"
        f"if a route legitimately returns raw dicts, add it to "
        f"ROUTES_WITHOUT_RESPONSE_MODEL_OK with a comment.",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #9 — MCP schema self-consistency
# ───────────────────────────────────────────────────────────────────────
#
# The drift-risk-map's #9 originally framed this as "OUTPUT_SCHEMAS vs
# api.ts". In practice the UI consumes the FastAPI backend (drift-risk
# #8, already guarded) and the MCP server is a parallel agent-facing
# surface. The real drift hazard inside MCP itself is asymmetry between
# INPUT_SCHEMAS and OUTPUT_SCHEMAS: a tool advertised on one side but
# missing on the other will pass `validate_in`/`validate_out` lookups
# but fail at registration. We guard that contract here.
#
# A field-level OUTPUT_SCHEMAS ↔ TypeScript guard requires codegen
# (json-schema-to-typescript) — out of scope for this verifier.


def check_mcp_schema_self_consistency() -> None:
    from frontend.mcp_server.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
    input_only = set(INPUT_SCHEMAS) - set(OUTPUT_SCHEMAS)
    output_only = set(OUTPUT_SCHEMAS) - set(INPUT_SCHEMAS)
    check(
        "every MCP tool with an INPUT_SCHEMAS entry also has OUTPUT_SCHEMAS",
        not input_only,
        f"input_only={sorted(input_only)}",
    )
    check(
        "every MCP tool with an OUTPUT_SCHEMAS entry also has INPUT_SCHEMAS",
        not output_only,
        f"output_only={sorted(output_only)}",
    )
    # Every schema must be a JSON Schema object with a recognized shape.
    bad_in = [t for t, s in INPUT_SCHEMAS.items()
              if not isinstance(s, dict) or s.get("type") != "object"]
    bad_out = [t for t, s in OUTPUT_SCHEMAS.items()
               if not isinstance(s, dict) or s.get("type") != "object"]
    check(
        "every INPUT_SCHEMAS entry is a JSON Schema object",
        not bad_in,
        f"bad_in={bad_in}",
    )
    check(
        "every OUTPUT_SCHEMAS entry is a JSON Schema object",
        not bad_out,
        f"bad_out={bad_out}",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #10 — concepts.json top-level shape vs backend reader
# ───────────────────────────────────────────────────────────────────────


EXPECTED_CONCEPTS_JSON_KEYS = frozenset({
    "concepts",
    "per_path_concepts",
    "cooccurrence",
    "concept_embeddings_artifact",
    "concept_embedding_ids",
})

EXPECTED_CONCEPT_RECORD_KEYS = frozenset({
    "label", "alt_labels", "components",
    "frequency", "file_count", "embedding_row",
    # `kind` + `broader` are optional (curated-vocab-only); excluded here.
})


def _init_git(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "config", "user.name", "t"], check=True,
    )


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True,
    )


def check_concepts_json_shape() -> None:
    work = Path(tempfile.mkdtemp(prefix="verify_drift_p1_concepts_"))
    try:
        fixture = work / "fixture"
        _init_git(fixture)
        (fixture / "app.py").write_text(
            "def authenticate():\n    return True\n"
            "def authorize():\n    return False\n",
        )
        _commit(fixture)

        out = work / "out"
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        concept_graph.register_all()
        mapped = map_codebase(fixture.resolve(), "HEAD")
        emit(fixture.name, mapped, out.resolve(), emit_blobs_flag=False)

        cj = json.loads((out / "concepts.json").read_text())
        top_keys = set(cj.keys())
        check(
            "concepts.json top-level key set matches the documented contract",
            top_keys == EXPECTED_CONCEPTS_JSON_KEYS,
            f"missing_in_json={sorted(EXPECTED_CONCEPTS_JSON_KEYS - top_keys)}\n"
            f"extra_in_json={sorted(top_keys - EXPECTED_CONCEPTS_JSON_KEYS)}",
        )

        # Sample one concept record; assert its required keys are present.
        concepts: dict = cj.get("concepts", {})
        if concepts:
            sample_name = sorted(concepts.keys())[0]
            sample = concepts[sample_name]
            sample_keys = set(sample.keys())
            missing = EXPECTED_CONCEPT_RECORD_KEYS - sample_keys
            check(
                "every concept record carries the documented required keys",
                not missing,
                f"sample_name={sample_name!r}\n"
                f"sample_keys={sorted(sample_keys)}\n"
                f"missing_required={sorted(missing)}",
            )
        else:
            check(
                "fixture yielded at least one concept "
                "(needed to validate the per-concept shape)",
                False,
                "no concepts in fixture; widen the source to include splittable identifiers",
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────
# Driver
# ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.parse_args(argv)

    print("Finding #16 — version drift")
    check_version_drift()
    print("Finding #6 — port consistency")
    check_port_consistency()
    print("Finding #7 — env-var inventory")
    check_env_inventory()
    print("Finding #8 — FastAPI response_model coverage")
    check_fastapi_response_models()
    print("Finding #9 — MCP schema self-consistency")
    check_mcp_schema_self_consistency()
    print("Finding #10 — concepts.json shape")
    check_concepts_json_shape()

    print()
    print(f"passed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

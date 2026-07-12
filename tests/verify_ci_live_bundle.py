#!/usr/bin/env python3
"""verify_ci_live_bundle.py — CI must not be able to skip its own backend suite.

BL-024. The `drift` job generated a live fixture bundle into `_tmp/ci-bundle`
and then ran the backend + MCP suites against it. Nothing ever set
`CBM_OUTPUT_DIR` to that path, and the suite's default is a *different* path
(`_tmp/usl-ng-core-map`). So `conftest.pytest_collection_modifyitems` found no
bundle, marked every live-bundle test skipped — and the job reported green.

The REST surface was therefore untested in CI while appearing tested. A test
that skips silently and still reports success is worse than no test: it
manufactures unearned confidence.

Two things must hold, and this verifier pins both:

  1. **Path agreement.** The directory the workflow *writes* the bundle to
     (`run_l3.py --out <dir>`) is the directory the test step *reads*
     (`CBM_OUTPUT_DIR`). A rename of one without the other is the original bug.

  2. **The skip must be unavailable in CI.** Path agreement alone is not
     enough: if bundle generation ever silently produced nothing, the suite
     would go back to skipping green. So the skip is opt-out —
     `CBM_REQUIRE_LIVE_BUNDLE=1` turns "bundle missing" from a skip into a hard
     collection error, and CI sets it. Locally, a fresh checkout with no bundle
     still skips, which is the behavior contributors want.
"""
from __future__ import annotations

import argparse
import re
import sys

from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/lint.yml"
CONFTEST = ROOT / "frontend/backend/tests/conftest.py"

#: The env var the backend conftest reads to locate the live bundle.
BUNDLE_ENV = "CBM_OUTPUT_DIR"
#: The env var that makes a missing bundle fatal instead of skippable.
REQUIRE_ENV = "CBM_REQUIRE_LIVE_BUNDLE"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _steps(workflow: dict) -> list[tuple[str, dict]]:
    """Every (job_name, step) pair in the workflow."""
    out: list[tuple[str, dict]] = []
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in (job.get("steps") or []):
            out.append((job_name, step))
    return out


def _env_for(job: dict, step: dict) -> dict:
    """Effective env for a step: job-level env overlaid with step-level env."""
    env = dict(job.get("env") or {})
    env.update(step.get("env") or {})
    return env


def main() -> int:
    argparse.ArgumentParser().parse_args()
    print("== CI live-bundle wiring (BL-024) ==")

    workflow = yaml.safe_load(WORKFLOW.read_text())
    jobs = workflow.get("jobs") or {}

    # --- 1. Find the step that GENERATES the bundle, and the path it writes.
    produced: dict[str, str] = {}   # job -> out dir
    for job_name, step in _steps(workflow):
        run = str(step.get("run") or "")
        m = re.search(r"run_l3\.py[^\n]*?--out\s+(?P<out>\S+)", run)
        if m:
            produced[job_name] = m.group("out")

    check("a CI step generates a live fixture bundle",
          bool(produced), "no `run_l3.py --out <dir>` step found")
    if not produced:
        print(f"\nPassed: {PASS}    Failed: {FAIL}")
        return 1

    # --- 2. Find the step that CONSUMES it (runs the backend suite).
    for job_name, out_dir in produced.items():
        job = jobs[job_name]
        consumers = [
            step for step in (job.get("steps") or [])
            if re.search(r"\b(make test-backend|pytest\b.*frontend/backend)",
                         str(step.get("run") or ""))
        ]
        check(f"{job_name}: a step runs the backend suite against the bundle",
              bool(consumers),
              "the job generates a bundle nothing consumes")

        for step in consumers:
            env = _env_for(job, step)
            name = step.get("name", "(unnamed)")

            # The bug: the consumer never pointed at the produced path.
            check(f"{job_name}/{name}: {BUNDLE_ENV} is set",
                  BUNDLE_ENV in env,
                  f"env={env or '{}'} — the suite would look at its default "
                  f"path and skip every live-bundle test, green")

            check(f"{job_name}/{name}: {BUNDLE_ENV} points at the generated bundle",
                  env.get(BUNDLE_ENV, "").rstrip("/") == out_dir.rstrip("/"),
                  f"{BUNDLE_ENV}={env.get(BUNDLE_ENV)!r} but the bundle is "
                  f"written to {out_dir!r}")

            # Path agreement is not enough — the skip itself must be disabled.
            check(f"{job_name}/{name}: {REQUIRE_ENV} makes a missing bundle fatal",
                  str(env.get(REQUIRE_ENV, "")).strip() not in ("", "0", "false"),
                  f"{REQUIRE_ENV}={env.get(REQUIRE_ENV)!r} — without it, a bundle "
                  f"that failed to generate would silently skip and report green")

    # --- 3. The conftest must actually honor the escape hatch.
    conftest = CONFTEST.read_text()
    check(f"conftest reads {BUNDLE_ENV}", BUNDLE_ENV in conftest)
    check(f"conftest honors {REQUIRE_ENV} (missing bundle becomes an error)",
          REQUIRE_ENV in conftest,
          "the workflow could set it, but nothing would act on it")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Documentation hygiene guards.

These checks cover repo-local documentation invariants that otherwise drift
silently: README disclaimer compliance and active Markdown local links.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
README_DISCLAIMER_RE = re.compile(
    r"## Disclaimer\s+"
    r"This work is subject to the methodological caveats and commitments "
    r"described in \[@DISCLAIMER\.md\]\((?P<target>[^)]+)\)\.\s+"
    r"> No statement or premise not backed by a real logical definition or "
    r"verifiable reference should be taken for granted\.",
    re.MULTILINE,
)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _active_markdown_files() -> list[Path]:
    roots = [REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md", REPO_ROOT / "PURPOSE.md", REPO_ROOT / "docs", REPO_ROOT / "frontend", REPO_ROOT / "plugins" / "llm_enrich" / "prompts"]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            for path in root.rglob("*.md"):
                parts = path.relative_to(REPO_ROOT).parts
                if any(x in parts for x in ("node_modules", "archive", "_tmp", ".claude")):
                    continue
                files.append(path)
    return sorted(files)


def _check_readme_disclaimers() -> list[str]:
    failures: list[str] = []
    for path in sorted(REPO_ROOT.rglob("README.md")):
        rel_parts = path.relative_to(REPO_ROOT).parts
        if any(part.startswith(".") or part in {"node_modules", "archive", "_tmp"} for part in rel_parts):
            continue
        text = path.read_text(encoding="utf-8")
        match = README_DISCLAIMER_RE.search(text)
        if not match:
            failures.append(f"{path.relative_to(REPO_ROOT)}: missing required DISCLAIMER.md section")
            continue
        expected = path.parent.relative_to(REPO_ROOT)
        expected_target = "./DISCLAIMER.md" if str(expected) == "." else "/".join([".."] * len(expected.parts) + ["DISCLAIMER.md"])
        if match.group("target") != expected_target:
            failures.append(
                f"{path.relative_to(REPO_ROOT)}: disclaimer target {match.group('target')!r} != {expected_target!r}"
            )
    return failures


def _check_active_local_links() -> list[str]:
    failures: list[str] = []
    for path in _active_markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            raw = match.group(1).split()[0].strip("<>")
            if raw.startswith(("http://", "https://", "mailto:", "#", "@")):
                continue
            target = raw.split("#", 1)[0]
            if not target:
                continue
            if not (path.parent / target).exists():
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(REPO_ROOT)}:{line}: broken local link {raw}")
    return failures


def main() -> int:
    failures = _check_readme_disclaimers() + _check_active_local_links()
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"failed: {len(failures)}")
        return 1
    print("PASS README disclaimer sections and active Markdown local links")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""codebase_mapper.tests_edges."""
from __future__ import annotations

import re

from collections import defaultdict
from pathlib import PurePosixPath

from .models import FileRecord, TestsEdge


def infer_tests_edges(records: list[FileRecord]) -> list[TestsEdge]:
    subjects_by_basename: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.type_ != "source_code":
            continue
        bn = PurePosixPath(r.path).stem
        if bn in ("__init__", "index", "mod", "lib", "main"):
            continue
        subjects_by_basename[bn].append(r.path)

    edges: set[TestsEdge] = set()
    for r in records:
        if r.type_ != "test_code":
            continue
        p = PurePosixPath(r.path)
        stem = p.stem
        in_tests_dir = "__tests__" in p.parts or "tests" in p.parts
        cand: str | None = None
        m = re.fullmatch(r"test_(.+)", stem)
        if m:
            cand = m.group(1)
        else:
            m = re.fullmatch(r"(.+)_test", stem)
            if m:
                cand = m.group(1)
            else:
                m = re.fullmatch(r"(.+)_(test|spec)", stem)
                if m:
                    cand = m.group(1)
                else:
                    m = re.fullmatch(r"(.+)\.(test|spec)", stem)
                    if m:
                        cand = m.group(1)
                    else:
                        # Foo-test.X → Foo.X (React/Jest convention)
                        m = re.fullmatch(r"(.+)-(test|spec)", stem)
                        if m:
                            cand = m.group(1)
                        elif in_tests_dir and stem not in ("index", "main"):
                            # __tests__/Foo.X → Foo.X (React/Jest convention,
                            # bare stem inside a test directory).
                            cand = stem
        if not cand:
            continue
        candidates = subjects_by_basename.get(cand, [])
        if len(candidates) == 1:
            edges.add(TestsEdge(r.path, candidates[0]))
        elif len(candidates) > 1:
            test_dir = list(p.parts[:-1])
            best, best_score, tie = None, -1, False
            for c in candidates:
                cd = list(PurePosixPath(c).parts[:-1])
                score = 0
                for a, b in zip(test_dir, cd):
                    if a == b:
                        score += 1
                    else:
                        break
                if score > best_score:
                    best, best_score, tie = c, score, False
                elif score == best_score:
                    tie = True
            if best is not None and not tie:
                edges.add(TestsEdge(r.path, best))
    return sorted(edges, key=lambda e: (e.test_path, e.subject_path))

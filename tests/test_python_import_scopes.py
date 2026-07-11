"""Python import extraction covers every scope; suffix heuristic is guarded.

Provenance: an external recount of a shipped bundle reconciled every disputed
figure, but exposed that `extract_python_ast_summary` read only `tree.body` —
function-scoped imports, `try/except ImportError` fallbacks, and
`if TYPE_CHECKING:` blocks were silently invisible, so the dependency graph
understated reality (doc-ray: 417 vs 336 edges) with no stated boundary.
The same review flagged the suffix index as a name-shadowing hazard: any
unique internal module suffix could capture an unresolved external name.

Contracts pinned here:

- imports are extracted from every scope, each tagged ``module`` (top-level
  statement), ``guarded`` (module level inside If/Try/loops), or ``nested``
  (inside a function/method/class);
- resolution turns nested/guarded imports into real edges — a lazy import
  is still a dependency;
- the suffix heuristic defers to a declared external dependency of the same
  name; an exact module-path match still wins over both;
- end-to-end, ``map_codebase`` emits edges for lazy imports and keeps the
  pytest-style same-directory sibling import (`import _helper`) working.

Run from the repo root:  uv run python -m pytest tests/test_python_import_scopes.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codebase_mapper.inspection.languages.python import (  # noqa: E402
    build_python_module_index,
    extract_python_ast_summary,
    resolve_python_imports,
)
from codebase_mapper.inspection.pipeline import map_codebase  # noqa: E402

SOURCE = b'''\
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myproj.models import Model

try:
    import fastjson as json_impl
except ImportError:
    import json as json_impl

for _ in range(1):
    import contextlib


def handler():
    from myproj.adapters import database
    import psycopg


class Service:
    import threading

    def run(self):
        from myproj import config
'''


def _summary():
    summary, errors = extract_python_ast_summary(SOURCE, "myproj/app.py")
    assert not errors
    return summary


def _by_scope(summary):
    out = {}
    for imp in summary["imports"]:
        key = imp["module"] if imp["kind"] == "import" else \
            f"{imp['module']}.{imp['name']}"
        out[key] = imp["scope"]
    return out


def test_every_scope_is_extracted_and_tagged():
    scopes = _by_scope(_summary())
    assert scopes["os"] == "module"
    assert scopes["typing.TYPE_CHECKING"] == "module"
    # module-level but conditional at import time
    assert scopes["myproj.models.Model"] == "guarded"       # TYPE_CHECKING
    assert scopes["fastjson"] == "guarded"                  # try:
    assert scopes["json"] == "guarded"                      # except ImportError:
    assert scopes["contextlib"] == "guarded"                # for-loop body
    # lazy: paid on call, not on import
    assert scopes["myproj.adapters.database"] == "nested"   # function body
    assert scopes["psycopg"] == "nested"
    assert scopes["threading"] == "nested"                  # class body
    assert scopes["myproj.config"] == "nested"              # method body


def test_top_level_surface_lists_are_unchanged():
    summary = _summary()
    assert summary["top_level_functions"] == ["handler"]
    assert summary["top_level_classes"] == ["Service"]


def test_nested_and_guarded_imports_resolve_to_edges():
    class R:  # minimal FileRecord stand-in for the index builder
        def __init__(self, path):
            self.path, self.language = path, "python"

    records = [R("myproj/app.py"), R("myproj/models.py"),
               R("myproj/adapters/database.py"), R("myproj/config.py"),
               R("myproj/__init__.py")]
    by_module, by_suffix = build_python_module_index(records, [""])
    dst, unresolved = resolve_python_imports(
        "myproj/app.py", _summary(), [""], by_module, by_suffix)
    assert "myproj/models.py" in dst            # guarded (TYPE_CHECKING)
    assert "myproj/adapters/database.py" in dst  # nested (function)
    assert "myproj/config.py" in dst             # nested (method)
    assert "psycopg" in unresolved               # external candidate


def test_suffix_heuristic_defers_to_declared_dependency():
    by_module = {"tools.psycopg": "tools/psycopg.py"}
    by_suffix = {"psycopg": "tools/psycopg.py"}
    summary = {"imports": [
        {"kind": "import", "module": "psycopg", "lineno": 1, "scope": "module"},
    ]}
    # unguarded: the internal suffix silently captures the external name
    dst, unresolved = resolve_python_imports(
        "app.py", summary, [""], by_module, by_suffix)
    assert dst == ["tools/psycopg.py"] and unresolved == []
    # guarded: a declared dependency of that name wins over the heuristic
    dst, unresolved = resolve_python_imports(
        "app.py", summary, [""], by_module, by_suffix,
        declared_external={"psycopg"})
    assert dst == [] and unresolved == ["psycopg"]
    # ...but an exact module-path match is not a heuristic and still wins
    summary_exact = {"imports": [
        {"kind": "import", "module": "tools.psycopg", "lineno": 1,
         "scope": "module"},
    ]}
    dst, _ = resolve_python_imports(
        "app.py", summary_exact, [""], by_module, by_suffix,
        declared_external={"psycopg"})
    assert dst == ["tools/psycopg.py"]


def test_end_to_end_lazy_and_sibling_imports_become_edges(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text("VALUE = 1\n")
    (repo / "pkg" / "app.py").write_text(
        "def main():\n    from pkg import core\n    return core.VALUE\n")
    (repo / "tests_dir").mkdir()
    (repo / "tests_dir" / "_helper.py").write_text("X = 1\n")
    (repo / "tests_dir" / "test_app.py").write_text(
        "import _helper\n\ndef test_x():\n    assert _helper.X == 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)

    mapped = map_codebase(repo.resolve(), "HEAD")
    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    assert ("pkg/app.py", "pkg/core.py") in edges          # lazy import
    assert ("tests_dir/test_app.py", "tests_dir/_helper.py") in edges  # sibling

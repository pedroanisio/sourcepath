"""E8 (error-free-mapping plan) — JSON-LD emission without the whole document
in RAM.

The rdflib path builds the full document plus a sorted copy in memory (the
class of step that killed kernel-scale emits, F9/F19). The streaming writer
consumes subject-sorted N-Triples and emits canonical JSON-LD node by node.
The arbiter is byte equality: on every fixture graph the streamed bytes must
equal the rdflib serialize→canonicalize path exactly — same @context, same
compaction, same ordering. If parity ever breaks, this suite fails before
any bundle does.

Run from the repo root:  python -m pytest tests/test_streaming_jsonld.py
"""
from __future__ import annotations

import json
import subprocess

import pytest

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


def _rdflib_canonical(graph) -> str:
    """The legacy path: rdflib serialize + the documented canonical sort."""
    data = graph.serialize(format="json-ld", auto_compact=True,
                           indent=2, sort_keys=True)
    doc = json.loads(data)

    def _sort(node):
        if isinstance(node, dict):
            return {k: _sort(v) for k, v in sorted(node.items())}
        if isinstance(node, list):
            items = [_sort(x) for x in node]

            def key(x):
                if isinstance(x, dict):
                    return (0, x.get("@id", ""), json.dumps(x, sort_keys=True))
                return (1, str(x))
            return sorted(items, key=key)
        return node

    return json.dumps(_sort(doc), indent=2, sort_keys=True) + "\n"


def _mapped_graph(tmp_path, files: dict[str, str]):
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
        build_inventory_graph,
    )
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.constants import CBMI_NS
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from rdflib import URIRef

    repo = tmp_path / "repo"
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    return build_inventory_graph(
        repo_iri=URIRef(f"{CBMI_NS}repo/fixture"), commit_sha=mapped["commit"],
        records=mapped["records"], import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"], pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
        possible_import_edges=mapped["possible_import_edges"],
    )


FIXTURES = {
    "python_and_docs": {
        "pkg/a.py": "import os\n\ndef f():\n    return 1\n",
        "pkg/tests/test_a.py": "from pkg.a import f\n",
        "README.md": "# hi\n",
    },
    "c_family_with_candidates": {
        "src/user.c": "#include <asm/io.h>\n#include \"local.h\"\nint u(void) { return 0; }\n",
        "src/local.h": "#define L 1\n",
        "arch/x86/include/asm/io.h": "#define IO_X 1\n",
        "arch/arm/include/asm/io.h": "#define IO_A 1\n",
    },
    "unicode_and_escapes": {
        "src/π.py": 'S = "quote \\" backslash \\\\ tab\\t"\n',
        "docs/naïve.txt": "café ☕\n",
    },
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_streamed_bytes_equal_rdflib_path(name, tmp_path):
    from codebase_mapper.emission.infrastructure.rdf.streaming_jsonld import (
        write_jsonld_streaming,
    )
    g = _mapped_graph(tmp_path, FIXTURES[name])
    expected = _rdflib_canonical(g)
    out = tmp_path / "inv.jsonld"
    engine = write_jsonld_streaming(g, out)
    assert engine == "streaming"
    assert out.read_text() == expected


def test_emit_uses_streaming_engine(tmp_path):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    assert manifest["emit_engines"]["inventory.jsonld"] == "streaming"
    doc = json.loads((tmp_path / "b" / "inventory.jsonld").read_text())
    assert "@graph" in doc and "@context" in doc

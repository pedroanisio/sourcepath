"""Bundle loading and backend query primitives."""
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

CBM = Namespace("https://codebase-mapper.example.org/cbm#")
CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")
CBML3 = Namespace("https://codebase-mapper.example.org/cbml3#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
CBMI_NS = "https://codebase-mapper.example.org/cbm/instance#"


@dataclass
class Bundle:
    output_dir: Path
    manifest: dict[str, Any]
    concepts: dict[str, Any]
    embeddings_meta: dict[str, Any]
    chunk_vectors: np.ndarray | None
    chunk_ids: list[str]
    concept_vectors: np.ndarray | None
    concept_ids: list[str]
    files: list[dict[str, Any]]
    file_by_path: dict[str, dict[str, Any]]
    imports: list[tuple[str, str]]
    imports_out: dict[str, list[str]]
    imports_in: dict[str, list[str]]
    tests: list[tuple[str, str]]
    tests_for_subject: dict[str, list[str]]
    subjects_for_test: dict[str, list[str]]
    chunks: list[dict[str, Any]]
    chunks_by_uri: dict[str, int]
    chunks_by_file: dict[str, list[int]]
    chunk_concepts: dict[int, list[str]]
    concept_chunks: dict[str, list[int]]
    cooccur: dict[str, list[tuple[str, int]]]
    xrefs: list[dict[str, Any]] = field(default_factory=list)
    xrefs_by_src_idx: dict[int, list[int]] = field(default_factory=dict)
    xrefs_by_dst_idx: dict[int, list[int]] = field(default_factory=dict)
    rust_items: list[dict[str, Any]] = field(default_factory=list)
    rust_items_by_file: dict[str, list[int]] = field(default_factory=dict)
    enrichment_file_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrichment_concept_description: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrichment_schema_purpose: dict[str, dict[str, Any]] = field(default_factory=dict)


def _resolve_file_type_uri(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


# A projection is the fixed set of structures the serving layer needs out of
# the RDF graph: files, import/test edges, chunks, and chunk→concept links.
# Returned as a tuple in the order the Bundle constructor consumes them.
Projection = tuple


def _assemble_projection(
    files: list[dict[str, Any]],
    file_by_uri: dict[str, dict[str, Any]],
    import_edges: list[tuple[str, str]],
    test_edges: list[tuple[str, str]],
    raw_chunks: list[dict[str, Any]],
    lexicalizes: list[tuple[str, str]],
) -> Projection:
    """Resolve raw triple-level edges into the path/index-keyed structures the
    serving layer queries.

    Both the JSON-LD and the rdflib extraction paths funnel through here, so
    chunk ordering, index assignment, and edge resolution are byte-identical
    regardless of which parser produced the raw triples — the only thing that
    differs between paths is the iteration order of multiset-equal adjacency
    lists, which every consumer sorts or counts before observing.
    """
    files.sort(key=lambda r: r["path"])

    imports: list[tuple[str, str]] = []
    imports_out: dict[str, list[str]] = {}
    imports_in: dict[str, list[str]] = {}
    for s_uri, o_uri in import_edges:
        s_path = file_by_uri.get(s_uri, {}).get("path")
        o_path = file_by_uri.get(o_uri, {}).get("path")
        if s_path and o_path:
            imports.append((s_path, o_path))
            imports_out.setdefault(s_path, []).append(o_path)
            imports_in.setdefault(o_path, []).append(s_path)

    tests: list[tuple[str, str]] = []
    tests_for_subject: dict[str, list[str]] = {}
    subjects_for_test: dict[str, list[str]] = {}
    for s_uri, o_uri in test_edges:
        test_path = file_by_uri.get(s_uri, {}).get("path")
        subject_path = file_by_uri.get(o_uri, {}).get("path")
        if test_path and subject_path:
            tests.append((test_path, subject_path))
            tests_for_subject.setdefault(subject_path, []).append(test_path)
            subjects_for_test.setdefault(test_path, []).append(subject_path)

    chunks: list[dict[str, Any]] = []
    for rc in raw_chunks:
        in_file_uri = rc.get("in_file_uri")
        chunks.append(
            {
                "uri": rc["uri"],
                "symbol": rc["symbol"],
                "kind": rc["kind"],
                "file": file_by_uri.get(in_file_uri, {}).get("path") if in_file_uri else None,
                "beginLine": rc["beginLine"],
                "endLine": rc["endLine"],
                "embeddingRow": rc["embeddingRow"],
                "contentSha256": rc["contentSha256"],
            }
        )
    chunks.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0))
    chunk_uri_to_idx: dict[str, int] = {}
    chunks_by_file: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        c["idx"] = i
        chunk_uri_to_idx[c["uri"]] = i
        if c["file"]:
            chunks_by_file.setdefault(c["file"], []).append(i)

    chunk_concepts: dict[int, list[str]] = {}
    concept_chunks: dict[str, list[int]] = {}
    for s_uri, o_uri in lexicalizes:
        idx = chunk_uri_to_idx.get(s_uri)
        if idx is None:
            continue
        name = _concept_name_from_uri(o_uri)
        if not name:
            continue
        chunk_concepts.setdefault(idx, []).append(name)
        concept_chunks.setdefault(name, []).append(idx)

    return (
        files,
        imports,
        imports_out,
        imports_in,
        tests,
        tests_for_subject,
        subjects_for_test,
        chunks,
        chunk_uri_to_idx,
        chunks_by_file,
        chunk_concepts,
        concept_chunks,
    )


def _project_from_jsonld(jsonld_path: Path) -> Projection:
    """Fast path: project the graph straight from the compacted JSON-LD node
    list using the C-backed ``json`` parser.

    The JSON-LD artifact carries the same triples as ``inventory.ttl`` but the
    stdlib parser is ~28x faster than rdflib's pure-Python Turtle tokenizer
    (0.2s vs 5s on a 46MB bundle), keeping cold loads well inside the per-tool
    wall-clock budget on large repositories.
    """
    data = json.loads(jsonld_path.read_text())
    ctx = {k: v for k, v in (data.get("@context") or {}).items() if isinstance(v, str)}
    graph = data.get("@graph") or []

    def expand(curie: str) -> str:
        if not isinstance(curie, str) or curie.startswith("http"):
            return curie
        prefix, sep, local = curie.partition(":")
        if sep and prefix in ctx:
            return ctx[prefix] + local
        return curie

    def node_types(node: dict[str, Any]) -> list[str]:
        t = node.get("@type")
        if isinstance(t, list):
            return t
        return [t] if t else []

    def literal(value: Any) -> Any:
        # JSON-LD typed literals are ``{"@type": ..., "@value": ...}``; plain
        # scalars (ints, strings) are emitted bare. A *repeated* predicate is
        # collapsed by JSON-LD into a list — take the first element so a single
        # malformed node (e.g. a chunk with two ``embeddingRow`` values) cannot
        # abort the whole projection (PALS's Law: untrusted generator output).
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            return value.get("@value")
        return value

    def id_refs(value: Any) -> list[str]:
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]
        out: list[str] = []
        for it in items:
            if isinstance(it, dict) and "@id" in it:
                out.append(expand(it["@id"]))
            elif isinstance(it, str):
                out.append(expand(it))
        return out

    files: list[dict[str, Any]] = []
    file_by_uri: dict[str, dict[str, Any]] = {}
    import_edges: list[tuple[str, str]] = []
    test_edges: list[tuple[str, str]] = []
    raw_chunks: list[dict[str, Any]] = []
    lexicalizes: list[tuple[str, str]] = []

    for node in graph:
        nid = node.get("@id")
        if nid is None:
            continue
        uri = expand(nid)
        types = node_types(node)

        if "cbm:File" in types:
            ftype_uri = None
            for type_uri in id_refs(node.get("cbm:type")):
                if "cbm/type" in type_uri:
                    ftype_uri = _resolve_file_type_uri(type_uri)
                    break
            size_b = literal(node.get("cbm:sizeBytes"))
            sha = literal(node.get("cbm:contentSha256"))
            lang_lit = literal(node.get("cbm:language"))
            rec = {
                "uri": uri,
                "path": str(node["cbm:path"]) if "cbm:path" in node else str(None),
                "language": str(lang_lit) if lang_lit else None,
                "type": ftype_uri,
                "size": int(size_b) if size_b is not None else None,
                "contentSha256": str(sha) if sha is not None else None,
            }
            files.append(rec)
            file_by_uri[uri] = rec

        if "cbml2:Chunk" in types:
            in_file = id_refs(node.get("cbml2:inFile"))
            raw_chunks.append(
                {
                    "uri": uri,
                    "symbol": _str_or_none(node.get("cbml2:symbol")),
                    "kind": _str_or_none(node.get("cbml2:kind")),
                    "in_file_uri": in_file[0] if in_file else None,
                    "beginLine": _int_or_none(literal(node.get("cbml2:beginLine"))),
                    "endLine": _int_or_none(literal(node.get("cbml2:endLine"))),
                    "embeddingRow": _int_or_none(literal(node.get("cbml2:embeddingRow"))),
                    "contentSha256": _str_or_none(literal(node.get("cbml2:contentSha256"))),
                }
            )

        for o_uri in id_refs(node.get("cbm:imports")):
            import_edges.append((uri, o_uri))
        for o_uri in id_refs(node.get("cbm:tests")):
            test_edges.append((uri, o_uri))
        for o_uri in id_refs(node.get("cbml3:lexicalizes")):
            lexicalizes.append((uri, o_uri))

    return _assemble_projection(
        files, file_by_uri, import_edges, test_edges, raw_chunks, lexicalizes
    )


def _project_from_rdflib(ttl_path: Path) -> Projection:
    """Fallback path: parse ``inventory.ttl`` with rdflib.

    Used only when the JSON-LD artifact is absent (older bundles). Produces an
    identical projection to :func:`_project_from_jsonld` for the same data.
    """
    g = Graph()
    g.parse(ttl_path, format="turtle")

    files: list[dict[str, Any]] = []
    file_by_uri: dict[str, dict[str, Any]] = {}
    for f in g.subjects(RDF.type, CBM.File):
        ftype_uri = None
        for t in g.objects(f, CBM.type):
            su = str(t)
            if "cbm/type" in su:
                ftype_uri = _resolve_file_type_uri(su)
                break
        size_b = g.value(f, CBM.sizeBytes)
        sha = g.value(f, CBM.contentSha256)
        lang_lit = g.value(f, CBM.language)
        rec = {
            "uri": str(f),
            "path": str(g.value(f, CBM.path)),
            "language": str(lang_lit) if lang_lit else None,
            "type": ftype_uri,
            "size": int(size_b) if size_b is not None else None,
            "contentSha256": str(sha) if sha is not None else None,
        }
        files.append(rec)
        file_by_uri[str(f)] = rec

    import_edges = [(str(s), str(o)) for s, o in g.subject_objects(CBM.imports)]
    test_edges = [(str(s), str(o)) for s, o in g.subject_objects(CBM.tests)]

    raw_chunks: list[dict[str, Any]] = []
    for c in g.subjects(RDF.type, CBML2.Chunk):
        in_file = g.value(c, CBML2.inFile)
        raw_chunks.append(
            {
                "uri": str(c),
                "symbol": _str_or_none(g.value(c, CBML2.symbol)),
                "kind": _str_or_none(g.value(c, CBML2.kind)),
                "in_file_uri": str(in_file) if in_file is not None else None,
                "beginLine": _int_or_none(g.value(c, CBML2.beginLine)),
                "endLine": _int_or_none(g.value(c, CBML2.endLine)),
                "embeddingRow": _int_or_none(g.value(c, CBML2.embeddingRow)),
                "contentSha256": _str_or_none(g.value(c, CBML2.contentSha256)),
            }
        )

    lexicalizes = [(str(s), str(o)) for s, o in g.subject_objects(CBML3.lexicalizes)]

    return _assemble_projection(
        files, file_by_uri, import_edges, test_edges, raw_chunks, lexicalizes
    )


def _load_graph_projection(output_dir: Path) -> Projection:
    """Project the inventory graph, preferring the fast JSON-LD parser and
    falling back to rdflib only when the JSON-LD artifact is unavailable."""
    jsonld_path = output_dir / "inventory.jsonld"
    if jsonld_path.exists():
        return _project_from_jsonld(jsonld_path)
    return _project_from_rdflib(output_dir / "inventory.ttl")


def load_bundle(output_dir: Path) -> Bundle:
    if not output_dir.exists():
        raise FileNotFoundError(f"output dir not found: {output_dir}")

    manifest = json.loads((output_dir / "run_manifest.json").read_text())
    concepts_path = output_dir / "concepts.json"
    concepts = json.loads(concepts_path.read_text()) if concepts_path.exists() else {}
    emb_meta_path = output_dir / "embeddings_meta.json"
    embeddings_meta = json.loads(emb_meta_path.read_text()) if emb_meta_path.exists() else {}

    chunk_npz_path = output_dir / "embeddings.npz"
    chunk_vectors = None
    chunk_ids: list[str] = []
    if chunk_npz_path.exists():
        npz = np.load(chunk_npz_path, allow_pickle=True)
        chunk_vectors = np.asarray(npz[embeddings_meta.get("vectors_field", "vectors")])
        chunk_ids = [
            s.decode() if isinstance(s, bytes) else str(s)
            for s in npz[embeddings_meta.get("ids_field", "ids")]
        ]

    concept_npz_path = output_dir / "concepts_embeddings.npz"
    concept_vectors = None
    concept_ids: list[str] = list(concepts.get("concept_embedding_ids") or [])
    if concept_npz_path.exists():
        npz = np.load(concept_npz_path, allow_pickle=True)
        concept_vectors = np.asarray(
            npz["vectors"] if "vectors" in npz.files else npz[npz.files[0]]
        )

    (
        files,
        imports,
        imports_out,
        imports_in,
        tests,
        tests_for_subject,
        subjects_for_test,
        chunks,
        chunk_uri_to_idx,
        chunks_by_file,
        chunk_concepts,
        concept_chunks,
    ) = _load_graph_projection(output_dir)

    cooccur: dict[str, list[tuple[str, int]]] = {}
    for entry in concepts.get("cooccurrence", []) or []:
        if len(entry) != 3:
            continue
        a, b_, w = entry
        cooccur.setdefault(a, []).append((b_, int(w)))
        cooccur.setdefault(b_, []).append((a, int(w)))
    for k in cooccur:
        cooccur[k].sort(key=lambda t: t[1], reverse=True)

    file_by_path = {r["path"]: r for r in files}
    xrefs, xrefs_by_src_idx, xrefs_by_dst_idx = _load_xrefs(
        output_dir / "xrefs.jsonl", chunk_uri_to_idx
    )
    rust_items, rust_items_by_file = _load_rust_items(output_dir / "rust_items.jsonl")
    enrich_fs, enrich_cd, enrich_sp = _load_enrichments(output_dir / "enrichments.jsonl")

    return Bundle(
        output_dir=output_dir,
        manifest=manifest,
        concepts=concepts,
        embeddings_meta=embeddings_meta,
        chunk_vectors=chunk_vectors,
        chunk_ids=chunk_ids,
        concept_vectors=concept_vectors,
        concept_ids=concept_ids,
        files=files,
        file_by_path=file_by_path,
        imports=imports,
        imports_out=imports_out,
        imports_in=imports_in,
        tests=tests,
        tests_for_subject=tests_for_subject,
        subjects_for_test=subjects_for_test,
        chunks=chunks,
        chunks_by_uri=chunk_uri_to_idx,
        chunks_by_file=chunks_by_file,
        chunk_concepts=chunk_concepts,
        concept_chunks=concept_chunks,
        cooccur=cooccur,
        xrefs=xrefs,
        xrefs_by_src_idx=xrefs_by_src_idx,
        xrefs_by_dst_idx=xrefs_by_dst_idx,
        rust_items=rust_items,
        rust_items_by_file=rust_items_by_file,
        enrichment_file_summary=enrich_fs,
        enrichment_concept_description=enrich_cd,
        enrichment_schema_purpose=enrich_sp,
    )


def _load_rust_items(
    sidecar_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    items: list[dict[str, Any]] = []
    by_file: dict[str, list[int]] = {}
    if not sidecar_path.exists():
        return items, by_file
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = row.get("path")
        if not path:
            continue
        idx = len(items)
        items.append(row)
        by_file.setdefault(path, []).append(idx)
    return items, by_file


def _chunk_id_to_uri(chunk_id: str) -> str:
    return f"{CBMI_NS}chunk/{urllib.parse.quote(chunk_id, safe='')}"


def _load_enrichments(
    sidecar_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    fs: dict[str, dict[str, Any]] = {}
    cd: dict[str, dict[str, Any]] = {}
    sp: dict[str, dict[str, Any]] = {}
    if not sidecar_path.exists():
        return fs, cd, sp
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = row.get("kind")
        target = row.get("target")
        if not kind or not target:
            continue
        if kind == "file_summary":
            fs[target] = row
        elif kind == "concept_description":
            cd[target] = row
        elif kind == "schema_purpose":
            sp[target] = row
    return fs, cd, sp


def _load_xrefs(
    sidecar_path: Path, chunks_by_uri: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[int, list[int]], dict[int, list[int]]]:
    xrefs: list[dict[str, Any]] = []
    by_src: dict[int, list[int]] = {}
    by_dst: dict[int, list[int]] = {}
    if not sidecar_path.exists():
        return xrefs, by_src, by_dst
    for line in sidecar_path.read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        src_idx = chunks_by_uri.get(_chunk_id_to_uri(raw["src_chunk_id"]))
        dst_idx = chunks_by_uri.get(_chunk_id_to_uri(raw["dst_chunk_id"]))
        if src_idx is None or dst_idx is None:
            continue
        edge_idx = len(xrefs)
        xrefs.append(
            {
                "src_idx": src_idx,
                "dst_idx": dst_idx,
                "kind": raw["kind"],
                "resolution": raw["resolution"],
                "resolver": raw["resolver"],
            }
        )
        by_src.setdefault(src_idx, []).append(edge_idx)
        by_dst.setdefault(dst_idx, []).append(edge_idx)
    return xrefs, by_src, by_dst


def xref_row(b: Bundle, peer_idx: int, edge: dict[str, Any]) -> dict[str, Any]:
    c = b.chunks[peer_idx]
    return {
        "idx": peer_idx,
        "symbol": c.get("symbol"),
        "kind": c.get("kind"),
        "file": c.get("file"),
        "beginLine": c.get("beginLine"),
        "endLine": c.get("endLine"),
        "embeddingRow": c.get("embeddingRow"),
        "xref_kind": edge["kind"],
        "resolution": edge["resolution"],
        "resolver": edge["resolver"],
    }


def chunk_payload(b: Bundle, idx: int, include_file: bool = True) -> dict[str, Any]:
    keys = ["idx", "symbol", "kind", "beginLine", "endLine", "embeddingRow"]
    if include_file:
        keys.insert(3, "file")
    return {key: b.chunks[idx].get(key) for key in keys}


def _concept_name_from_uri(uri: str) -> str | None:
    if "#concept/" in uri:
        return uri.rsplit("#concept/", 1)[-1]
    if "/concept/" in uri:
        return uri.rsplit("/concept/", 1)[-1]
    return None


def _bundles_root() -> Path:
    return Path(os.environ.get("CBM_BUNDLES_ROOT", "_tmp")).resolve()


def _validate_bundle_name(name: str) -> None:
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid bundle name: {name!r}")


def _bundle_info(path: Path) -> dict[str, Any] | None:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        m = json.loads(manifest_path.read_text())
    except Exception:
        return None
    return {
        "name": path.name,
        "path": str(path),
        "repo_name": m.get("repo_name"),
        "commit_sha": m.get("commit_sha"),
        "generated_at": m.get("generated_at"),
        "tool_version": m.get("tool_version"),
        "files": (m.get("counts") or {}).get("files"),
    }


def list_bundles() -> list[dict[str, Any]]:
    seen: set[Path] = set()
    out: list[dict[str, Any]] = []

    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        info = _bundle_info(p)
        if info:
            out.append(info)
            seen.add(p)

    root = _bundles_root()
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            cp = child.resolve()
            if cp in seen:
                continue
            info = _bundle_info(child)
            if info:
                out.append(info)
                seen.add(cp)
    return out


def _default_bundle_name() -> str | None:
    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        if _bundle_info(p) is not None:
            return p.name
    items = list_bundles()
    return items[0]["name"] if items else None


def _resolve_bundle_path(name: str | None) -> Path:
    if name:
        _validate_bundle_name(name)
        env_out = os.environ.get("CBM_OUTPUT_DIR")
        if env_out:
            p = Path(env_out).resolve()
            if p.name == name and _bundle_info(p) is not None:
                return p
        p = (_bundles_root() / name).resolve()
        if not p.is_dir() or _bundle_info(p) is None:
            raise HTTPException(
                status_code=404,
                detail=f"bundle '{name}' not found or missing run_manifest.json",
            )
        return p

    env_out = os.environ.get("CBM_OUTPUT_DIR")
    if env_out:
        p = Path(env_out).resolve()
        if _bundle_info(p) is not None:
            return p
    items = list_bundles()
    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"no bundles found in {_bundles_root()}; set CBM_OUTPUT_DIR or place bundles under CBM_BUNDLES_ROOT",
        )
    return Path(items[0]["path"]).resolve()


@lru_cache(maxsize=4)
def _load_bundle_cached(path_str: str) -> Bundle:
    return load_bundle(Path(path_str))


def get_bundle(name: str | None = None) -> Bundle:
    path = _resolve_bundle_path(name)
    return _load_bundle_cached(str(path))


def ensure_bundle_exists(name: str | None = None) -> str:
    """Validate that a bundle is resolvable *without* parsing its graph.

    ``select_bundle`` only needs to confirm the bundle exists before stashing
    the choice in session state; forcing a full ``load_bundle`` (a multi-second
    RDF parse on large repositories) just to validate is wasteful and was the
    direct cause of ``select_bundle`` timing out. Resolution is a cheap
    manifest-existence check. Returns the resolved bundle directory name and
    raises ``HTTPException`` (404/400) when the bundle is missing or invalid.
    """
    return _resolve_bundle_path(name).name


def cold_load_allowance_seconds(name: str | None = None) -> float:
    """Extra wall-clock budget to grant a tool that may trigger a cold bundle
    load, scaled to the size of the graph artifact it will parse.

    The steady-state per-tool budget is tuned for warm, in-memory queries. A
    one-time cold load of a large repository legitimately needs more headroom,
    and a single fixed ceiling cannot serve both a 16-file repo and a
    16,000-file one. This returns a size-derived allowance (0.0 when the
    bundle can't be resolved) that callers add to the base budget — a ceiling,
    never a delay, so warm calls are unaffected.
    """
    try:
        path = _resolve_bundle_path(name)
    except Exception:  # noqa: BLE001 — budget hint must never raise
        return 0.0
    jsonld = path / "inventory.jsonld"
    if jsonld.exists():
        # Fast stdlib JSON path: ~0.4s for 46MB observed; 0.05s/MB is generous.
        return (jsonld.stat().st_size / 1_000_000) * 0.05
    ttl = path / "inventory.ttl"
    if ttl.exists():
        # rdflib Turtle fallback is ~10x slower; budget accordingly.
        return (ttl.stat().st_size / 1_000_000) * 0.2
    return 0.0


def _clear_bundle_cache() -> None:
    _load_bundle_cached.cache_clear()


get_bundle.cache_clear = _clear_bundle_cache  # type: ignore[attr-defined]


def walk_paths(
    start: str,
    adjacency: dict[str, list[str]],
    depth: int,
    limit: int,
) -> tuple[list[str], bool]:
    seen = {start}
    frontier = [start]
    out: list[str] = []
    for _ in range(depth):
        next_frontier: list[str] = []
        for src in frontier:
            for dst in sorted(adjacency.get(src, [])):
                if dst in seen:
                    continue
                seen.add(dst)
                out.append(dst)
                next_frontier.append(dst)
                if len(out) >= limit:
                    return out, True
        frontier = next_frontier
        if not frontier:
            break
    return out, False


def walk_xref_chunks(
    seeds: list[int],
    adjacency: dict[int, list[int]],
    edges: list[dict[str, Any]],
    peer_key: str,
    depth: int,
    limit: int,
) -> tuple[list[int], bool]:
    seen = set(seeds)
    frontier = list(seeds)
    out: list[int] = []
    for _ in range(depth):
        next_frontier: list[int] = []
        for node in frontier:
            for e_idx in adjacency.get(node, []):
                peer = edges[e_idx][peer_key]
                if peer in seen:
                    continue
                seen.add(peer)
                out.append(peer)
                next_frontier.append(peer)
                if len(out) >= limit:
                    return out, True
        frontier = next_frontier
        if not frontier:
            break
    return out, False

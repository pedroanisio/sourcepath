#!/usr/bin/env python3
"""
cbm_report.py — the complete report for any codebase-mapper output set.

Point it at a bundle directory. It discovers every artifact the tool can emit,
verifies every hash the manifest claims, reports on every layer present, and
says plainly when a layer is absent. Optional companions (arc4d3 ABox,
decomposition, build plan) are auto-detected next to the bundle or passed
explicitly. Nothing here is fastapi-specific: the same discovery + auto-detect
works for any generated cbm bundle (the fastapi set is just the reference
fixture, being the one bundle that carries every companion at once).

Coverage is exhaustive by design — L1 inventory/graph, L2 chunks+embeddings,
L3 concepts (incl. the concept-embedding space), L4 enrichment receipts, the
cbm→SPDX/OWL vocabulary alignment (ontology-mapping.ttl), plus the decomposer
and recomposer companions. Parse caches (the .nt reduction, t-SNE positions)
are written to a per-bundle temp dir, NEVER into the bundle itself.

Outputs (choose with --formats): a self-contained HTML report in the
"Measured Ink" visual language, a Markdown twin with a provenance disclaimer,
and a machine-readable JSON model. Every figure is labeled FACT (measured),
DERIVED (computed projection/clustering), or UNVERIFIED (LLM-authored,
pending validation).

Examples:
  python3 cbm_report.py --bundle _tmp/fastapi
  python3 cbm_report.py --bundle _tmp/fastapi \\
      --abox fastapi-abox.ttl --decomposition d.yaml --buildplan b.yaml \\
      --validate-shacl --out fastapi_report
"""
import argparse, glob, hashlib, html, json, math, os, sys, tempfile, time, urllib.parse
from collections import Counter, defaultdict

# ----------------------------------------------------------------------------
# palette (Measured Ink)
PAPER, INK, GREY, PALE, FAINT, VERM = "#f4eee1", "#1c1a17", "#6f6a60", "#b7b0a2", "#ded7c7", "#c8371f"
TONE = {"certain": INK, "strong": GREY, "probable": PALE, "weak": FAINT, "unknown": FAINT,
        "High": INK, "Medium": GREY, "Low": PALE, "Unknown": FAINT}
CONF_R = {"High": 1.0, "Medium": 0.66, "Low": 0.38, "Unknown": 0.16}
CBM = "https://codebase-mapper.example.org/cbm#"
C2 = "https://codebase-mapper.example.org/cbml2#"
C3 = "https://codebase-mapper.example.org/cbml3#"
C4 = "https://codebase-mapper.example.org/cbml4#"
ESC = html.escape

def log(*a): print("[cbm-report]", *a, file=sys.stderr)

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()

# ----------------------------------------------------------------------------
# discovery & verification
KNOWN = ["run_manifest.json", "inventory.ttl", "inventory.jsonld", "ontology-mapping.ttl",
         "shapes.shacl.ttl", "enrichments.jsonl", "embeddings.npz", "embeddings_meta.json",
         "concepts.json", "concepts_embeddings.npz", "ast_coverage.json", "rust_items.jsonl"]

def discover(bundle, args):
    found = {k: os.path.join(bundle, k) for k in KNOWN if os.path.exists(os.path.join(bundle, k))}
    found["blobs_dir"] = os.path.join(bundle, "blobs") if os.path.isdir(os.path.join(bundle, "blobs")) else None
    stem = os.path.basename(os.path.abspath(bundle.rstrip("/")))
    def auto(explicit, patterns):
        if explicit: return explicit
        for pat in patterns:
            for base in (bundle, os.path.dirname(bundle.rstrip("/")) or ".", "."):
                hits = sorted(glob.glob(os.path.join(base, pat)))
                if base != bundle:
                    # A shared parent directory holds many bundles'
                    # companions; outside the bundle dir only accept
                    # files that name this bundle. Attaching another
                    # repo's decomposition/abox would be silent
                    # cross-bundle contamination (observed live: a
                    # 380 MB linux decomposition globbed onto zod).
                    hits = [h for h in hits if stem in os.path.basename(h)]
                if hits: return hits[0]
        return None
    found["abox"] = auto(args.abox, ["*abox*.ttl"])
    found["decomposition"] = auto(args.decomposition, ["*decomposition*.yaml", "*decomposition*.yml"])
    found["buildplan"] = auto(args.buildplan, ["*buildplan*.yaml", "*build_plan*.yaml"])
    return found

def verify_hashes(bundle, man, found):
    """Recompute every sha256 the output set claims. Returns list of rows."""
    rows = []
    def check(label, path, claimed):
        ok = None
        if path and os.path.exists(path) and claimed:
            ok = sha256(path) == claimed
        rows.append({"artifact": label, "claimed": (claimed or "")[:16], "ok": ok})
    for name, meta in (man.get("artifacts") or {}).items():
        check(name, os.path.join(bundle, name), meta.get("sha256"))
    for ext, meta in (man.get("extensions") or {}).items():
        for name, fmeta in (meta.get("files") or {}).items():
            if isinstance(fmeta, dict) and fmeta.get("sha256"):
                check(f"{ext}/{name}", os.path.join(bundle, name), fmeta["sha256"])
    if found.get("embeddings_meta.json") and found.get("embeddings.npz"):
        em = json.load(open(found["embeddings_meta.json"]))
        check("embeddings_meta→embeddings.npz", found["embeddings.npz"], em.get("artifact_sha256"))
    return rows

# ----------------------------------------------------------------------------
# cache location — NEVER inside the bundle (a report tool must not litter the
# artifact set it reads). --cache-dir wins; otherwise a per-bundle temp dir.
def resolve_cache_dir(bundle, cache_dir):
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True); return cache_dir
    key = hashlib.sha1(os.path.abspath(bundle).encode()).hexdigest()[:12]
    d = os.path.join(tempfile.gettempdir(), "cbm_report_cache", key)
    os.makedirs(d, exist_ok=True); return d

# ----------------------------------------------------------------------------
# loaders
def _load_pyoxigraph():
    """Import seam (patched in tests). Returns the module or None."""
    try:
        import pyoxigraph
        return pyoxigraph
    except ImportError:
        return None


class GraphView:
    """Read-only, rdflib-compatible view over a pyoxigraph (Rust) store.

    Implements exactly the surface the report/dossier consume — ``len``,
    ``subject_objects``, ``objects``, plus grouped-count helpers — and
    converts every term to its rdflib equivalent, so downstream string
    ops, hashing, set membership, and ``int()`` coercion behave
    identically to a parsed rdflib graph. Measured on the 67.4M-triple
    torvalds/linux bundle: the backing store builds once in ~144 s at
    2.5 GB and re-opens instantly; rdflib needed tens of minutes and
    ~87 GB for the same load.
    """

    engine = "oxigraph"

    def __init__(self, store, ox):
        self._store = store
        self._ox = ox

    def __len__(self):
        return len(self._store)

    def _to_rdflib(self, t):
        import rdflib
        ox = self._ox
        if isinstance(t, ox.NamedNode):
            return rdflib.URIRef(t.value)
        if isinstance(t, ox.BlankNode):
            return rdflib.BNode(t.value)
        if t.language:
            return rdflib.Literal(t.value, lang=t.language)
        dt = t.datatype
        # Simple literals report xsd:string; rdflib's parser yields them
        # as plain Literals — normalize so equality matches.
        if dt is None or dt.value == "http://www.w3.org/2001/XMLSchema#string":
            return rdflib.Literal(t.value)
        return rdflib.Literal(t.value, datatype=rdflib.URIRef(dt.value))

    def _to_ox(self, t):
        import rdflib
        ox = self._ox
        if t is None:
            return None
        if isinstance(t, rdflib.URIRef):
            return ox.NamedNode(str(t))
        if isinstance(t, rdflib.BNode):
            return ox.BlankNode(str(t))
        raise TypeError(f"unsupported pattern term: {t!r}")

    def subject_objects(self, predicate):
        for q in self._store.quads_for_pattern(
                None, self._to_ox(predicate), None, None):
            yield self._to_rdflib(q.subject), self._to_rdflib(q.object)

    def objects(self, subject, predicate):
        for q in self._store.quads_for_pattern(
                self._to_ox(subject), self._to_ox(predicate), None, None):
            yield self._to_rdflib(q.object)

    def predicate_counts(self):
        """Counter{URIRef: occurrences} via one Rust-side GROUP BY —
        replaces a full-graph Python iteration."""
        from collections import Counter as _C
        rows = self._store.query(
            "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p")
        return _C({self._to_rdflib(r["p"]): int(r["n"].value) for r in rows})

    def class_counts(self):
        """Counter{class-term: instances} via Rust-side GROUP BY."""
        from collections import Counter as _C
        rows = self._store.query(
            "SELECT ?t (COUNT(?s) AS ?n) WHERE "
            "{ ?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t } "
            "GROUP BY ?t")
        return _C({self._to_rdflib(r["t"]): int(r["n"].value) for r in rows})


# One open view per store dir: RocksDB permits a single writer, so a
# second load_graph in the same process must reuse the handle.
_STORE_CACHE: dict[str, "GraphView"] = {}


def load_graph(found, cache_dir):
    import rdflib
    st = os.stat(found["inventory.ttl"])
    t0 = time.time()
    ox = _load_pyoxigraph()
    if ox is not None:
        # Persistent RocksDB store keyed on the source's mtime+size so a
        # regenerated bundle never reuses a stale store. Builds once,
        # re-opens instantly on every later report/dossier run.
        sdir = os.path.join(cache_dir, f"store_{int(st.st_mtime)}_{st.st_size}")
        if sdir in _STORE_CACHE:
            view = _STORE_CACHE[sdir]
            log(f"graph: {len(view):,} triples (open handle, engine=oxigraph)")
            return view
        os.makedirs(cache_dir, exist_ok=True)
        try:
            store = ox.Store(sdir)
        except OSError:
            # Another process holds the write lock. Reports only read —
            # attach read-only; if that opener is still mid-build the
            # store may be empty, in which case fall through to rdflib
            # rather than analyze a half-loaded graph.
            store = ox.Store.read_only(sdir)
            if len(store) == 0:
                log("store locked by another process and still empty — "
                    "falling back to rdflib for this run")
                store = None
        if store is not None:
            if len(store) == 0:
                store.bulk_load(path=found["inventory.ttl"],
                                format=ox.RdfFormat.TURTLE)
                src = "ttl→store"
            else:
                src = "store-cache"
            view = GraphView(store, ox)
            _STORE_CACHE[sdir] = view
            log(f"graph: {len(view):,} triples ({src}, {time.time()-t0:.1f}s, "
                "engine=oxigraph)")
            return view
    # Fallback: the original rdflib path with its NT parse cache.
    g = rdflib.Graph()
    nt = os.path.join(cache_dir, f"inv_{int(st.st_mtime)}_{st.st_size}.nt")
    if os.path.exists(nt):
        g.parse(nt, format="nt"); src = "cache"
    else:
        g.parse(found["inventory.ttl"], format="turtle"); src = "ttl"
        try: g.serialize(nt, format="nt")
        except Exception: pass
    log(f"graph: {len(g):,} triples ({src}, {time.time()-t0:.1f}s, "
        "engine=rdflib)")
    return g

def name_of(u): return urllib.parse.unquote(str(u).split("#file/")[-1])

def graph_analytics(g, man):
    import rdflib
    U = rdflib.URIRef
    # Whole-graph counters come from grouped counts: one Rust-side
    # GROUP BY on a GraphView, or a single Python pass on plain rdflib.
    # Ties in most_common() are broken explicitly (count desc, name asc)
    # so both engines produce byte-identical analytics.
    if hasattr(g, "predicate_counts"):
        pred_counts = g.predicate_counts()
        cls_counts = g.class_counts()
    else:
        pred_counts = Counter(g.predicates())
        cls_counts = Counter(g.objects(None, rdflib.RDF.type))
    ns = Counter()
    for p, c in pred_counts.items():
        s = str(p)
        key = s.split("#")[0].rsplit("/", 1)[-1] if "#" in s else s.rsplit("/", 2)[-2]
        ns[key] += c
    classes = Counter()
    for o, c in cls_counts.items():
        classes[str(o).split("#")[-1]] += c
    preds = Counter()
    for p, c in pred_counts.items():
        preds[str(p)] += c

    def _ranked(counter, limit=None):
        items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return items[:limit] if limit else items
    ftype = {s: str(o).split("#")[-1] for s, o in g.subject_objects(U(CBM + "type"))}
    src = {s for s, t in ftype.items() if t == "source_code"}
    tst = {s for s, t in ftype.items() if t == "test_code"}
    edges = list(g.subject_objects(U(CBM + "imports")))
    indeg, outdeg = Counter(), Counter()
    for s, o in edges: indeg[o] += 1; outdeg[s] += 1
    # main package detection
    def top(p): return p.split("/")[0]
    # main package = the top-level dir whose files the rest of the repo imports most
    # (import mass), preferring a clean manifest source root when one names a real dir.
    mass = Counter()
    for f in src: mass[top(name_of(f))] += indeg[f]
    roots = [r.strip("/").split("/")[0] for r in (man.get("python_source_roots") or [])
             if r and r.strip("/") not in (".", "", "src")]
    cand = [r for r in roots if mass.get(r)] or [k for k, _ in mass.most_common(3)]
    main_pkg = max(cand, key=lambda k: mass.get(k, 0)) if cand else ""
    def subsystem(p):
        if main_pkg and p.startswith(main_pkg + "/"):
            q = p.split("/")
            return f"{main_pkg}/{q[1]}" if len(q) > 2 else f"{main_pkg} core"
        return top(p)
    # chokepoints & interchanges
    chokepoints = [{"file": name_of(f), "in": c, "out": outdeg[f]} for f, c in indeg.most_common(12)]
    cross = defaultdict(set)
    for s, o in edges:
        ss, so = subsystem(name_of(s)), subsystem(name_of(o))
        if so == f"{main_pkg} core" and ss != so: cross[name_of(o)].add(ss)
    interchanges = sorted(((f, sorted(l)) for f, l in cross.items() if len(l) >= 2),
                          key=lambda x: -len(x[1]))
    # test evidence: shipped heuristic vs typed-import derivation
    tests_edges = [(name_of(s), name_of(o)) for s, o in g.subject_objects(U(CBM + "tests"))]
    te_objs = Counter(o for _, o in tests_edges)
    t2s = [(s, o) for s, o in edges if s in tst and o in src]
    t2s_targets = Counter(name_of(o) for _, o in t2s)
    # external deps & pins
    ext = Counter(name_of(o).split("#")[-1] if "#file/" not in str(o) else name_of(o)
                  for _, o in g.subject_objects(U(CBM + "importsExternal")))
    pins = []
    for s, o in list(g.subject_objects(U(CBM + "pinsDependency")))[:2000]:
        pins.append(str(o).split("#")[-1].split("/")[-1])
    # receipts (cbml4 in-graph)
    summ, model, psha, gat = {}, {}, {}, {}
    for s, o in g.subject_objects(U(C4 + "fileSummary")): summ[s] = str(o)
    for s, o in g.subject_objects(U(C4 + "fileSummaryModel")): model[s] = str(o)
    for s, o in g.subject_objects(U(C4 + "fileSummaryPromptSha")): psha[s] = str(o)
    for s, o in g.subject_objects(U(C4 + "fileSummaryGeneratedAt")): gat[s] = str(o)
    receipts = []
    for f in sorted(summ, key=lambda f: -(indeg[f] + outdeg[f]))[:3]:
        receipts.append({"file": name_of(f), "summary": summ[f], "model": model.get(f, ""),
                         "prompt_sha": psha.get(f, ""), "generated_at": gat.get(f, "")})
    # districts input
    inFile = {s: o for s, o in g.subject_objects(U(C2 + "inFile"))}
    row = {s: int(o) for s, o in g.subject_objects(U(C2 + "embeddingRow"))}
    endl = defaultdict(int)
    for s, o in g.subject_objects(U(C2 + "endLine")):
        f = inFile.get(s)
        if f is not None: endl[f] = max(endl[f], int(o))
    dfiles = sorted({f for f in inFile.values() if f in src or f in tst}, key=lambda f: name_of(f))
    # metro model
    subs = defaultdict(list)
    for f in src: subs[subsystem(name_of(f))].append(f)
    line_names = ([f"{main_pkg} core"] if f"{main_pkg} core" in subs else []) + \
        sorted([k for k in subs if k.startswith(main_pkg + "/")], key=lambda k: (-len(subs[k]), k))[:5]
    anchor_votes = defaultdict(Counter)
    for s, o in edges:
        ss, so = subsystem(name_of(s)), subsystem(name_of(o))
        if ss in line_names and so == f"{main_pkg} core" and ss != so:
            anchor_votes[ss][o] += 1
    metro = {"main_pkg": main_pkg, "interchanges": [f for f, _ in interchanges], "lines": []}
    for ln in line_names:
        rk = sorted(subs[ln], key=lambda f: -(indeg[f] + outdeg[f]))
        metro["lines"].append({
            "name": ln,
            "anchor": name_of(anchor_votes[ln].most_common(1)[0][0]) if anchor_votes[ln] else None,
            "stations": [{"file": name_of(f), "in": indeg[f], "out": outdeg[f],
                          "summary": summ.get(f, ""), "model": model.get(f, ""),
                          "sha": psha.get(f, "")[:12]} for f in rk[:8 if ln.endswith("core") else 5]]})
    # degree histograms: in-degree over source files, out-degree over
    # source + test files (test out-edges are the evidence the report cites)
    def deg_hist(pop, deg):
        buckets = [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3–4", 3, 4),
                   ("5–8", 5, 8), ("9–16", 9, 16), ("17–32", 17, 32),
                   ("33+", 33, float("inf"))]
        return [(lab, sum(1 for f in pop if lo <= deg[f] <= hi))
                for lab, lo, hi in buckets]
    return {"triples": len(g), "ns": _ranked(ns), "classes": _ranked(classes, 12),
            "top_preds": _ranked(preds, 12),
            "n_src": len(src), "n_tst": len(tst),
            "deg_hist": {"in": deg_hist(src, indeg),
                         "out": deg_hist(src | tst, outdeg)},
            "edges": len(edges), "chokepoints": chokepoints,
            "interchanges": [{"file": f, "lines": l} for f, l in interchanges[:10]],
            "tests_edges": {"n": len(tests_edges),
                            "top_objects": [(name_of(o), c) for o, c in te_objs.most_common(4)]},
            "test_evidence": {"typed_import_edges": len(t2s),
                              "top_targets": t2s_targets.most_common(6)},
            "external": ext.most_common(15), "pins_n": len(pins),
            "receipts": receipts,
            "_district": {"files": dfiles, "inFile": inFile, "row": row, "endl": endl,
                          "ftype": ftype, "subsystem": subsystem}, "_metro": metro}

def load_enrichments(path):
    kinds, models = Counter(), Counter()
    n, complete, near_cap = 0, 0, 0
    tlens = []
    REQ = ("model", "prompt_sha", "generated_at", "target_sha")
    with open(path) as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line); n += 1
            kinds[r.get("kind", "?")] += 1; models[r.get("model", "?")] += 1
            if all(r.get(k) for k in REQ): complete += 1
            t = r.get("text", ""); tlens.append(len(t))
            if len(t) >= 3990: near_cap += 1
    return {"n": n, "kinds": kinds.most_common(), "models": models.most_common(),
            "provenance_complete": complete, "near_4000_cap": near_cap,
            "text_len_mean": (sum(tlens) / n) if n else 0,
            "text_len_max": max(tlens) if tlens else 0}

def load_concepts(path):
    c = json.load(open(path))
    concepts = c.get("concepts") or c.get("concept_embedding_ids") or []
    per_path = c.get("per_path_concepts") or {}
    freq = Counter()
    for _, cl in per_path.items():
        for k in (cl or []): freq[k] += 1
    co = c.get("cooccurrence")
    co_n = len(co) if hasattr(co, "__len__") else 0
    return {"n_concepts": len(concepts), "n_paths": len(per_path),
            "top": freq.most_common(15), "cooccurrence_entries": co_n}

def load_concept_embeddings(path):
    """L3 concept vectors — reported alongside concepts.json as a cross-check."""
    if not path: return None
    try:
        import numpy as np
        z = np.load(path, allow_pickle=True)
    except Exception as e:
        log("concept embeddings unreadable:", e); return None
    field = "vectors" if "vectors" in z else next(iter(z.files), None)
    if not field: return None
    v = z[field]
    return {"n_vectors": int(v.shape[0]),
            "dim": int(v.shape[1]) if getattr(v, "ndim", 1) > 1 else 0}

def load_ontology_mapping(path):
    """The bundle's formal alignment of cbm vocabulary to external ontologies
    (SPDX 3.0.1, OWL). FACT: parsed straight from ontology-mapping.ttl."""
    if not path: return None
    import rdflib
    from rdflib.namespace import RDFS, OWL
    g = rdflib.Graph()
    try:
        g.parse(path, format="turtle")
    except Exception as e:
        log("ontology-mapping unreadable:", e); return None
    def tail(u):
        s = str(u); return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]
    RELS = {RDFS.subClassOf: "subClassOf", OWL.equivalentClass: "equivalentClass",
            RDFS.seeAlso: "seeAlso"}
    rels, hosts = [], set()
    for s, p, o in g:
        if p in RELS and not str(o).startswith(CBM) and isinstance(o, rdflib.URIRef):
            rels.append({"term": tail(s), "rel": RELS[p],
                         "target": tail(o), "target_full": str(o)})
            hosts.add(urllib.parse.urlparse(str(o)).netloc or str(o))
    rels.sort(key=lambda r: (r["term"], r["rel"]))
    return {"rels": rels, "n": len(rels), "hosts": sorted(hosts)}

def load_ast_coverage(path):
    a = json.load(open(path))
    langs = []
    for lang, m in sorted((a.get("by_language") or {}).items()):
        langs.append({"lang": lang, **{k: m.get(k, 0) for k in
                      ("files", "files_with_ast", "files_zero_ast", "files_with_parse_errors",
                       "symbols_extracted", "imports_extracted", "silent_zero_symbol_files")}})
    return {"langs": langs, "totals": a.get("totals") or {},
            "notes": a.get("notes") or [],
            "silent_list": (a.get("silent_zero_symbol_file_list") or [])[:20],
            "silent_truncated": a.get("silent_zero_symbol_file_list_truncated")}

def load_abox(path):
    if not path: return None
    import rdflib
    g = rdflib.Graph(); g.parse(path, format="turtle")
    A = "https://w3id.org/arc4d3/software-architecture-dimensions#"
    U = rdflib.URIRef; tail = lambda u: str(u).split("#")[-1]
    dims = []
    for s in g.subjects(rdflib.RDF.type, U(A + "DimensionApplication")):
        d = {"id": tail(s)}
        for p, o in g.predicate_objects(s):
            pn = tail(p)
            if pn == "appliesDimension": d["dim"] = tail(o)
            elif pn == "dominantValue": d["dominant"] = tail(o)
            elif pn == "usesClassificationValue": d.setdefault("values", []).append(tail(o))
            elif pn == "confidenceLevel": d["conf"] = str(o)
            elif pn == "revealsRisk": d["risk"] = tail(o)
            elif pn == "evidenceSummary": pass
        ev = next(g.objects(U(str(s).replace("App_", "Ev_")), U(A + "evidenceSummary")), None)
        d["evidence"] = str(ev)[:220] if ev else ""
        dims.append(d)
    dims.sort(key=lambda d: d.get("dim", ""))
    risks = []
    for s in g.subjects(rdflib.RDF.type, U(A + "RiskFinding")):
        lbl = next(g.objects(s, rdflib.RDFS.label), None) or next(g.objects(s, rdflib.RDFS.comment), "")
        risks.append({"id": tail(s), "label": str(lbl)})
    return {"dims": dims, "risks": risks, "triples": len(g),
            "creator": str(next(g.objects(None, U("http://purl.org/dc/terms/creator")), ""))}

# ----------------------------------------------------------------------------
# assets: kind grouping + inline image gallery (bytes from the verified
# content-addressed blob store — FACT, and self-contained in the HTML)

_ASSET_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"},
    "font": {".woff", ".woff2", ".ttf", ".otf", ".eot"},
    "audio/video": {".mp3", ".mp4", ".webm", ".wav", ".ogg"},
    "design": {".ai", ".psd", ".sketch", ".fig"},
    "document": {".pdf", ".eps"},
}
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".svg": "image/svg+xml",
               ".ico": "image/x-icon", ".webp": "image/webp"}


def asset_kind(path):
    """Human kind for an asset path: image / font / audio-video /
    design / document / other."""
    ext = os.path.splitext(path)[1].lower()
    for kind, exts in _ASSET_KINDS.items():
        if ext in exts:
            return kind
    return "other"


def image_gallery_items(files, blobs_dir, *, limit=60, max_bytes=300_000):
    """Gallery entries for image assets, smallest first.

    ``files`` is (path, content_sha256, size_bytes) triples; bytes come
    from the blob store and are embedded as data URIs so the report
    stays self-contained. Caps are disclosed: returns (items, omitted)
    where ``omitted`` counts images dropped by the count limit, the
    per-image byte cap, or a missing blob — never silently (PALS's Law).
    """
    import base64
    candidates = sorted(
        ((p, sha, size) for p, sha, size in files
         if os.path.splitext(p)[1].lower() in _IMAGE_MIME),
        key=lambda t: (t[2], t[0]))
    items, omitted = [], 0
    for path, sha, size in candidates:
        if len(items) >= limit or size > max_bytes:
            omitted += 1
            continue
        blob = os.path.join(blobs_dir, sha)
        if not os.path.exists(blob):
            omitted += 1
            continue
        with open(blob, "rb") as fh:
            data = fh.read()
        mime = _IMAGE_MIME[os.path.splitext(path)[1].lower()]
        items.append({
            "path": path, "size_bytes": size, "mime": mime,
            "data_uri": f"data:{mime};base64,"
                        + base64.b64encode(data).decode("ascii"),
        })
    return items, omitted


def asset_inventory(g, found):
    """Census of asset/binary files by kind (count + bytes) plus the
    image-gallery entries. Bytes come from the graph's sizeBytes facts;
    gallery pixels from the blob store when the bundle carries one."""
    import rdflib
    U = rdflib.URIRef
    ftype = {s: str(o).split("#")[-1]
             for s, o in g.subject_objects(U(CBM + "type"))}
    paths = {s: str(o) for s, o in g.subject_objects(U(CBM + "path"))}
    sizes = {s: int(o) for s, o in g.subject_objects(U(CBM + "sizeBytes"))}
    shas = {s: str(o) for s, o in g.subject_objects(U(CBM + "contentSha256"))}
    kinds = defaultdict(lambda: {"n": 0, "bytes": 0})
    files = []
    for subj, t in ftype.items():
        if t not in ("asset", "binary"):
            continue
        path = paths.get(subj, "")
        kind = asset_kind(path) if t == "asset" else "binary (unknown)"
        kinds[kind]["n"] += 1
        kinds[kind]["bytes"] += sizes.get(subj, 0)
        if subj in shas:
            files.append((path, shas[subj], sizes.get(subj, 0)))
    census = sorted(((k, v["n"], v["bytes"]) for k, v in kinds.items()),
                    key=lambda x: (-x[1], x[0]))
    if found.get("blobs_dir"):
        items, omitted = image_gallery_items(files, found["blobs_dir"])
    else:
        items, omitted = [], sum(
            1 for p, _s, _z in files
            if os.path.splitext(p)[1].lower() in _IMAGE_MIME)
    return census, {"items": items, "omitted": omitted}


def gallery_html(items, omitted):
    """The gallery grid fragment for emit_html."""
    if not items:
        return "<p class='absent'>No image assets in the blob store.</p>"
    cells = "".join(
        f"<figure class='thumb'><img src='{it['data_uri']}' "
        f"alt='{html.escape(it['path'])}' loading='lazy'/>"
        f"<figcaption>{html.escape(it['path'])}"
        f"<span class='sz'>{it['size_bytes']:,} B</span></figcaption></figure>"
        for it in items)
    note = (f"<p class='cap'>{omitted} image(s) omitted "
            "(size/count caps or blob unavailable) — the full set lives in "
            "the bundle's <code>blobs/</code> store.</p>" if omitted else "")
    return f"<div class='gallery'>{cells}</div>{note}"


def _yaml_load(path):
    """YAML via libyaml's C loader when present — the pure-Python parser
    needs hours on kernel-scale decomposition files (380 MB observed)."""
    import yaml
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(path) as fh:
        return yaml.load(fh, Loader=loader)


def load_decomp(path):
    if not path: return None
    D = _yaml_load(path)
    parts = D.get("parts", [])
    return {"n_parts": len(parts),
            "kinds": Counter(p.get("kind") for p in parts).most_common(),
            "layers": Counter(str(p.get("layer")) for p in parts).most_common(8),
            "conf": Counter(p.get("overall_confidence") or p.get("responsibility_confidence")
                            or "unknown" for p in parts).most_common(),
            "part_conf": [(p.get("kind"), p.get("overall_confidence")
                           or p.get("responsibility_confidence") or "unknown") for p in parts],
            "relationships": len(D.get("relationships", [])),
            "gates": len(D.get("quality_gates", [])),
            "cycles": {"module": D.get("repository", {}).get("n_module_cycles", 0),
                       "file": D.get("repository", {}).get("n_file_cycles", 0)},
            "build_groups": [len(x) if isinstance(x, list) else 1 for x in D.get("build_order", [])],
            "purpose": D.get("repository", {}).get("purpose", ""),
            "purpose_conf": D.get("repository", {}).get("purpose_confidence", "")}

def load_buildplan(path):
    if not path: return None
    B = _yaml_load(path)
    steps = B.get("steps", [])
    seq, cum, phases = [], 0, {}
    for s in steps:
        c = len(s.get("creates", []) or []); cum += c
        ph = phases.setdefault(s["phase"], {"n": 0, "title": s.get("phase_title", ""),
                                            "creates": 0, "conf": Counter()})
        ph["n"] += 1; ph["creates"] += c; ph["conf"][s.get("confidence", "unknown")] += 1
        seq.append({"phase": s["phase"], "conf": s.get("confidence", "unknown"),
                    "creates": c, "cum": cum})
    vio = B.get("architecture_intent", {}).get("known_violations_to_not_replicate_blindly", [])
    return {"n_steps": len(steps), "seq": seq, "total_creates": cum,
            "phases": [{"phase": k, "title": v["title"], "n": v["n"], "creates": v["creates"],
                        "conf": dict(v["conf"])} for k, v in sorted(phases.items())],
            "skipped": [{"phase": str(x.get("phase", x))[:60], "reason": str(x.get("reason", ""))[:120]}
                        if isinstance(x, dict) else {"phase": str(x)[:60], "reason": ""}
                        for x in B.get("skipped_phases", [])],
            "violations": Counter(v.get("kind") for v in vio).most_common(),
            "assumptions": [str(a)[:160] for a in B.get("open_assumptions", [])],
            "conf": Counter(s.get("confidence", "unknown") for s in steps).most_common(),
            "style": B.get("architecture_intent", {}).get("style", ""),
            "style_conf": B.get("architecture_intent", {}).get("confidence", "")}

def district_xy(dist, bundle, cache_dir, skip):
    files = dist["files"]
    if skip or not files: return None
    emb = os.path.join(bundle, "embeddings.npz")
    sig = f"{int(os.stat(emb).st_mtime)}_{len(files)}" if os.path.exists(emb) else str(len(files))
    cache = os.path.join(cache_dir, f"district_xy_{sig}.json")
    if os.path.exists(cache):
        import numpy as np
        Y = np.array(json.load(open(cache))["xy"])
        if len(Y) == len(files): return Y
    try:
        import numpy as np
        from sklearn.decomposition import PCA
        from sklearn.manifold import TSNE
    except ImportError:
        log("sklearn unavailable — district map skipped"); return None
    M = np.load(os.path.join(bundle, "embeddings.npz"))["vectors"]
    acc = defaultdict(list)
    fs = set(files)
    for ch, f in dist["inFile"].items():
        r = dist["row"].get(ch)
        if r is not None and f in fs: acc[f].append(M[r])
    X = np.vstack([np.mean(acc[f], axis=0) for f in files])
    Xp = PCA(n_components=min(50, X.shape[1]), random_state=42).fit_transform(X)
    Y = TSNE(n_components=2, perplexity=min(30, max(5, len(files) // 20)),
             random_state=42, init="pca", learning_rate="auto").fit_transform(Xp)
    Y = (Y - Y.min(0)) / (np.ptp(Y, 0) + 1e-9)
    json.dump({"xy": Y.round(4).tolist()}, open(cache, "w"))
    return Y

# ----------------------------------------------------------------------------
# SVG builders (geometry ported from the QA'd map/poster generators)
def svg_wheel(abox, W=560, H=560):
    if not abox: return "<p class='absent'>arc4d3 ABox not provided for this run.</p>"
    cx, cy, R = W / 2, H / 2 + 4, min(W, H) / 2 - 78
    dims = abox["dims"]; N = max(1, len(dims))
    out = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    for ring in (0.16, 0.38, 0.66, 1.0):
        out.append(f"<circle cx='{cx}' cy='{cy}' r='{R*ring:.1f}' fill='none' "
                   f"stroke='{FAINT if ring < 1 else PALE}' stroke-width='0.8'/>")
    for i, d in enumerate(dims):
        th = math.pi / 2 - 2 * math.pi * i / N
        x1, y1 = math.cos(th), -math.sin(th)
        r = CONF_R.get(d.get("conf", "Unknown"), 0.16)
        risky = "risk" in d
        col = VERM if risky else TONE.get(d.get("conf"), PALE)
        out.append(f"<line x1='{cx+R*0.16*x1:.1f}' y1='{cy+R*0.16*y1:.1f}' "
                   f"x2='{cx+R*x1:.1f}' y2='{cy+R*y1:.1f}' stroke='{FAINT}' stroke-width='0.7'/>")
        out.append(f"<line x1='{cx+R*0.16*x1:.1f}' y1='{cy+R*0.16*y1:.1f}' "
                   f"x2='{cx+R*r*x1:.1f}' y2='{cy+R*r*y1:.1f}' stroke='{col}' stroke-width='1.6'/>")
        out.append(f"<circle cx='{cx+R*r*x1:.1f}' cy='{cy+R*r*y1:.1f}' r='4.4' fill='{col}'>"
                   f"<title>{ESC(d.get('dim',''))} = {ESC(d.get('dominant') or '/'.join(d.get('values',[])) or '—')}"
                   f" · confidence {ESC(d.get('conf','?'))}{' · RISK' if risky else ''}\n{ESC(d.get('evidence',''))}</title></circle>")
        if risky:
            out.append(f"<circle cx='{cx+R*r*x1:.1f}' cy='{cy+R*r*y1:.1f}' r='7.6' fill='none' "
                       f"stroke='{VERM}' stroke-width='1.1'/>")
        rl = R * (1.10 + 0.115 * (i % 2))
        lx, ly = cx + rl * x1, cy + rl * y1
        deg = -math.degrees(th)
        deg, anch = (deg + 180, "end") if x1 < 0 else (deg, "start")
        val = d.get("dominant") or "/".join(d.get("values", [])) or "—"
        val = val if len(val) <= 17 else val[:16] + "…"
        out.append(f"<text x='{lx:.1f}' y='{ly:.1f}' font-size='7.6' class='mono' "
                   f"fill='{VERM if risky else GREY}' text-anchor='{anch}' "
                   f"transform='rotate({deg:.1f} {lx:.1f} {ly:.1f})'>"
                   f"{ESC(d.get('dim','')[:3])} {ESC(val)}</text>")
    out.append(f"<text x='{cx}' y='{cy+2}' text-anchor='middle' font-size='30' class='disp' fill='{INK}'>{N}</text>")
    out.append(f"<text x='{cx}' y='{cy+18}' text-anchor='middle' font-size='8' class='caps' fill='{GREY}'>DIMENSIONS</text>")
    out.append("</svg>")
    return "".join(out)

def svg_metro(metro, W=1180, H=470):
    if not metro["lines"]: return "<p class='absent'>No package structure detected for a metro view.</p>"
    core = metro["lines"][0]
    order = sorted(core["stations"], key=lambda s: (s["file"].endswith("__init__.py"), -s["out"], s["in"]))
    if not order: return "<p class='absent'>Core line empty.</p>"
    X0, DX, YC = 70, (W - 200) / max(1, len(order) - 1), H / 2 + 10
    pos = {s["file"]: (X0 + i * DX, YC) for i, s in enumerate(order)}
    xs = [pos[s["file"]][0] for s in order]
    inter = set(metro["interchanges"])
    out = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    out.append(f"<line x1='{xs[0]-36}' y1='{YC}' x2='{xs[-1]+36}' y2='{YC}' stroke='{VERM}' "
               f"stroke-width='6' stroke-linecap='round'/>")
    def station(x, y, col, s):
        ic = s["file"] in inter
        tip = (f"{s['file']} — imported-by {s['in']} · imports {s['out']}"
               + (f"\n“{s['summary'][:180]}” — {s['model']} · prompt {s['sha']}" if s.get("summary") else ""))
        return (f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{9 if ic else 5.6}' "
                f"fill='{'#fff' if ic else col}' stroke='{INK if ic else col}' "
                f"stroke-width='{2.4 if ic else 2}'><title>{ESC(tip)}</title></circle>")
    def lab(x, y, t, dy): return (f"<text x='{x:.1f}' y='{y+dy:.1f}' font-size='9.5' class='mono' fill='{INK}' "
                                  f"transform='rotate(-38 {x:.1f} {y+dy:.1f})'>{ESC(t)}</text>")
    for s in order:
        x, y = pos[s["file"]]
        out.append(station(x, y, VERM, s)); out.append(lab(x - 4, y, s["file"].split("/")[-1], -16))
    tones = [INK, GREY, "#8d8779", "#a49d8d", PALE]
    lanes = [(-88, ), (-158, ), (88, ), (158, ), (-228, )]
    for j, L in enumerate(metro["lines"][1:6]):
        dy = lanes[j][0]; col = tones[j % len(tones)]
        anchor = L["anchor"] or order[-1]["file"]
        ax, _ = pos.get(anchor, (xs[-1], YC)); yb = YC + dy
        hdir = -1 if ax > W * 0.68 else 1
        ex = ax + hdir * abs(dy)
        seq = [s for s in L["stations"] if not s["file"].endswith("__init__.py")][:3] \
            + [s for s in L["stations"] if s["file"].endswith("__init__.py")]
        if not seq: continue
        stx = [ex + hdir * (24 + i * 96) for i in range(len(seq))]
        if hdir > 0 and stx[-1] + 30 > W - 15:
            sh = stx[-1] + 30 - (W - 15); ex -= sh; stx = [x - sh for x in stx]
        if hdir < 0 and stx[-1] - 30 < 15:
            sh = 15 - (stx[-1] - 30); ex += sh; stx = [x + sh for x in stx]
        dash = " stroke-dasharray='2 7'" if L["anchor"] is None else ""
        out.append(f"<polyline points='{ax:.0f},{YC:.0f} {ex:.0f},{yb:.0f} {stx[-1]+hdir*30:.0f},{yb:.0f}' "
                   f"fill='none' stroke='{col}' stroke-width='4' stroke-linecap='round' "
                   f"stroke-linejoin='round'{dash}/>")
        nm = L["name"] + (" · re-export only" if L["anchor"] is None else "")
        lx2, anch2 = (min(ex + 6, W - 12), "end") if hdir < 0 else (ex - 6, "end")
        out.append(f"<text x='{lx2:.0f}' y='{yb + (16 if dy>0 else -10):.0f}' class='caps' "
                   f"font-size='10.5' fill='{col}' text-anchor='{anch2}'>{ESC(nm)}</text>")
        for x, s in zip(stx, seq):
            out.append(station(x, yb, col, s))
            out.append(lab(x - 4, yb, s["file"].split("/")[-1], 20 if dy > 0 else -14))
    out.append("</svg>")
    return "".join(out)

def svg_district(dist, Y, W=1180, H=640):
    if Y is None: return "<p class='absent'>Embeddings absent or skipped — no district map for this run.</p>"
    files = dist["files"]; endl = dist["endl"]; ftype = dist["ftype"]
    import numpy as np
    P = np.column_stack([30 + Y[:, 0] * (W - 60), 44 + (1 - Y[:, 1]) * (H - 100)])
    indeg = {i: 0 for i in range(len(files))}
    out = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    for i, f in enumerate(files):
        x, y = P[i]; p = name_of(f)
        if ftype.get(f) == "test_code":
            out.append(f"<circle cx='{x:.0f}' cy='{y:.0f}' r='2.6' fill='none' stroke='{PALE}' stroke-width='0.8'/>")
        else:
            r = 2.8 + min(7.5, math.sqrt(max(endl.get(f, 10), 1)) / 4.6)
            out.append(f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{r:.1f}' fill='{GREY}' opacity='0.9'>"
                       f"<title>{ESC(p)} · ~{endl.get(f,0)} lines</title></circle>")
    out.append(f"<text x='30' y='{H-14}' class='serifit' font-size='11' fill='{GREY}'>"
               "tests render hollow — they settle beside what they test; the fog is the coverage</text>")
    out.append("</svg>")
    return "".join(out)

def svg_barcode(build, W=1180, H=250):
    if not build: return "<p class='absent'>Build plan not provided for this run.</p>"
    seq = build["seq"]; phases = build["phases"]
    n = max(1, len(seq)); maxc = max((s["creates"] for s in seq), default=1) or 1
    gap = 8; usable = W - 40 - gap * (len(phases) - 1)
    widths = {p["phase"]: max(6, p["n"] / n * usable) for p in phases}
    xstart, x = {}, 20
    for p in phases: xstart[p["phase"]] = x; x += widths[p["phase"]] + gap
    out = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    idx = Counter(); base = H - 58
    for s in seq:
        ph = s["phase"]; i = idx[ph]; idx[ph] += 1
        pn = next(p["n"] for p in phases if p["phase"] == ph)
        w = widths[ph] / pn
        bx = xstart[ph] + i * w
        h = 8 + 118 * math.log1p(s["creates"]) / math.log1p(maxc)
        out.append(f"<rect x='{bx:.1f}' y='{base-h:.1f}' width='{max(0.8,w*0.72):.2f}' height='{h:.1f}' "
                   f"fill='{TONE.get(s['conf'], FAINT)}'/>")
    pts, idx = [], Counter()
    for s in seq:
        ph = s["phase"]; i = idx[ph]; idx[ph] += 1
        pn = next(p["n"] for p in phases if p["phase"] == ph)
        w = widths[ph] / pn
        pts.append(f"{xstart[ph]+i*w+w*0.36:.1f},{base - 12 - 130*s['cum']/max(1,build['total_creates']):.1f}")
    out.append(f"<polyline points='{' '.join(pts)}' fill='none' stroke='{VERM}' stroke-width='1.8'/>")
    lx, ly = pts[-1].split(","); 
    out.append(f"<text x='{float(lx):.0f}' y='{float(ly)-8:.0f}' class='mono' font-size='11' "
               f"fill='{VERM}' text-anchor='end'>{build['total_creates']:,} files</text>")
    for p in phases:
        cx = xstart[p["phase"]] + widths[p["phase"]] / 2
        if widths[p["phase"]] > 16:
            out.append(f"<text x='{cx:.0f}' y='{base+16}' text-anchor='middle' class='caps' font-size='10' fill='{INK}'>{p['phase']:02d}</text>")
        if widths[p["phase"]] > 60:
            out.append(f"<text x='{cx:.0f}' y='{base+30}' text-anchor='middle' class='mono' font-size='7.5' fill='{PALE}'>{p['n']} steps · {p['creates']} files</text>")
        t = p["title"]
        if widths[p["phase"]] > 90:
            out.append(f"<text x='{cx:.0f}' y='{base+44}' text-anchor='middle' class='serif' font-size='9' fill='{GREY}'>{ESC(t[:30])}</text>")
    out.append("</svg>")
    return "".join(out)

def svg_waffle(decomp, W=560, H=330):
    if not decomp: return "<p class='absent'>Decomposition not provided for this run.</p>"
    order = {"certain": 0, "strong": 1, "probable": 2, "weak": 3, "unknown": 4}
    kindrank = {k: i for i, (k, _) in enumerate(decomp["kinds"])}
    cells = sorted(decomp["part_conf"], key=lambda kc: (kindrank.get(kc[0], 99), order.get(kc[1], 9)))
    cols = 24; cw = (W - 20) / cols; ch = cw * 1.28
    out = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    for i, (kind, conf) in enumerate(cells):
        r, c = divmod(i, cols)
        out.append(f"<rect x='{10+c*cw:.1f}' y='{8+r*ch:.1f}' width='{cw*0.78:.1f}' height='{ch*0.78:.1f}' "
                   f"fill='{TONE.get(conf, FAINT)}'><title>{ESC(str(kind))} · {ESC(str(conf))}</title></rect>")
    out.append("</svg>")
    return "".join(out)

def bars(pairs, total, unit=""):
    rows = []
    mx = max((v for _, v in pairs), default=1)
    for k, v in pairs:
        w = 100 * v / mx
        rows.append(f"<tr><td class='mono'>{ESC(str(k))}</td>"
                    f"<td class='barcell'><div class='bar' style='width:{w:.1f}%'></div></td>"
                    f"<td class='num'>{v:,}{unit}</td></tr>")
    return f"<table class='bars'>{''.join(rows)}</table>"

# ----------------------------------------------------------------------------
# emitters
CSS = f"""
:root{{--paper:{PAPER};--ink:{INK};--grey:{GREY};--pale:{PALE};--faint:{FAINT};--verm:{VERM}}}
*{{box-sizing:border-box}} body{{background:var(--paper);color:var(--ink);margin:0;
 font-family:Georgia,'Times New Roman',serif;font-size:14.5px;line-height:1.55}}
.page{{max-width:1240px;margin:0 auto;padding:44px 40px 60px}}
.mono,td.mono,.num{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}}
.caps{{font-family:ui-monospace,Menlo,monospace;letter-spacing:.14em;text-transform:uppercase}}
.disp{{font-weight:700;letter-spacing:-.01em}} .serifit{{font-style:italic}}
header .kicker{{color:var(--grey);font-size:12px}} header h1{{font-size:44px;margin:.05em 0 .1em;font-weight:800;letter-spacing:-.02em}}
header .meta{{color:var(--grey);font-size:12.5px}} header .ok{{color:var(--ink)}} header .bad{{color:var(--verm)}}
hr{{border:0;border-top:1px solid var(--ink);margin:26px 0 10px}}
h2{{font-size:13px;margin:26px 0 4px}} h2 .tag{{float:right;color:var(--grey);font-weight:400;font-size:10.5px;letter-spacing:.06em}}
section{{margin-bottom:8px}}
.counters{{display:grid;grid-template-columns:repeat(6,1fr);gap:0;border-top:1px solid var(--ink);border-bottom:1px solid var(--ink);margin:14px 0}}
.counters div{{padding:14px 6px;text-align:center;border-left:1px solid var(--faint)}}
.counters div:first-child{{border-left:0}}
.counters b{{display:block;font-size:22px;font-family:ui-monospace,Menlo,monospace}}
.counters span{{font-size:9.5px;color:var(--grey)}}
.grid2{{display:grid;grid-template-columns:1.15fr .85fr;gap:34px}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
td,th{{padding:3.5px 8px 3.5px 0;vertical-align:top;text-align:left}}
th{{font-size:10px;color:var(--grey);letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--pale)}}
tr+tr td{{border-top:1px solid #eee7d6}}
.num{{text-align:right;white-space:nowrap}}
.bars .barcell{{width:52%}} .bar{{height:9px;background:var(--grey)}}
.receipt{{background:#efe8d8;border-left:3px solid var(--verm);padding:10px 14px;margin:10px 0;font-size:12.5px}}
.receipt .mono{{font-size:11px;color:var(--grey)}}
.absent{{color:var(--pale);font-style:italic}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;margin:12px 0}}
.thumb{{margin:0;border:1px solid var(--faint);padding:6px;background:#fff}}
.thumb img{{width:100%;height:90px;object-fit:contain;display:block}}
.thumb figcaption{{font-size:9px;word-break:break-all;color:var(--grey);margin-top:4px}}
.thumb .sz{{display:block;color:var(--pale)}}
.risk{{border-left:3px solid var(--verm);padding-left:12px;margin:10px 0}}
.risk b{{font-family:ui-monospace,Menlo,monospace;font-size:12px}}
.fine{{font-size:11px;color:var(--grey)}} .finer{{font-size:10px;color:var(--pale)}}
.fact{{color:var(--ink)}} .derived{{color:var(--grey)}} .unv{{color:var(--verm)}}
details summary{{cursor:pointer;color:var(--grey);font-size:12px}}
svg text.mono{{font-family:ui-monospace,Menlo,monospace}} svg text.caps{{font-family:ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase}}
svg text.serif,svg text.serifit{{font-family:Georgia,serif}} svg text.serifit{{font-style:italic}} svg text.disp{{font-weight:700}}
footer{{margin-top:34px;border-top:1px solid var(--ink);padding-top:10px;color:var(--grey);font-size:11px}}
@media print{{.page{{padding:10mm}}}}
"""

def sec(title, tag, body):
    return f"<section><hr/><h2 class='caps'>{ESC(title)}<span class='tag mono'>{ESC(tag)}</span></h2>{body}</section>"


# --- Evidence banner (standing operator override, CLAUDE.md Rule 5) ---------
# One phrase across every report generator: the Rust crate (tools/cbm-report),
# the authored-report pipeline (report_to_pdf.py), the reporting contract
# (docs/reporting/report-spec.schema.json: disclaimer_mode
# `evidence_basis_banner`), the dossier's provenance page, and the structural
# reports emitted here. tests/test_evidence_banner.py pins the phrase and the
# call sites.

EVIDENCE_BANNER_LABEL = "Evidence basis & confidence"
EVIDENCE_BANNER_TEXT = (
    "Structural figures are mechanically extracted from the bundle and "
    "evidence-backed (FACT); projections and clusterings are deterministic, "
    "disclosed computations over those facts (DERIVED); LLM-authored text is "
    "untrusted output, confidence-tagged and pending validation (UNVERIFIED). "
    "Validate interpretive claims before high-stakes decisions."
)


def evidence_banner_html():
    return ("<div class='banner fine'><b class='caps'>"
            f"{ESC(EVIDENCE_BANNER_LABEL)}</b> — {ESC(EVIDENCE_BANNER_TEXT)}"
            "</div>")


def evidence_banner_md():
    return f"\n**{EVIDENCE_BANNER_LABEL}.** {EVIDENCE_BANNER_TEXT}"


# --- Mechanical caveats (flaw map F15/F16) ---------------------------------
# The X-ray previously printed bundle figures as bare FACT even when the
# manifest itself proved they were distorted (91,736 import edges shown with
# no trace of the 407,936 extracted directives). Every caveat below is
# derived from run_manifest.json / the render's own inputs — no repo-specific
# knowledge — so a reader of this report alone inherits the disclosures.

IMPORT_RESOLUTION_FLOOR = 0.5   # resolved/extracted below this is a caveat
PARSE_ERROR_SHARE_FLOOR = 0.10  # flagged source files above this share
UNLANGUAGED_SHARE_FLOOR = 0.10  # files with no language above this share


def mechanical_caveats(man, found=None):
    """Caveat dicts ({id, severity, text}) computed from the manifest.

    An empty list means the manifest crossed none of the disclosure
    thresholds — not that nothing is wrong (PALS's Law: these are
    threshold checks on mechanical figures, not a verification).
    """
    caveats = []
    counts = man.get("counts") or {}
    cov = man.get("ast_coverage") or {}
    totals = cov.get("totals") or {}

    extracted = totals.get("imports_extracted") or 0
    resolved = counts.get("import_edges") or 0
    if extracted and resolved / extracted < IMPORT_RESOLUTION_FLOOR:
        caveats.append({
            "id": "import_resolution", "severity": "serious",
            "text": (f"The import graph resolves {resolved:,} of "
                     f"{extracted:,} extracted directives "
                     f"({resolved / extracted:.0%}). Coupling, centrality and "
                     f"blast-radius figures understate reality."),
        })

    n_source = cov.get("n_source_files") or totals.get("files") or 0
    flagged = totals.get("files_with_parse_errors") or 0
    if n_source and flagged / n_source > PARSE_ERROR_SHARE_FLOOR:
        nodes = totals.get("parse_error_nodes")
        caveats.append({
            "id": "parse_errors", "severity": "warning",
            "text": (f"{flagged:,} of {n_source:,} source files "
                     f"({flagged / n_source:.0%}) carry tree-sitter parse "
                     f"errors" + (f" ({nodes:,} error nodes)" if nodes else "")
                     + "; symbol and import counts from them are partial."),
        })

    by_lang = man.get("files_by_language") or {}
    n_files = counts.get("files") or 0
    unlang = by_lang.get("(none)") or 0
    if n_files and unlang / n_files > UNLANGUAGED_SHARE_FLOOR:
        caveats.append({
            "id": "unlanguaged_files", "severity": "warning",
            "text": (f"{unlang:,} of {n_files:,} files "
                     f"({unlang / n_files:.0%}) have no language "
                     f"classification and carry no per-language facts."),
        })

    l3 = (man.get("extensions") or {}).get("l3_40_concepts_artifact") or {}
    gap = l3.get("n_concepts_without_embedding") or 0
    if gap:
        caveats.append({
            "id": "concept_embedding_gap", "severity": "warning",
            "text": (f"{gap:,} of {l3.get('n_concepts', 0):,} concepts have "
                     f"no centroid vector (no embedded chunk in their "
                     f"lexicalizing files); semantic-neighbor results skip "
                     f"them."),
        })

    if "degradations" not in man:
        caveats.append({
            "id": "degradations_unknown", "severity": "warning",
            "text": ("This manifest predates degradation disclosure: whether "
                     "any layer self-disabled during the run is unknown."),
        })
    else:
        for d in man["degradations"]:
            caveats.append({
                "id": "degradation", "severity": "serious",
                "text": (f"Layer degradation recorded: "
                         f"{d.get('component', '?')} — "
                         f"{d.get('reason', '?')}"
                         + (f", affecting {d['affected_files']:,} files"
                            if d.get("affected_files") else "")
                         + (f", {d['skipped']:,} records skipped"
                            if d.get("skipped") else "") + "."),
            })

    for name in ("abox", "decomposition", "buildplan"):
        if found is not None and not found.get(name):
            caveats.append({
                "id": f"companion_missing_{name}", "severity": "note",
                "text": (f"Rendered without the {name} input — the "
                         f"corresponding layer reads as absent, not as "
                         f"nonexistent. Pass --{name} to wire it in."),
            })
    return caveats


def caveats_html(caveats):
    if not caveats:
        return sec("Data caveats", "FACT",
                   "<p class='fine'>No mechanical caveats: the manifest "
                   "crossed none of the disclosure thresholds.</p>")
    rows = "".join(
        f"<li><b class='caps'>{ESC(c['severity'])}</b> · {ESC(c['text'])}</li>"
        for c in caveats)
    return sec("Data caveats", "FACT",
               "<p class='fine'>Derived from run_manifest.json and the "
               "render inputs — read every figure below through these.</p>"
               f"<ul>{rows}</ul>")


def emit_html(M, out):
    man = M["manifest"]; G = M["graph"]
    hv = M["hash_rows"]; ok = sum(1 for r in hv if r["ok"]); tot = sum(1 for r in hv if r["ok"] is not None)
    shacl = man.get("shacl_self_check", {})
    ind = M.get("shacl_independent")
    head = f"""
<header>
 <div class='kicker caps'>Structural X-Ray · complete report</div>
 <h1 class='disp'>{ESC(M['repo'])}</h1>
 <div class='meta mono'>commit {ESC(M['commit'][:12])} · generated {ESC(str(man.get('generated_at',''))[:19])}
  · tool v{ESC(str(man.get('tool_version','?')))} · vocabulary {ESC(str(man.get('vocabulary_version','?')))}<br/>
  <span class='{ 'ok' if ok==tot else 'bad'}'>input hashes independently recomputed: {ok}/{tot} match</span>
  · SHACL self-check: {'conforms' if shacl.get('conforms') else str(shacl.get('conforms'))}
  {('· independent re-validation: <b>'+('conforms' if ind[0] else 'VIOLATIONS')+f'</b> ({ind[1]:.0f}s)') if ind else ''}</div>
</header>"""
    c = man.get("counts", {})
    counters = "".join(f"<div><b>{v:,}</b><span class='caps'>{ESC(k)}</span></div>" for k, v in [
        ("files", c.get("files", 0)), ("triples", G["triples"]),
        ("import edges", G["edges"]),
        ("parts", (M.get("decomp") or {}).get("n_parts", 0)),
        ("rebuild steps", (M.get("build") or {}).get("n_steps", 0)),
        ("L4 receipts", (M.get("enrich") or {}).get("n", 0))])
    body = [head, f"<div class='counters'>{counters}</div>"]

    # The evidence banner frames every figure; caveats follow immediately —
    # every figure below must be read through both.
    body.append(evidence_banner_html())
    body.append(caveats_html(M.get("caveats") or
                             mechanical_caveats(man, M.get("found"))))

    rows = "".join(f"<tr><td class='mono'>{ESC(r['artifact'])}</td><td class='mono'>{ESC(r['claimed'])}…</td>"
                   f"<td class='num'>{'match' if r['ok'] else ('<span class=unv>MISMATCH</span>' if r['ok'] is False else 'n/a')}</td></tr>"
                   for r in hv)
    blobs = M.get("blobs")
    body.append(sec("Verification", "every claim recomputed · FACT",
        f"<table><tr><th>artifact</th><th>claimed sha256</th><th>recomputed</th></tr>{rows}</table>"
        + (f"<p class='fine'>blob store: {blobs['on_disk']:,} objects on disk vs "
           f"{blobs['claimed']:,} claimed in manifest — {'match' if blobs['on_disk']==blobs['claimed'] else '<span class=unv>MISMATCH</span>'}.</p>" if blobs else "")
        + (f"<p class='finer'>a JSON-LD serialization twin (inventory.jsonld, {M['jsonld_bytes']/1048576:.1f} MB) ships beside the Turtle graph — same triples, alternate encoding.</p>" if M.get("jsonld_bytes") else "")))

    fl = bars(sorted(man.get("files_by_language", {}).items(), key=lambda x: -x[1])[:8], None)
    ft = bars(sorted(man.get("files_by_type", {}).items(), key=lambda x: -x[1]), None)
    ac = M.get("ast")
    acrows = ""
    if ac:
        acrows = "<table><tr><th>language</th><th class='num'>files</th><th class='num'>with AST</th><th class='num'>zero-AST</th><th class='num'>parse errors</th><th class='num'>symbols</th><th class='num'>imports</th></tr>" + \
            "".join(f"<tr><td class='mono'>{ESC(l['lang'])}</td><td class='num'>{l['files']}</td>"
                    f"<td class='num'>{l['files_with_ast']}</td><td class='num'>{l['files_zero_ast']}</td>"
                    f"<td class='num'>{l['files_with_parse_errors']}</td><td class='num'>{l['symbols_extracted']}</td>"
                    f"<td class='num'>{l['imports_extracted']}</td></tr>" for l in ac["langs"]) + "</table>"
        sym_tot = sum(l["symbols_extracted"] for l in ac["langs"])
        acrows += (f"<p class='fine'>symbols_extracted total: <b>{sym_tot}</b>"
                   + (" — no first-class symbol entities in this run; chunk-level cbml2:symbol labels exist. Disclosed, not hidden." if sym_tot == 0 else "")
                   + (f" · silent zero-symbol files listed: {len(ac['silent_list'])}"
                      + (" (truncated)" if ac.get("silent_truncated") else "") if ac["silent_list"] else "") + "</p>")
    body.append(sec("Inventory census", "manifest + ast_coverage.json · FACT",
        f"<div class='grid2'><div><h3 class='caps fine'>By language</h3>{fl}<h3 class='caps fine'>By type</h3>{ft}</div><div>{acrows}</div></div>"))

    census = M.get("assets") or []
    gal = M.get("_gallery") or {"items": [], "omitted": 0}
    if census:
        crow = "".join(
            f"<tr><td>{ESC(k)}</td><td class='num'>{n:,}</td>"
            f"<td class='num'>{b:,} B</td></tr>" for k, n, b in census)
        body.append(sec(
            "Assets & binaries", "kinds FACT · pixels from the verified blob store",
            f"<table class='kv'><tr><th>kind</th><th>files</th><th>bytes</th></tr>{crow}</table>"
            + gallery_html(gal["items"], gal["omitted"])))

    om = M.get("ontomap")
    if om and om["rels"]:
        orows = "".join(
            f"<tr><td class='mono'>cbm:{ESC(r['term'])}</td>"
            f"<td class='fine'>{ESC(r['rel'])}</td>"
            f"<td class='mono'><span title='{ESC(r['target_full'])}'>{ESC(r['target'])}</span></td></tr>"
            for r in om["rels"])
        body.append(sec("Vocabulary alignment", "ontology-mapping.ttl · FACT",
            "<p class='fine'>The cbm vocabulary declares formal alignment "
            "(subClassOf / equivalentClass / seeAlso) to external standard ontologies — "
            + ", ".join("<span class='mono'>" + ESC(h) + "</span>" for h in om["hosts"])
            + ". This is what makes a cbm bundle interoperable rather than a private schema.</p>"
            f"<table><tr><th>cbm term</th><th>relation</th><th>external term</th></tr>{orows}</table>"))

    nsrows = bars(G["ns"], None)
    clrows = bars(G["classes"], None)
    chok = "<table><tr><th>imported by</th><th>file</th><th class='num'>imports</th></tr>" + "".join(
        f"<tr><td class='num'>{r['in']}</td><td class='mono'>{ESC(r['file'])}</td><td class='num'>{r['out']}</td></tr>"
        for r in G["chokepoints"][:8]) + "</table>"
    inter = "".join(f"<tr><td class='mono'>{ESC(i['file'])}</td><td>{ESC(', '.join(i['lines']))}</td>"
                    f"<td class='num'>{len(i['lines'])}</td></tr>" for i in G["interchanges"][:8])
    body.append(sec("Graph layer — mechanical facts", f"{G['triples']:,} triples · FACT",
        f"<div class='grid2'><div><h3 class='caps fine'>Namespaces</h3>{nsrows}"
        f"<h3 class='caps fine'>Classes</h3>{clrows}</div>"
        f"<div><h3 class='caps fine'>Import chokepoints</h3>{chok}"
        f"<h3 class='caps fine'>Interchanges (imported by ≥2 subsystems)</h3><table><tr><th>file</th><th>subsystems</th><th class='num'>n</th></tr>{inter}</table></div></div>"))

    te = G["tests_edges"]; tev = G["test_evidence"]
    tewarn = ""
    if te["top_objects"] and te["n"] and te["top_objects"][0][1] / te["n"] > 0.5:
        f0, c0 = te["top_objects"][0]
        tewarn = (f"<p class='risk'><b>heuristic precision finding</b> — {c0} of {te['n']} "
                  f"cbm:tests edges point at one file (<span class='mono'>{ESC(f0)}</span>): "
                  f"a name-collision signature. FACT, measured from this graph.</p>")
    body.append(sec("Test evidence — measured twice", "FACT",
        f"<p>Shipped heuristic (<span class='mono'>cbm:tests</span>): <b>{te['n']}</b> edges. "
        f"Typed derivation (test-typed → source-typed imports): <b>{tev['typed_import_edges']}</b> edges"
        + (f" — a {tev['typed_import_edges']/max(1,te['n']):.0f}× larger, mechanically verified evidence base." if te['n'] else ".")
        + "</p>" + tewarn
        + "<h3 class='caps fine'>Top typed-import targets from tests</h3>"
        + bars(tev["top_targets"], None)
        + "<p class='fine'>Recommendation carried from the sample X-Ray: derive test evidence from typed imports minus test-infrastructure targets; retire the stem heuristic. PROPOSAL.</p>"))

    body.append(sec("The metro", "topology FACT · geometry DERIVED", svg_metro(G["_metro"])))
    body.append(sec("The districts", "positions DERIVED (t-SNE seed 42) · sizes FACT",
                    svg_district(G["_district"], M.get("district_xy"))))

    en = M.get("enrich")
    if en:
        pc = 100 * en["provenance_complete"] / max(1, en["n"])
        enr = (f"<p>{en['n']:,} records · kinds: " + ", ".join(f"{k} {v}" for k, v in en["kinds"])
               + " · models: " + ", ".join(f"<span class='mono'>{ESC(k)}</span> ×{v}" for k, v in en["models"])
               + f".</p><p>Per-record provenance (model + prompt_sha + generated_at + target_sha): "
               f"<b>{pc:.1f}%</b> complete ({en['provenance_complete']:,}/{en['n']:,}). "
               f"Text length mean {en['text_len_mean']:.0f} · max {en['text_len_max']:,}"
               + (f" · <span class='unv'>{en['near_4000_cap']} records at the 4,000-char cap</span> (known truncation gap)." if en["near_4000_cap"] else "."))
        for r in G["receipts"][:2]:
            enr += (f"<div class='receipt'>“{ESC(r['summary'][:260])}”"
                    f"<div class='mono'>{ESC(r['file'])} · {ESC(r['model'])} · prompt {ESC(r['prompt_sha'][:16])}… · {ESC(r['generated_at'])}</div></div>")
        body.append(sec("L4 — AI layer, with receipts", "UNVERIFIED content · provenance FACT", enr))
    else:
        body.append(sec("L4 — AI layer", "absent", "<p class='absent'>enrichments.jsonl not present in this run.</p>"))

    co = M.get("concepts")
    if co:
        ce = M.get("concepts_emb")
        ce_line = ""
        if ce:
            xchk = ("matches concepts.json" if ce["n_vectors"] == co["n_concepts"]
                    else f"vs {co['n_concepts']:,} concept ids in concepts.json")
            ce_line = ("<p class='fine'>Concept embedding space "
                       "(<span class='mono'>concepts_embeddings.npz</span>): "
                       f"<b>{ce['n_vectors']:,}</b> vectors · dim {ce['dim']} — {xchk}.</p>")
        body.append(sec("L3 — concepts", "DERIVED",
            f"<p>{co['n_concepts']:,} concepts over {co['n_paths']:,} paths · "
            f"co-occurrence entries {co['cooccurrence_entries']:,}.</p>" + ce_line
            + "<h3 class='caps fine'>Most widespread concepts (paths containing)</h3>"
            + bars(co["top"], None)))
    em = M.get("emeta")
    if em:
        body.append(sec("L2 — chunks & embeddings", "FACT",
            f"<p class='mono fine'>{em.get('n_chunks',0):,} chunks · model {ESC(str(em.get('backend',{}).get('name')))} · "
            f"d={em.get('dimension')} · normalized={em.get('normalized')} · dtype {ESC(str(em.get('vector_dtype')))}</p>"))

    dc = M.get("decomp")
    if dc:
        body.append(sec("The decomposition", "decomposer · interpretive fields confidence-tagged",
            f"<div class='grid2'><div>{svg_waffle(dc)}<p class='finer'>240-cell waffle: one cell per part, tone = confidence.</p></div>"
            f"<div><table><tr><th>kind</th><th class='num'>parts</th></tr>"
            + "".join(f"<tr><td class='mono'>{ESC(str(k))}</td><td class='num'>{v}</td></tr>" for k, v in dc["kinds"])
            + "</table>"
            f"<p class='fine'>{dc['n_parts']} parts · confidence "
            + " / ".join(f"{k} {v}" for k, v in dc["conf"])
            + f"<br/>{dc['relationships']} relationships · {dc['gates']:,} quality gates · "
            f"cycles: module {dc['cycles']['module']}, file {dc['cycles']['file']} · order groups {dc['build_groups']}</p>"
            f"<p class='serifit fine'>purpose ({ESC(str(dc['purpose_conf']))}-confidence): “{ESC(str(dc['purpose'])[:180])}”</p></div></div>"))
    else:
        body.append(sec("The decomposition", "absent", "<p class='absent'>decomposition YAML not provided.</p>"))

    bd = M.get("build")
    if bd:
        sk = "".join(f"<li><span class='mono'>{ESC(s['phase'])}</span> — {ESC(s['reason'])}</li>" for s in bd["skipped"])
        vio = " · ".join(f"<span class='unv'>{ESC(str(k))} ×{v}</span>" for k, v in bd["violations"])
        asm = "".join(f"<li>{ESC(a)}</li>" for a in bd["assumptions"])
        body.append(sec("The reconstruction", "recomposer · order FACT · rationale confidence-tagged",
            svg_barcode(bd)
            + f"<p class='fine'>{bd['n_steps']} ordered steps · confidence "
            + " / ".join(f"{k} {v}" for k, v in bd["conf"])
            + f" · architecture style: <span class='mono'>{ESC(str(bd['style']))}</span> ({ESC(str(bd['style_conf']))}).</p>"
            + (f"<p class='fine'>known violations flagged “do not replicate blindly”: {vio}</p>" if bd["violations"] else "")
            + (f"<details><summary>{len(bd['skipped'])} phases skipped with stated reasons</summary><ul class='fine'>{sk}</ul></details>" if bd["skipped"] else "")
            + (f"<details><summary>{len(bd['assumptions'])} open assumptions</summary><ul class='fine'>{asm}</ul></details>" if bd["assumptions"] else "")))
    else:
        body.append(sec("The reconstruction", "absent", "<p class='absent'>build plan YAML not provided.</p>"))

    ab = M.get("abox")
    if ab:
        risks = "".join(f"<div class='risk'><b>{ESC(r['id'])}</b><br/><span class='fine'>{ESC(r['label'][:260])}</span></div>"
                        for r in ab["risks"] if not r["id"].startswith("Overlay"))
        dimtable = "<details><summary>all 24 dimension rows</summary><table><tr><th>dimension</th><th>value</th><th>confidence</th></tr>" + \
            "".join(f"<tr><td class='mono'>{ESC(d.get('dim',''))}</td>"
                    f"<td class='mono'>{ESC(d.get('dominant') or '/'.join(d.get('values',[])) or '—')}</td>"
                    f"<td>{ESC(d.get('conf','?'))}{' · <span class=unv>RISK</span>' if 'risk' in d else ''}</td></tr>"
                    for d in ab["dims"]) + "</table></details>"
        body.append(sec("arc4d3 — twenty-four dimensions", "LLM-classified · UNVERIFIED until SHACL exit 0",
            f"<div class='grid2'><div>{svg_wheel(ab)}</div><div>{risks}"
            f"<p class='serifit fine'>classified by {ESC(ab['creator'][:90])} — the artifact itself declares its output unverified until validated.</p>{dimtable}</div></div>"))
    else:
        body.append(sec("arc4d3 dimensions", "absent", "<p class='absent'>ABox not provided.</p>"))

    ri = M.get("rust")
    body.append(sec("Rust items", "FACT",
        f"<p class='fine'>{ri:,} records in rust_items.jsonl" + (" — not applicable to this repository." if ri == 0 else ".") + "</p>"
        if ri is not None else "<p class='absent'>rust_items.jsonl not present.</p>"))

    body.append(f"""<footer>
<b class='caps'>Legend</b> — <span class='fact'>FACT</span> measured from artifacts ·
<span class='derived'>DERIVED</span> computed projection/clustering/aesthetic ·
<span class='unv'>UNVERIFIED</span> LLM-authored, pending validation.<br/>
Report generated by cbm_report.py · design language “Measured Ink” · references: Beck 1933; Nöllenburg &amp; Wolff, IEEE TVCG 2011;
Wu et al., CGF 2020; Wettel &amp; Lanza, CodeCity ICSE'08; CodeCharta; Kuhn, Software Cartography.
No information herein should be taken for granted; statements without a verifiable basis may be invalid.
</footer>""")
    doc = f"<!doctype html><meta charset='utf-8'><title>{ESC(M['repo'])} — complete report</title><style>{CSS}</style><div class='page'>{''.join(body)}</div>"
    open(out + ".html", "w").write(doc)
    log("wrote", out + ".html", f"({len(doc)/1024:.0f} KB)")

def emit_md(M, out):
    man = M["manifest"]; G = M["graph"]
    hv = M["hash_rows"]; ok = sum(1 for r in hv if r["ok"]); tot = sum(1 for r in hv if r["ok"] is not None)
    L = []
    L.append(f"""---
disclaimer: >
  No information in this document should be taken for granted. Any statement
  or premise not backed by a real logical definition or a verifiable reference
  may be invalid, erroneous, or a hallucination. FACT = measured from the
  artifacts; DERIVED = computed projection/clustering; UNVERIFIED = LLM-authored,
  pending validation.
repo: {M['repo']}
commit: {M['commit']}
tool_version: "{man.get('tool_version','?')}"
generated_by: cbm_report.py
---

# {M['repo']} — complete structural report

**Verification (FACT).** Input hashes independently recomputed: **{ok}/{tot} match**.
SHACL self-check (manifest): {man.get('shacl_self_check',{}).get('conforms')}."""
    + (f" Independent re-validation: **{'conforms' if M['shacl_independent'][0] else 'VIOLATIONS'}** ({M['shacl_independent'][1]:.0f}s)." if M.get('shacl_independent') else ""))
    L.append(evidence_banner_md())
    caveats = M.get("caveats") or mechanical_caveats(man, M.get("found"))
    if caveats:
        L.append("\n**Data caveats (FACT).** Read every figure through these:\n"
                 + "\n".join(f"- **{c['severity']}** — {c['text']}"
                             for c in caveats))
    else:
        L.append("\n**Data caveats (FACT).** None: the manifest crossed no "
                 "disclosure threshold.")
    c = man.get("counts", {})
    L.append(f"\n**Headline counts.** files {c.get('files',0):,} · triples {G['triples']:,} · "
             f"import edges {G['edges']:,} · parts {(M.get('decomp') or {}).get('n_parts',0)} · "
             f"rebuild steps {(M.get('build') or {}).get('n_steps',0)} · L4 receipts {(M.get('enrich') or {}).get('n',0)}")
    L.append("\n## Hash table\n\n| artifact | claimed | recomputed |\n|---|---|---|")
    for r in hv:
        L.append(f"| `{r['artifact']}` | `{r['claimed']}…` | {'match' if r['ok'] else ('**MISMATCH**' if r['ok'] is False else 'n/a')} |")
    L.append("\n## Chokepoints (import in-degree, FACT)\n\n| in | file | out |\n|---:|---|---:|")
    for r in G["chokepoints"][:8]:
        L.append(f"| {r['in']} | `{r['file']}` | {r['out']} |")
    te, tev = G["tests_edges"], G["test_evidence"]
    L.append(f"\n## Test evidence (FACT)\n\nShipped `cbm:tests`: **{te['n']}** edges. "
             f"Typed derivation (test→source imports): **{tev['typed_import_edges']}** edges.")
    if te["top_objects"] and te["n"] and te["top_objects"][0][1] / te["n"] > 0.5:
        f0, c0 = te["top_objects"][0]
        L.append(f"Precision finding: {c0}/{te['n']} heuristic edges target one file (`{f0}`).")
    en = M.get("enrich")
    if en:
        pc = 100 * en["provenance_complete"] / max(1, en["n"])
        L.append(f"\n## L4 receipts\n\n{en['n']:,} records · provenance complete **{pc:.1f}%** · "
                 f"models: {', '.join(f'`{k}`×{v}' for k,v in en['models'])}"
                 + (f" · {en['near_4000_cap']} at 4,000-char cap (known gap)" if en['near_4000_cap'] else ""))
        for r in G["receipts"][:1]:
            L.append(f"\n> “{r['summary'][:220]}”\n> — `{r['file']}` · {r['model']} · prompt `{r['prompt_sha'][:16]}…`")
    ce, em2 = M.get("concepts_emb"), M.get("emeta")
    if ce or em2:
        parts = []
        if em2: parts.append(f"L2 chunks **{em2.get('n_chunks',0):,}** · embed dim {em2.get('dimension')} "
                             f"(`{em2.get('backend',{}).get('name')}`)")
        if ce: parts.append(f"L3 concept vectors **{ce['n_vectors']:,}** · dim {ce['dim']}")
        L.append("\n## Semantic layers (FACT)\n\n" + " · ".join(parts))
    om = M.get("ontomap")
    if om and om["rels"]:
        L.append("\n## Vocabulary alignment (FACT)\n\ncbm terms formally aligned to external "
                 "ontologies (" + ", ".join(f"`{h}`" for h in om["hosts"])
                 + "):\n\n| cbm term | relation | external term |\n|---|---|---|")
        for r in om["rels"]:
            L.append(f"| `cbm:{r['term']}` | {r['rel']} | `{r['target']}` |")
    for key, title in (("decomp", "Decomposition"), ("build", "Reconstruction"), ("abox", "arc4d3 dimensions")):
        v = M.get(key)
        if not v:
            L.append(f"\n## {title}\n\n_Not provided for this run._"); continue
        if key == "decomp":
            L.append(f"\n## {title}\n\n{v['n_parts']} parts · confidence "
                     + " / ".join(f"{k} {n}" for k, n in v["conf"])
                     + f" · {v['relationships']} relationships · {v['gates']:,} quality gates · "
                       f"cycles module {v['cycles']['module']} / file {v['cycles']['file']}")
        elif key == "build":
            L.append(f"\n## {title}\n\n{v['n_steps']} steps → {v['total_creates']:,} files · "
                     + " / ".join(f"{k} {n}" for k, n in v["conf"])
                     + f" · {len(v['skipped'])} phases skipped with reasons · violations: "
                     + ", ".join(f"{k}×{n}" for k, n in v["violations"]))
        else:
            cf = Counter(d.get("conf") for d in v["dims"])
            L.append(f"\n## {title}\n\n{len(v['dims'])} dimensions (UNVERIFIED until SHACL): "
                     + " / ".join(f"{k} {n}" for k, n in cf.most_common())
                     + f" · risks: " + ", ".join(r["id"] for r in v["risks"] if not r["id"].startswith("Overlay")))
    L.append("\n---\n_Generated by cbm_report.py — the report re-verifies its inputs on every run._\n")
    open(out + ".md", "w").write("\n".join(L))
    log("wrote", out + ".md")

# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--abox"); ap.add_argument("--decomposition"); ap.add_argument("--buildplan")
    ap.add_argument("--out", default="cbm_report")
    ap.add_argument("--formats", default="html,md,json")
    ap.add_argument("--cache-dir")
    ap.add_argument("--validate-shacl", action="store_true",
                    help="independently re-validate inventory against bundled shapes (pyshacl)")
    ap.add_argument("--skip-embeddings", action="store_true", help="skip t-SNE district map")
    a = ap.parse_args(argv)

    found = discover(a.bundle, a)
    if "run_manifest.json" not in found:
        sys.exit("run_manifest.json not found in bundle")
    man = json.load(open(found["run_manifest.json"]))
    M = {"manifest": man,
         "repo": man.get("repo_name") or os.path.basename(a.bundle.rstrip("/")),
         "commit": man.get("commit_sha", ""),
         "found": {k: bool(v) for k, v in found.items()}}
    M["caveats"] = mechanical_caveats(man, found)
    M["hash_rows"] = verify_hashes(a.bundle, man, found)
    if found.get("blobs_dir"):
        M["blobs"] = {"on_disk": len(os.listdir(found["blobs_dir"])),
                      "claimed": man.get("counts", {}).get("unique_blobs_written", 0)}
    M["jsonld_bytes"] = (os.path.getsize(found["inventory.jsonld"])
                         if found.get("inventory.jsonld") else 0)
    cache = resolve_cache_dir(a.bundle, a.cache_dir)
    log("cache dir:", cache)
    g = load_graph(found, cache)
    if a.validate_shacl and found.get("shapes.shacl.ttl"):
        import rdflib
        from pyshacl import validate
        t0 = time.time()
        sh = rdflib.Graph(); sh.parse(found["shapes.shacl.ttl"], format="turtle")
        conforms, _, _ = validate(g, shacl_graph=sh, inference="none")
        M["shacl_independent"] = (bool(conforms), time.time() - t0)
        log("independent SHACL:", conforms)
    M["graph"] = graph_analytics(g, man)
    M["assets"], M["_gallery"] = asset_inventory(g, found)
    M["district_xy"] = district_xy(M["graph"]["_district"], a.bundle, cache, a.skip_embeddings)
    M["enrich"] = load_enrichments(found["enrichments.jsonl"]) if found.get("enrichments.jsonl") else None
    M["concepts"] = load_concepts(found["concepts.json"]) if found.get("concepts.json") else None
    M["concepts_emb"] = load_concept_embeddings(found.get("concepts_embeddings.npz"))
    M["ontomap"] = load_ontology_mapping(found.get("ontology-mapping.ttl"))
    M["emeta"] = json.load(open(found["embeddings_meta.json"])) if found.get("embeddings_meta.json") else None
    M["ast"] = load_ast_coverage(found["ast_coverage.json"]) if found.get("ast_coverage.json") else None
    M["rust"] = (sum(1 for l in open(found["rust_items.jsonl"]) if l.strip())
                 if found.get("rust_items.jsonl") else None)
    M["abox"] = load_abox(found.get("abox"))
    M["decomp"] = load_decomp(found.get("decomposition"))
    M["build"] = load_buildplan(found.get("buildplan"))

    fmts = {f.strip() for f in a.formats.split(",")}
    if "html" in fmts: emit_html(M, a.out)
    if "md" in fmts: emit_md(M, a.out)
    if "json" in fmts:
        slim = {k: v for k, v in M.items() if not k.startswith("_")}
        slim["graph"] = {k: v for k, v in M["graph"].items() if not k.startswith("_")}
        slim["district_xy"] = None if M["district_xy"] is None else len(M["district_xy"])
        json.dump(slim, open(a.out + ".model.json", "w"), indent=1, default=str)
        log("wrote", a.out + ".model.json")
    return 0

if __name__ == "__main__":
    sys.exit(main())

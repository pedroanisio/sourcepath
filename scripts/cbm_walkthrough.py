#!/usr/bin/env python3
"""cbm_walkthrough.py — narrated customer walkthrough for any cbm bundle.

Where cbm_report.py is the one-page *proof* (verify everything, say what each
figure is), this is the *demo*: it freezes one run of the queries the cbm MCP
server and UI answer live, into a single self-contained page that tells the
story of a repository the way you would to a prospect —

  1. Orientation        — what this bundle is (counts, languages, verification).
  2. The keystone        — the most-depended-on source file, fully materialized:
                           its symbols (per-function/class L2 chunks with exact
                           spans), both import directions, and its L4 summary
                           with model/prompt provenance.
  3. Blast radius        — the transitive set that changes if you touch it.
  4. A concept, exploded — one domain concept with its cooccurrence neighborhood
                           and the files that lexicalize it.
  5. Ask it a question   — a plain-English query answered by sbert over the
                           embedding space; lexical fallback on hash bundles.

Nothing is repo-specific — point --bundle at any generated cbm bundle. Every
panel names the MCP tool that answers it live, so the page reads as a frozen
transcript of the real product surface, not a bespoke render.

    python scripts/cbm.py walkthrough --bundle _tmp/fastapi \\
        --query "validate request body against a schema"

ARCHITECTURAL REQUIREMENT (PALS's LAW): the L4 summary shown here is LLM-authored
and displayed with its provenance, never as verified fact. Every other panel is
mechanically derived from the graph/embeddings and is measured, not asserted.
"""
import argparse, json, os, re, sys, urllib.parse
from collections import Counter, defaultdict

from codebase_mapper.shared_kernel.settings import default_report_path, load_env

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cbm_report as R  # reuse palette, CSS, banner, load_graph, cache, bars

ESC = R.ESC
CBM, C2, C3, C4 = R.CBM, R.C2, R.C3, R.C4


def log(*a): print("[cbm-walkthrough]", *a, file=sys.stderr)


def default_out(repo, when=None):
    """Standardized default output stem: <reports_dir>/<repo>__walkthrough__<UTC-ts>."""
    return str(default_report_path(repo, "walkthrough", when=when))


# ----------------------------------------------------------------------------
# chunk-IRI parsing (deterministic cbm chunk id → symbol/kind/lines).
# Format owned by plugins/chunks_embeddings/embedder._chunk_id;
# tests/test_cbm_walkthrough.py couples this parser to those helpers.
_CHUNK_RE = re.compile(r"^(?P<kind>[^:]+):(?P<symbol>.+):L(?P<b>\d+)-L(?P<e>\d+):b\d+-\d+$")

def parse_chunk(uri):
    """`#chunk/<path>#<kind>:<symbol>:L<b>-L<e>:b<..>` → dict, or None."""
    dec = urllib.parse.unquote(str(uri))
    if "#chunk/" not in dec:
        return None
    tail = dec.split("#chunk/", 1)[1]
    if "#" not in tail:
        return None
    path, rest = tail.split("#", 1)
    m = _CHUNK_RE.match(rest)
    if not m:
        return {"path": path, "symbol": rest, "kind": "chunk", "b": 0, "e": 0}
    return {"path": path, "symbol": m.group("symbol"), "kind": m.group("kind"),
            "b": int(m.group("b")), "e": int(m.group("e"))}


def pkg_name(u):
    """`#pkg/<safe-name>` → package name (inverse of the emitter's quoting)."""
    s = urllib.parse.unquote(str(u))
    return s.split("#pkg/", 1)[-1]


# ----------------------------------------------------------------------------
# analysis (the same questions the MCP tools answer)
def analyze(g):
    import rdflib
    U = rdflib.URIRef
    ftype = {s: str(o).split("#")[-1] for s, o in g.subject_objects(U(CBM + "type"))}
    src = {s for s, t in ftype.items() if t == "source_code"}
    edges = list(g.subject_objects(U(CBM + "imports")))
    indeg, outdeg = Counter(), Counter()
    adj_out, adj_in = defaultdict(set), defaultdict(set)
    for s, o in edges:
        indeg[o] += 1; outdeg[s] += 1
        adj_out[s].add(o); adj_in[o].add(s)
    # chunks by file (L2) + embedding-row inverse (for semantic search)
    chunks_by_file = defaultdict(list)
    row_to_uri = {}
    for s, o in g.subject_objects(U(C2 + "inFile")):
        meta = parse_chunk(s)
        if meta:
            meta["uri"] = str(s)
            chunks_by_file[o].append(meta)
    for s, o in g.subject_objects(U(C2 + "embeddingRow")):
        row_to_uri[int(o)] = str(s)
    # L4 file summaries (in-graph, with provenance) keyed by file subject
    summ = {s: str(o) for s, o in g.subject_objects(U(C4 + "fileSummary"))}
    model = {s: str(o) for s, o in g.subject_objects(U(C4 + "fileSummaryModel"))}
    psha = {s: str(o) for s, o in g.subject_objects(U(C4 + "fileSummaryPromptSha"))}
    gat = {s: str(o) for s, o in g.subject_objects(U(C4 + "fileSummaryGeneratedAt"))}
    ext = Counter()
    for s, o in g.subject_objects(U(CBM + "importsExternal")):
        ext[pkg_name(o)] += 1
    return {"ftype": ftype, "src": src, "indeg": indeg, "outdeg": outdeg,
            "adj_out": adj_out, "adj_in": adj_in, "chunks_by_file": chunks_by_file,
            "row_to_uri": row_to_uri, "summ": summ, "model": model, "psha": psha,
            "gat": gat, "n_edges": len(edges), "ext": ext}


def pick_keystone(A, focus):
    if focus:
        for f in A["src"]:
            if R.name_of(f) == focus:
                return f
        log(f"--focus {focus} not found as source_code; falling back to auto")
    # A re-export hub (__init__.py) or a one-line shim tops import in-degree but
    # makes a hollow demo. Prefer the most-imported file that carries real logic:
    # rank by in-degree among files with >= 3 own symbols, excluding package
    # __init__ shims. Relax the filter only if nothing qualifies.
    def n_syms(f):
        return sum(1 for m in A["chunks_by_file"].get(f, []) if m["kind"] != "file")
    substantive = [f for f in A["src"]
                   if R.name_of(f).rsplit("/", 1)[-1] != "__init__.py" and n_syms(f) >= 3]
    pool = substantive or list(A["src"])
    # Keystone = both central AND rich: import in-degree x own-symbol count.
    # A re-export hub (huge in-degree, zero symbols) scores 0; a rich-but-
    # peripheral file (many symbols, low in-degree) scores low; the genuinely
    # load-bearing engine (imported widely AND full of logic) wins.
    def score(f):
        return A["indeg"][f] * n_syms(f)
    ranked = sorted(pool, key=lambda f: (-score(f), R.name_of(f)))
    return ranked[0] if ranked else None


def blast_radius(A, keystone, depth=3, cap=400):
    """BFS both directions over cbm:imports — the file_impact answer."""
    def bfs(adj):
        seen, frontier = set(), {keystone}
        for _ in range(depth):
            nxt = set()
            for n in frontier:
                for m in adj.get(n, ()):
                    if m not in seen and m != keystone:
                        seen.add(m); nxt.add(m)
                        if len(seen) >= cap: return seen
            frontier = nxt
            if not frontier: break
        return seen
    deps = bfs(A["adj_out"]); dependents = bfs(A["adj_in"])
    return deps, dependents


# ----------------------------------------------------------------------------
# concept neighborhood (from concepts.json — cheap and exact)
def concept_story(found, keystone_path):
    if not found.get("concepts.json"):
        return None
    c = json.load(open(found["concepts.json"]))
    per_path = c.get("per_path_concepts") or {}
    co = c.get("cooccurrence") or {}
    freq = Counter()
    for _, cl in per_path.items():
        for k in (cl or []): freq[k] += 1
    if not freq:
        return None
    # Prefer a concept the keystone actually lexicalizes; else the most frequent.
    ks = set(per_path.get(keystone_path, []) or [])
    pool = [(k, n) for k, n in freq.most_common() if k in ks] or freq.most_common()
    # skip trivial tokens; prefer a well-connected, multi-file concept
    pick = next((k for k, n in pool if len(k) > 3 and n >= 3), pool[0][0])
    # cooccurrence ships as a flat list of [a, b, weight] triples
    neigh_map = Counter()
    for row in co:
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            continue
        a, b, w = row
        if a == pick: neigh_map[b] += w
        elif b == pick: neigh_map[a] += w
    neigh = neigh_map.most_common(12)
    files = sorted(p for p, cl in per_path.items() if pick in (cl or []))
    return {"name": pick, "freq": freq[pick], "n_files": len(files),
            "neighbors": neigh, "files": files[:8], "total_concepts": len(freq)}


# ----------------------------------------------------------------------------
# semantic search over embeddings.npz — the "ask it a question" panel
def _encode_query_ollama(model, query):
    """Embed the query through the Ollama model the bundle was built with.
    Returns a float32 vector, or None so the caller answers lexically."""
    import os

    import httpx
    import numpy as np

    host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    r = httpx.post(f"{host}/api/embed",
                   json={"model": model, "input": [query]}, timeout=30.0)
    r.raise_for_status()
    rows = r.json().get("embeddings") or []
    if len(rows) != 1:
        return None
    v = np.asarray(rows[0], dtype="float32")
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


def semantic(found, A, query, k=8):
    if not (found.get("embeddings.npz") and query):
        return None
    meta = json.load(open(found["embeddings_meta.json"])) if found.get("embeddings_meta.json") else {}
    name = meta.get("backend", {}).get("name") or "sentence-transformers/all-MiniLM-L6-v2"
    # A hash backend's vectors carry no semantics — encoding the query
    # against them would rank noise. Two backend families carry real
    # semantics: sentence-transformer model ids ("org/model") and Ollama
    # tags ("ollama:<model>"). Anything else answers lexically.
    is_ollama = name.startswith("ollama:")
    if "/" not in name and not is_ollama:
        log(f"backend {name!r} carries no semantics — lexical panel")
        return _lexical(A, query, k)
    try:
        import numpy as np
    except Exception as e:
        log("numpy unavailable — semantic panel falls back to lexical:", e)
        return _lexical(A, query, k)
    try:
        M = np.load(found["embeddings.npz"])["vectors"].astype("float32")
        if is_ollama:
            q = _encode_query_ollama(name.split(":", 1)[1], query)
            if q is None:
                log("ollama returned no query embedding — lexical fallback")
                return _lexical(A, query, k)
            if q.shape[0] != M.shape[1]:
                log(f"ollama query dim {q.shape[0]} != bundle dim {M.shape[1]}"
                    " — lexical fallback")
                return _lexical(A, query, k)
        else:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(name)
            q = model.encode([query], normalize_embeddings=True).astype("float32")[0]
    except Exception as e:
        log("query encode failed — lexical fallback:", e); return _lexical(A, query, k)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9) if not meta.get("normalized") else M
    scores = Mn @ q
    top = np.argsort(-scores)[:k]
    hits = []
    for r in top:
        uri = A["row_to_uri"].get(int(r))
        if not uri: continue
        m = parse_chunk(uri) or {}
        hits.append({"score": float(scores[r]), "path": m.get("path", ""),
                     "symbol": m.get("symbol", ""), "kind": m.get("kind", ""),
                     "b": m.get("b", 0), "e": m.get("e", 0)})
    return {"mode": "semantic", "model": name, "query": query, "hits": hits}


def _lexical(A, query, k):
    toks = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    scored = []
    for f, chs in A["chunks_by_file"].items():
        for m in chs:
            hay = (m["symbol"] + " " + m["path"]).lower()
            s = sum(1 for t in toks if t in hay)
            if s: scored.append((s, m))
    scored.sort(key=lambda x: -x[0])
    return {"mode": "lexical", "model": "symbol/path match", "query": query,
            "hits": [{"score": s, "path": m["path"], "symbol": m["symbol"],
                      "kind": m["kind"], "b": m["b"], "e": m["e"]}
                     for s, m in scored[:k]]}


# ----------------------------------------------------------------------------
# render
def shacl_word(shacl):
    if shacl.get("conforms") is True:
        return "conforms"
    return "skipped" if shacl.get("skipped") else "FAILED"


def scene(n, title, tool, body):
    return (f"<section class='scene'><hr/>"
            f"<div class='scenehead'><span class='scenen disp'>{n}</span>"
            f"<h2 class='caps'>{ESC(title)}</h2>"
            f"<span class='tool mono'>MCP · {ESC(tool)}</span></div>{body}</section>")


def emit(bundle, man, A, keystone, deps, dependents, ks_chunks, concept, sem, found, out):
    repo = man.get("repo_name") or os.path.basename(bundle.rstrip("/"))
    ksp = R.name_of(keystone)
    c = man.get("counts", {})
    shacl = shacl_word(man.get("shacl_self_check", {}))
    langs = sorted((man.get("files_by_language") or {}).items(), key=lambda x: -x[1])[:6]

    head = f"""
<header>
 <div class='kicker caps'>codebase-mapper · guided walkthrough</div>
 <h1 class='disp'>{ESC(repo)}</h1>
 <div class='meta mono'>a frozen transcript of the read-only MCP surface — every scene below is one live tool call.
   commit {ESC(str(man.get('commit_sha',''))[:12])} · {c.get('files',0):,} files · SHACL {shacl}</div>
</header>{R.evidence_banner_html()}"""

    # scene 1 — orientation
    langbar = R.bars(langs, None)
    s1 = scene("1", "Orientation — what am I looking at", "orient_bundle · bundle_summary",
        f"<div class='grid2'><div><p>{ESC(repo)} is <b>{c.get('files',0):,}</b> files "
        f"({A['n_edges']:,} internal import edges, {len(A['ext']):,} external packages). "
        f"The bundle re-verifies: SHACL self-check <b>{shacl}</b>, "
        f"content-addressed blobs, provenance on every LLM line.</p>"
        f"<p class='fine'>This page never asks you to trust it — the report companion recomputes every hash. "
        f"Here we just <i>explore</i>.</p></div><div><h3 class='caps fine'>By language</h3>{langbar}</div></div>")

    # scene 2 — the keystone, fully materialized
    ks_summary = A["summ"].get(keystone, "")
    prov = (f"<div class='mono finer'>model {ESC(A['model'].get(keystone,'?'))} · "
            f"prompt {ESC(A['psha'].get(keystone,'')[:16])}… · {ESC(A['gat'].get(keystone,''))}</div>") if ks_summary else ""
    symrows = "".join(
        f"<tr><td class='mono'>{ESC(m['symbol'])}</td><td class='fine'>{ESC(m['kind'])}</td>"
        f"<td class='num'>L{m['b']}–{m['e']}</td></tr>"
        for m in sorted(ks_chunks, key=lambda m: m["b"])
        if m["kind"] != "file")
    imps_out = sorted(R.name_of(o) for o in A["adj_out"].get(keystone, ()))
    imps_in = sorted(R.name_of(o) for o in A["adj_in"].get(keystone, ()))
    real_syms = [m for m in ks_chunks if m["kind"] != "file"]
    s2 = scene("2", "The keystone — one file, every detail", "file_detail",
        f"<p><span class='mono'>{ESC(ksp)}</span> is the most-depended-on source file "
        f"(<b>{A['indeg'][keystone]}</b> importers). The bundle holds it at symbol resolution — "
        f"<b>{len(real_syms)}</b> functions/classes, each an addressable chunk with an exact span and a content hash:</p>"
        + (f"<div class='receipt'>“{ESC(ks_summary[:240])}”{prov}<div class='finer unv'>L4 · LLM-authored, shown with provenance — advisory, not fact.</div></div>" if ks_summary else "")
        + f"<div class='grid2'><div><h3 class='caps fine'>Symbols ({len(real_syms)})</h3>"
        f"<div class='scroll'><table><tr><th>symbol</th><th>kind</th><th class='num'>lines</th></tr>{symrows}</table></div></div>"
        f"<div><h3 class='caps fine'>Imports out ({len(imps_out)})</h3>"
        f"<p class='mono fine'>{ESC(', '.join(m.split('/')[-1] for m in imps_out)) or '—'}</p>"
        f"<h3 class='caps fine'>Imported by ({len(imps_in)})</h3>"
        f"<p class='mono fine'>{ESC(', '.join(m.split('/')[-1] for m in imps_in)) or '—'}</p></div></div>")

    # scene 3 — blast radius
    def fmt(paths, limit=28):
        ps = sorted(paths, key=lambda f: R.name_of(f))
        shown = ", ".join(ESC(R.name_of(f).split("/")[-1]) for f in ps[:limit])
        more = f" <span class='pale'>+{len(ps)-limit} more</span>" if len(ps) > limit else ""
        return shown + more
    s3 = scene("3", "Blast radius — what breaks if you touch it", "file_impact (depth 3)",
        f"<div class='counters'><div><b>{len(deps)}</b><span class='caps'>it depends on</span></div>"
        f"<div><b>{len(dependents)}</b><span class='caps'>depend on it</span></div>"
        f"<div><b>{len(deps)+len(dependents)}</b><span class='caps'>total impact set</span></div></div>"
        f"<p class='fine'><b>Dependents (change-risk):</b> {fmt(dependents)}</p>"
        f"<p class='fine'><b>Dependencies (its surface):</b> {fmt(deps)}</p>"
        f"<p class='finer'>This is the answer a reviewer wants before approving a change to "
        f"<span class='mono'>{ESC(ksp.split('/')[-1])}</span> — computed from the graph, not guessed.</p>")

    # scene 4 — a concept exploded
    if concept:
        nb = R.bars(concept["neighbors"], None) if concept["neighbors"] else "<p class='fine'>(no cooccurrence recorded)</p>"
        s4 = scene("4", "A concept, exploded", "concept_detail · concept_neighborhood",
            f"<p>Beyond files and imports, the bundle carries a concept graph "
            f"({concept['total_concepts']:,} concepts). Take <b>“{ESC(concept['name'])}”</b> — "
            f"lexicalized across <b>{concept['n_files']:,}</b> files. "
            f"Its meaning is the company it keeps:</p>"
            f"<div class='grid2'><div><h3 class='caps fine'>Cooccurring concepts</h3>{nb}</div>"
            f"<div><h3 class='caps fine'>Where it lives</h3><p class='mono fine'>"
            + "<br/>".join(ESC(f) for f in concept["files"]) + "</p></div></div>")
    else:
        s4 = scene("4", "A concept, exploded", "concept_detail",
                   "<p class='absent'>No concept layer in this bundle.</p>")

    # scene 5 — ask it a question
    if sem and sem["hits"]:
        def hit_sym(h):
            s = h["symbol"]
            return h["path"].split("/")[-1] if s in ("<file>", "") else s
        hitrows = "".join(
            f"<tr><td class='num'>{h['score']:.2f}</td>"
            f"<td class='mono'>{ESC(hit_sym(h))}</td>"
            f"<td class='fine'>{ESC(h['path'])}"
            + (f" · L{h['b']}–{h['e']}" if h['b'] else "") + "</td></tr>"
            for h in sem["hits"])
        s5 = scene("5", "Ask it a question — plain English in, code out", "semantic_neighbors",
            f"<p>The question — typed as prose, not keywords:</p>"
            f"<p class='q serifit'>“{ESC(sem['query'])}”</p>"
            f"<p class='fine'>Answered by <span class='mono'>{ESC(sem['model'])}</span> "
            f"(mode: <b>{sem['mode']}</b>) over the bundle's chunk index. "
            f"It surfaces the exact material: the docs that explain it and the functions that implement it:</p>"
            f"<table><tr><th class='num'>score</th><th>symbol</th><th>where</th></tr>{hitrows}</table>"
            f"<p class='finer'>Swap the query for anything a new hire would ask on day one — "
            f"the same index answers it. This is the surface an AI agent consumes over MCP.</p>")
    else:
        s5 = scene("5", "Ask it a question", "semantic_neighbors",
                   "<p class='absent'>No embeddings in this bundle — semantic search unavailable.</p>")

    foot = (f"<footer><b class='caps'>What this was</b> — five live queries against one bundle, frozen to a page. "
            f"Files → symbols → impact → concepts → semantic search, each a call the cbm MCP server and UI answer on demand. "
            f"Mechanical panels are measured; the single L4 quote is LLM-authored and shown with provenance, never as fact.<br/>"
            f"Generated by cbm_walkthrough.py · reads any generated cbm bundle.</footer>")

    css_extra = """
.scene{margin:8px 0} .scenehead{display:flex;align-items:baseline;gap:14px;margin:18px 0 8px}
.scenen{font-size:30px;color:var(--verm);line-height:1;min-width:34px}
.scenehead h2{margin:0;font-size:14px} .tool{margin-left:auto;color:var(--pale);font-size:10.5px;letter-spacing:.06em}
.q{font-size:20px;color:var(--ink);border-left:3px solid var(--verm);padding:2px 0 2px 14px;margin:8px 0}
.scroll{max-height:340px;overflow:auto;border:1px solid var(--faint)} .scroll table{margin:0}
.scroll th{position:sticky;top:0;background:var(--paper)} .pale{color:var(--pale)}
.banner{border:1px solid var(--pale);padding:8px 12px;margin:14px 0}
header h1{font-size:46px}
"""
    doc = (f"<!doctype html><meta charset='utf-8'><title>{ESC(repo)} — cbm walkthrough</title>"
           f"<style>{R.CSS}{css_extra}</style><div class='page'>"
           + head + s1 + s2 + s3 + s4 + s5 + foot + "</div>")
    open(out + ".html", "w").write(doc)
    log("wrote", out + ".html", f"({len(doc)/1024:.0f} KB)")


# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--focus", help="force the keystone file (bundle-relative path)")
    ap.add_argument("--query", default="where is request validation handled",
                    help="the natural-language question for scene 5")
    ap.add_argument("--out", default=None,
                    help="output stem (default: $CBM_REPORTS_DIR/<repo>__walkthrough__<timestamp>)")
    ap.add_argument("--cache-dir")
    a = ap.parse_args(argv)
    load_env()  # .env (repo-scoped) fills gaps; real environment always wins

    man_path = os.path.join(a.bundle, "run_manifest.json")
    if not os.path.exists(man_path):
        sys.exit("run_manifest.json not found in bundle")
    man = json.load(open(man_path))
    found = {k: os.path.join(a.bundle, k) for k in
             ("inventory.ttl", "embeddings.npz", "embeddings_meta.json", "concepts.json")
             if os.path.exists(os.path.join(a.bundle, k))}
    if "inventory.ttl" not in found:
        sys.exit("inventory.ttl not found in bundle")
    if a.out is None:
        repo = man.get("repo_name") or os.path.basename(a.bundle.rstrip("/"))
        a.out = default_out(repo)
        log("out:", a.out)

    cache = R.resolve_cache_dir(a.bundle, a.cache_dir)
    g = R.load_graph(found, cache)
    A = analyze(g)
    keystone = pick_keystone(A, a.focus)
    if keystone is None:
        sys.exit("no source_code files in bundle")
    ksp = R.name_of(keystone)
    log("keystone:", ksp)
    deps, dependents = blast_radius(A, keystone)
    ks_chunks = A["chunks_by_file"].get(keystone, [])
    concept = concept_story(found, ksp)
    sem = semantic(found, A, a.query)
    emit(a.bundle, man, A, keystone, deps, dependents, ks_chunks, concept, sem, found, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

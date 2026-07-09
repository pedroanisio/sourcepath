//! The report itself: seven A4 pages assembled from the chart components.
//! Every figure on these pages is mechanically derived from bundle artifacts;
//! the one LLM-authored artifact (enrichments.jsonl) contributes only metadata
//! counts, and the epistemics page discloses that split (PALS's LAW).

use printpdf::{Mm, PdfDocumentReference, PdfLayerIndex, PdfPageIndex};

use crate::ingest::enrichments::{EnrichmentStats, BUCKET_SECS};
use crate::ingest::manifest::RunManifest;
use crate::ingest::rust_items::RustItemStats;
use crate::pdf::canvas::{Align, Canvas, FontKind, Fonts};
use crate::pdf::charts::*;
use crate::pdf::theme::*;
use crate::stats::InventoryStats;
use crate::util::{bytes_human, compact, epoch_hhmm, group, pct};

pub struct ReportData {
    pub manifest: RunManifest,
    pub inventory: InventoryStats,
    pub enrichments: Option<EnrichmentStats>,
    pub rust_items: Option<RustItemStats>,
    /// "YYYY-MM-DD" the report was rendered.
    pub rendered_on: String,
}

const TOTAL_PAGES: usize = 8;
const PT_TO_MM: f64 = 25.4 / 72.0;

pub fn render(
    doc: &PdfDocumentReference,
    first_page: (PdfPageIndex, PdfLayerIndex),
    fonts: &Fonts,
    data: &ReportData,
) {
    let pages: [(&str, &str, fn(&Canvas, &ReportData)); TOTAL_PAGES] = [
        ("", "", cover),
        ("LANGUAGE LANDSCAPE", "Files and content bytes by language and file type", languages),
        ("EXTRACTION HEALTH", "AST coverage, symbols, imports, and parse quality", extraction),
        ("REPOSITORY STRUCTURE", "Where the bytes live: top-level directories and file sizes", structure),
        ("RUST IN THE TREE", "Mechanically extracted Rust items from the kernel's Rust subsystem", rust_items_page),
        ("CHUNKS & EMBEDDINGS", "L2 chunking and the embedding artifact", chunks_embeddings),
        ("CONCEPTS & LLM ENRICHMENT", "L3 concept graph and L4 enrichment run", concepts_enrichment),
        ("DATA QUALITY & EPISTEMICS", "Mechanical anomaly flags and verification status", quality),
    ];

    for (i, (kicker, subtitle, draw)) in pages.iter().enumerate() {
        let layer = if i == 0 {
            doc.get_page(first_page.0).get_layer(first_page.1)
        } else {
            let (page, layer) = doc.add_page(Mm((PAGE_W * PT_TO_MM) as f32), Mm((PAGE_H * PT_TO_MM) as f32), "content");
            doc.get_page(page).get_layer(layer)
        };
        let c = Canvas::new(layer, fonts);
        c.rect(0.0, 0.0, PAGE_W, PAGE_H, SURFACE);
        if i > 0 {
            frame(&c, data, kicker, subtitle, i + 1);
        }
        draw(&c, data);
    }
}

fn frame(c: &Canvas, data: &ReportData, kicker: &str, subtitle: &str, page_no: usize) {
    c.text(kicker, MARGIN, 58.0, 8.0, FontKind::Bold, INK_MUTED, Align::Left);
    c.text(subtitle, MARGIN, 76.0, 13.0, FontKind::Bold, INK, Align::Left);
    c.hline(MARGIN, PAGE_W - MARGIN, 88.0, GRIDLINE, HAIRLINE);
    footer(c, data, page_no);
}

fn footer(c: &Canvas, data: &ReportData, page_no: usize) {
    let m = &data.manifest;
    c.hline(MARGIN, PAGE_W - MARGIN, PAGE_H - 38.0, GRIDLINE, HAIRLINE);
    c.text(
        &format!(
            "codebase-mapper {} · {} @ {} · cbm-report {} · figures mechanical, L4 text LLM-unverified",
            m.tool_version,
            m.repo_name,
            &m.commit_sha[..10.min(m.commit_sha.len())],
            data.rendered_on
        ),
        MARGIN,
        PAGE_H - 26.0,
        6.3,
        FontKind::Regular,
        INK_MUTED,
        Align::Left,
    );
    c.text(
        &format!("Page {page_no} of {TOTAL_PAGES}"),
        PAGE_W - MARGIN,
        PAGE_H - 26.0,
        6.3,
        FontKind::Regular,
        INK_MUTED,
        Align::Right,
    );
}

fn section(c: &Canvas, y: f64, title: &str) -> f64 {
    c.text(title, MARGIN, y, 9.5, FontKind::Bold, INK, Align::Left);
    y + 14.0
}

/// Sorted (desc) top-n of a (label, count) map with the remainder folded into "other (k)".
fn fold_top_n<'a, I>(map: I, n: usize) -> Vec<(String, u64)>
where
    I: IntoIterator<Item = (&'a String, &'a u64)>,
{
    let mut v: Vec<_> = map.into_iter().map(|(k, &c)| (k.clone(), c)).collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    if v.len() > n {
        let rest: u64 = v[n..].iter().map(|e| e.1).sum();
        let k = v.len() - n;
        v.truncate(n);
        v.push((format!("other ({k})"), rest));
    }
    v
}

fn count_rows(entries: &[(String, u64)], total: u64) -> Vec<HBarRow> {
    entries
        .iter()
        .map(|(label, v)| HBarRow {
            label: label.clone(),
            value: *v as f64,
            value_label: format!("{} · {}", group(*v), pct(*v, total)),
            color: SERIES[0],
        })
        .collect()
}

fn byte_rows(entries: &[(String, u64)], total: u64) -> Vec<HBarRow> {
    entries
        .iter()
        .map(|(label, v)| HBarRow {
            label: label.clone(),
            value: *v as f64 / 1e6, // MB axis
            value_label: format!("{} · {}", bytes_human(*v), pct(*v, total)),
            color: SERIES[0],
        })
        .collect()
}

// ---------------------------------------------------------------- page 1

fn cover(c: &Canvas, data: &ReportData) {
    let m = &data.manifest;
    let inv = &data.inventory;

    c.text("CODEBASE-MAPPER · BUNDLE REPORT", MARGIN, 104.0, 9.0, FontKind::Bold, INK_MUTED, Align::Left);
    c.text(&m.repo_name, MARGIN, 150.0, 34.0, FontKind::Bold, INK, Align::Left);
    c.text(
        "Knowledge-graph bundle: inventory, chunks, embeddings, concepts, and LLM enrichment",
        MARGIN,
        172.0,
        10.5,
        FontKind::Regular,
        INK_SECONDARY,
        Align::Left,
    );
    c.hline(MARGIN, PAGE_W - MARGIN, 192.0, GRIDLINE, HAIRLINE);

    // Provenance block
    let shacl = match &m.shacl_self_check {
        Some(s) if s.conforms => "conforms",
        Some(_) => "DOES NOT CONFORM",
        None => "not recorded",
    };
    let total_bytes: u64 = m.listed_artifacts().iter().map(|a| a.1).sum();
    let kv: [(&str, String); 6] = [
        ("Commit", m.commit_sha.clone()),
        ("Emitted", m.generated_at.clone()),
        ("Tool", format!("codebase-mapper {}", m.tool_version)),
        ("Vocabulary", m.vocabulary_version.clone()),
        ("SHACL self-check", shacl.to_string()),
        ("Listed artifacts", format!("{} on disk", bytes_human(total_bytes))),
    ];
    let mut y = 218.0;
    for (k, v) in &kv {
        c.text(k, MARGIN, y, 8.0, FontKind::Regular, INK_MUTED, Align::Left);
        c.text(v, MARGIN + 110.0, y, 8.0, FontKind::Regular, INK, Align::Left);
        y += 16.5;
    }

    // Headline tiles — 2 rows × 3
    let tiles: [(&str, String, String); 6] = [
        ("Files inventoried", group(m.counts.files), format!("{} source files", group(m.ast_coverage.n_source_files))),
        ("Chunks", group(inv.chunk_count), format!("{} truncated for embedding", group(inv.chunks_truncated))),
        ("Symbols extracted", group(m.ast_coverage.totals.symbols_extracted), format!("{} imports", group(m.ast_coverage.totals.imports_extracted))),
        ("Concepts", group(inv.concept_count), format!("{} co-occurrence pairs", data.manifest.extensions.concepts.as_ref().map(|e| compact(e.n_cooccurrence)).unwrap_or_default()),),
        ("Embedded vectors", m.extensions.embeddings.as_ref().map(|e| group(e.n_chunks)).unwrap_or_default(), m.extensions.embeddings.as_ref().map(|e| format!("{}-dim, normalized", e.dimension)).unwrap_or_default()),
        ("LLM enrichments", m.extensions.l4.as_ref().map(|e| group(e.n_enrichments)).unwrap_or_default(), "unverified LLM output".into()),
    ];
    let tile_w = (CONTENT_W - 2.0 * 10.0) / 3.0;
    let tile_h = 62.0;
    let top = y + 22.0;
    for (i, (label, value, note)) in tiles.iter().enumerate() {
        let tx = MARGIN + (i % 3) as f64 * (tile_w + 10.0);
        let ty = top + (i / 3) as f64 * (tile_h + 12.0);
        stat_tile(c, tx, ty, tile_w, tile_h, label, value, note);
    }

    // Artifact sizes
    let y = top + 2.0 * (tile_h + 12.0) + 34.0;
    let y = section(c, y, "Bundle artifacts by size  (GB)");
    let arts = m.listed_artifacts();
    let rows: Vec<HBarRow> = arts
        .iter()
        .take(8)
        .map(|(name, size)| HBarRow {
            label: name.clone(),
            value: *size as f64 / 1e9,
            value_label: bytes_human(*size),
            color: SERIES[0],
        })
        .collect();
    HBarChart { x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 132.0, rows, row_gap: 8.5 }.draw(c);

    footer(c, data, 1);
}

// ---------------------------------------------------------------- page 2

fn languages(c: &Canvas, data: &ReportData) {
    let m = &data.manifest;
    let inv = &data.inventory;

    let files_total: u64 = m.files_by_language.values().sum();
    let y = section(c, 112.0, "Files by language");
    let entries = fold_top_n(&m.files_by_language, 10);
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&entries, files_total), row_gap: 7.0,
    };
    let h = chart.height();
    chart.draw(c);

    let y = section(c, y + h + 26.0, "Content bytes by language  (MB)");
    let entries = fold_top_n(&inv.bytes_by_language, 10);
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: byte_rows(&entries, inv.file_bytes_total), row_gap: 7.0,
    };
    let h2 = chart.height();
    chart.draw(c);

    let types_total: u64 = m.files_by_type.values().sum();
    let y = section(c, y + h2 + 26.0, "Files by classified type");
    let entries = fold_top_n(&m.files_by_type, 9);
    HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&entries, types_total), row_gap: 7.0,
    }
    .draw(c);
}

// ---------------------------------------------------------------- page 3

fn extraction(c: &Canvas, data: &ReportData) {
    let t = &data.manifest.ast_coverage.totals;
    let n_src = data.manifest.ast_coverage.n_source_files;

    let tiles: [(&str, String, String); 4] = [
        ("AST coverage", pct(t.files_with_ast, n_src), format!("{} of {} source files", group(t.files_with_ast), group(n_src))),
        ("Symbols", compact(t.symbols_extracted), format!("{} total", group(t.symbols_extracted))),
        ("Imports", compact(t.imports_extracted), format!("{} total", group(t.imports_extracted))),
        ("Parse-error files", compact(t.files_with_parse_errors), format!("{} of source files", pct(t.files_with_parse_errors, n_src))),
    ];
    draw_tile_row(c, 108.0, &tiles);

    let by_lang = &data.manifest.ast_coverage.by_language;
    let mut sym: Vec<(String, u64)> = by_lang
        .iter()
        .filter(|(_, v)| v.symbols_extracted > 0)
        .map(|(k, v)| (k.clone(), v.symbols_extracted))
        .collect();
    sym.sort_by(|a, b| b.1.cmp(&a.1));
    let y = section(c, 196.0, "Symbols extracted by language");
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&sym, t.symbols_extracted), row_gap: 7.0,
    };
    let h = chart.height();
    chart.draw(c);

    let mut imp: Vec<(String, u64)> = by_lang
        .iter()
        .filter(|(_, v)| v.imports_extracted > 0)
        .map(|(k, v)| (k.clone(), v.imports_extracted))
        .collect();
    imp.sort_by(|a, b| b.1.cmp(&a.1));
    let y = section(c, y + h + 24.0, "Imports extracted by language");
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&imp, t.imports_extracted), row_gap: 7.0,
    };
    let h2 = chart.height();
    chart.draw(c);

    // Parse quality: only languages where a tree-sitter parser ran.
    let mut rows: Vec<(String, f64, f64)> = by_lang
        .iter()
        .filter(|(_, v)| v.files_with_ast > 200)
        .map(|(k, v)| {
            (
                k.clone(),
                v.files_with_parse_errors as f64 / v.files_with_ast as f64 * 100.0,
                v.silent_zero_symbol_files as f64 / v.files_with_ast as f64 * 100.0,
            )
        })
        .collect();
    rows.sort_by(|a, b| b.1.total_cmp(&a.1));
    let y = section(c, y + h2 + 24.0, "Parse quality by language  (share of files with AST)");
    let chart = GroupedHBarChart {
        x: MARGIN, y: y + 12.0, w: CONTENT_W, label_w: 92.0,
        max_value: rows.iter().map(|r| r.1.max(r.2)).fold(0.0, f64::max),
        rows,
        colors: [STATUS_SERIOUS, STATUS_WARNING],
        value_fmt: |v| if v > 0.0 && v < 0.1 { "<0.1%".into() } else { format!("{v:.1}%") },
    };
    legend(c, MARGIN + 92.0 + 8.0, y + 2.0, &[
        ("files with parse errors", STATUS_SERIOUS),
        ("silent zero-symbol files", STATUS_WARNING),
    ]);
    chart.draw(c);
}

// ---------------------------------------------------------------- page 4

fn structure(c: &Canvas, data: &ReportData) {
    let inv = &data.inventory;
    let m = &data.manifest;

    let n_dirs = inv.files_by_top_dir.len().saturating_sub(1); // minus "(root)"
    let root_files = inv.files_by_top_dir.get("(root)").copied().unwrap_or(0);
    let tiles: [(&str, String, String); 4] = [
        ("Repository content", bytes_human(inv.file_bytes_total), format!("{} files", group(inv.file_count))),
        ("Top-level directories", group(n_dirs as u64), format!("{} files at the root", group(root_files))),
        ("Content blobs stored", group(m.counts.unique_blobs_written), "deduplicated by sha256".into()),
        ("AST summaries", bytes_human(m.counts.ast_summary_total_bytes), "embedded in file nodes".into()),
    ];
    draw_tile_row(c, 108.0, &tiles);

    let y = section(c, 196.0, "Content bytes by top-level directory  (MB)");
    let entries = fold_top_n(&inv.bytes_by_top_dir, 14);
    let shown_files: u64 = entries
        .iter()
        .filter_map(|(label, _)| inv.files_by_top_dir.get(label))
        .sum();
    let rows: Vec<HBarRow> = entries
        .iter()
        .map(|(label, v)| HBarRow {
            label: label.clone(),
            value: *v as f64 / 1e6,
            value_label: format!(
                "{} · {} files · {}",
                bytes_human(*v),
                inv.files_by_top_dir
                    .get(label)
                    .map(|&f| compact(f))
                    // the folded "other (k)" row: everything not shown above
                    .unwrap_or_else(|| compact(inv.file_count - shown_files)),
                pct(*v, inv.file_bytes_total)
            ),
            color: SERIES[0],
        })
        .collect();
    let chart = HBarChart { x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 96.0, rows, row_gap: 7.0 };
    let h = chart.height();
    chart.draw(c);

    let y = section(c, y + h + 28.0, "File size distribution  (files per size bucket)");
    let hist = &inv.file_size_hist;
    let labels: Vec<(usize, String)> = (0..hist.counts.len()).map(|i| (i, hist.label(i))).collect();
    ColumnChart {
        x: MARGIN + 34.0,
        y: y + 10.0,
        w: CONTENT_W - 34.0,
        h: 128.0,
        values: hist.counts.iter().map(|&v| v as f64).collect(),
        color: SERIES[0],
        x_labels: labels,
        y_label: String::new(),
    }
    .draw(c);
}

// ---------------------------------------------------------------- page 5

fn rust_items_page(c: &Canvas, data: &ReportData) {
    let Some(r) = &data.rust_items else {
        c.text(
            "No rust_items.jsonl in this bundle.",
            MARGIN,
            120.0,
            8.5,
            FontKind::Regular,
            INK_SECONDARY,
            Align::Left,
        );
        return;
    };
    let m = &data.manifest;

    let rs_files = m.files_by_language.get("rust").copied().unwrap_or(0);
    let sidecar_files = m.rust_items_sidecar.as_ref().map(|s| s.n_files).unwrap_or(0);
    let tiles: [(&str, String, String); 4] = [
        ("Rust items", group(r.total), "structs, fns, impls, traits…".into()),
        ("Files with items", group(sidecar_files), format!("of {} Rust files", group(rs_files))),
        ("Public items", pct(r.pub_items, r.total), format!("{} pub", group(r.pub_items))),
        ("Async items", group(r.async_items), String::new()),
    ];
    draw_tile_row(c, 108.0, &tiles);

    let y = section(c, 196.0, "Items by kind");
    let entries = fold_top_n(&r.by_kind, 11);
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&entries, r.total), row_gap: 7.0,
    };
    let h = chart.height();
    chart.draw(c);

    let y = section(c, y + h + 26.0, "Items by top-level directory");
    let entries = fold_top_n(&r.by_top_dir, 8);
    HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&entries, r.total), row_gap: 7.0,
    }
    .draw(c);
}

// ---------------------------------------------------------------- page 6

fn chunks_embeddings(c: &Canvas, data: &ReportData) {
    let inv = &data.inventory;

    let avg = if inv.chunk_count > 0 { inv.chunk_bytes_total / inv.chunk_count } else { 0 };
    let tiles: [(&str, String, String); 4] = [
        ("Chunks", group(inv.chunk_count), format!("{} of text", bytes_human(inv.chunk_bytes_total))),
        ("Mean chunk size", bytes_human(avg), format!("{} lines covered", compact(inv.chunk_lines_total))),
        ("Truncated for embedding", group(inv.chunks_truncated), format!("{} of chunks", pct(inv.chunks_truncated, inv.chunk_count))),
        ("Chunk kinds", group(inv.chunks_by_kind.len() as u64), String::new()),
    ];
    draw_tile_row(c, 108.0, &tiles);

    let y = section(c, 196.0, "Chunks by kind");
    let entries = fold_top_n(&inv.chunks_by_kind, 8);
    let chart = HBarChart {
        x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 92.0,
        rows: count_rows(&entries, inv.chunk_count), row_gap: 7.0,
    };
    let h = chart.height();
    chart.draw(c);

    let y = section(c, y + h + 28.0, "Chunk size distribution  (chunks per size bucket)");
    let hist = &inv.chunk_size_hist;
    ColumnChart {
        x: MARGIN + 34.0,
        y: y + 10.0,
        w: CONTENT_W - 34.0,
        h: 128.0,
        values: hist.counts.iter().map(|&v| v as f64).collect(),
        color: SERIES[0],
        x_labels: (0..hist.counts.len()).map(|i| (i, hist.label(i))).collect(),
        y_label: String::new(),
    }
    .draw(c);

    let y = y + 10.0 + 128.0 + 30.0;
    let y = section(c, y, "Embedding artifact");
    if let Some(e) = &data.manifest.extensions.embeddings {
        let npz = e.files.values().map(|f| f.size_bytes).max().unwrap_or(0);
        let tiles: [(&str, String, String); 4] = [
            ("Vectors", group(e.n_chunks), "one per chunk".into()),
            ("Dimension", e.dimension.to_string(), if e.backend.normalized { "L2-normalized".into() } else { "not normalized".into() }),
            ("Backend", short_model(&e.backend.name), e.backend.name.clone()),
            ("Artifact size", bytes_human(npz), "float32 .npz".into()),
        ];
        draw_tile_row(c, y + 6.0, &tiles);
    } else {
        c.text("No embeddings extension in this bundle.", MARGIN, y + 16.0, 8.5, FontKind::Regular, INK_SECONDARY, Align::Left);
    }
}

fn short_model(name: &str) -> String {
    name.rsplit('/').next().unwrap_or(name).to_string()
}

// ---------------------------------------------------------------- page 6

fn concepts_enrichment(c: &Canvas, data: &ReportData) {
    let inv = &data.inventory;
    let m = &data.manifest;

    let coocc = m.extensions.concepts.as_ref().map(|e| e.n_cooccurrence).unwrap_or(0);
    let n_enrich = m.extensions.l4.as_ref().map(|e| e.n_enrichments).unwrap_or(0);
    let tiles: [(&str, String, String); 4] = [
        ("Concepts", group(inv.concept_count), format!("{} occurrences", compact(inv.concept_occurrences_total))),
        ("Co-occurrence pairs", compact(coocc), group(coocc)),
        ("Single-use concepts", pct(inv.singleton_concepts, inv.concept_count), format!("{} concepts", group(inv.singleton_concepts))),
        ("LLM enrichments", group(n_enrich), "L4, unverified".into()),
    ];
    draw_tile_row(c, 108.0, &tiles);

    let y = section(c, 196.0, "Top concepts by occurrence count");
    let rows: Vec<HBarRow> = inv
        .top_concepts()
        .into_iter()
        .map(|t| HBarRow {
            label: t.label.clone(),
            value: t.occurrences as f64,
            value_label: format!("{} · {} files", group(t.occurrences), compact(t.files)),
            color: SERIES[0],
        })
        .collect();
    let chart = HBarChart { x: MARGIN, y: y + 4.0, w: CONTENT_W, label_w: 96.0, rows, row_gap: 7.0 };
    let h = chart.height();
    chart.draw(c);

    let y = y + h + 28.0;
    match &data.enrichments {
        Some(e) if !e.timeline.is_empty() => {
            let y = section(c, y, "L4 enrichment throughput  (enrichments per 10 minutes, UTC)");
            let (values, labels) = timeline_series(e);
            ColumnChart {
                x: MARGIN + 34.0,
                y: y + 10.0,
                w: CONTENT_W - 34.0,
                h: 118.0,
                values,
                color: SERIES[0],
                x_labels: labels,
                y_label: String::new(),
            }
            .draw(c);
            let kinds = e
                .by_kind
                .iter()
                .map(|(k, v)| format!("{} {}", group(*v), k))
                .collect::<Vec<_>>()
                .join(" · ");
            let models = e.by_model.keys().cloned().collect::<Vec<_>>().join(", ");
            let avg_chars = if e.total > 0 { e.text_chars_total / e.total } else { 0 };
            c.text(
                &format!("{kinds} · model: {models} · mean text {avg_chars} chars"),
                MARGIN + 34.0,
                y + 10.0 + 118.0 + 24.0,
                7.5,
                FontKind::Regular,
                INK_SECONDARY,
                Align::Left,
            );
        }
        _ => {
            c.text(
                "No enrichments.jsonl in this bundle — L4 was not run or its artifact is missing.",
                MARGIN,
                y + 12.0,
                8.5,
                FontKind::Regular,
                INK_SECONDARY,
                Align::Left,
            );
        }
    }
}

/// Re-bucket the 10-minute timeline to at most 48 columns, hour labels.
fn timeline_series(e: &EnrichmentStats) -> (Vec<f64>, Vec<(usize, String)>) {
    let merge = e.timeline.len().div_ceil(48).max(1);
    let values: Vec<f64> = e
        .timeline
        .chunks(merge)
        .map(|ch| ch.iter().map(|&(_, v)| v as f64).sum())
        .collect();
    let bucket_secs = BUCKET_SECS * merge as i64;
    let label_every = (values.len() / 6).max(1);
    let labels = (0..values.len())
        .step_by(label_every)
        .map(|i| (i, epoch_hhmm(e.timeline[i * merge].0)))
        .collect();
    let _ = bucket_secs;
    (values, labels)
}

// ---------------------------------------------------------------- page 7

struct Flag {
    status: Rgb01,
    badge: &'static str,
    title: String,
    detail: String,
}

fn quality(c: &Canvas, data: &ReportData) {
    let inv = &data.inventory;
    let m = &data.manifest;
    let t = &m.ast_coverage.totals;

    // Evidence-basis banner (operator-approved epistemic split).
    let by = 104.0;
    c.rect(MARGIN, by, CONTENT_W, 64.0, blend_over_surface(SERIES[0], 0.07));
    c.rect(MARGIN, by, 3.0, 64.0, SERIES[0]);
    c.text("Evidence basis & confidence", MARGIN + 14.0, by + 16.0, 9.0, FontKind::Bold, INK, Align::Left);
    c.text_wrapped(
        "Every count and chart in this report is mechanically derived from bundle artifacts \
         (inventory.jsonld, run_manifest.json, sidecars) and can be recomputed from them. \
         The text content of L4 enrichments is LLM-authored and is treated as unverified by \
         default (PALS's LAW): validate it before high-stakes use. Flags below are threshold \
         checks on mechanical figures, not interpretations.",
        MARGIN + 14.0,
        by + 30.0,
        CONTENT_W - 28.0,
        7.5,
        10.5,
        FontKind::Regular,
        INK_SECONDARY,
    );

    let mut flags: Vec<Flag> = Vec::new();

    if let Some(s) = &m.shacl_self_check {
        flags.push(Flag {
            status: if s.conforms { STATUS_GOOD } else { STATUS_CRITICAL },
            badge: if s.conforms { "PASS" } else { "FAIL" },
            title: "SHACL self-check".into(),
            detail: if s.conforms {
                "The emitted graph conforms to the bundle's own SHACL shapes.".into()
            } else {
                "The emitted graph violates its SHACL shapes — inspect the validation report.".into()
            },
        });
    }

    // Independent recount vs the manifest's own numbers — drift detection.
    let mut drift: Vec<String> = Vec::new();
    if inv.file_count != m.counts.files {
        drift.push(format!("files {} vs {}", group(inv.file_count), group(m.counts.files)));
    }
    if let Some(e) = &m.extensions.embeddings {
        if inv.chunk_count != e.n_chunks {
            drift.push(format!("chunks {} vs {}", group(inv.chunk_count), group(e.n_chunks)));
        }
    }
    if let Some(e) = &m.extensions.concepts {
        if inv.concept_count != e.n_concepts {
            drift.push(format!(
                "concepts {} vs {}",
                group(inv.concept_count),
                group(e.n_concepts)
            ));
        }
    }
    flags.push(if drift.is_empty() {
        Flag {
            status: STATUS_GOOD,
            badge: "PASS",
            title: "Inventory recount matches the manifest".into(),
            detail: "Files, chunks, and concepts counted independently from inventory.jsonld \
                     equal the manifest's recorded totals.".into(),
        }
    } else {
        Flag {
            status: STATUS_WARNING,
            badge: "FLAG",
            title: "Inventory and manifest disagree".into(),
            detail: format!(
                "Recounting inventory.jsonld gives {} (graph vs manifest). The graph is the \
                 ground truth; the manifest counter drifted at emit time.",
                drift.join("; ")
            ),
        }
    });

    let n_commit_times = inv.distinct_commit_times.len();
    if n_commit_times == 1 && !inv.commit_times_overflowed {
        flags.push(Flag {
            status: STATUS_SERIOUS,
            badge: "FLAG",
            title: "Git timestamps flattened".into(),
            detail: format!(
                "All {} file nodes carry the same gitCommitTime ({}). This is the signature of a \
                 shallow clone: commit-time provenance is the clone time, not author history, and \
                 any time-based analysis on this bundle is invalid.",
                group(inv.file_count),
                inv.distinct_commit_times.iter().next().map(String::as_str).unwrap_or("?")
            ),
        });
    }

    if let Some(objc) = m.files_by_language.get("objective-c").copied().filter(|&n| n > 1000) {
        flags.push(Flag {
            status: STATUS_SERIOUS,
            badge: "FLAG",
            title: format!("{} files classified as Objective-C", group(objc)),
            detail: "The Linux kernel contains no Objective-C. This volume is consistent with the \
                     known .m/.h retag defect in the classifier; language shares and per-language \
                     extraction figures inherit the error."
                .into(),
        });
    }

    if t.files_with_parse_errors * 100 > t.files * 30 {
        flags.push(Flag {
            status: STATUS_SERIOUS,
            badge: "FLAG",
            title: format!(
                "{} source files ({}) have tree-sitter parse errors",
                group(t.files_with_parse_errors),
                pct(t.files_with_parse_errors, t.files)
            ),
            detail: "Symbols and imports from these files are extracted from partially parsed \
                     trees and undercount reality; kernel C with GCC extensions is the dominant \
                     contributor."
                .into(),
        });
    }

    if t.silent_zero_symbol_files > 0 {
        flags.push(Flag {
            status: STATUS_WARNING,
            badge: "FLAG",
            title: format!("{} silent zero-symbol files", group(t.silent_zero_symbol_files)),
            detail: "Files whose parse produced an AST but zero symbols and no recorded error — \
                     extraction gaps that are invisible unless counted, as here.".into(),
        });
    }

    if let Some(none) = m.files_by_language.get("(none)").copied().filter(|&n| n > 0) {
        flags.push(Flag {
            status: STATUS_WARNING,
            badge: "FLAG",
            title: format!("{} files ({}) have no language classification", group(none), pct(none, m.counts.files)),
            detail: "Kconfig, devicetree, headers-only trees and data files fall outside the \
                     classifier; they carry no per-language facts.".into(),
        });
    }

    if let (Some(l4), Some(concepts)) = (&m.extensions.l4, &m.extensions.concepts) {
        let described = l4.by_kind.get("concept_description").copied().unwrap_or(0);
        if described * 1000 < concepts.n_concepts {
            flags.push(Flag {
                status: STATUS_WARNING,
                badge: "FLAG",
                title: format!(
                    "L4 concept coverage is {} ({} of {} concepts)",
                    pct(described, concepts.n_concepts),
                    group(described),
                    group(concepts.n_concepts)
                ),
                detail: "By design the enricher describes only curated-vocabulary-matched \
                         concepts, so this measures how little of this domain the vocabulary \
                         covers. Degradation cannot be ruled out from the bundle alone: \
                         concept-path failures are not disclosed in the manifest.".into(),
            });
        }
        let summaries = l4.by_kind.get("file_summary").copied().unwrap_or(0);
        flags.push(Flag {
            status: if summaries * 100 >= m.ast_coverage.n_source_files * 60 { STATUS_GOOD } else { STATUS_WARNING },
            badge: "INFO",
            title: format!(
                "File summaries cover {} of source files",
                pct(summaries, m.ast_coverage.n_source_files)
            ),
            detail: format!(
                "{} of {} source files have an LLM summary. The summaries themselves are \
                 unverified LLM output.",
                group(summaries),
                group(m.ast_coverage.n_source_files)
            ),
        });
    }

    let mut y = by + 64.0 + 22.0;
    for f in &flags {
        c.rect(MARGIN, y - 8.0, 7.0, 7.0, f.status);
        c.text(f.badge, MARGIN + 12.0, y, 7.0, FontKind::Bold, INK_MUTED, Align::Left);
        c.text(&f.title, MARGIN + 44.0, y, 8.8, FontKind::Bold, INK, Align::Left);
        let end = c.text_wrapped(&f.detail, MARGIN + 44.0, y + 12.0, CONTENT_W - 44.0, 7.5, 10.5, FontKind::Regular, INK_SECONDARY);
        y = end + 12.0;
    }
}

fn draw_tile_row(c: &Canvas, y: f64, tiles: &[(&str, String, String)]) {
    let n = tiles.len() as f64;
    let w = (CONTENT_W - (n - 1.0) * 10.0) / n;
    for (i, (label, value, note)) in tiles.iter().enumerate() {
        stat_tile(c, MARGIN + i as f64 * (w + 10.0), y, w, 58.0, label, value, note);
    }
}

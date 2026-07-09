//! cbm-report — renders a polished PDF report from a codebase-mapper bundle.
//!
//! Usage: cbm-report <bundle-or-parent-dir> [-o <output.pdf>]
//!
//! The inventory (multi-GB JSON-LD) is streamed in 64 MB blocks and folded in
//! parallel; nothing close to the file size is ever resident.

mod ingest;
mod pdf;
mod stats;
mod util;

use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use printpdf::{Mm, PdfDocument};

use pdf::canvas::Fonts;
use pdf::pages::{render, ReportData};
use pdf::theme::{PAGE_H, PAGE_W};

fn main() -> Result<()> {
    let (dir, out) = parse_args()?;
    let started = Instant::now();

    let bundle_root = ingest::manifest::locate_bundle_root(&dir)?;
    eprintln!("bundle: {}", bundle_root.display());
    let manifest = ingest::manifest::load(&bundle_root)?;

    let inv_path = bundle_root.join("inventory.jsonld");
    let inventory = ingest::inventory::scan(&inv_path, |done, total| {
        eprint!(
            "\rscanning inventory.jsonld: {:>3.0}% ({:.1} / {:.1} GB)",
            done as f64 / total.max(1) as f64 * 100.0,
            done as f64 / 1e9,
            total as f64 / 1e9
        );
        let _ = std::io::stderr().flush();
    })
    .context("scan inventory.jsonld")?;
    eprintln!();

    let enrichments = read_optional(bundle_root.join("enrichments.jsonl"), ingest::enrichments::scan)?;
    let rust_items = read_optional(bundle_root.join("rust_items.jsonl"), ingest::rust_items::scan)?;
    if let Some(r) = &rust_items {
        // Parsed for cross-checking the manifest sidecar; the report renders the
        // manifest numbers, so only disagreements are worth a line on stderr.
        if r.total != manifest.rust_items_sidecar.as_ref().map(|s| s.n_items).unwrap_or(r.total) {
            eprintln!(
                "note: rust_items.jsonl has {} items but the manifest records {:?}",
                r.total,
                manifest.rust_items_sidecar.as_ref().map(|s| s.n_items)
            );
        }
    }

    let rendered_on = util::epoch_date(
        SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0),
    );
    let out_path = out.unwrap_or_else(|| dir.join(format!("{}-bundle-report.pdf", manifest.repo_name)));
    let data = ReportData { manifest, inventory, enrichments, rust_items, rendered_on };

    const PT_TO_MM: f64 = 25.4 / 72.0;
    let (doc, page1, layer1) = PdfDocument::new(
        format!("{} — codebase-mapper bundle report", data.manifest.repo_name),
        Mm((PAGE_W * PT_TO_MM) as f32),
        Mm((PAGE_H * PT_TO_MM) as f32),
        "content",
    );
    let fonts = Fonts::load(&doc)?;
    render(&doc, (page1, layer1), &fonts, &data);

    let file = std::fs::File::create(&out_path)
        .with_context(|| format!("create {}", out_path.display()))?;
    doc.save(&mut BufWriter::new(file)).context("write PDF")?;

    eprintln!(
        "wrote {} in {:.1}s ({} chunks, {} files, {} concepts scanned)",
        out_path.display(),
        started.elapsed().as_secs_f64(),
        util::group(data.inventory.chunk_count),
        util::group(data.inventory.file_count),
        util::group(data.inventory.concept_count),
    );
    Ok(())
}

fn read_optional<T>(path: PathBuf, f: impl Fn(&std::path::Path) -> Result<T>) -> Result<Option<T>> {
    if path.is_file() {
        Ok(Some(f(&path)?))
    } else {
        eprintln!("note: {} not present, section will be marked absent", path.display());
        Ok(None)
    }
}

fn parse_args() -> Result<(PathBuf, Option<PathBuf>)> {
    let mut args = std::env::args().skip(1);
    let mut dir = None;
    let mut out = None;
    while let Some(a) = args.next() {
        match a.as_str() {
            "-o" | "--output" => {
                out = Some(PathBuf::from(args.next().context("-o needs a path")?));
            }
            "-h" | "--help" => {
                println!("usage: cbm-report <bundle-or-parent-dir> [-o <output.pdf>]");
                std::process::exit(0);
            }
            _ if dir.is_none() => dir = Some(PathBuf::from(a)),
            other => bail!("unexpected argument: {other}"),
        }
    }
    Ok((dir.unwrap_or_else(|| PathBuf::from(".")), out))
}

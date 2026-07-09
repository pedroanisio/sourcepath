//! Parallel streaming pass over inventory.jsonld (multi-GB): sequential 64 MB
//! reads feed the splitter; each batch of extracted objects is parsed and
//! folded on the rayon pool, then partial stats are merged.

use std::fs::File;
use std::io::Read;
use std::path::Path;

use anyhow::{Context, Result};
use rayon::prelude::*;

use crate::ingest::splitter::GraphSplitter;
use crate::stats::{GraphNode, InventoryStats};

const BLOCK_SIZE: usize = 64 << 20;

pub fn scan(path: &Path, mut progress: impl FnMut(u64, u64)) -> Result<InventoryStats> {
    let mut file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let total = file.metadata()?.len();
    let mut splitter = GraphSplitter::new();
    let mut block = vec![0u8; BLOCK_SIZE];
    let mut read_total = 0u64;
    let mut stats = InventoryStats::default();
    let mut skipped: u64 = 0;

    loop {
        let n = read_full(&mut file, &mut block)?;
        if n == 0 {
            break;
        }
        read_total += n as u64;
        let items = splitter.feed(&block[..n]);

        let (batch, bad) = items
            .par_iter()
            .fold(
                || (InventoryStats::default(), 0u64),
                |(mut acc, mut bad), item| {
                    match serde_json::from_slice::<GraphNode>(item) {
                        Ok(node) => acc.fold(node),
                        Err(_) => bad += 1,
                    }
                    (acc, bad)
                },
            )
            .reduce(
                || (InventoryStats::default(), 0u64),
                |(a, ba), (b, bb)| (a.merge(b), ba + bb),
            );
        stats = stats.merge(batch);
        skipped += bad;
        progress(read_total, total);
    }

    if skipped > 0 {
        // PALS's-law posture applies to our own pipeline too: never silently
        // drop records — surface the count so totals can be audited.
        eprintln!("warning: {skipped} @graph items failed to parse and were skipped");
    }
    Ok(stats)
}

fn read_full(file: &mut File, buf: &mut [u8]) -> Result<usize> {
    let mut filled = 0;
    while filled < buf.len() {
        let n = file.read(&mut buf[filled..])?;
        if n == 0 {
            break;
        }
        filled += n;
    }
    Ok(filled)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn end_to_end_small_graph() {
        let doc = r#"{
          "@context": {"x": "y"},
          "@graph": [
            {"@id": "1", "cbml2:kind": "file", "nif:beginIndex": 0, "nif:endIndex": 500},
            {"@id": "2", "cbml2:kind": "symbol", "nif:beginIndex": 0, "nif:endIndex": 2000},
            {"@id": "3", "cbm:path": "fs/ext4/inode.c", "cbm:sizeBytes": 9000, "cbm:language": "c"},
            {"@id": "4", "skos:prefLabel": {"@value": "inode"}, "cbml3:occurrenceCount": 42}
          ]
        }"#;
        let mut tmp = tempfile_path("inv.jsonld");
        std::fs::File::create(&tmp.0).unwrap().write_all(doc.as_bytes()).unwrap();
        let stats = scan(&tmp.0, |_, _| {}).unwrap();
        assert_eq!(stats.chunk_count, 2);
        assert_eq!(stats.chunks_by_kind["symbol"], 1);
        assert_eq!(stats.file_count, 1);
        assert_eq!(stats.bytes_by_top_dir["fs"], 9000);
        assert_eq!(stats.concept_count, 1);
        tmp.1();
    }

    fn tempfile_path(name: &str) -> (std::path::PathBuf, impl FnOnce()) {
        let dir = std::env::temp_dir().join(format!("cbm-report-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join(name);
        let cleanup_dir = dir;
        (p, move || {
            let _ = std::fs::remove_dir_all(cleanup_dir);
        })
    }
}

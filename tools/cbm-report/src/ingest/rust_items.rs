//! rust_items.jsonl — mechanically extracted Rust items (structs, fns, impls…).

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader};
use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::stats::top_dir;

#[derive(Debug, Deserialize)]
struct RustItemLine {
    kind: Option<String>,
    is_pub: Option<bool>,
    is_async: Option<bool>,
    path: Option<String>,
}

#[derive(Debug, Default)]
pub struct RustItemStats {
    pub total: u64,
    pub by_kind: BTreeMap<String, u64>,
    pub pub_items: u64,
    pub async_items: u64,
    pub by_top_dir: BTreeMap<String, u64>,
    pub malformed_lines: u64,
}

pub fn scan(path: &Path) -> Result<RustItemStats> {
    let file = std::fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let reader = BufReader::with_capacity(1 << 20, file);
    let mut s = RustItemStats::default();
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(item) = serde_json::from_str::<RustItemLine>(&line) else {
            s.malformed_lines += 1;
            continue;
        };
        s.total += 1;
        *s.by_kind.entry(item.kind.unwrap_or_else(|| "(unknown)".into())).or_default() += 1;
        if item.is_pub == Some(true) {
            s.pub_items += 1;
        }
        if item.is_async == Some(true) {
            s.async_items += 1;
        }
        if let Some(p) = &item.path {
            *s.by_top_dir.entry(top_dir(p).to_string()).or_default() += 1;
        }
    }
    Ok(s)
}

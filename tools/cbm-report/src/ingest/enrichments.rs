//! enrichments.jsonl — L4 LLM output. We aggregate its *metadata* (kinds,
//! models, timing, text length); the text itself is LLM-authored and treated
//! as unverified per PALS's LAW.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader};
use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::util::parse_iso8601_utc;

/// Timeline bucket width for the throughput chart.
pub const BUCKET_SECS: i64 = 600;

#[derive(Debug, Deserialize)]
struct EnrichmentLine {
    generated_at: Option<String>,
    kind: Option<String>,
    model: Option<String>,
    text: Option<String>,
}

#[derive(Debug, Default)]
pub struct EnrichmentStats {
    pub total: u64,
    pub malformed_lines: u64,
    pub by_kind: BTreeMap<String, u64>,
    pub by_model: BTreeMap<String, u64>,
    /// (bucket_epoch, count) — 10-minute buckets, sorted ascending, gaps kept.
    pub timeline: Vec<(i64, u64)>,
    pub first_epoch: Option<i64>,
    pub last_epoch: Option<i64>,
    pub text_chars_total: u64,
    pub text_chars_max: u64,
}

pub fn scan(path: &Path) -> Result<EnrichmentStats> {
    let file = std::fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let reader = BufReader::with_capacity(4 << 20, file);
    let mut s = EnrichmentStats::default();
    let mut buckets: BTreeMap<i64, u64> = BTreeMap::new();

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(e) = serde_json::from_str::<EnrichmentLine>(&line) else {
            s.malformed_lines += 1;
            continue;
        };
        s.total += 1;
        *s.by_kind.entry(e.kind.unwrap_or_else(|| "(unknown)".into())).or_default() += 1;
        *s.by_model.entry(e.model.unwrap_or_else(|| "(unknown)".into())).or_default() += 1;
        if let Some(epoch) = e.generated_at.as_deref().and_then(parse_iso8601_utc) {
            s.first_epoch = Some(s.first_epoch.map_or(epoch, |f: i64| f.min(epoch)));
            s.last_epoch = Some(s.last_epoch.map_or(epoch, |l: i64| l.max(epoch)));
            *buckets.entry(epoch - epoch.rem_euclid(BUCKET_SECS)).or_default() += 1;
        }
        if let Some(t) = &e.text {
            let chars = t.chars().count() as u64;
            s.text_chars_total += chars;
            s.text_chars_max = s.text_chars_max.max(chars);
        }
    }

    // Fill gaps so the timeline shows idle periods honestly.
    if let (Some(&first), Some(&last)) = (
        buckets.keys().next(),
        buckets.keys().next_back(),
    ) {
        let mut t = first;
        while t <= last {
            s.timeline.push((t, buckets.get(&t).copied().unwrap_or(0)));
            t += BUCKET_SECS;
        }
    }
    Ok(s)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn aggregates_kinds_models_timeline() {
        let dir = std::env::temp_dir().join(format!("cbm-enr-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("enrichments.jsonl");
        let mut f = std::fs::File::create(&p).unwrap();
        writeln!(f, r#"{{"generated_at":"2026-07-09T15:00:01Z","kind":"file_summary","model":"m1","text":"abcd"}}"#).unwrap();
        writeln!(f, r#"{{"generated_at":"2026-07-09T15:00:09Z","kind":"file_summary","model":"m1","text":"ab"}}"#).unwrap();
        writeln!(f, r#"{{"generated_at":"2026-07-09T15:25:00Z","kind":"concept_description","model":"m1","text":"x"}}"#).unwrap();
        writeln!(f, "not json").unwrap();
        let s = scan(&p).unwrap();
        assert_eq!(s.total, 3);
        assert_eq!(s.malformed_lines, 1);
        assert_eq!(s.by_kind["file_summary"], 2);
        // 15:00→15:25 spans buckets 15:00, 15:10, 15:20 — the idle 15:10 kept as zero
        assert_eq!(s.timeline.len(), 3);
        assert_eq!(s.timeline[0].1, 2);
        assert_eq!(s.timeline[1].1, 0);
        assert_eq!(s.timeline[2].1, 1);
        assert_eq!(s.text_chars_max, 4);
        let _ = std::fs::remove_dir_all(dir);
    }
}

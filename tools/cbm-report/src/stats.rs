//! Aggregation model for the streaming inventory pass and the sidecar files.
//!
//! Every accumulator supports `merge` so rayon can fold partial results from
//! parallel batches deterministically.

use std::collections::{BinaryHeap, HashMap, HashSet};

use serde::Deserialize;

/// Upper bound on distinct git commit timestamps we track exactly; past this
/// the exact set no longer matters (the interesting signal is cardinality 1).
const MAX_TRACKED_COMMIT_TIMES: usize = 1_000;
/// How many top concepts (by occurrence count) to keep.
pub const TOP_CONCEPTS: usize = 15;

/// One node of the JSON-LD `@graph`, with only the fields the report needs.
/// Node families are discriminated by which fields are present:
/// chunks carry `cbml2:kind`, files carry `cbm:path`, concepts `skos:prefLabel`.
#[derive(Debug, Default, Deserialize)]
pub struct GraphNode {
    // --- chunk fields (cbml2:Chunk) ---
    #[serde(rename = "cbml2:kind")]
    pub chunk_kind: Option<String>,
    #[serde(rename = "nif:beginIndex")]
    pub begin_index: Option<u64>,
    #[serde(rename = "nif:endIndex")]
    pub end_index: Option<u64>,
    #[serde(rename = "cbml2:beginLine")]
    pub begin_line: Option<u64>,
    #[serde(rename = "cbml2:endLine")]
    pub end_line: Option<u64>,
    #[serde(rename = "cbml2:truncatedForEmbedding")]
    pub truncated: Option<bool>,
    // --- file fields (cbm:File) ---
    #[serde(rename = "cbm:path")]
    pub path: Option<String>,
    #[serde(rename = "cbm:sizeBytes")]
    pub size_bytes: Option<u64>,
    #[serde(rename = "cbm:language")]
    pub language: Option<String>,
    /// One string in JSON-LD when a node carries a single diagnostic, an
    /// array when it carries several (e.g. "parse_errors_present" plus the
    /// quantified "parse_error_nodes:<N>" since flaw-map F8).
    #[serde(rename = "cbm:extractionError")]
    pub extraction_error: Option<OneOrMany<String>>,
    #[serde(rename = "cbm:gitCommitTime")]
    pub git_commit_time: Option<TypedValue>,
    // --- concept fields (skos:Concept) ---
    #[serde(rename = "skos:prefLabel")]
    pub pref_label: Option<LangValue>,
    #[serde(rename = "cbml3:occurrenceCount")]
    pub occurrence_count: Option<u64>,
    #[serde(rename = "cbml3:fileCount")]
    pub file_count: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct TypedValue {
    #[serde(rename = "@value")]
    pub value: String,
}

/// JSON-LD compacts single-element property arrays to scalars; a property
/// can therefore arrive as either shape.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum OneOrMany<T> {
    One(T),
    Many(Vec<T>),
}

impl<T> OneOrMany<T> {
    pub fn into_vec(self) -> Vec<T> {
        match self {
            OneOrMany::One(v) => vec![v],
            OneOrMany::Many(v) => v,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct LangValue {
    #[serde(rename = "@value")]
    pub value: String,
}

/// Log-spaced byte-size histogram. Bucket i covers [floor(i), floor(i+1)).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SizeHistogram {
    pub floors: &'static [u64],
    pub counts: Vec<u64>,
}

// Decimal floors so bucket labels round-trip exactly through bytes_human.
pub const CHUNK_SIZE_FLOORS: &[u64] = &[0, 256, 1_000, 4_000, 16_000, 64_000, 256_000];
pub const FILE_SIZE_FLOORS: &[u64] = &[
    0, 1_000, 4_000, 16_000, 64_000, 256_000, 1_000_000, 16_000_000,
];

impl SizeHistogram {
    pub fn new(floors: &'static [u64]) -> Self {
        Self { floors, counts: vec![0; floors.len()] }
    }

    pub fn add(&mut self, size: u64) {
        let idx = self
            .floors
            .iter()
            .rposition(|&f| size >= f)
            .unwrap_or(0);
        self.counts[idx] += 1;
    }

    pub fn merge(&mut self, other: &Self) {
        debug_assert_eq!(self.floors, other.floors);
        for (a, b) in self.counts.iter_mut().zip(&other.counts) {
            *a += b;
        }
    }

    /// Human label for bucket i, e.g. "256 B – 1 kB" or "≥ 1 MB".
    pub fn label(&self, i: usize) -> String {
        let lo = crate::util::bytes_human(self.floors[i]);
        match self.floors.get(i + 1) {
            Some(&hi) => format!("{} – {}", lo, crate::util::bytes_human(hi)),
            None => format!("≥ {}", lo),
        }
    }
}

/// A concept retained in the top-K heap.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct TopConcept {
    pub occurrences: u64,
    pub files: u64,
    pub label: String,
}

/// Everything the streaming pass over inventory.jsonld accumulates.
#[derive(Debug)]
pub struct InventoryStats {
    // chunks
    pub chunk_count: u64,
    pub chunks_by_kind: HashMap<String, u64>,
    pub chunk_bytes_total: u64,
    pub chunk_size_hist: SizeHistogram,
    pub chunk_lines_total: u64,
    pub chunks_truncated: u64,
    // files
    pub file_count: u64,
    pub file_bytes_total: u64,
    pub file_size_hist: SizeHistogram,
    pub bytes_by_language: HashMap<String, u64>,
    pub files_by_top_dir: HashMap<String, u64>,
    pub bytes_by_top_dir: HashMap<String, u64>,
    pub extraction_errors: HashMap<String, u64>,
    pub distinct_commit_times: HashSet<String>,
    pub commit_times_overflowed: bool,
    // concepts
    pub concept_count: u64,
    pub concept_occurrences_total: u64,
    pub singleton_concepts: u64,
    /// min-heap of the top-K concepts by occurrence count
    top_concepts: BinaryHeap<std::cmp::Reverse<TopConcept>>,
}

impl Default for InventoryStats {
    fn default() -> Self {
        Self {
            chunk_count: 0,
            chunks_by_kind: HashMap::new(),
            chunk_bytes_total: 0,
            chunk_size_hist: SizeHistogram::new(CHUNK_SIZE_FLOORS),
            chunk_lines_total: 0,
            chunks_truncated: 0,
            file_count: 0,
            file_bytes_total: 0,
            file_size_hist: SizeHistogram::new(FILE_SIZE_FLOORS),
            bytes_by_language: HashMap::new(),
            files_by_top_dir: HashMap::new(),
            bytes_by_top_dir: HashMap::new(),
            extraction_errors: HashMap::new(),
            distinct_commit_times: HashSet::new(),
            commit_times_overflowed: false,
            concept_count: 0,
            concept_occurrences_total: 0,
            singleton_concepts: 0,
            top_concepts: BinaryHeap::new(),
        }
    }
}

/// Top-level path segment, with root-level files folded into "(root)".
pub fn top_dir(path: &str) -> &str {
    match path.find('/') {
        Some(i) => &path[..i],
        None => "(root)",
    }
}

impl InventoryStats {
    pub fn fold(&mut self, node: GraphNode) {
        if let Some(kind) = node.chunk_kind {
            self.chunk_count += 1;
            *self.chunks_by_kind.entry(kind).or_default() += 1;
            if let (Some(b), Some(e)) = (node.begin_index, node.end_index) {
                let bytes = e.saturating_sub(b);
                self.chunk_bytes_total += bytes;
                self.chunk_size_hist.add(bytes);
            }
            if let (Some(b), Some(e)) = (node.begin_line, node.end_line) {
                self.chunk_lines_total += e.saturating_sub(b) + 1;
            }
            if node.truncated == Some(true) {
                self.chunks_truncated += 1;
            }
        } else if let Some(path) = node.path {
            self.file_count += 1;
            let dir = top_dir(&path).to_string();
            *self.files_by_top_dir.entry(dir.clone()).or_default() += 1;
            if let Some(size) = node.size_bytes {
                self.file_bytes_total += size;
                self.file_size_hist.add(size);
                *self.bytes_by_top_dir.entry(dir).or_default() += size;
                let lang = node.language.unwrap_or_else(|| "(none)".to_string());
                *self.bytes_by_language.entry(lang).or_default() += size;
            }
            if let Some(err) = node.extraction_error {
                for e in err.into_vec() {
                    *self.extraction_errors.entry(e).or_default() += 1;
                }
            }
            if let Some(t) = node.git_commit_time {
                if self.distinct_commit_times.len() < MAX_TRACKED_COMMIT_TIMES {
                    self.distinct_commit_times.insert(t.value);
                } else if !self.distinct_commit_times.contains(&t.value) {
                    self.commit_times_overflowed = true;
                }
            }
        } else if let Some(label) = node.pref_label {
            // skos:Collection nodes also carry prefLabel; a concept is the
            // node family with occurrence statistics (cbml3:occurrenceCount /
            // cbml3:fileCount). Without this guard the three vocabulary
            // collections are miscounted as concepts.
            if node.occurrence_count.is_none() && node.file_count.is_none() {
                return;
            }
            self.concept_count += 1;
            let occ = node.occurrence_count.unwrap_or(0);
            self.concept_occurrences_total += occ;
            if occ <= 1 {
                self.singleton_concepts += 1;
            }
            self.top_concepts.push(std::cmp::Reverse(TopConcept {
                occurrences: occ,
                files: node.file_count.unwrap_or(0),
                label: label.value,
            }));
            if self.top_concepts.len() > TOP_CONCEPTS {
                self.top_concepts.pop();
            }
        }
        // Nodes matching no family (e.g. future vocabulary) are counted nowhere:
        // the report only claims what it recognizes.
    }

    pub fn merge(mut self, other: Self) -> Self {
        self.chunk_count += other.chunk_count;
        for (k, v) in other.chunks_by_kind {
            *self.chunks_by_kind.entry(k).or_default() += v;
        }
        self.chunk_bytes_total += other.chunk_bytes_total;
        self.chunk_size_hist.merge(&other.chunk_size_hist);
        self.chunk_lines_total += other.chunk_lines_total;
        self.chunks_truncated += other.chunks_truncated;
        self.file_count += other.file_count;
        self.file_bytes_total += other.file_bytes_total;
        self.file_size_hist.merge(&other.file_size_hist);
        for (k, v) in other.bytes_by_language {
            *self.bytes_by_language.entry(k).or_default() += v;
        }
        for (k, v) in other.files_by_top_dir {
            *self.files_by_top_dir.entry(k).or_default() += v;
        }
        for (k, v) in other.bytes_by_top_dir {
            *self.bytes_by_top_dir.entry(k).or_default() += v;
        }
        for (k, v) in other.extraction_errors {
            *self.extraction_errors.entry(k).or_default() += v;
        }
        for t in other.distinct_commit_times {
            if self.distinct_commit_times.len() < MAX_TRACKED_COMMIT_TIMES {
                self.distinct_commit_times.insert(t);
            } else if !self.distinct_commit_times.contains(&t) {
                self.commit_times_overflowed = true;
            }
        }
        self.commit_times_overflowed |= other.commit_times_overflowed;
        self.concept_count += other.concept_count;
        self.concept_occurrences_total += other.concept_occurrences_total;
        self.singleton_concepts += other.singleton_concepts;
        for c in other.top_concepts {
            self.top_concepts.push(c);
            if self.top_concepts.len() > TOP_CONCEPTS {
                self.top_concepts.pop();
            }
        }
        self
    }

    /// Top concepts, highest occurrence count first.
    pub fn top_concepts(&self) -> Vec<TopConcept> {
        let mut v: Vec<_> = self.top_concepts.iter().map(|r| r.0.clone()).collect();
        v.sort_by(|a, b| b.cmp(a));
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(json: &str) -> GraphNode {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn classifies_chunk_file_concept() {
        let mut s = InventoryStats::default();
        s.fold(node(
            r#"{"cbml2:kind":"file","nif:beginIndex":0,"nif:endIndex":1000,
                "cbml2:beginLine":1,"cbml2:endLine":40,"cbml2:truncatedForEmbedding":true}"#,
        ));
        s.fold(node(
            r#"{"cbm:path":"drivers/gpu/x.c","cbm:sizeBytes":2048,"cbm:language":"c",
                "cbm:extractionError":"parse_errors_present",
                "cbm:gitCommitTime":{"@type":"xsd:dateTime","@value":"2026-07-09T15:26:51+00:00"}}"#,
        ));
        s.fold(node(
            r#"{"skos:prefLabel":{"@language":"en","@value":"mutex"},
                "cbml3:occurrenceCount":500,"cbml3:fileCount":80}"#,
        ));
        assert_eq!(s.chunk_count, 1);
        assert_eq!(s.chunk_bytes_total, 1000);
        assert_eq!(s.chunks_truncated, 1);
        assert_eq!(s.chunk_lines_total, 40);
        assert_eq!(s.file_count, 1);
        assert_eq!(s.bytes_by_language["c"], 2048);
        assert_eq!(s.files_by_top_dir["drivers"], 1);
        assert_eq!(s.extraction_errors["parse_errors_present"], 1);
        assert_eq!(s.distinct_commit_times.len(), 1);
        assert_eq!(s.concept_count, 1);
        assert_eq!(s.top_concepts()[0].label, "mutex");
    }

    #[test]
    fn multi_valued_extraction_errors_parse_as_file() {
        // Since flaw-map F8 a flagged file carries two diagnostics, which
        // JSON-LD serializes as an array. 32,197 kernel file nodes were
        // silently skipped when this field only accepted a scalar.
        let mut s = InventoryStats::default();
        s.fold(node(
            r#"{"cbm:path":"fs/ext4/inode.c","cbm:sizeBytes":10,"cbm:language":"c",
                "cbm:extractionError":["parse_errors_present","parse_error_nodes:37"]}"#,
        ));
        assert_eq!(s.file_count, 1);
        assert_eq!(s.extraction_errors["parse_errors_present"], 1);
        assert_eq!(s.extraction_errors["parse_error_nodes:37"], 1);
    }

    #[test]
    fn top_dir_of_root_file() {
        assert_eq!(top_dir("Makefile"), "(root)");
        assert_eq!(top_dir("drivers/gpu/x.c"), "drivers");
    }

    #[test]
    fn skos_collection_is_not_a_concept() {
        let mut s = InventoryStats::default();
        // A skos:Collection carries prefLabel but no occurrence statistics.
        s.fold(node(r#"{"skos:prefLabel":{"@value":"code_structure"}}"#));
        assert_eq!(s.concept_count, 0);
        s.fold(node(
            r#"{"skos:prefLabel":{"@value":"mutex"},"cbml3:occurrenceCount":5}"#,
        ));
        assert_eq!(s.concept_count, 1);
    }

    #[test]
    fn histogram_buckets() {
        let mut h = SizeHistogram::new(CHUNK_SIZE_FLOORS);
        h.add(0);
        h.add(255);
        h.add(256);
        h.add(1024);
        h.add(300_000);
        assert_eq!(h.counts[0], 2);
        assert_eq!(h.counts[1], 1);
        assert_eq!(h.counts[2], 1);
        assert_eq!(h.counts[6], 1);
        assert_eq!(h.label(1), "256 B – 1.0 kB");
        assert_eq!(h.label(6), "≥ 256 kB");
    }

    #[test]
    fn top_k_keeps_largest_and_merge_works() {
        let mut a = InventoryStats::default();
        let mut b = InventoryStats::default();
        for i in 0..20u64 {
            let target = if i % 2 == 0 { &mut a } else { &mut b };
            target.fold(node(&format!(
                r#"{{"skos:prefLabel":{{"@value":"c{i}"}},"cbml3:occurrenceCount":{}}}"#,
                i * 10
            )));
        }
        let merged = a.merge(b);
        let top = merged.top_concepts();
        assert_eq!(top.len(), TOP_CONCEPTS);
        assert_eq!(top[0].occurrences, 190);
        assert_eq!(top[0].label, "c19");
        assert!(top.iter().all(|c| c.occurrences >= 50));
        assert_eq!(merged.singleton_concepts, 1); // only c0 with occ 0
    }
}

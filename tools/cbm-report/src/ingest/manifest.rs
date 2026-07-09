//! run_manifest.json — the bundle's own pre-aggregated facts. Everything here
//! is mechanically derived by codebase-mapper at emit time.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct RunManifest {
    pub repo_name: String,
    pub commit_sha: String,
    pub generated_at: String,
    pub tool_version: String,
    pub vocabulary_version: String,
    #[serde(default)]
    pub artifacts: BTreeMap<String, ArtifactEntry>,
    pub ast_coverage: AstCoverage,
    pub counts: Counts,
    pub files_by_language: BTreeMap<String, u64>,
    pub files_by_type: BTreeMap<String, u64>,
    #[serde(default)]
    pub extensions: Extensions,
    #[serde(default)]
    pub rust_items_sidecar: Option<RustSidecar>,
    #[serde(default)]
    pub shacl_self_check: Option<ShaclCheck>,
}

#[derive(Debug, Deserialize)]
pub struct ArtifactEntry {
    pub size_bytes: u64,
}

#[derive(Debug, Deserialize)]
pub struct AstCoverage {
    pub by_language: BTreeMap<String, LangCoverage>,
    pub totals: LangCoverage,
    pub n_source_files: u64,
    #[serde(default)]
    pub files: BTreeMap<String, ArtifactEntry>,
}

#[derive(Debug, Default, Deserialize)]
pub struct LangCoverage {
    pub files: u64,
    pub files_with_ast: u64,
    pub files_with_parse_errors: u64,
    pub imports_extracted: u64,
    pub silent_zero_symbol_files: u64,
    pub symbols_extracted: u64,
}

#[derive(Debug, Deserialize)]
pub struct Counts {
    pub files: u64,
    pub unique_blobs_written: u64,
    #[serde(default)]
    pub ast_summary_total_bytes: u64,
}

#[derive(Debug, Default, Deserialize)]
pub struct Extensions {
    #[serde(rename = "l2_40_embeddings_artifact")]
    pub embeddings: Option<EmbeddingsExt>,
    #[serde(rename = "l3_40_concepts_artifact")]
    pub concepts: Option<ConceptsExt>,
    #[serde(rename = "l4_50_artifact")]
    pub l4: Option<L4Ext>,
}

#[derive(Debug, Deserialize)]
pub struct EmbeddingsExt {
    pub n_chunks: u64,
    pub dimension: u64,
    pub backend: Backend,
    #[serde(default)]
    pub files: BTreeMap<String, ArtifactEntry>,
}

#[derive(Debug, Deserialize)]
pub struct Backend {
    pub name: String,
    pub normalized: bool,
}

#[derive(Debug, Deserialize)]
pub struct ConceptsExt {
    pub n_concepts: u64,
    pub n_cooccurrence: u64,
    #[serde(default)]
    pub files: BTreeMap<String, ArtifactEntry>,
}

#[derive(Debug, Deserialize)]
pub struct L4Ext {
    pub n_enrichments: u64,
    pub by_kind: BTreeMap<String, u64>,
    #[serde(default)]
    pub files: BTreeMap<String, ArtifactEntry>,
}

#[derive(Debug, Deserialize)]
pub struct RustSidecar {
    pub n_items: u64,
    pub n_files: u64,
    #[serde(default)]
    pub files: BTreeMap<String, ArtifactEntry>,
}

#[derive(Debug, Deserialize)]
pub struct ShaclCheck {
    pub conforms: bool,
}

impl RunManifest {
    /// Every artifact file the manifest lists, as (name, size_bytes) — for the
    /// artifact-size chart and the bundle total.
    pub fn listed_artifacts(&self) -> Vec<(String, u64)> {
        let mut out: Vec<(String, u64)> = Vec::new();
        let mut push = |m: &BTreeMap<String, ArtifactEntry>| {
            for (name, e) in m {
                out.push((name.clone(), e.size_bytes));
            }
        };
        push(&self.artifacts);
        push(&self.ast_coverage.files);
        if let Some(e) = &self.extensions.embeddings {
            push(&e.files);
        }
        if let Some(e) = &self.extensions.concepts {
            push(&e.files);
        }
        if let Some(e) = &self.extensions.l4 {
            push(&e.files);
        }
        if let Some(r) = &self.rust_items_sidecar {
            push(&r.files);
        }
        out.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        out.dedup_by(|a, b| a.0 == b.0);
        out
    }
}

/// Locate the directory holding run_manifest.json: the given dir itself, or a
/// single child directory (the layout `<out>/<repo_name>/run_manifest.json`).
pub fn locate_bundle_root(dir: &Path) -> Result<PathBuf> {
    if dir.join("run_manifest.json").is_file() {
        return Ok(dir.to_path_buf());
    }
    let mut candidates = Vec::new();
    for entry in std::fs::read_dir(dir).with_context(|| format!("read {}", dir.display()))? {
        let p = entry?.path();
        if p.is_dir() && p.join("run_manifest.json").is_file() {
            candidates.push(p);
        }
    }
    match candidates.len() {
        1 => Ok(candidates.pop().unwrap()),
        0 => bail!(
            "no run_manifest.json found in {} or its immediate children",
            dir.display()
        ),
        _ => bail!(
            "multiple bundles under {}: pass one of them explicitly",
            dir.display()
        ),
    }
}

pub fn load(bundle_root: &Path) -> Result<RunManifest> {
    let p = bundle_root.join("run_manifest.json");
    let data = std::fs::read(&p).with_context(|| format!("read {}", p.display()))?;
    serde_json::from_slice(&data).with_context(|| format!("parse {}", p.display()))
}

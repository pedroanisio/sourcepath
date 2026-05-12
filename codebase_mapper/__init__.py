"""codebase_mapper — auto-generated package facade.

Re-exports the public surface so consumers can `from codebase_mapper import X`.
The splitter regenerates this file; do not edit by hand.
"""
from __future__ import annotations

from .blobs import emit_blobs
from .classify import classify, language_of, path_excluded, refine_phases
from .cli import main
from .constants import ASSET_EXT, CBM, CBMI, CBMI_NS, CBMP, CBMP_NS, CBMT, CBMT_NS, CBM_NS, DATA_EXT, DEFAULT_PHASES, LANG_BY_EXT, MAN_PAGE_EXTS, PHASE_VOCABULARY, SH, SPDX_CORE_NS, SPDX_SOFTWARE_NS, TOOL_VERSION, TYPE_VOCABULARY, VOCABULARY_VERSION
from .emit_bundle import emit
from .extensions import (
    Aggregator, ArtifactEmitter, GraphContributor, ImportResolver,
    LanguageAnalyzer, PipelineCtx, RecordEnricher, ResolveResult,
    ShapeContributor,
    iter_aggregators, iter_artifact_emitters, iter_graph_contributors,
    iter_import_resolvers, iter_language_analyzers,
    iter_record_enrichers, iter_shape_contributors,
    register_aggregator, register_artifact_emitter, register_graph_contributor,
    register_import_resolver, register_language_analyzer,
    register_record_enricher, register_shape_contributor,
    reset_registries,
)
from .git_plumbing import git, git_bytes, list_tree, read_blob, resolve_commit
from .languages.c import extract_c_ast_summary, resolve_c_includes
from .languages.dart import detect_dart_package_name, extract_dart_ast_summary, resolve_dart_imports
from .languages.go import detect_go_module, extract_go_ast_summary, go_package_root, resolve_go_imports
from .languages.kotlin import build_kotlin_fqn_index, extract_kotlin_ast_summary, resolve_kotlin_imports
from .languages.python import build_python_module_index, detect_python_source_roots, extract_python_ast_summary, resolve_python_imports
from .languages.ruby import extract_ruby_ast_summary, resolve_ruby_imports
from .languages.rust import crate_for_file, detect_rust_workspaces, extract_rust_ast_summary, resolve_rust_imports
from .languages.swift import detect_swift_modules, extract_swift_ast_summary, resolve_swift_imports
from .languages.tsjs import TSJS_EXT_CANDIDATES, TSJS_INDEX_CANDIDATES, extract_tsjs_ast_summary, find_governing_tsconfig, load_tsconfigs, resolve_tsjs_import, tsjs_bare_package_root
from .lockfiles import parse_cargo_lock, parse_gemfile_lock, parse_go_sum, parse_gradle_lockfile, parse_package_lock_json, parse_package_resolved, parse_pnpm_lock_yaml, parse_pubspec_lock, parse_uv_lock, pinned_dependencies
from .manifests import REQ_LINE, declared_dependencies, parse_build_gradle, parse_cargo_toml, parse_gemfile, parse_gemspec, parse_go_mod, parse_package_json, parse_package_swift, parse_pubspec_yaml, parse_pyproject_toml, parse_requirements_txt, parse_setup_cfg
from .models import DeclaresDependencyEdge, FileRecord, ImportEdge, ImportExternalEdge, PinsDependencyEdge, TestsEdge
from .pipeline import map_codebase
from .rdf_emit import build_inventory_graph, build_ontology_mapping_graph, build_shacl_graph, file_iri, package_iri, phase_iri, release_iri, type_iri
from .reconstruct import reconstruct, verify_reconstructed, verify_roundtrip
from .self_test import self_test
from .tests_edges import infer_tests_edges

__all__ = [
    'ASSET_EXT',
    'CBM',
    'CBMI',
    'CBMI_NS',
    'CBMP',
    'CBMP_NS',
    'CBMT',
    'CBMT_NS',
    'CBM_NS',
    'DATA_EXT',
    'DEFAULT_PHASES',
    'DeclaresDependencyEdge',
    'FileRecord',
    'ImportEdge',
    'ImportExternalEdge',
    'LANG_BY_EXT',
    'MAN_PAGE_EXTS',
    'PHASE_VOCABULARY',
    'PinsDependencyEdge',
    'REQ_LINE',
    'SH',
    'SPDX_CORE_NS',
    'SPDX_SOFTWARE_NS',
    'TOOL_VERSION',
    'TSJS_EXT_CANDIDATES',
    'TSJS_INDEX_CANDIDATES',
    'TYPE_VOCABULARY',
    'TestsEdge',
    'VOCABULARY_VERSION',
    'build_inventory_graph',
    'build_kotlin_fqn_index',
    'build_ontology_mapping_graph',
    'build_python_module_index',
    'build_shacl_graph',
    'classify',
    'crate_for_file',
    'declared_dependencies',
    'detect_dart_package_name',
    'detect_go_module',
    'detect_python_source_roots',
    'detect_rust_workspaces',
    'detect_swift_modules',
    'emit',
    'emit_blobs',
    'extract_c_ast_summary',
    'extract_dart_ast_summary',
    'extract_go_ast_summary',
    'extract_kotlin_ast_summary',
    'extract_python_ast_summary',
    'extract_ruby_ast_summary',
    'extract_rust_ast_summary',
    'extract_swift_ast_summary',
    'extract_tsjs_ast_summary',
    'file_iri',
    'find_governing_tsconfig',
    'git',
    'git_bytes',
    'go_package_root',
    'infer_tests_edges',
    'language_of',
    'list_tree',
    'load_tsconfigs',
    'main',
    'map_codebase',
    'package_iri',
    'parse_build_gradle',
    'parse_cargo_lock',
    'parse_cargo_toml',
    'parse_gemfile',
    'parse_gemfile_lock',
    'parse_gemspec',
    'parse_go_mod',
    'parse_go_sum',
    'parse_gradle_lockfile',
    'parse_package_json',
    'parse_package_lock_json',
    'parse_package_resolved',
    'parse_package_swift',
    'parse_pnpm_lock_yaml',
    'parse_pubspec_lock',
    'parse_pubspec_yaml',
    'parse_pyproject_toml',
    'parse_requirements_txt',
    'parse_setup_cfg',
    'parse_uv_lock',
    'path_excluded',
    'phase_iri',
    'pinned_dependencies',
    'read_blob',
    'reconstruct',
    'refine_phases',
    'release_iri',
    'resolve_c_includes',
    'resolve_commit',
    'resolve_dart_imports',
    'resolve_go_imports',
    'resolve_kotlin_imports',
    'resolve_python_imports',
    'resolve_ruby_imports',
    'resolve_rust_imports',
    'resolve_swift_imports',
    'resolve_tsjs_import',
    'self_test',
    'tsjs_bare_package_root',
    'type_iri',
    'verify_reconstructed',
    'verify_roundtrip',
]


# Auto-register built-in LanguageAnalyzers and ImportResolvers so the host
# is functional immediately upon import. reset_registries() re-registers
# them after clearing.
from ._builtins import register_builtins as _register_builtins
_register_builtins()

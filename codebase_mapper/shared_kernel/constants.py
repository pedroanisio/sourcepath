"""codebase_mapper.shared_kernel.constants."""
from __future__ import annotations

from rdflib import Namespace


TOOL_VERSION = "0.5.0"

VOCABULARY_VERSION = "v1"

CBM_NS = "https://codebase-mapper.example.org/cbm#"

CBMT_NS = "https://codebase-mapper.example.org/cbm/type#"

CBMP_NS = "https://codebase-mapper.example.org/cbm/phase#"

CBMI_NS = "https://codebase-mapper.example.org/cbm/instance#"

CBMXR_NS = "https://codebase-mapper.example.org/cbmxr#"

CBML4_NS = "https://codebase-mapper.example.org/cbml4#"

SPDX_SOFTWARE_NS = "https://spdx.org/rdf/3.0.1/terms/Software/"

SPDX_CORE_NS = "https://spdx.org/rdf/3.0.1/terms/Core/"

CBM = Namespace(CBM_NS)

CBMT = Namespace(CBMT_NS)

CBMP = Namespace(CBMP_NS)

CBMI = Namespace(CBMI_NS)

CBMXR = Namespace(CBMXR_NS)

CBML4 = Namespace(CBML4_NS)

SH = Namespace("http://www.w3.org/ns/shacl#")

XREF_KINDS = ("calls", "subclassOf", "overrides", "references")

XREF_RESOLUTIONS = ("exact", "heuristic", "ambiguous")

XREF_UNRESOLVED_REASONS = (
    "module_not_in_repo",
    "symbol_not_exported",
    "ambiguous",
    "dynamic_dispatch",
    "language_unsupported",
)

TYPE_VOCABULARY = (
    "source_code", "test_code", "configuration", "documentation",
    "environment", "container", "build_script", "dependency_manifest",
    "lockfile", "ci_cd", "data", "asset", "binary", "generated",
    "license", "unknown",
)

PHASE_VOCABULARY = ("build", "compile", "runtime", "test", "ci", "deploy", "dev")

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript", ".cts": "typescript", ".mts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".coffee": "coffeescript",
    ".rs": "rust",
    ".rb": "ruby", ".rake": "ruby", ".gemspec": "ruby", ".ru": "ruby", ".builder": "ruby",
    ".ruby": "ruby",
    ".erb": "erb", ".tt": "thor-template",
    ".tmpl": "template",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c",
    ".m": "objective-c", ".mm": "objective-cpp",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hxx": "cpp", ".h++": "cpp", ".ipp": "cpp", ".tpp": "cpp",
    ".inl": "cpp",
    ".cs": "csharp", ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".proto": "protobuf",
    ".nix": "nix",
    ".frag": "glsl", ".vert": "glsl", ".comp": "glsl", ".geom": "glsl",
    ".tesc": "glsl", ".tese": "glsl", ".glsl": "glsl",
    ".wgsl": "wgsl", ".hlsl": "hlsl", ".metal": "metal",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".sql": "sql", ".lua": "lua",
}

ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
             ".woff", ".woff2", ".ttf", ".otf", ".eot",
             ".mp3", ".mp4", ".webm", ".wav", ".ogg",
             ".ai", ".psd", ".sketch", ".fig"}

DATA_EXT = {".csv", ".tsv", ".parquet", ".jsonl", ".xml",
            ".stderr", ".stdout",
            ".fish", ".ps1", ".nu", ".elv", ".elvish", ".zsh-completion",
            ".eml", ".log", ".raw", ".mab", ".dtd", ".zoo",
            # TLS / certificate test material
            ".pem", ".crt", ".key", ".der", ".p12", ".pfx", ".csr"}

# Man-page formats and other manpage-like file types.
MAN_PAGE_EXTS = {".roff", ".man", ".groff",
                 ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".9"}

DEFAULT_PHASES = {
    "source_code":         ["runtime"],
    "test_code":           ["test"],
    "configuration":       ["runtime"],
    "documentation":       ["dev"],
    "environment":         ["runtime"],
    "container":           ["build", "deploy"],
    "build_script":        ["build"],
    "dependency_manifest": ["build", "runtime"],
    "lockfile":            ["build"],
    "ci_cd":               ["ci"],
    "data":                ["runtime"],
    "asset":               ["runtime"],
    "binary":              ["runtime"],
    "generated":           ["build"],
    "license":             ["dev"],
    "unknown":             ["runtime"],
}

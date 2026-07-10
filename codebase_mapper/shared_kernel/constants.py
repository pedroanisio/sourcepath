"""codebase_mapper.shared_kernel.constants."""
from __future__ import annotations

from rdflib import Namespace

# Pure (framework-free) namespace strings and controlled vocabularies
# live in vocabulary.py so domain-layer code can import them without
# pulling rdflib; re-exported here for every existing consumer.
from .vocabulary import (  # noqa: F401
    CBM_NS,
    CBMI_NS,
    CBML4_NS,
    CBMP_NS,
    CBMT_NS,
    CBMXR_NS,
    PHASE_VOCABULARY,
    SPDX_CORE_NS,
    SPDX_SOFTWARE_NS,
    TYPE_VOCABULARY,
)


TOOL_VERSION = "0.5.0"

# v2 (semver: additive): cbm:possibleImport edge tier + concept
# embedding_source provenance (error-free-mapping E4/E7).
VOCABULARY_VERSION = "v2"

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
    ".clj": "clojure", ".cljs": "clojure", ".cljc": "clojure", ".cljr": "clojure",
    ".cbl": "cobol", ".cob": "cobol", ".cpy": "cobol", ".cobol": "cobol",
    ".proto": "protobuf",
    ".nix": "nix",
    ".frag": "glsl", ".vert": "glsl", ".comp": "glsl", ".geom": "glsl",
    ".tesc": "glsl", ".tese": "glsl", ".glsl": "glsl",
    ".wgsl": "wgsl", ".hlsl": "hlsl", ".metal": "metal",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".sql": "sql", ".lua": "lua",
    # error-free-mapping E2: the measured unlanguaged families. Language
    # tagging is decoupled from AST support — a correct census needs no
    # parser; asm/devicetree/kconfig/make get line-oriented extractors.
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json",
    ".rst": "restructuredtext",
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text",
    ".s": "asm",  # .S normalizes to .s (suffix lookups lowercase)
    ".dts": "devicetree", ".dtsi": "devicetree", ".dtso": "devicetree",
    ".mk": "make",
    ".cfm": "cfml", ".cfc": "cfml",
}

#: Languages that are data or prose, not executable code. They get a
#: language for census correctness but must not trip code-shaped rules
#: (e.g. a YAML fixture under tests/ is not test_code).
DATA_DOC_LANGUAGES = frozenset(
    {"yaml", "json", "restructuredtext", "markdown", "text"})

ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
             ".woff", ".woff2", ".ttf", ".otf", ".eot",
             ".mp3", ".mp4", ".webm", ".wav", ".ogg",
             ".ai", ".psd", ".sketch", ".fig",
             # Document/print assets: without these a committed logo PDF
             # falls through to the null-byte sniff and reads as opaque
             # "binary" (observed: zod's 8 "binaries" were all logo PDFs).
             ".pdf", ".eps"}

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

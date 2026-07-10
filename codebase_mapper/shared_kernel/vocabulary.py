"""codebase_mapper.shared_kernel.vocabulary — pure vocabulary constants.

Namespace IRI strings and the controlled vocabularies, with no
framework imports. Domain-layer modules (which the import-linter
contract ``domain-ports-avoid-framework-imports`` bars from rdflib)
import these from here; ``constants.py`` re-exports everything and adds
the rdflib ``Namespace`` bindings for infrastructure code.
"""
from __future__ import annotations


CBM_NS = "https://codebase-mapper.example.org/cbm#"

CBMT_NS = "https://codebase-mapper.example.org/cbm/type#"

CBMP_NS = "https://codebase-mapper.example.org/cbm/phase#"

CBMI_NS = "https://codebase-mapper.example.org/cbm/instance#"

CBMXR_NS = "https://codebase-mapper.example.org/cbmxr#"

CBML4_NS = "https://codebase-mapper.example.org/cbml4#"

SPDX_SOFTWARE_NS = "https://spdx.org/rdf/3.0.1/terms/Software/"

SPDX_CORE_NS = "https://spdx.org/rdf/3.0.1/terms/Core/"

TYPE_VOCABULARY = (
    "source_code", "test_code", "configuration", "documentation",
    "environment", "container", "build_script", "dependency_manifest",
    "lockfile", "ci_cd", "data", "asset", "binary", "generated",
    "license", "unknown",
)

PHASE_VOCABULARY = ("build", "compile", "runtime", "test", "ci", "deploy", "dev")

"""Classification rules — role, layer, stability, reusability, risk.

Each function returns a value *and* the Confidence with which it is asserted, so
the honesty of every label is machine-checkable (Part IV). Graph-derived facts
(file type, instability) are ``certain``/``strong``; anything resting on a path
segment or a name is a ``probable`` hypothesis and says so.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .metrics import instability as _instability
from .model import Confidence

# ── entry-point heuristic (kept consistent with the MCP repository_summary) ───
_ENTRY_POINT_BASENAMES: dict[str, str] = {
    "__main__.py": "python_main",
    "main.py": "python_main",
    "cli.py": "python_cli",
    "app.py": "python_app",
    "server.py": "python_app",
    "main.rs": "rust_main",
    "main.go": "go_main",
    "index.js": "js_index",
    "index.ts": "js_index",
    "index.jsx": "js_index",
    "index.tsx": "js_index",
    "index.mjs": "js_index",
}

# Directory-name → architectural layer. This is a *hypothesis* table: a segment
# named "infrastructure" strongly suggests a layer but does not prove one, so
# every hit is reported at PROBABLE confidence.
_LAYER_SEGMENTS: dict[str, str] = {
    "shared_kernel": "shared_kernel", "kernel": "shared_kernel",
    "common": "shared_kernel", "shared": "shared_kernel",
    "domain": "domain", "entities": "domain", "model": "domain", "models": "domain",
    "application": "application", "usecase": "application", "usecases": "application",
    "use_cases": "application", "services": "application",
    "infrastructure": "infrastructure", "infra": "infrastructure",
    "adapters": "adapter", "adapter": "adapter", "transport": "adapter",
    "serving": "adapter", "http": "adapter", "rest": "adapter", "grpc": "adapter",
    "api": "adapter", "backend": "adapter", "frontend": "adapter",
    "mcp_server": "adapter", "ui": "presentation", "web": "presentation",
    "cli": "adapter",
    "plugins": "extension", "plugin": "extension", "extensions": "extension",
    "tests": "test", "test": "test", "__tests__": "test",
}

_INFRA_TYPES = frozenset({
    "build_script", "ci_cd", "container", "environment", "lockfile",
    "dependency_manifest", "configuration",
})
_INFRA_PHASES = frozenset({"build", "ci", "deploy", "compile"})


def entrypoint_kind(path: str, file_type: str | None) -> str | None:
    """Return a ``<language>_<role>`` entry-point tag, or None.

    Mirrors ``frontend/mcp_server/handlers.py:_entry_point_kind`` so the
    decomposer and the live MCP surface agree on what counts as an entry point.
    """
    p = PurePosixPath(path)
    name, parts, suffix = p.name, p.parts, p.suffix
    kind = _ENTRY_POINT_BASENAMES.get(name)
    if kind is not None:
        if file_type == "source_code":
            return kind
        if name == "__main__.py":
            return kind
        return None
    if "bin" in parts and suffix == ".rs":
        return "rust_bin"
    if "bin" in parts and not suffix and file_type == "source_code":
        return "shell_bin"
    return None


def layer_of(path: str) -> tuple[str | None, Confidence]:
    """Guess an architectural layer from directory naming (a hypothesis).

    Scans path segments *outermost→innermost* and returns the first match, so a
    file under ``frontend/backend/serving/application`` reports the outer
    ``adapter`` boundary it sits behind rather than its inner concern. Returns
    ``(None, UNKNOWN)`` when nothing matches.
    """
    for seg in PurePosixPath(path).parts[:-1]:
        hit = _LAYER_SEGMENTS.get(seg)
        if hit:
            return hit, Confidence.PROBABLE
    return None, Confidence.UNKNOWN


def file_role(file_rec: dict[str, Any], phases: list[str]) -> tuple[str, Confidence]:
    """Classify a single file's role with confidence.

    Ordered so the strongest evidence wins: file *type* (test/generated) is a
    deterministic extractor output → ``certain``; infra by type or build phase →
    ``strong``; adapter/core by naming → ``probable``.
    """
    ftype = file_rec.get("type")
    if ftype == "test_code":
        return "test", Confidence.CERTAIN
    if ftype == "generated":
        return "generated", Confidence.CERTAIN
    if ftype in _INFRA_TYPES:
        return "infrastructure", Confidence.STRONG
    if phases and set(phases) & _INFRA_PHASES and "runtime" not in phases:
        return "infrastructure", Confidence.STRONG
    layer, _ = layer_of(file_rec.get("path", ""))
    if layer in {"adapter", "presentation"}:
        return "adapter", Confidence.PROBABLE
    if layer == "infrastructure":
        return "infrastructure", Confidence.PROBABLE
    if ftype == "source_code":
        return "core", Confidence.WEAK   # provisional; refined by centrality upstream
    return "supporting", Confidence.PROBABLE


def classify_stability(ca: int, ce: int) -> tuple[float | None, float | None, Confidence]:
    """Instability I and stability 1−I (Martin). The numbers are ``certain``;
    an isolated node yields ``(None, None, UNKNOWN)``."""
    i = _instability(ca, ce)
    if i is None:
        return None, None, Confidence.UNKNOWN
    return round(i, 3), round(1.0 - i, 3), Confidence.CERTAIN


def reusability(role: str, ca: int, ce: int, name: str) -> str:
    """Reusability class from coupling shape.

    High fan-in with low fan-out is the signature of a reusable/shared component;
    the reverse (or being test/generated) marks it replaceable/internal.
    """
    if role in {"test", "generated"}:
        return "replaceable"
    seg = name.lower()
    if any(k in seg for k in ("shared_kernel", "kernel", "common", "util", "constants")) and ca >= 3:
        return "reusable"
    if ca >= 3 and ce <= max(1, ca // 3):
        return "reusable"
    if ca == 0:
        return "replaceable"
    return "internal"


def assess_risk(
    *, in_cycle: bool, is_god: bool, sdp_violation: bool, high_fanin_unstable: bool
) -> tuple[str, list[str], Confidence]:
    """Combine risk signals into a level + reasons + confidence.

    Cycle participation is ``certain`` (graph fact); god-module and Stable-
    Dependencies-Principle violations are ``strong`` (thresholded metrics).
    """
    reasons: list[str] = []
    conf = Confidence.STRONG
    if in_cycle:
        reasons.append("participates in an import cycle")
        conf = Confidence.CERTAIN
    if is_god:
        reasons.append("god-module: high fan-in and high fan-out")
    if sdp_violation:
        reasons.append("depends on a less-stable component (SDP violation)")
    if high_fanin_unstable:
        reasons.append("high fan-in on an unstable component")
    if not reasons:
        return "low", [], Confidence.STRONG
    level = "high" if (in_cycle or is_god) else "elevated"
    return level, reasons, conf


def module_role(
    file_roles: list[tuple[str, Confidence]],
    layer: str | None,
    ca: int,
    ce: int,
    is_runtime: bool,
) -> tuple[str, Confidence]:
    """Aggregate a module's role from its files' roles plus its coupling shape.

    Homogeneous test/generated modules inherit that role at ``certain``. Mixed
    code modules resolve by layer hypothesis, then by the classic "stable core"
    shape (high Ca, low Ce, runtime) → ``core``.
    """
    code_roles = [r for r, _ in file_roles]
    if code_roles and all(r == "test" for r in code_roles):
        return "test", Confidence.CERTAIN
    if code_roles and all(r == "generated" for r in code_roles):
        return "generated", Confidence.CERTAIN
    if layer == "extension":
        return "adapter", Confidence.PROBABLE
    if layer in {"adapter", "presentation"}:
        return "adapter", Confidence.PROBABLE
    if layer == "infrastructure":
        return "infrastructure", Confidence.STRONG
    # Stable-core signature: depended upon far more than it depends outward.
    if is_runtime and ca >= 3 and ce <= max(1, ca):
        if ce == 0 or ca >= 2 * ce:
            return "core", Confidence.STRONG
        return "core", Confidence.PROBABLE
    if layer == "shared_kernel":
        return "core", Confidence.STRONG
    if code_roles and "infrastructure" in code_roles and \
            code_roles.count("infrastructure") > len(code_roles) // 2:
        return "infrastructure", Confidence.PROBABLE
    return "supporting", Confidence.PROBABLE

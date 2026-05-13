"""Server-side validators and error type for MCP tool handlers.

These run *after* the JSON Schema input check (which the handler decorator
invokes first). They reject domain-level invariants the schema can't
express cleanly across all validator implementations: path traversal,
bundle name shape, sha hex purity, etc.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath


class ToolError(Exception):
    """Domain error raised by handlers. Carries a stable ``code`` so the
    transport layer (Phase 3) can map it to MCP's JSON-RPC error shape
    without string-matching the message.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover — used only in debugging
        return f"ToolError(code={self.code!r}, message={self.message!r})"


# Standard codes — keep this list short and stable.
NOT_FOUND = "not_found"
INVALID_ARGUMENT = "invalid_argument"
TOO_LARGE = "too_large"
INTERNAL = "internal_error"


def validate_bundle_name(name: str) -> None:
    """Reject names containing path-traversal characters or shapes."""
    if not name:
        raise ToolError(INVALID_ARGUMENT, "bundle name is empty")
    if "/" in name or "\\" in name:
        raise ToolError(INVALID_ARGUMENT, f"bundle name must not contain separators: {name!r}")
    if ".." in name:
        raise ToolError(INVALID_ARGUMENT, f"bundle name must not contain '..': {name!r}")
    if name.startswith("."):
        raise ToolError(INVALID_ARGUMENT, f"bundle name must not start with '.': {name!r}")


def validate_relative_path(path: str) -> str:
    """Normalize and reject paths that escape the bundle.

    Returns the normalized POSIX path on success; raises ToolError otherwise.
    """
    if not path:
        raise ToolError(INVALID_ARGUMENT, "path is empty")
    if path.startswith("/") or path.startswith("\\"):
        raise ToolError(INVALID_ARGUMENT, f"path must be relative: {path!r}")
    if os.path.isabs(path):  # catches Windows drive letters too
        raise ToolError(INVALID_ARGUMENT, f"path must be relative: {path!r}")
    # PurePosixPath collapses ./ but keeps .. — check after normalization
    norm = PurePosixPath(path).as_posix()
    if any(part == ".." for part in PurePosixPath(norm).parts):
        raise ToolError(INVALID_ARGUMENT, f"path traversal not allowed: {path!r}")
    return norm


_HEX = set("0123456789abcdef")


def validate_sha256(sha: str) -> None:
    if len(sha) != 64 or any(c not in _HEX for c in sha):
        raise ToolError(INVALID_ARGUMENT, f"sha must be 64 lowercase hex chars: {sha!r}")


def truncate_text(text: str | None, max_bytes: int = 2048) -> tuple[str | None, bool]:
    """Truncate UTF-8 text to at most ``max_bytes`` bytes; return (text, was_truncated).

    Cuts on a UTF-8 character boundary so we never split a multi-byte glyph.
    """
    if text is None:
        return None, False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    # back off to the last whole UTF-8 char boundary
    truncated = encoded[:max_bytes]
    for back in range(min(4, len(truncated))):
        try:
            return truncated[: len(truncated) - back].decode("utf-8"), True
        except UnicodeDecodeError:
            continue
    return truncated.decode("utf-8", errors="replace"), True

"""Deployment settings — .env autoload, canonical directories, report naming.

Single source of truth for three deployment concerns that were previously
scattered or hardcoded:

- **``.env`` autoload** (:func:`load_env`): entry points (``cbm`` CLI,
  backend, MCP server, report scripts) load a ``KEY=VALUE`` file discovered
  by walking upward from the CWD. Values NEVER override the real process
  environment unless ``override=True`` — deployment env always wins. The
  committed inventory of every variable is ``.env.example`` (enforced by
  ``tests/verify_drift_p1.py``).
- **Canonical directories**: :func:`bundles_root` (``CBM_BUNDLES_ROOT``,
  default ``_tmp`` — the literal lives HERE and nowhere else; a grep guard
  in ``tests/test_env_settings.py`` fails any re-hardcoding) and
  :func:`reports_dir` (``CBM_REPORTS_DIR``, default ``reports``).
- **Report naming**: ``<source>__<kind>__<YYYYMMDDTHHMMSSZ>[.ext]`` via
  :func:`report_stem` / :func:`default_report_path`. Source and kind are
  slugged (lowercase, ``[a-z0-9._-]``), the timestamp is UTC at second
  resolution, and ``__`` is reserved as the field separator so filenames
  parse back unambiguously. :func:`default_report_path` bumps a ``-N``
  suffix instead of ever colliding with an earlier run.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BUNDLES_ROOT = "_tmp"
DEFAULT_REPORTS_DIR = "reports"
REPORT_STEM_SEP = "__"
REPORT_TS_FORMAT = "%Y%m%dT%H%M%SZ"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SLUG_KEEP_RE = re.compile(r"[^a-z0-9._-]+")


# --------------------------------------------------------------- .env load

def find_env_file(start: Path | None = None) -> Path | None:
    """First ``.env`` found walking upward from ``start`` (default: CWD).

    The walk stops at the first repository boundary (a directory containing
    ``.git``, checked after that directory itself): a ``.env`` sitting in a
    PARENT of the repo is somebody else's configuration and must not leak in.
    """
    node = (start or Path.cwd()).resolve()
    for candidate in (node, *node.parents):
        env = candidate / ".env"
        if env.is_file():
            return env
        if (candidate / ".git").exists():
            return None
    return None


def load_env(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load a dotenv file into ``os.environ``; return the values applied.

    ``path=None`` discovers the file via :func:`find_env_file`; no file
    found is a silent no-op. An explicit ``path`` that does not exist
    raises ``FileNotFoundError`` (the caller asserted it is there).
    Keys already present in the environment are skipped unless
    ``override=True``. Malformed non-comment lines raise ``ValueError``
    with the line number — a config file that half-loads is worse than
    one that fails loudly.
    """
    if path is None:
        found = find_env_file()
        if found is None:
            return {}
        path = found
    if not Path(path).is_file():
        raise FileNotFoundError(f".env file not found: {path}")

    applied: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}: line {lineno} is not KEY=VALUE: {raw!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key.endswith("_*"):
            # Wildcard family entry (e.g. CBM_MCP_TIMEOUT_*=): documents a
            # dynamically-named group for the env-inventory drift guard;
            # never a loadable variable.
            continue
        if not _KEY_RE.match(key):
            raise ValueError(f"{path}: line {lineno} has invalid key {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            # `KEY=` is a placeholder (an .env copied from .env.example),
            # not a value: exporting "" would make every consumer see the
            # variable as set-but-empty and silently disable its documented
            # unset-fallback (e.g. CORS default origins, CBM_UNSHALLOW).
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


# ------------------------------------------------------ canonical folders

def bundles_root() -> Path:
    """Root directory holding persisted bundles (one subdir per bundle)."""
    return Path(os.environ.get("CBM_BUNDLES_ROOT", DEFAULT_BUNDLES_ROOT)).resolve()


def reports_dir(*, ensure: bool = False) -> Path:
    """Directory where generated reports land; created when ``ensure``."""
    d = Path(os.environ.get("CBM_REPORTS_DIR", DEFAULT_REPORTS_DIR)).resolve()
    if ensure:
        d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------- report naming

def report_slug(text: str) -> str:
    """Filename-safe field: lowercase, ``[a-z0-9._-]``, no ``__`` runs."""
    slug = _SLUG_KEEP_RE.sub("-", text.strip().lower()).strip("-._")
    slug = re.sub(r"_{2,}", "-", slug)
    if not slug:
        raise ValueError(f"cannot derive a report name field from {text!r}")
    return slug


def report_stem(source: str, kind: str, when: datetime | None = None) -> str:
    """``<source>__<kind>__<YYYYMMDDTHHMMSSZ>`` (UTC, second resolution)."""
    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("report_stem requires a timezone-aware datetime")
    ts = when.astimezone(timezone.utc).strftime(REPORT_TS_FORMAT)
    return REPORT_STEM_SEP.join((report_slug(source), report_slug(kind), ts))


def default_report_path(
    source: str,
    kind: str,
    ext: str = "",
    when: datetime | None = None,
    *,
    ensure: bool = True,
) -> Path:
    """Standardized output path under :func:`reports_dir`.

    ``ext`` is appended when given (``"pdf"`` → ``….pdf``); an empty ext
    returns a bare stem for tools that fan out to several suffixes. If a
    previous run already produced files off the same stem, a ``-2``/``-3``
    suffix is bumped in — two runs never overwrite each other.
    """
    root = reports_dir(ensure=ensure)
    base = report_stem(source, kind, when)
    stem, n = base, 1
    while any(root.glob(stem + "*")):
        n += 1
        stem = f"{base}-{n}"
    name = f"{stem}.{ext.lstrip('.')}" if ext else stem
    return root / name

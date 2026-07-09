#!/usr/bin/env python3
"""pack_clean_zip.py — package a clean source zip from a NUL-delimited file list.

Reads NUL-separated paths on stdin (as produced by
`git ls-files --cached --others --exclude-standard -z`) and writes a
deflate-compressed zip. Every entry is nested under a single top-level
directory so the archive expands cleanly into one folder.

Used by the Makefile `dist-zip` target. Kept as a standalone script because
the `zip(1)` binary is not guaranteed to be present and git's built-in zip
support (`git archive`) only sees committed files — this preserves the
"tracked + new, .gitignore-honored" selection while staying dependency-free.

Usage:
    git ls-files --cached --others --exclude-standard -z \
        | python3 scripts/pack_clean_zip.py <output.zip> <top-level-dir>
"""
from __future__ import annotations

import os
import sys
import zipfile


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: pack_clean_zip.py <output.zip> <top-level-dir>",
              file=sys.stderr)
        return 2

    out_path, root = argv
    raw = sys.stdin.buffer.read()
    names = [chunk.decode("utf-8") for chunk in raw.split(b"\x00") if chunk]

    written = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for name in names:
            # Skip directories and dangling entries (deleted-but-listed, symlinks
            # to nowhere). Only real files land in the archive.
            if not os.path.isfile(name):
                continue
            zf.write(name, os.path.join(root, name))
            written += 1

    print(f"wrote {out_path} ({written} files, "
          f"{os.path.getsize(out_path)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

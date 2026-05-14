#!/usr/bin/env python3
"""verify_roundtrip.py — locks the blob-based byte-perfect roundtrip contract.

Fixture mixes Python source, TypeScript, markdown, a zero-byte file, a
binary asset (non-UTF8 bytes), and a duplicate of one of the text files
(to exercise content-SHA dedup in the blob store).

Tests:
  1. verify_roundtrip(repo) returns roundtrip_ok=True; every inventoried
     file is written and SHA-equal; no missing/extra/mismatching paths.
  2. Blob store deduplicates by content_sha256 (duplicate files share a blob).
  3. Binary and zero-byte files are inventoried and roundtrip byte-equal.
  4. Mutation: corrupting one blob byte makes verify_reconstructed report
     a SHA mismatch for the corresponding path.
  5. Mutation: deleting a blob makes reconstruct report missing_blob_count > 0.
  6. Mutation: an unexpected file in out_dir is flagged via extras_count.
  7. CLI parity: `python -m codebase_mapper --verify-roundtrip --repo F`
     exits 0 and prints a report with roundtrip_ok=true.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import URIRef

from codebase_mapper.emission.infrastructure.storage.filesystem_blob_store import emit_blobs
from codebase_mapper.shared_kernel.constants import CBMI_NS
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import build_inventory_graph
from codebase_mapper.emission.application.reconstruct import (
    reconstruct,
    verify_reconstructed,
    verify_roundtrip,
)


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:10]:
                print(f"        {line}")
        FAIL += 1


TS_SRC = (
    'import { readFile } from "fs/promises";\n'
    "\n"
    "export async function loadConfig<T>(path: string): Promise<T> {\n"
    '    const raw = await readFile(path, "utf-8");\n'
    "    return JSON.parse(raw) as T;\n"
    "}\n"
)

BINARY_BYTES = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03\xff\xfe\xfd"


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)

    (target / "app.py").write_text(
        '"""Sample app."""\n'
        "import json\n"
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class User:\n"
        "    name: str\n"
        "    authenticated: bool = False\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        '    print(json.dumps({"hi": "there"}))\n'
    )

    (target / "utils.ts").write_text(TS_SRC)
    # Duplicate content -> same content_sha256 -> single blob in the store.
    (target / "utils_copy.ts").write_text(TS_SRC)
    (target / "README.md").write_text("# Roundtrip fixture\n\nSee app.py.\n")
    (target / "empty.txt").write_text("")
    (target / "asset.bin").write_bytes(BINARY_BYTES)

    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True
    )


def emit_inventory_and_blobs(repo: Path, out_dir: Path) -> dict:
    """Minimal emit: inventory.ttl + blobs/ only (no SHACL/manifest/plugins)."""
    mapped = map_codebase(repo, "HEAD")
    repo_iri = URIRef(f"{CBMI_NS}repo/{repo.name}")
    inv = build_inventory_graph(
        repo_iri=repo_iri,
        commit_sha=mapped["commit"],
        records=mapped["records"],
        import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"],
        pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
    )
    inv.serialize(destination=str(out_dir / "inventory.ttl"), format="turtle")
    emit_blobs(mapped["records"], repo, mapped["blob_by_path"], out_dir / "blobs")
    return mapped


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true", help="don't delete the workdir on exit")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_roundtrip_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        repo_root = Path(__file__).resolve().parent.parent

        # --- 1. End-to-end roundtrip via the public API ---
        report = verify_roundtrip(fixture.resolve(), "HEAD", [])
        check(
            "verify_roundtrip: roundtrip_ok=True",
            report.get("roundtrip_ok") is True,
            json.dumps(report, indent=2, sort_keys=True),
        )
        recon = report["reconstruction"]
        check(
            "verify_roundtrip: all inventoried files written",
            recon["files_written"] == recon["files_in_inventory"] and recon["files_in_inventory"] > 0,
            f"written={recon['files_written']} inv={recon['files_in_inventory']}",
        )
        check(
            "verify_roundtrip: no missing blobs",
            recon["missing_blob_count"] == 0,
            f"missing={recon['missing_blob_count']}",
        )
        ver = report["verification"]
        check(
            "verify_roundtrip: verification.ok=True (no mismatch/missing/extras)",
            ver["ok"] and not ver["mismatches"] and ver["missing_count"] == 0 and ver["extras_count"] == 0,
            json.dumps(ver, indent=2, sort_keys=True),
        )

        # --- 2. Manual emit for inspecting + mutating the artifacts ---
        out = work / "manual_emit"
        out.mkdir()
        mapped = emit_inventory_and_blobs(fixture.resolve(), out)
        records = mapped["records"]
        records_by_path = {r.path: r for r in records}
        unique_shas = {r.content_sha256 for r in records}
        blob_files = sorted(p.name for p in (out / "blobs").iterdir())

        check(
            "blob store deduplicates by content_sha256",
            sorted(blob_files) == sorted(unique_shas) and len(records) > len(unique_shas),
            f"records={len(records)} unique={len(unique_shas)} blobs={len(blob_files)}",
        )

        # --- 3. Binary + zero-byte files are inventoried and roundtrip ---
        check(
            "binary file inventoried",
            "asset.bin" in records_by_path
            and records_by_path["asset.bin"].content_sha256
                == hashlib.sha256(BINARY_BYTES).hexdigest(),
            f"recorded={records_by_path.get('asset.bin')}",
        )
        check(
            "zero-byte file inventoried with size 0",
            "empty.txt" in records_by_path
            and records_by_path["empty.txt"].size_bytes == 0
            and records_by_path["empty.txt"].content_sha256
                == hashlib.sha256(b"").hexdigest(),
            f"recorded={records_by_path.get('empty.txt')}",
        )

        recon_clean = work / "recon_clean"
        recon_clean.mkdir()
        reconstruct(out / "inventory.ttl", out / "blobs", recon_clean)
        check(
            "binary file roundtrips byte-equal",
            (recon_clean / "asset.bin").read_bytes() == BINARY_BYTES,
            "",
        )
        check(
            "zero-byte file roundtrips byte-equal",
            (recon_clean / "empty.txt").read_bytes() == b"",
            "",
        )

        # --- 4. Mutation: corrupted blob -> SHA mismatch ---
        target_record = records_by_path["app.py"]
        blob_path = out / "blobs" / target_record.content_sha256
        original_bytes = blob_path.read_bytes()
        blob_path.write_bytes(original_bytes[:-1] + bytes([(original_bytes[-1] + 1) % 256]))

        recon_corrupt = work / "recon_corrupt"
        recon_corrupt.mkdir()
        reconstruct(out / "inventory.ttl", out / "blobs", recon_corrupt)
        ver_c = verify_reconstructed(
            recon_corrupt, {r.path: r.content_sha256 for r in records}
        )
        check(
            "mutation: corrupted blob produces SHA mismatch for affected path",
            (not ver_c["ok"])
            and len(ver_c["mismatches"]) > 0
            and any(m[0] == "app.py" for m in ver_c["mismatches"]),
            json.dumps(ver_c, indent=2, sort_keys=True),
        )
        blob_path.write_bytes(original_bytes)  # restore

        # --- 5. Mutation: deleted blob -> missing_blob_count > 0 ---
        blob_path.unlink()
        recon_missing = work / "recon_missing"
        recon_missing.mkdir()
        recon_report = reconstruct(out / "inventory.ttl", out / "blobs", recon_missing)
        check(
            "mutation: deleted blob is reported as missing",
            recon_report["missing_blob_count"] > 0
            and target_record.content_sha256 in recon_report["missing_blobs"],
            json.dumps(recon_report, indent=2, sort_keys=True),
        )
        blob_path.write_bytes(original_bytes)  # restore

        # --- 6. Mutation: extra file in out_dir flagged ---
        recon_extra = work / "recon_extra"
        recon_extra.mkdir()
        reconstruct(out / "inventory.ttl", out / "blobs", recon_extra)
        (recon_extra / "stray.txt").write_text("not in inventory")
        ver_e = verify_reconstructed(
            recon_extra, {r.path: r.content_sha256 for r in records}
        )
        check(
            "mutation: extra file in out_dir flagged via extras_count",
            ver_e["extras_count"] > 0 and "stray.txt" in ver_e["extras_sample"],
            json.dumps(ver_e, indent=2, sort_keys=True),
        )

        # --- 7. CLI parity ---
        cli = subprocess.run(
            [
                sys.executable, "-m", "codebase_mapper",
                "--verify-roundtrip", "--repo", str(fixture),
            ],
            env={**os.environ, "PYTHONPATH": str(repo_root)},
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        cli_report: dict = {}
        try:
            cli_report = json.loads(cli.stdout)
        except Exception:
            pass
        check(
            "CLI: --verify-roundtrip exits 0 and reports roundtrip_ok=true",
            cli.returncode == 0 and cli_report.get("roundtrip_ok") is True,
            f"rc={cli.returncode}\nstdout_tail={cli.stdout[-800:]}\nstderr_tail={cli.stderr[-800:]}",
        )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

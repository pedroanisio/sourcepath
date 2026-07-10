"""Asset/binary clarity in reporting + the image gallery.

Observed on the zod bundle: all 8 files typed ``binary`` were logo PDFs
(.pdf missing from ASSET_EXT), and the report's language chart lumps
every non-code file into "(none)" with no further breakdown — while the
verified blob store already holds every image content-addressed, which
makes a gallery essentially free.

Pinned here:

- ``.pdf`` / ``.eps`` classify as ``asset``, leaving ``binary`` to mean
  only "unknown file with null bytes";
- ``asset_kind()`` groups asset extensions into human kinds (image /
  font / audio-video / design / document);
- ``image_gallery_items()`` builds data-URI gallery entries from the
  blob store with *disclosed* caps — omitted images are counted, never
  silently dropped (PALS's Law);
- the HTML gallery section embeds images inline (self-contained report)
  and states the omission count.

Run from the repo root:  python -m pytest tests/test_report_assets.py
"""
from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
import cbm_report as CR  # noqa: E402

from codebase_mapper.inspection.classify import classify  # noqa: E402

_PDF_HEAD = b"%PDF-1.7\n\x00\x01binarybytes"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
    "YGBgAAAABQABh6FO1AAAAABJRU5ErkJggg==")  # 1x1 px


# ---------------------------------------------------------------------------
# classifier: pdf/eps are assets, not opaque binaries
# ---------------------------------------------------------------------------


def test_pdf_and_eps_classify_as_asset():
    assert classify("logo/Logo.pdf", _PDF_HEAD) == "asset"
    assert classify("art/print.eps", b"%!PS-Adobe-3.0 EPSF-3.0\x00") == "asset"


def test_true_unknown_binary_still_binary():
    assert classify("mystery.bin", b"\x00\x01\x02\x03") == "binary"


# ---------------------------------------------------------------------------
# asset kinds
# ---------------------------------------------------------------------------


def test_asset_kind_groups():
    assert CR.asset_kind("logo.png") == "image"
    assert CR.asset_kind("a/b.woff2") == "font"
    assert CR.asset_kind("intro.mp4") == "audio/video"
    assert CR.asset_kind("brand.ai") == "design"
    assert CR.asset_kind("Logo.pdf") == "document"
    assert CR.asset_kind("whatever.xyz") == "other"


# ---------------------------------------------------------------------------
# gallery items from the blob store
# ---------------------------------------------------------------------------


def _blobs(tmp_path, entries):
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    triples = []
    for i, (path, data) in enumerate(entries):
        sha = f"{i:064x}"
        (blobs / sha).write_bytes(data)
        triples.append((path, sha, len(data)))
    return str(blobs), triples


def test_gallery_items_build_data_uris(tmp_path):
    blobs_dir, files = _blobs(tmp_path, [
        ("logo/logo.png", _PNG),
        ("docs/icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("README.md", b"# not an image"),
    ])
    items, omitted = CR.image_gallery_items(files, blobs_dir)
    assert [it["path"] for it in items] == ["docs/icon.svg", "logo/logo.png"]
    assert omitted == 0
    png = next(it for it in items if it["path"].endswith(".png"))
    assert png["data_uri"].startswith("data:image/png;base64,")
    assert base64.b64decode(png["data_uri"].split(",", 1)[1]) == _PNG
    svg = next(it for it in items if it["path"].endswith(".svg"))
    assert svg["data_uri"].startswith("data:image/svg+xml;base64,")


def test_gallery_caps_are_disclosed_not_silent(tmp_path):
    blobs_dir, files = _blobs(tmp_path, [
        (f"img/{i}.png", _PNG) for i in range(5)
    ] + [("img/huge.png", _PNG * 100_000)])  # ~7 MB, over per-image cap
    items, omitted = CR.image_gallery_items(files, blobs_dir, limit=3)
    assert len(items) == 3
    assert omitted == 3  # 2 over the count limit + 1 over the byte cap


def test_gallery_missing_blob_is_omitted_and_counted(tmp_path):
    blobs_dir, files = _blobs(tmp_path, [("a.png", _PNG)])
    files.append(("ghost.png", "f" * 64, 10))  # sha not in blob store
    items, omitted = CR.image_gallery_items(files, blobs_dir)
    assert [it["path"] for it in items] == ["a.png"]
    assert omitted == 1


# ---------------------------------------------------------------------------
# HTML section
# ---------------------------------------------------------------------------


def test_assets_section_html_embeds_and_discloses(tmp_path):
    blobs_dir, files = _blobs(tmp_path, [("logo/logo.png", _PNG)])
    items, _ = CR.image_gallery_items(files, blobs_dir)
    html = CR.gallery_html(items, omitted=2)
    assert "data:image/png;base64," in html
    assert "logo/logo.png" in html
    assert "2 image(s) omitted" in html


def test_gallery_html_empty_states_absence():
    html = CR.gallery_html([], omitted=0)
    assert "no image assets" in html.lower()

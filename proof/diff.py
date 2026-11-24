"""Pixel diff and thumbnails of proof screenshots, with Pillow (a core Hermes dependency).

A pure-Python diff took 7 to 61 s per pair and would run in the shared gateway; Pillow does the
same pair in about a second. Pillow is imported inside the functions, so the module loads where it
is missing (the plugin's test venv) and only these two calls need it.
"""
from __future__ import annotations

import io
from pathlib import Path


def diff_png(a: Path, b: Path, threshold: int = 24) -> dict:
    """Share of pixels whose strongest channel differs by more than `threshold` (0-255).

    Images of different sizes are compared on the larger canvas, padded with white, so a page that
    grew or shrank counts the added or removed area as changed.
    """
    from PIL import Image, ImageChops

    with Image.open(a) as ia, Image.open(b) as ib:
        ra, rb = ia.convert("RGB"), ib.convert("RGB")
    width, height = max(ra.width, rb.width), max(ra.height, rb.height)
    if ra.size != (width, height):
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(ra, (0, 0))
        ra = canvas
    if rb.size != (width, height):
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(rb, (0, 0))
        rb = canvas
    r, g, bl = ImageChops.difference(ra, rb).split()
    strongest = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    limit = max(0, min(255, int(threshold)))
    mask = strongest.point(lambda v: 255 if v > limit else 0)
    changed = mask.histogram()[255]
    total = width * height
    pct = round(changed * 100.0 / total, 3) if total else 0.0
    return {"pct": pct, "changed": changed, "total": total, "size": [width, height]}


def thumbnail(png: Path, width: int = 360) -> bytes:
    """The top of a screenshot scaled to `width`, as JPEG bytes (about 20 KB for a page)."""
    from PIL import Image

    with Image.open(png) as im:
        rgb = im.convert("RGB")
    width = max(16, int(width))
    scale = width / rgb.width
    height = max(1, round(rgb.height * scale))
    small = rgb.resize((width, height), Image.LANCZOS)
    small = small.crop((0, 0, width, min(height, round(width * 1.25))))
    out = io.BytesIO()
    small.save(out, format="JPEG", quality=72, optimize=True)
    return out.getvalue()

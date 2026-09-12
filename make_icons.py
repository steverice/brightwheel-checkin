#!/usr/bin/env python3
"""Draw the site's icons.

    python3 make_icons.py

Writes three files into `docs/`:

  icon.svg              the ring mark, what a current browser uses, and what a
                        retina tab actually renders at (32 device pixels, not 16)
  favicon.ico           16/32/48 on the dark tile, for the fallback path and for
                        the bare `/favicon.ico` a browser requests unprompted —
                        the ring at 32 and 48, dropped at 16
  apple-touch-icon.png  180x180, the ring mark in its dark palette, for a page
                        saved to an iPhone home screen — which this site
                        invites, since it hands out iOS shortcuts

**Two marks, because one does not survive every size.** The ring nods to
Brightwheel's own eight-capsule logo, so the icon rhymes with the app the
shortcuts drive, and at 32px and up it is the better mark by a distance. At a
true 16 pixels the capsules shrink below a pixel each and read as speckle around
the check — measured by rendering it and looking, not assumed. So the 16px entry
drops the ring and keeps the check alone, the shape that survives the shrink,
while every larger size keeps it. Icon sets normally simplify at the smallest
size; this is that. It is one mark in one palette throughout, so the small entry
reads as the same icon with a detail removed, not as a second design.

The ring is drawn in this page's indigo rather than Brightwheel's rainbow, and
that is deliberate: this is an unofficial tool, the shortcuts it hands out ask a
parent for their Brightwheel password, and a tab icon is the one place someone
looks to check they are where they think they are. Rhyming with the brand is
friendly. Wearing it would be a claim this site has no right to make.

**The ring mark carries no background and adapts to the tab strip.** A paper
tile is invisible on a light strip and, on a dark one, turns the icon into a
bright box with a small check crowded inside it — worse than no tile at all. So
`icon.svg` is groundless and swaps its palette under `prefers-color-scheme`. Not
just the check: the ring is a light-to-dark ramp, and its dark end disappears
into a dark strip along with the check, so the whole ramp shifts. `favicon.ico`
cannot carry a media query, which is why it alone keeps a solid tile — it has to
be legible against a background it cannot know. That tile is ink, the same dark
palette the touch icon uses and the same one the SVG shows in dark mode, so the
fallback is the same mark rather than a second colorway; and on a dark strip its
edge simply vanishes into the strip, leaving the groundless look anyway.

**Everything is rasterized from the SVG by `rsvg-convert`**, at each target size
rather than by downscaling one large master. Rendering at the target lets cairo
compute exact pixel coverage, which is visibly cleaner at 16px than a
supersampled downscale; and having a single renderer means the vector and the
rasters cannot drift apart, so there is nothing to keep in sync and no parity
check to run. Needs `librsvg` — `brew install librsvg`. The rendered files are
committed, so nothing in the build depends on it.
"""

from __future__ import annotations

import colorsys
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from console import info

REPO = Path(__file__).resolve().parent
DOCS = REPO / "docs"

BRAND = "#4f52d6"  # --brand, straight from the page's :root
PAPER = "#f5f6fb"  # --paper
INK = "#1f1c46"  # --ink

BOX = 64  # viewBox for both marks

# --- the ring mark -------------------------------------------------------
COUNT = 8
RING_R = 24.0
CAP_L, CAP_W = 15.4, 6.7
CAP_TILT = 20  # degrees clockwise
RING_CHECK = [(11.0, 33.0), (26.0, 48.0), (54.5, 15.0)]
RING_CHECK_W = 15.0
# Drawn at full size the check fouls the ring: its left cap sits at 177°, against
# the spoke at 180°, and its right cap at 323° against the one at 315°, so both
# ends merge into a spoke instead of crossing it. Pulling the check in toward the
# center opens a gap at each end. It cannot clear the ring altogether — the inner
# void is only ~13 across — and it should not try, because the stroke is the only
# part of this mark that still reads at 32px.
RING_CHECK_SCALE = 0.82

# --- the plain mark, for 16px ---------------------------------------------
TILE_RADIUS = 14
TILE_CHECK = [(17.0, 33.0), (26.0, 42.0), (47.0, 21.0)]
TILE_CHECK_W = 8.0
TILE_INSET = 0.78  # shrink the ring group so it clears the tile's corners


def ramp(lo: float, hi: float) -> list[str]:
    """A light-to-dark scale of the brand hue, standing in for the rainbow."""
    r, g, b = (int(BRAND[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    return [
        "#{:02x}{:02x}{:02x}".format(
            *(int(v * 255) for v in colorsys.hls_to_rgb(h, lo + (i / (COUNT - 1)) * (hi - lo), s))
        )
        for i in range(COUNT)
    ]


LIGHT_RAMP = ramp(0.34, 0.68)  # on a light strip
DARK_RAMP = ramp(0.55, 0.88)  # on a dark one, shifted up to stay visible


def capsule(i: int) -> tuple[float, float, float]:
    """Centre and rotation for one capsule of the ring."""
    a = math.radians(i * 360 / COUNT - 90)
    return (BOX / 2 + RING_R * math.cos(a), BOX / 2 + RING_R * math.sin(a), i * 360 / COUNT + CAP_TILT)


def path_of(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{'ML'[n > 0]}{x:.2f} {y:.2f}" for n, (x, y) in enumerate(points))


def ring_check() -> tuple[list[tuple[float, float]], float]:
    """The ring mark's check, pulled in so its caps clear the spokes."""
    c = BOX / 2
    return (
        [(c + (x - c) * RING_CHECK_SCALE, c + (y - c) * RING_CHECK_SCALE) for x, y in RING_CHECK],
        RING_CHECK_W * RING_CHECK_SCALE,
    )


def ring_svg(themed: bool = True, dark: bool = False, ground: str | None = None) -> str:
    """The ring mark.

    `themed` embeds the `prefers-color-scheme` swap, for the SVG a browser
    reads. `dark` instead bakes the dark palette straight in, because a
    rasterizer has no viewer to ask and would otherwise always render the light
    one. `ground` fills behind the mark, for the touch icon, which iOS requires
    to be opaque.
    """
    ramp = DARK_RAMP if dark else LIGHT_RAMP
    check = PAPER if dark else INK
    style = ["    " + " ".join(f".c{i}{{fill:{c}}}" for i, c in enumerate(ramp)), f"    .k{{stroke:{check}}}"]
    if themed:
        swap = " ".join(f".c{i}{{fill:{c}}}" for i, c in enumerate(DARK_RAMP))
        style.append(f"    @media (prefers-color-scheme: dark){{{swap} .k{{stroke:{PAPER}}}}}")
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}" '
            f'role="img" aria-label="Brightwheel Check-In Shortcuts">'
        ),
        "  <style>",
        *style,
        "  </style>",
    ]
    if ground:
        out.append(f'  <rect width="{BOX}" height="{BOX}" fill="{ground}"/>')
    for i in range(COUNT):
        px, py, deg = capsule(i)
        out.append(
            f'  <rect class="c{i}" x="{px - CAP_L / 2:.2f}" y="{py - CAP_W / 2:.2f}" '
            f'width="{CAP_L}" height="{CAP_W}" rx="{CAP_W / 2}" '
            f'transform="rotate({deg:.0f} {px:.2f} {py:.2f})"/>'
        )
    pts, width = ring_check()
    out.append(
        f'  <path class="k" d="{path_of(pts)}" fill="none" '
        f'stroke-width="{width:.2f}" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
    )
    out.append("</svg>\n")
    return "\n".join(out)


def tile_svg(ring: bool = False) -> str:
    """The tiled mark: the dark palette on a solid ink tile, with the ring around
    the check at sizes big enough to hold it.

    Every `.ico` entry is tiled, because a `.ico` carries no media query and has
    to read against a strip it cannot know. Keeping one ground across all its
    sizes also means the 16px entry is the same mark with the ring dropped,
    rather than a different design.

    The tile is ink rather than brand indigo so that this is the *same* mark as
    everywhere else, not a second colorway: it matches the touch icon exactly
    and matches what the SVG shows in dark mode. It also degrades well — on a
    dark strip the tile's edge simply vanishes into the strip, leaving the
    capsules and the check floating, which is the groundless look the SVG uses
    there anyway.
    """
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}" '
            f'role="img" aria-label="Brightwheel Check-In Shortcuts">'
        ),
        f'  <rect width="{BOX}" height="{BOX}" rx="{TILE_RADIUS}" fill="{INK}"/>',
    ]
    if ring:
        # The ring mark is drawn to fill its own square, so on a tile its
        # capsules run into the rounded corners. Inset the whole group — ring
        # and check together, so their relationship is untouched.
        c = BOX / 2
        out.append(f'  <g transform="translate({c} {c}) scale({TILE_INSET}) translate({-c} {-c})">')
        for i, color in enumerate(DARK_RAMP):
            px, py, deg = capsule(i)
            out.append(
                f'    <rect x="{px - CAP_L / 2:.2f}" y="{py - CAP_W / 2:.2f}" '
                f'width="{CAP_L}" height="{CAP_W}" rx="{CAP_W / 2}" '
                f'fill="{color}" '
                f'transform="rotate({deg:.0f} {px:.2f} {py:.2f})"/>'
            )
        pts, width = ring_check()
        out.append(
            f'    <path d="{path_of(pts)}" fill="none" stroke="{PAPER}" '
            f'stroke-width="{width:.2f}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )
        out.append("  </g>")
    else:
        out.append(
            f'  <path d="{path_of(TILE_CHECK)}" fill="none" stroke="{PAPER}" '
            f'stroke-width="{TILE_CHECK_W}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )
    out.append("</svg>\n")
    return "\n".join(out)


def rasterize(svg_text: str, px: int) -> Image.Image:
    """One PNG straight from the vector at the size it will be shown."""
    with tempfile.TemporaryDirectory() as tmp:
        s, o = Path(tmp) / "i.svg", Path(tmp) / "i.png"
        s.write_text(svg_text)
        try:
            subprocess.run(
                ["rsvg-convert", "--width", str(px), "--height", str(px), "--output", str(o), str(s)],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            sys.exit("rsvg-convert not found — brew install librsvg")
        return Image.open(o).convert("RGBA").copy()


def write_ico(path: Path, images: list[Image.Image]) -> None:
    """An .ico holding one PNG per size.

    Pillow's writer takes a single image and resizes it, which would undo the
    point of rendering each size from the vector — so the container is written
    here. PNG payloads inside .ico have been read by every browser since IE on
    Vista.
    """
    blobs = []
    for im in images:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.png"
            im.save(p, "PNG", optimize=True)
            blobs.append(p.read_bytes())
    offset = 6 + 16 * len(blobs)
    out = [struct.pack("<HHH", 0, 1, len(blobs))]
    # blobs was built by appending exactly one entry per image, in order.
    for im, blob in zip(images, blobs, strict=True):
        w = 0 if im.width >= 256 else im.width
        h = 0 if im.height >= 256 else im.height
        out.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    path.write_bytes(b"".join(out + blobs))


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)

    (DOCS / "icon.svg").write_text(ring_svg())
    info(f"  wrote docs/icon.svg  ring, theme-aware  {(DOCS / 'icon.svg').stat().st_size} bytes")

    # The ring only earns its space from 32px up; at 16 its capsules fall below
    # a pixel each and read as speckle around the check, so that entry drops it.
    entries = [(16, False), (32, True), (48, True)]
    write_ico(DOCS / "favicon.ico", [rasterize(tile_svg(ring=r), n) for n, r in entries])
    info(
        "  wrote docs/favicon.ico  "
        + ", ".join(f"{n}{'=ring' if r else '=plain'}" for n, r in entries)
        + f"  {(DOCS / 'favicon.ico').stat().st_size} bytes"
    )

    touch = rasterize(ring_svg(themed=False, dark=True, ground=INK), 180).convert("RGB")
    touch.save(DOCS / "apple-touch-icon.png", "PNG", optimize=True)
    info(
        f"  wrote docs/apple-touch-icon.png  ring, dark, opaque  180x180  "
        f"{(DOCS / 'apple-touch-icon.png').stat().st_size} bytes"
    )


if __name__ == "__main__":
    main()

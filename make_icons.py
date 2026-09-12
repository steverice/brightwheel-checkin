#!/usr/bin/env python3
"""Draw the site's favicon: a check knocked out of a brand-indigo tile.

    python3 make_icons.py

Writes three files into `docs/`, because no single format covers everything:

  icon.svg              what a current browser prefers, and the only one that
                        stays sharp on a scaled display
  favicon.ico           16/32/48 raster fallback, and what answers the bare
                        `/favicon.ico` a browser requests without being asked
  apple-touch-icon.png  180x180, for a page saved to an iPhone home screen —
                        which this site invites, since it hands out iOS shortcuts

The shape is the ✅ emoji's idea rather than the emoji itself. An emoji favicon
is drawn from whichever emoji font the *viewer* happens to have, so it would be
Apple's green tile on a Mac, Segoe's on Windows and Noto's on Android; and the
one format that cannot be an emoji is the touch icon, which has to be a raster,
which would mean committing one vendor's artwork. Drawing it settles all three:
one appearance everywhere, the page's own indigo rather than a green that
appears nowhere else in the palette, and nothing redistributed.

The geometry lives here once, in a normalized square, and both the vector and
the rasters are generated from it — so the SVG and the PNGs cannot drift apart
the way two hand-kept copies would.
"""
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent
DOCS = REPO / "docs"

BRAND = "#4f52d6"      # --brand, straight from the page's :root
WHITE = "#ffffff"

# One 64-unit square, the SVG's viewBox and the rasters' reference frame.
BOX = 64
RADIUS = 14            # ~22% — a tile, not a pill
STROKE = 8             # ~12.5%, chunky enough to survive the 16px render
CHECK = [(17, 33), (26, 42), (47, 21)]

# Rendered this many times over, then resampled down. A check at 16px is two
# pixels wide, so the antialiasing is most of what makes it legible; drawing
# straight at 16 gives a staircase instead.
SUPERSAMPLE = 16


def draw(size, rounded):
    """The mark at one pixel size. `rounded` is off for the iOS touch icon,
    which gets masked and rounded by the system — rounding it here too leaves a
    dark fringe in the corners where our transparent corner meets theirs."""
    s = size * SUPERSAMPLE
    k = s / BOX
    img = Image.new("RGB", (s, s), BRAND)
    d = ImageDraw.Draw(img)

    if rounded:
        # Paint the ground, then carve the corners back to transparent by
        # compositing, so the tile keeps a clean edge at every size.
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=RADIUS * k, fill=BRAND)

    pts = [(x * k, y * k) for x, y in CHECK]
    w = int(STROKE * k)
    d.line(pts, fill=WHITE, width=w, joint="curve")
    # `joint="curve"` rounds the elbow but leaves the two ends squared off, so
    # cap them by hand to match the SVG's stroke-linecap.
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def svg():
    path = " ".join(f"{'ML'[i > 0]}{x} {y}" for i, (x, y) in enumerate(CHECK))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX} {BOX}" '
        f'role="img" aria-label="Brightwheel Check-In Shortcuts">\n'
        f'  <rect width="{BOX}" height="{BOX}" rx="{RADIUS}" fill="{BRAND}"/>\n'
        f'  <path d="{path}" fill="none" stroke="{WHITE}" stroke-width="{STROKE}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>\n'
        f'</svg>\n'
    )


def main():
    DOCS.mkdir(parents=True, exist_ok=True)

    (DOCS / "icon.svg").write_text(svg())
    print(f"  wrote docs/icon.svg  {(DOCS / 'icon.svg').stat().st_size} bytes")

    # One master, resampled by the ICO writer into the three sizes a browser
    # picks between.
    draw(256, rounded=True).save(DOCS / "favicon.ico",
                                 sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"  wrote docs/favicon.ico  16/32/48  "
          f"{(DOCS / 'favicon.ico').stat().st_size} bytes")

    touch = draw(180, rounded=False).convert("RGB")
    touch.save(DOCS / "apple-touch-icon.png", "PNG", optimize=True)
    print(f"  wrote docs/apple-touch-icon.png  {touch.size[0]}x{touch.size[1]}  "
          f"{(DOCS / 'apple-touch-icon.png').stat().st_size} bytes")


if __name__ == "__main__":
    main()

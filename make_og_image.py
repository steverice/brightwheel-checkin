#!/usr/bin/env python3
"""Draw the link-preview card, the picture a shared link unfurls into.

    python3 make_og_image.py

iMessage, Slack, Signal and the rest all read the same Open Graph tags out of
`docs/index.html`, and without an `og:image` they render a bare title and a
domain. This draws the missing picture at 1200x630 — the 1.91:1 that every
scraper crops toward — and writes it to `docs/img/og.png`.

The card is the page's own hero, rebuilt as a still: the headline, a brand rule,
the standfirst, the site's own mark, and the domain. It carries **no
child's name and no school**, unlike the notification banners further down the
page, because an `og:image` is fetched and cached by every service a link is
ever pasted into and cannot be recalled from any of them.

Fonts are fetched, not bundled. The page sets Outfit and Nunito Sans from Google
Fonts, neither of which ships with macOS, so drawing the card in them means
downloading them — from `github.com/google/fonts`, whose files are real
TrueType, and not from the `css2` endpoint, which hands back a container PIL
cannot open even when asked with a legacy User-Agent. Both are variable fonts,
so the weights come from named instances rather than separate files. They land
in a gitignored `.fonts/` and are re-used on later runs.

So this script needs the network on a cold cache. That is why `docs/img/og.png`
is committed alongside it: nothing in the build depends on this running, and it
only needs to run again when the card itself changes.
"""
import re
import sys
import textwrap
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import make_icons

REPO = Path(__file__).resolve().parent
OUT = REPO / "docs" / "img" / "og.png"
FONT_CACHE = REPO / ".fonts"

# 1200x630 is the size every scraper documents, and 1.91:1 is what iMessage and
# Slack crop toward. Drawing at exactly that size means nobody has to crop.
W, H = 1200, 630
PAD = 88

# Straight from the page's :root, so the card and the page it advertises cannot
# drift apart without someone editing both.
PAPER = "#f5f6fb"
INK = "#1f1c46"
MUTED = "#5c5f7a"
BRAND = "#4f52d6"

# A plain hyphen, where the page uses U+2011. The page can afford the
# non-breaking one because a browser falls back to another font for a glyph the
# first one lacks; PIL does not, and Outfit has no U+2011, so it drew a tofu box
# until this was an ASCII hyphen. Nothing wraps in a raster anyway.
HEADLINE = "Effortless check-in."
STANDFIRST = "Check your kids in and out of Brightwheel automatically on iOS"
DOMAIN = "code.steverice.org"
ICON = 140           # big enough that the ring resolves rather than speckles
FOOT_PAD = 56        # the mark sits closer to the edge than the text margin, so
                     # it has room to grow without crowding the standfirst
TOP = 118            # headline baseline, pulled up to free that room

# Variable fonts from the upstream repo. The bracketed axis list is part of the
# filename, so it has to be percent-encoded to survive the URL.
FONTS = {
    "outfit.ttf": "https://github.com/google/fonts/raw/main/ofl/outfit/Outfit%5Bwght%5D.ttf",
    "nunito.ttf": "https://github.com/google/fonts/raw/main/ofl/nunitosans/"
                  "NunitoSans%5BYTLC%2Copsz%2Cwdth%2Cwght%5D.ttf",
}


def font_file(name):
    """The cached TrueType, downloaded on first use."""
    path = FONT_CACHE / name
    if path.exists():
        return path
    FONT_CACHE.mkdir(exist_ok=True)
    print(f"  fetching {name}")
    data = urllib.request.urlopen(FONTS[name], timeout=60).read()
    # A TrueType file starts with 0x00010000 or "true"; anything else means the
    # host handed back a redirect page or a web font, and PIL's later failure
    # would not say which.
    if data[:4] not in (b"\x00\x01\x00\x00", b"true", b"ttcf"):
        sys.exit(f"{name}: not a TrueType file (starts {data[:4]!r})")
    path.write_bytes(data)
    return path


def face(name, size, instance):
    """One weight of a variable font, picked by its named instance."""
    f = ImageFont.truetype(str(font_file(name)), size)
    f.set_variation_by_name(instance)
    return f


def assert_renderable(font, text, label):
    """Stop if the font lacks a glyph, rather than drawing a tofu box.

    FreeType silently substitutes .notdef for a missing character, and a card is
    only looked at once, by which time it is published. So rather than trust the
    eye, render each character alone and compare it with a private-use codepoint
    no font defines: anything that draws identically to that is .notdef.
    """
    notdef = bytes(font.getmask(""))
    missing = {c for c in set(text) if not c.isspace()
               and bytes(font.getmask(c)) == notdef}
    if missing:
        sys.exit(f"{label}: {font.getname()[0]} has no glyph for "
                 f"{', '.join(repr(c) for c in sorted(missing))}")


def width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def wrap(draw, text, font, limit):
    """Greedy wrap to a pixel width, so editing the standfirst cannot overflow."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and width(draw, trial, font) > limit:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def main():
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    headline = face("outfit.ttf", 96, "Bold")
    stand = face("nunito.ttf", 40, "Regular")
    domain = face("nunito.ttf", 30, "SemiBold")

    assert_renderable(headline, HEADLINE, "headline")
    assert_renderable(stand, STANDFIRST, "standfirst")
    assert_renderable(domain, DOMAIN, "domain")

    # Headline, optically flush left: textbbox reports the ink, so drawing at
    # -bbox[0] puts the glyph's left edge on the margin rather than its sidebearing.
    top = TOP
    bbox = d.textbbox((0, 0), HEADLINE, font=headline)
    d.text((PAD - bbox[0], top - bbox[1]), HEADLINE, font=headline, fill=INK)
    headline_bottom = top + (bbox[3] - bbox[1])

    # A short brand rule under the headline, the one piece of pure color.
    rule_y = headline_bottom + 44
    d.rounded_rectangle([PAD, rule_y, PAD + 132, rule_y + 10], radius=5, fill=BRAND)

    # Standfirst, wrapped to the text column.
    y = rule_y + 66
    for line in wrap(d, STANDFIRST, stand, W - 2 * PAD - 120):
        d.text((PAD, y), line, font=stand, fill=MUTED)
        y += 56

    # Footer: the site's own mark, and the domain opposite. Rendered from
    # make_icons rather than redrawn, so the card cannot drift from the favicon.
    # Drawn large enough for the ring to resolve — at the size the three dots
    # used to occupy it collapsed into the same speckle that makes the 16px
    # favicon drop the ring entirely, and here there is no reason to be small.
    mark = make_icons.rasterize(make_icons.ring_svg(themed=False), ICON)
    mark_top = H - FOOT_PAD - ICON
    if mark_top < y + 8:
        sys.exit(f"the mark at {ICON}px would overlap the standfirst "
                 f"(mark top {mark_top}, text bottom {y}) — shrink ICON, "
                 f"raise TOP, or cut FOOT_PAD")
    img.paste(mark, (PAD, mark_top), mark)
    # Domain centered on the mark rather than sharing a baseline with it: the
    # mark has no baseline, so optical centering is the only alignment there is.
    dw = width(d, DOMAIN, domain)
    dbox = d.textbbox((0, 0), DOMAIN, font=domain)
    d.text((W - PAD - dw, mark_top + ICON // 2 - (dbox[3] + dbox[1]) // 2),
           DOMAIN, font=domain, fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"  wrote {OUT.relative_to(REPO)}  {img.size[0]}x{img.size[1]}  "
          f"{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

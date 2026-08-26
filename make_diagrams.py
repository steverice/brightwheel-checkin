#!/usr/bin/env python3
"""Draw the first-run setup diagrams the wrappers show.

Each wrapper explains how to attach its own trigger, so there are two: Check In
points at the Arrive action, Check Out at Leave. The screenshots underneath
were captured from an iOS 27 simulator (assets/capture-*.png); recapture them
if the Shortcuts action picker changes, then re-run this.

    python3 make_diagrams.py

Needs Pillow. Only for regenerating the diagrams — ./build.sh just reads the
PNGs this writes.
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).parent
ASSETS = HERE / "assets"

# The action row to ring, measured in the source screenshots.
PLAN = {
    "check-in": {
        "capture": "capture-arrive.png", "row": (62, 714, 1146, 871),
        "title": "Run this when you arrive",
        "action": "Arrive",
    },
    "check-out": {
        "capture": "capture-leave.png", "row": (62, 705, 1146, 885),
        "title": "Run this when you leave",
        "action": "Leave",
    },
}
CROP = (40, 150, 1166, 950)
WIDTH = 860


def font(size, bold=False):
    for path in ("/System/Library/Fonts/SFNSRounded.ttf",
                 "/System/Library/Fonts/SFNS.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            f = ImageFont.truetype(path, size)
            if bold and hasattr(f, "set_variation_by_name"):
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            return f
        except Exception:
            continue
    return ImageFont.load_default()


def draw(kind, spec):
    shot = Image.open(ASSETS / spec["capture"]).convert("RGB")
    crop = shot.crop(CROP)
    scale = WIDTH / crop.width
    crop = crop.resize((WIDTH, int(crop.height * scale)), Image.LANCZOS)

    canvas = Image.new("RGB", (940, 1320), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.text((40, 36), spec["title"], font=font(46, True), fill=(20, 20, 20))
    d.text((40, 100), "One-time setup. This shortcut only.",
           font=font(30), fill=(110, 110, 110))

    top = 164
    canvas.paste(crop, (40, top))
    d.rounded_rectangle((40, top, 40 + WIDTH, top + crop.height), radius=14,
                        outline=(205, 205, 205), width=2)
    x0, y0, x1, y1 = spec["row"]
    d.rounded_rectangle((40 + int((x0 - CROP[0]) * scale),
                         top + int((y0 - CROP[1]) * scale),
                         40 + int((x1 - CROP[0]) * scale),
                         top + int((y1 - CROP[1]) * scale)),
                        radius=16, outline=(230, 60, 70), width=6)

    steps = ["1.  Tap Edit on this shortcut.",
             "2.  Tap the search field at the bottom.",
             f'3.  Search "{spec["action"]}" and tap it under Automation.',
             "4.  Pick the school, and set the time range.",
             "5.  Choose Run Immediately."]
    y = top + crop.height + 40
    for line in steps:
        d.text((44, y), line, font=font(30), fill=(35, 35, 35))
        y += 44
    d.text((44, y + 16), "Location Services for Shortcuts must be Always,",
           font=font(26), fill=(120, 120, 120))
    d.text((44, y + 50), "or the geofence never fires.",
           font=font(26), fill=(120, 120, 120))

    # 64 colors is lossless enough for flat UI art and a third of the bytes,
    # which matters because this ends up base64'd inside the shortcut.
    out = ASSETS / f"setup-{kind}.png"
    canvas.convert("P", palette=Image.ADAPTIVE, colors=64).save(
        out, format="PNG", optimize=True)
    return out, out.stat().st_size


if __name__ == "__main__":
    for kind, spec in PLAN.items():
        path, size = draw(kind, spec)
        print(f"{path.name:24} {size:>7} bytes")

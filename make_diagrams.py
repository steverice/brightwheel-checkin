#!/usr/bin/env python3
"""Draw the first-run setup diagrams the wrappers show.

Each wrapper explains how to attach its own trigger, so there are two: Check In
points at the Arrive action, Check Out at Leave.

Each diagram is a before/after pair. The top panel is the action picker with
the row to tap ringed; the bottom panel is the same trigger once it has a
location and a time range, so there is something to compare your own screen
against. Knowing what to search for is only half of it — "have I finished?" is
the question the steps alone cannot answer.

All four screenshots came off an iOS 27 simulator (assets/capture-*.png, and
capture-*-set.png for the configured ones). Recapture them if the Shortcuts UI
changes, then re-run this.

    python3 make_diagrams.py

Needs Pillow. Only for regenerating the diagrams — ./build.sh just reads the
PNGs this writes.
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).parent
ASSETS = HERE / "assets"

# The action row to ring. Both screenshots are the same screen — they differ
# only in the label inside this row — so the rectangle is measured once and
# shared. Ringing them from two hand-measured rectangles is what made the two
# diagrams disagree: one ring cut into the card, the other sat around it.
#
# Measured, not eyeballed: the card is the white rounded rect, found by scanning
# for near-white at x=1000 (clear of the icon and the text) and along y=795.
# Outset by 4px so the stroke sits outside the card instead of over its edge.
ROW = (56, 699, 1149, 889)

PLAN = {
    "check-in": {
        "capture": "capture-arrive.png",
        "result": "capture-arrive-set.png",
        "title": "Make this run when you arrive",
        "action": "Arrive",
    },
    "check-out": {
        "capture": "capture-leave.png",
        "result": "capture-leave-set.png",
        "title": "Make this run when you leave",
        "action": "Leave",
    },
}
CROP = (40, 150, 1166, 950)
# The configured trigger plus its three toggles, with the shortcut's own title
# bar and the Comment below it cropped away.
RESULT_CROP = (45, 390, 1160, 1170)
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


def panel(name, box):
    """A screenshot region, cropped and scaled to the diagram width."""
    shot = Image.open(ASSETS / name).convert("RGB")
    crop = shot.crop(box)
    scale = WIDTH / crop.width
    return crop.resize((WIDTH, int(crop.height * scale)), Image.LANCZOS), scale


def draw(kind, spec):
    picker, scale = panel(spec["capture"], CROP)
    result, _ = panel(spec["result"], RESULT_CROP)

    # No step for Confirm Before Run. It already defaults to off — checked on a
    # simulator, untouched, straight after adding the trigger — so unattended
    # runs need nothing done to it, and whether you want a prompt is your call
    # rather than a setup step. The lower panel still shows the toggle.
    steps = ["1.  Tap Edit on this shortcut.",
             "2.  Tap the search field at the bottom.",
             f'3.  Search "{spec["action"]}" and tap it under Automation.',
             "4.  Pick the school, and set the time range."]

    # Lay the page out first so the canvas is exactly as tall as its contents.
    # It used to be a hard-coded 1320, which silently clipped anything added.
    head_y = 164
    top = head_y + 46
    steps_y = top + picker.height + 40
    caption_y = steps_y + len(steps) * 44 + 24
    result_y = caption_y + 46
    foot_y = result_y + result.height + 34
    height = foot_y + 96

    canvas = Image.new("RGB", (940, height), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.text((40, 36), spec["title"], font=font(46, True), fill=(20, 20, 20))
    # "Make this run ..." rather than "Run this ...", which parsed as an order
    # to run the shortcut right now — the opposite of the point. The subtitle
    # says the setup is expected: a wrapper with no trigger does nothing you
    # could not do by running Brightwheel Attendance by hand.
    d.text((40, 100), "One-time setup, and the whole point of this shortcut.",
           font=font(30), fill=(110, 110, 110))

    d.text((44, head_y), "How to set it up:", font=font(30, True),
           fill=(20, 20, 20))
    canvas.paste(picker, (40, top))
    d.rounded_rectangle((40, top, 40 + WIDTH, top + picker.height), radius=14,
                        outline=(205, 205, 205), width=2)
    x0, y0, x1, y1 = ROW
    d.rounded_rectangle((40 + int((x0 - CROP[0]) * scale),
                         top + int((y0 - CROP[1]) * scale),
                         40 + int((x1 - CROP[0]) * scale),
                         top + int((y1 - CROP[1]) * scale)),
                        radius=16, outline=(230, 60, 70), width=6)

    y = steps_y
    for line in steps:
        d.text((44, y), line, font=font(30), fill=(35, 35, 35))
        y += 44

    d.text((44, caption_y), "When it is set up, it looks like this:",
           font=font(30, True), fill=(20, 20, 20))
    canvas.paste(result, (40, result_y))
    d.rounded_rectangle((40, result_y, 40 + WIDTH, result_y + result.height),
                        radius=14, outline=(205, 205, 205), width=2)

    d.text((44, foot_y), "Location Services for Shortcuts must be Always,",
           font=font(26), fill=(120, 120, 120))
    d.text((44, foot_y + 34), "or the geofence never fires.",
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

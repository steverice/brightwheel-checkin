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
capture-*-set.png for the configured ones). They are stored **already cropped
to the region used**, and are pasted whole — a full 1206x2622 screenshot is
four times the bytes for the same picture, and these live in git.

To recapture, take a full screenshot on an iPhone 17 Pro simulator and cut out
the same region before saving it here:

    picker  crop (40, 224, 1166, 950)  -> 1126x726
    result  crop (45, 390, 1160, 1027) -> 1115x637

then re-run this. ROW below is in the picker's cropped coordinates, so it
shifts with that box.

Both boxes are tight on purpose. The picker starts at the top of the search
field, because the sheet's gray surround and its drag handle are not part of
what you are matching, and the result stops at a row boundary just under
Automation, cutting off Confirm Before Run. The page is rendered scaled to fit
inside an alert, so anything that is not being compared against is costing the
rest of it width.

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
# for near-white at x=1000 (clear of the icon and the text) and along y=795 of
# the full screenshot. Outset by 4px so the stroke sits outside the card instead
# of over its edge, then shifted by the picker crop's origin (40, 224).
ROW = (16, 475, 1109, 665)

TITLE = "One-time automation setup"

PLAN = {
    "check-in": {
        "capture": "capture-arrive.png",
        "result": "capture-arrive-set.png",
        "subtitle": "Automatically check in when you arrive at school",
        "action": "Arrive",
    },
    "check-out": {
        "capture": "capture-leave.png",
        "result": "capture-leave-set.png",
        "subtitle": "Automatically check out when you leave school",
        "action": "Leave",
    },
}
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


def panel(name):
    """A capture, scaled to the diagram width. Stored pre-cropped, so whole."""
    shot = Image.open(ASSETS / name).convert("RGB")
    scale = WIDTH / shot.width
    return shot.resize((WIDTH, int(shot.height * scale)), Image.LANCZOS), scale


def draw(kind, spec):
    picker, scale = panel(spec["capture"])
    result, _ = panel(spec["result"])

    # No step for Confirm Before Run, and it is cropped out of the lower panel.
    # It already defaults to off — checked on a simulator, untouched, straight
    # after adding the trigger — so unattended runs need nothing done to it, and
    # whether you want a prompt is your call rather than a setup step.
    steps = ["1.  Tap Edit on this shortcut.",
             "2.  Tap the search field at the bottom.",
             f'3.  Search "{spec["action"]}" and tap it under Automation.',
             "4.  Pick the school, and set the time range."]

    # Lay the page out first so the canvas is exactly as tall as its contents.
    # It used to be a hard-coded 1320, which silently clipped anything added.
    #
    # Neither panel is labeled. "How to set it up" and "When it is set up, it
    # looks like this" were two lines saying what the order already says, and
    # the sheet renders this whole page scaled to fit — every line costs width.
    top = 156
    steps_y = top + picker.height + 32
    result_y = steps_y + len(steps) * 44 + 24
    foot_y = result_y + result.height + 28
    height = foot_y + 56

    canvas = Image.new("RGB", (940, height), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    # The title names the task; the subtitle names the result. Both wrappers
    # share the title, and only the subtitle changes direction.
    d.text((40, 36), TITLE, font=font(46, True), fill=(20, 20, 20))
    d.text((40, 100), spec["subtitle"], font=font(30), fill=(110, 110, 110))

    canvas.paste(picker, (40, top))
    d.rounded_rectangle((40, top, 40 + WIDTH, top + picker.height), radius=14,
                        outline=(205, 205, 205), width=2)
    x0, y0, x1, y1 = ROW
    d.rounded_rectangle((40 + int(x0 * scale),
                         top + int(y0 * scale),
                         40 + int(x1 * scale),
                         top + int(y1 * scale)),
                        radius=16, outline=(230, 60, 70), width=6)

    y = steps_y
    for line in steps:
        d.text((44, y), line, font=font(30), fill=(35, 35, 35))
        y += 44

    canvas.paste(result, (40, result_y))
    d.rounded_rectangle((40, result_y, 40 + WIDTH, result_y + result.height),
                        radius=14, outline=(205, 205, 205), width=2)

    # One line at 26 runs 906px wide against 856 of room; 24 fits with the
    # sentence intact, which beats rewording it to save two points.
    d.text((44, foot_y),
           "Location Services for Shortcuts must be Always, "
           "or the geofence never fires.",
           font=font(24), fill=(120, 120, 120))

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

#!/usr/bin/env python3
"""Draw the first-run setup diagrams the wrappers show.

Each wrapper explains how to attach its own trigger, so there are two. Both
point at the Arrive action: the school asks that children be checked out on the
parents' way in, so the check-out fires on arriving for pickup rather than on
leaving. What separates the two is the time range, which is why the steps and
the subtitle name it and the shared picker panel does not.

Each diagram is a before/after pair. The top panel is the action picker with
the row to tap ringed; the bottom panel is the same trigger once it has a
location and a time range, so there is something to compare your own screen
against. Knowing what to search for is only half of it — "have I finished?" is
the question the steps alone cannot answer.

All three screenshots came off an iOS 27 simulator (assets/capture-*.png, and
capture-*-set.png for the configured ones). Both diagrams share one picker
capture, because both ring the same row; only the configured panel differs.
They are stored **already cropped to the region used**, and are pasted whole —
a full 1206x2622 screenshot is four times the bytes for the same picture, and
these live in git.

**"Washington Elementary School" in the captures is a stand-in, not anybody's
school**, picked because it could be any of dozens of them. The location a
trigger names is the one thing in these screenshots that could identify a
family, so a capture must never show a real one. Nothing else here is personal:
the screenshots are simulator screens, and the diagrams ship inside the wrappers
where every parent who installs one sees them.

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

# The action row to ring. Both diagrams ring Arrive, so they now share one
# picker capture outright and the rectangle is measured once. Ringing them from
# two hand-measured rectangles is what made the two diagrams disagree back when
# each had its own screenshot: one ring cut into the card, the other sat around
# it.
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
        "subtitle": "Automatically check in when you drop off in the morning",
        "action": "Arrive",
        "when": "drop-off time",
    },
    "check-out": {
        "capture": "capture-arrive.png",
        "result": "capture-arrive-pm-set.png",
        "subtitle": "Automatically check out on your way in to pick up",
        "action": "Arrive",
        "when": "pickup time",
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
    # Step 4 names the time range rather than just asking for one. Both
    # wrappers now ring the same row in the same picture, so the range is the
    # only thing that tells the two guides apart on screen.
    steps = ["1.  Tap Edit on this shortcut.",
             "2.  Tap the search field at the bottom.",
             f'3.  Search "{spec["action"]}" and tap it under Automation.',
             f'4.  Pick the school, and set the time range to {spec["when"]}.']

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

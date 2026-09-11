#!/usr/bin/env python3
"""Generate "Brightwheel Share Links": a shortcut that shares the shortcuts.

An iCloud link is a snapshot taken when you share, so every release needs three
fresh ones, and the only way to mint them is from inside Shortcuts — the
`shortcuts` CLI has no share command and the AppleScript dictionary exposes
only `run`. This builds a shortcut that mints all three and puts them on the
clipboard as the exact markup `docs/index.html` wants.

It finds each shortcut **by name, every time it runs**, rather than through a
picker. A picker stores a workflow identifier, publishing a new build mints a
new one, and a picked publisher therefore goes stale on every release. It can't
be pre-seeded by name either; all three shapes tried asked for a shortcut when
run. Looking the shortcut up stores nothing that can go stale. See `TESTING.md`.

Before minting anything it checks that the library holds exactly one copy of
each, under its exact name, and stops with a notification otherwise. A numbered
copy ("Brightwheel Attendance 1", which macOS can leave when you import over an
existing name) is refused rather than shared: parents would install it under
that name, and the wrappers call Brightwheel Attendance by its exact name.

Run it on macOS 27 or iOS 27, where the action exists. Each link still costs one
confirmation tap.
"""
import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from build_shortcuts import (act, attach, comment, cond_input, out,  # noqa: E402
                             ts, uuids, var)

NAME = "Brightwheel Share Links"
TARGETS = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]

# Verified against the v78 ToolKit database rather than guessed: the action is
# com.apple.shortcuts.CreateShortcutiCloudLinkAction, "Create iCloud Link for
# Shortcut", one parameter keyed `shortcut`, present on iOS 27 and macOS 27.
ICLOUD_LINK = "com.apple.shortcuts.CreateShortcutiCloudLinkAction"


def descriptor():
    """Every com.apple.* AppIntent needs one, or the file will not import."""
    return {
        "BundleIdentifier": "com.apple.shortcuts",
        "Name": "Shortcuts",
        "TeamIdentifier": "0000000000",
        "AppIntentIdentifier": "CreateShortcutiCloudLinkAction",
    }


def build():
    i = iter(uuids(160))
    A = []

    A.append(comment(
        "--- WHAT THIS IS ---\n"
        "Mints a fresh iCloud link for each of the three Brightwheel shortcuts "
        "and copies them to the clipboard as the markup docs/index.html wants.\n\n"
        "It finds each one by name every time it runs, so a fresh import is simply "
        "found again. Nothing needs picking, and nothing goes stale between "
        "releases.\n\n"
        "Each link asks you to confirm: three taps, once per release. If anything "
        "about the library is wrong it stops before the first one."
    ))

    u_all = next(i)
    A.append(act("is.workflow.actions.getmyworkflows", UUID=u_all,
                 CustomOutputName="My Shortcuts"))
    # A list of shortcuts coerced to text is their names, one per line —
    # measured on the Mac, see TESTING.md ("A whole library coerces to its names").
    u_names = next(i)
    A.append(act("is.workflow.actions.gettext", UUID=u_names,
                 CustomOutputName="Library Names",
                 WFTextActionText=ts(out(u_all, "My Shortcuts"))))
    u_one = next(i)
    A.append(act("is.workflow.actions.number", UUID=u_one,
                 WFNumberActionNumber="1"))

    def count_of(pattern, text):
        """Match Text then Count: how many times `pattern` occurs in `text`."""
        u_m, u_c = next(i), next(i)
        A.append(act("is.workflow.actions.text.match", UUID=u_m,
                     WFMatchTextPattern=pattern, text=ts(text)))
        A.append(act("is.workflow.actions.count", UUID=u_c, WFCountType="Items",
                     WFInput=attach(out(u_m, "Matches")),
                     Input=attach(out(u_m, "Matches"))))
        return u_c

    def stop_if_above(src, name, value, why, title, body):
        """Notify and stop when `src` is more than `value`.

        Condition 2 is "is greater than" (ARCHITECTURE.md, "A numeric If accepts
        a Math output directly"), so this is the only comparison used.
        """
        g = next(i)
        A.append(comment(why))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g, WFControlFlowMode=0,
                     WFCondition=2, WFNumberValue=value,
                     WFInput=cond_input(out(src, name))))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts(title),
                     WFNotificationActionBody=ts(body)))
        A.append(act("is.workflow.actions.exit"))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g, WFControlFlowMode=2))

    # --- every check, for every target, before anything is minted ---
    names = out(u_names, "Library Names")
    for t in TARGETS:
        any_copy = count_of(f"(?m)^{t}( \\d+)?$", names)
        stop_if_above(
            any_copy, "Count", "1",
            f"Stop if there is more than one {t}.\n"
            f"- Condition counts every copy, numbered or not\n"
            f"- Linking either one would be a guess about which is the new build",
            f"More than one {t}",
            "Delete the old copy and run this again. Nothing was shared.")
        numbered = count_of(f"(?m)^{t} \\d+$", names)
        stop_if_above(
            numbered, "Count", "0",
            f"Stop if the only copy has a number after its name.\n"
            f"- Condition counts copies named {t} followed by a number\n"
            f"- It would be shared under that name, and the wrappers call "
            f"{t} by its exact name",
            f"{t} has a number after its name",
            f"Rename it to {t} and run this again. Nothing was shared.")
        exact = count_of(f"(?m)^{t}$", names)
        u_gap = next(i)
        A.append(act("is.workflow.actions.math", UUID=u_gap,
                     WFInput=attach(out(u_one, "Number")), WFMathOperation="-",
                     WFMathOperand=attach(out(exact, "Count"))))
        stop_if_above(
            u_gap, "Calculation Result", "0",
            f"Stop if {t} is not in the library.\n"
            f"- Condition is one minus the exact-name count\n"
            f"- Above zero means there is none",
            f"{t} is not in your library",
            "Import it and run this again. Nothing was shared.")

    # --- the walk: exactly one of each exists now, under its exact name ---
    g_loop = next(i)
    A.append(comment(
        "Walk the library and link each of the three.\n"
        "- Input is My Shortcuts, the whole library\n"
        "- Each Repeat Item is one shortcut, found by its exact name"))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=g_loop, WFControlFlowMode=0,
                 WFInput=attach(out(u_all, "My Shortcuts"))))
    u_name = next(i)
    A.append(act("is.workflow.actions.gettext", UUID=u_name,
                 CustomOutputName="Name", WFTextActionText=ts(var("Repeat Item"))))
    for t in TARGETS:
        hit = count_of(f"^{t}$", out(u_name, "Name"))
        g, u_link = next(i), next(i)
        A.append(comment(
            f"Link {t} when this item is it.\n"
            f"- Condition counts an exact match on this item's name\n"
            f"- The item goes to Create iCloud Link as a variable, never a "
            f"picked value"))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g, WFControlFlowMode=0,
                     WFCondition=2, WFNumberValue="0",
                     WFInput=cond_input(out(hit, "Count"))))
        A.append(act(ICLOUD_LINK, UUID=u_link, CustomOutputName=f"Link for {t}",
                     AppIntentDescriptor=descriptor(),
                     shortcut=attach(var("Repeat Item"))))
        A.append(act("is.workflow.actions.setvariable", WFVariableName=f"{t} Link",
                     WFInput=attach(out(u_link, f"Link for {t}"))))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=g_loop, WFControlFlowMode=2))

    # --- hand them to update_links.py ---
    parts = []
    for t in TARGETS:
        parts += ['<li><a href="', var(f"{t} Link"), f'">{t}</a></li>\n']
    u_mk = next(i)
    A.append(comment(
        "Copy the markup docs/index.html wants.\n"
        "- Three <li> lines, in the page's order\n"
        "- Then run tools/verify_links.py and tools/update_links.py"))
    A.append(act("is.workflow.actions.gettext", UUID=u_mk,
                 CustomOutputName="Markup", WFTextActionText=ts(*parts)))
    A.append(act("is.workflow.actions.setclipboard",
                 WFInput=attach(out(u_mk, "Markup"))))
    A.append(act("is.workflow.actions.showresult", Text=ts(
        "Copied three links. Now run: "
        "python3 tools/verify_links.py --clipboard --erase")))

    return {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        # 59749 is a chain link; the color is the same indigo the page uses.
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59749,
                           "WFWorkflowIconStartColor": 946986751},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": NAME,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }


# Validator complaints that are expected here. Anything else is real.
#   The prompt-comment rule, waived in build.sh for the same reason: the
#   Shortcuts Playground attribution comment was removed on purpose, so the
#   second action is not a prompt block.
WAIVED = (
    "Second action must be the prompt Comment block",
)


def main():
    import subprocess

    dest = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "dist-tools")
    dest.mkdir(parents=True, exist_ok=True)
    plist = build()
    xml = dest / f"{NAME}.xml"
    xml.write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_XML))

    r = subprocess.run(["validate-shortcut", str(xml),
                        "--target-platform", "ios", "--target-macos", "27"],
                       capture_output=True, text=True)
    unexpected = [l for l in r.stdout.splitlines()
                  if l.startswith("- ") and not any(w in l for w in WAIVED)]
    if unexpected:
        print("FAILED", *unexpected, sep="\n")
        return 1

    subprocess.run(["sign-shortcut", str(xml), "--name", NAME, "--mode", "anyone"],
                   check=True, capture_output=True, text=True)
    signed = Path.home() / "Documents" / "Shortcuts Playground" / f"{NAME}.shortcut"
    target = dest / f"{NAME}.shortcut"
    target.write_bytes(signed.read_bytes())
    print(f"{NAME}: {len(plist['WFWorkflowActions'])} actions, "
          f"validated, signed -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

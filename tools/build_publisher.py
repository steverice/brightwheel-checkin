#!/usr/bin/env python3
"""Generate "Brightwheel Share Links": a shortcut that shares the shortcuts.

An iCloud link is a snapshot taken when you share, so every release needs three
fresh ones, and the only way to mint them is from inside Shortcuts — the
`shortcuts` CLI has no share command and the AppleScript dictionary exposes
only `run`. This builds a shortcut that calls Create iCloud Link for Shortcut
three times and puts the result on the clipboard as the exact markup
`docs/index.html` wants, so refreshing the page after a release is one run and
one paste.

Run it on macOS 27 or iOS 27, where that action exists. The three shortcuts have
to be in your library first, and each link costs one confirmation tap.
"""
import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from build_shortcuts import act, attach, comment, out, ts, uuids  # noqa: E402

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
    i = iter(uuids(20))
    A = []

    A.append(comment(
        "--- WHAT THIS IS ---\n"
        "Mints a fresh iCloud link for each of the three Brightwheel shortcuts "
        "and copies them to the clipboard as the markup docs/index.html wants.\n\n"
        "Run this after cutting a release, then paste over the three links in "
        "the page. An iCloud link is frozen at the moment you share it, so the "
        "old ones keep serving the old shortcut until you replace them.\n\n"
        "Each link asks you to confirm. Three taps, once per release."
    ))

    links = []
    for target in TARGETS:
        u = next(i)
        links.append((target, u))
        A.append(act(ICLOUD_LINK, UUID=u,
                     CustomOutputName=f"{target} Link",
                     AppIntentDescriptor=descriptor(),
                     # The picker is left empty on purpose: a workflow reference
                     # has no verified serialization, and an invented one either
                     # fails to import or imports as a parameter that silently
                     # points at nothing. Choose each shortcut once in the
                     # editor and it stays chosen.
                     shortcut=""))

    parts = []
    for target, u in links:
        parts.extend([
            f'<li><a href="',
            out(u, f"{target} Link"),
            f'">{target}</a></li>\n',
        ])
    A.append(comment(
        "--- CHOOSE THE THREE, ONCE ---\n"
        "Each action above has an empty Shortcut picker. That is not an "
        "oversight and it cannot be filled in by the generator: the parameter "
        "is a reference to a workflow, and a workflow's identifier is minted "
        "when it is imported, so it is different in every library. Open each "
        "action, pick the shortcut it names, and it stays picked."
    ))

    u_text = next(i)
    A.append(act("is.workflow.actions.gettext", UUID=u_text,
                 CustomOutputName="Markup", WFTextActionText=ts(*parts)))

    A.append(comment(
        "--- WHAT LANDS ON THE CLIPBOARD ---\n"
        "Three <li> lines, in the order the page lists them. Paste them over "
        "the three inside <ul class=\"downloads\"> in docs/index.html and the "
        "page is current again."
    ))

    A.append(act("is.workflow.actions.setclipboard",
                 WFInput=attach(out(u_text, "Markup"))))
    A.append(act("is.workflow.actions.showresult",
                 Text=ts("Copied. Paste over the three links in "
                         "docs/index.html.")))

    return {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        # 59749 is a chain link; the colour is the same indigo the page uses.
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
#   1. The prompt-comment rule, waived in build.sh for the same reason: the
#      Shortcuts Playground attribution comment was removed on purpose, so the
#      second action is not a prompt block.
#   2. The three empty `shortcut` parameters. Normally an empty parameter is a
#      silent-failure trap and worth failing a build over — but this one is a
#      reference to a workflow, and a workflow's identifier is minted at import
#      time, so it differs in every library. No value written here could be
#      right anywhere but the machine that wrote it. The picker is filled in
#      once, by hand, and the comment above the actions says so.
WAIVED = (
    "Second action must be the prompt Comment block",
    "CreateShortcutiCloudLinkAction -> shortcut",
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

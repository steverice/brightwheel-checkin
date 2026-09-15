#!/usr/bin/env python3
"""Generate, validate, and sign the three Brightwheel Shortcuts.

Brightwheel Attendance does the work; Check In and Check Out are wrappers
that carry the triggers and hand it a direction. README.md has the endpoint
contract. The plist primitives, the structural checks, and the validate-and-
sign pipeline come from shortcut-forge.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import argcomplete
from shortcut_forge_lib.actions import GREATER_THAN, LESS_THAN, ActionList
from shortcut_forge_lib.build import Shortcut, build_all
from shortcut_forge_lib.checks import CheckError
from shortcut_forge_lib.plist import (
    EXTENSION_INPUT,
    act,
    attach,
    comment,
    cond_input,
    dict_field,
    document,
    import_question,
    kv,
    kv_dict,
    out,
    ts,
    var,
)
from shortcut_forge_lib.toolchain import SigningError, ToolNotFoundError, ValidationError
from shortcut_forge_lib.uuids import random_uuids

from console import error, info, warning

if TYPE_CHECKING:
    from typing import Any

BASE = "https://schools.mybrightwheel.com/api/v1"
# Kept so an overridden BASE can be reported as such. The integration tests
# point BASE at a mock; nothing else should.
DEFAULT_BASE = BASE
CLIENT_NAME = "ios"
CLIENT_VERSION = "3.103.0"

# The sign-in code prompt. Short on purpose: the sheet sits over whatever the
# email is being read in, and every line makes it taller. "Done" is the
# sheet's own button name. Leading zeros are optional because the field is
# numeric and drops one as the next digit is typed; the run pads them back.
CODE_PROMPT = '6-digit code from Brightwheel, leading 0s optional. Leave empty and tap "Done" to re-send.'
# The prompt over a code pasted from the clipboard. That sheet is a text
# field, which keeps a leading zero, so the prompt does not mention them.
CODE_PROMPT_PASTED = '6-digit code from Brightwheel. Leave empty and tap "Done" to re-send.'
# The prompt on a run that has just sent a code, which can afford a line more:
# the sheet is the first thing this person sees, and the masked address the
# start response reports goes between the two halves.
CODE_PROMPT_SENT = (
    "Check ",
    ' for a 6-digit code and enter here. Leading 0s optional. Leave empty and tap "Done" to re-send.',
)
CODE_PROMPT_SENT_NOWHERE = "your email"

# When a code was sent, kept beside the token so a run a few minutes later
# asks for the code instead of sending another. Text, not a Date: Store
# Content keeps nothing of a Date object (measured: the archive holds zero
# items), and this format reads back as a date, where the ISO form with a
# zone offset came back hours off.
CODE_SENT_KEY = "BrightwheelCodeSentAt"
CODE_SENT_FORMAT = "yyyy-MM-dd HH:mm:ss"
# Minutes since the send, as Get Time Between Dates reports them: negative
# for a time in the past and truncated toward zero, so one digit with an
# optional sign is "under ten minutes ago". Nothing stored yields no number.
CODE_SENT_RECENT = r"^-?[0-9]$"

ATTENDANCE = "Brightwheel Attendance"

# Validator errors that are expected and deliberate. Anything else is real and
# must stop the build.
#   1. The Shortcuts Playground attribution comment was removed on purpose.
#   2. is.workflow.actions.scanbarcode, on two counts. It has no row in the
#      bundled iOS 27 ToolKit snapshot, so the validator calls it macOS-only,
#      and the validator also demands an imageFile parameter. Neither holds for
#      the iOS live scanner, which takes WFScanCodeActionMode and no image at
#      all. Both are contradicted by a working shortcut exported off an iOS 27
#      phone; the docs describe the macOS scan-an-image variant.
#   3. Glyphs 62021 and 62022 (a plane departing / arriving). The validator
#      checks against a 507-entry mapping; the device's own icon picker offers
#      far more than that. Both numbers came out of real shortcuts built on a
#      device, and both were rendered on a simulator to confirm. Waived by
#      number rather than by rule, so a genuine typo in a glyph still fails.
#   4. "Unit conversion detected". The wrappers carry the setup diagram as a
#      base64 string, and ~100k characters of it trip the heuristic that
#      looks for unit words in action text. There is no measurement action
#      in either wrapper.
#   5. The two comment-block rules. Both exist to keep the Shortcuts Playground
#      attribution comment in the file; that comment was removed on purpose, so
#      the second action is no longer a prompt block and the wrappers sit one
#      under the density threshold. The comments that remain each explain a
#      real block, and adding a fourth to satisfy a ratio would be noise in a
#      shortcut whose working part is two actions.
WAIVED = [
    "Shortcuts Playground prompt text",
    r"is\.workflow\.actions\.scanbarcode",
    "Scan QR or Barcode missing imageFile",
    "WFWorkflowIconGlyphNumber (62021|62022) is not in the official",
    "Unit conversion detected",
    "Second action must be the prompt Comment block",
    "Insufficient Comment blocks",
]


ENV_KEYS = {
    "school_id": "BRIGHTWHEEL_SCHOOL_ID",
    "code": "BRIGHTWHEEL_CHECKIN_CODE",
    "secret": "BRIGHTWHEEL_SCHOOL_SECRET",
    "email": "BRIGHTWHEEL_EMAIL",
    "password": "BRIGHTWHEEL_PASSWORD",
}


def load_env(path: str | Path) -> dict[str, str]:
    """Read a KEY=VALUE .env for debug builds.

    A debug build has real credentials baked into it, so it must never be
    committed or shared. build.sh writes debug output to dist-debug/, which is
    gitignored, and refuses to put it in dist/.
    """
    env = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in ENV_KEYS.values() if not env.get(k)]
    if missing:
        raise SystemExit(f"{path} is missing or has empty: {', '.join(missing)}")
    return env


# --- shortcut assembly --------------------------------------------------
# `checked_in` is the DESIRED state, not the child's current state.
#   checked_in: true  -> checks the child IN
#   checked_in: false -> checks the child OUT


# Each SETUP entry is (key, kind, prompt, blurb, action_value, prompt_default).
#
# kind "text" binds the question to a Text action, "number" to a Number action.
# Number is right for the window bounds because they only ever feed arithmetic,
# so it does not matter whether the field renders 8 or 8.0. It would be wrong
# for the check-in code, which is interpolated into JSON as a string.
#
# action_value is what sits in the action itself. The validator rejects an empty
# one, and it is what gets used if a question is skipped, so for the secrets it
# is a marker that fails loudly rather than looking like a real value.
SETUP = [
    (
        "email",
        "text",
        "Brightwheel account email",
        "Used to sign in again when the session token stops working.",
        "not set",
        "",
    ),
    (
        "password",
        "text",
        "Brightwheel account password",
        "Used together with the email, only when signing in again.",
        "not set",
        "",
    ),
    ("code", "text", "Brightwheel check-in code", "Your 4-digit guardian check-in code.", "not set", ""),
]

# The answer field autocapitalizes, and an import question exposes no setting
# that turns it off. A password typed as "hunter2" is therefore stored as
# "Hunter2", and the sign-in fails later with credentials that look right —
# so the question that suffers for it says so. Measured on iOS 27 24A434.
# Digits are unaffected, which is why the check-in code needs no such warning,
# and the email is only likely to survive it because most services fold case.
QUESTION_NOTES = {
    "password": (
        "\n\nPasswords are case-sensitive, and this field capitalizes the first "
        "letter for you. Check it matches your password exactly before moving on."
    ),
}

# iOS 27 asks these one at a time, and only the last page carries the button
# that commits them. That button is dead: from 24A5408d through the 27.0
# release candidate 24A434, tapping "Add Shortcut" after answering a question
# does nothing at all — no install, no error. "Skip Setup" commits every answer
# correctly, which its name gives nobody any reason to expect, so the last
# question has to say so. Measured, not assumed: all three answers were read
# back out of the installed shortcut. See TESTING.md.
LAST_QUESTION_NOTE = (
    "\n\niOS 27: if \u201cAdd Shortcut\u201d does nothing when you tap it, tap "
    "\u201cSkip Setup\u201d instead \u2014 it saves these answers too. That is an "
    "iOS bug, not a problem with what you typed."
)


def build(env: dict[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    """The shortcut that does the work. Direction arrives as Shortcut Input."""
    name = ATTENDANCE
    # 62329 is a ring of petals — close to the Brightwheel logo — on pink.
    # Both taken from a share link's icon_glyph / a resolved palette value and
    # then checked on a simulator, because the bundled glyph names are wrong.
    glyph, color = 62329, 3980825855

    actions = ActionList(random_uuids())
    i = actions.uuids
    u = {k: next(i) for k in ("code", "email", "password")}
    questions = []

    # Every branch below goes through actions.count_matches(): Text, Match Text,
    # Count. A Dictionary Value compared directly in an If reads as blank and
    # the branch never fires, and an empty string still satisfies "has any
    # value", so presence has to be measured rather than tested. The roster
    # and the wording dictionary are read with Get Dictionary Value, but every
    # one of those values is used as text; the moment a dictionary value has
    # to decide a branch, it comes back through the count.
    actions.append(
        comment(
            "Brightwheel — Attendance\n\n"
            "Checks your children in or out by talking to the Brightwheel API "
            "directly, without opening the app. Who the children are, which room "
            "they are in and who is checking them in all come from Brightwheel "
            "when it runs — nothing about your family is built into this "
            "shortcut.\n\n"
            "Brightwheel Check In and Brightwheel Check Out start this one and tell "
            "it which direction to go; those are the two that carry the automation "
            "triggers. Run this on its own and it simply asks.\n\n"
            "Built to be run unattended by a location trigger, so it does not stop "
            "to ask anything on the normal path. Everything it needs is collected "
            "once, when you import it. When it should run is decided by the "
            "trigger's own time range, not by this shortcut.\n\n"
            "Each run:\n"
            "1. Checks the saved sign-in is still valid, and signs in again by itself "
            "if it has expired.\n"
            "2. Looks up whether each child is already checked in or out.\n"
            "3. Skips anyone already the way this run wants them, so running it "
            "twice is harmless.\n"
            "4. Sends the request for everyone else and posts a notification per child."
        )
    )
    actions.append(
        comment(
            "This shortcut saves the Brightwheel session token in its own on-device "
            "storage and refreshes it automatically, because a background automation "
            "cannot stop to ask you to paste a new one.\n\n"
            "The three values below are filled in when you import the shortcut, so "
            "neither your password nor your check-in code is stored in the shortcut "
            "file itself. Your session token is never in the file either: it is kept "
            "under this shortcut, so deleting the shortcut takes the token with it.\n\n"
            "The school's code is the one thing kept outside this shortcut, so that "
            're-importing does not send you back for the QR code. "Forget saved '
            'sign-in and school code" in the menu clears both.\n\n'
            "If you ever share or export this shortcut, clear the three Text actions "
            "below first. The check-in code authenticates as you."
        )
    )

    # ---- which direction ----
    #
    # Direction comes from Shortcut Input, set by whichever wrapper started this.
    # It deliberately is NOT inferred from the clock: the trigger time ranges
    # live on the device and can be edited there, so a baked-in threshold would
    # silently disagree with them and send the wrong direction.
    #
    # Run with no input, this asks. That is what makes the shortcut safe to
    # have in the library: saying its name to Siri opens a menu, and canceling
    # the menu sends nothing.
    u_words, u_civ, u_verb, u_already = (next(i) for _ in range(4))
    g_valid = next(i)
    ext = EXTENSION_INPUT

    u_ext, u_min, u_mout = next(i), next(i), next(i)
    g_menu = next(i)

    c_valid = actions.count_matches(ext, "^(in|out)$")
    actions.append(
        comment(
            "Work out which direction this run goes.\n"
            "- Condition counts whether a direction was handed in\n"
            "- Brightwheel Check In and Check Out hand one in\n"
            "- Run on its own there is none, so it asks instead"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_valid,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_valid, "Count")),
        )
    )
    actions.append(act("is.workflow.actions.gettext", UUID=u_ext, WFTextActionText=ts(ext)))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Direction", WFInput=attach(out(u_ext, "Text")))
    )
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_valid, WFControlFlowMode=1)
    )
    actions.append(
        comment(
            "Ask, when nothing was handed in.\n"
            "- Each choice sets Direction to the same in or out a wrapper would\n"
            "- Canceling the menu stops the shortcut, so nothing is sent"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.choosefrommenu",
            UUID=next(i),
            GroupingIdentifier=g_menu,
            WFControlFlowMode=0,
            WFMenuPrompt="Check the children in or out?",
            WFMenuItems=["Check In", "Check Out", "Show the school's code", "Forget saved sign-in and school code"],
        )
    )
    actions.append(
        act(
            "is.workflow.actions.choosefrommenu",
            UUID=next(i),
            GroupingIdentifier=g_menu,
            WFControlFlowMode=1,
            WFMenuItemTitle="Check In",
        )
    )
    actions.append(act("is.workflow.actions.gettext", UUID=u_min, WFTextActionText="in"))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Direction", WFInput=attach(out(u_min, "Text")))
    )
    actions.append(
        act(
            "is.workflow.actions.choosefrommenu",
            UUID=next(i),
            GroupingIdentifier=g_menu,
            WFControlFlowMode=1,
            WFMenuItemTitle="Check Out",
        )
    )
    actions.append(act("is.workflow.actions.gettext", UUID=u_mout, WFTextActionText="out"))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Direction", WFInput=attach(out(u_mout, "Text")))
    )
    # Puts the school's code back on screen as the QR it was scanned from, and
    # scans one when nothing is stored yet. What is stored is the scanned
    # payload verbatim, so the image this draws is the one taped up by the
    # door — which is the point: a second phone can be set up from it, and it
    # is a way back into the Brightwheel app when the shortcut is the thing
    # misbehaving. It is not a secret being spread any further than it already
    # is; the same string sits in this shortcut's own storage, which its owner
    # can read. Sends nothing to Brightwheel either way, so priming a second
    # phone at the school costs neither a sign-in nor a check-in.
    actions.append(
        act(
            "is.workflow.actions.choosefrommenu",
            UUID=next(i),
            GroupingIdentifier=g_menu,
            WFControlFlowMode=1,
            WFMenuItemTitle="Show the school's code",
        )
    )
    u_qrc = next(i)
    actions.append(
        act(
            "is.workflow.actions.getstoredcontent",
            UUID=u_qrc,
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
        )
    )
    c_qrc = actions.count_matches(out(u_qrc, "Stored Content"))
    g_scan = next(i)
    u_qsc = next(i)
    actions.append(
        comment(
            "Scan a code when there is none saved.\n"
            "- Condition counts whether anything is stored, because an empty "
            'string still satisfies "has any value"\n'
            '- Condition 2 against 0 is "more than none", so the first branch is '
            "the one where a code exists\n"
            "- Nothing is stored until a run at the school has scanned it, so "
            "the second branch is the first run and a rotated code alike\n"
            "- Show Alert explains what to point the camera at, and its Cancel "
            "is the answer away from the school, where there is nothing to scan"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_scan,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_qrc, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Shown Code",
            WFInput=attach(out(u_qrc, "Stored Content")),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_scan, WFControlFlowMode=1))
    actions.append(
        act(
            "is.workflow.actions.alert",
            WFAlertActionTitle=ts("No school code saved yet"),
            WFAlertActionMessage=ts(
                "Point the camera at the check-in code on the school's sign-in tablet, "
                "and it will be saved and drawn back here."
            ),
            WFAlertActionCancelButtonShown=True,
        )
    )
    actions.append(act("is.workflow.actions.scanbarcode", UUID=u_qsc, WFScanCodeActionMode=0))
    actions.append(
        act(
            "is.workflow.actions.setstoredcontent",
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
            WFInput=ts(out(u_qsc, "QR/Barcodes")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Shown Code",
            WFInput=attach(out(u_qsc, "QR/Barcodes")),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_scan, WFControlFlowMode=2))

    c_shown = actions.count_matches(var("Shown Code"))
    g_qr = next(i)
    actions.append(
        comment(
            "Draw the code, or say there is none.\n"
            "- Condition counts the variable both branches above set, so a scan "
            "that was dismissed lands here rather than in the QR action\n"
            "- Drawing back what was just scanned is the confirmation that it "
            "was read and stored whole"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_qr,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_shown, "Count")),
        )
    )
    u_qri = next(i)
    # Low error correction. The redundancy in a QR code buys tolerance for
    # physical insult — creases, glare, a torn corner, a bad angle — and this
    # code is drawn on a phone screen and read off it, so it has none to
    # survive. What the levels do cost is size: the payload is 127 bytes, which
    # fits a version 6 symbol (41x41) at Low but needs a version 8 (49x49) at
    # the action's Medium default. Low spends that budget on bigger modules
    # instead, which is the thing that actually helps a camera.
    actions.append(
        act(
            "is.workflow.actions.generatebarcode",
            UUID=u_qri,
            CustomOutputName="School Code QR",
            WFQRErrorCorrectionLevel="Low",
            WFText=ts(var("Shown Code")),
        )
    )
    actions.append(act("is.workflow.actions.previewdocument", WFInput=attach(out(u_qri, "School Code QR"))))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_qr, WFControlFlowMode=1))
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("Still no school code saved"),
            WFNotificationActionBody=ts(
                "Nothing was scanned. A check-in run at the school scans the code by the door and keeps it."
            ),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_qr, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.exit"))

    # The only way to clear the school code from the device. Deleting the
    # shortcut takes the token with it, but the code lives in the shared store,
    # which outlives it — so the reinstall everyone reaches for first still
    # leaves a stale code behind. Sends nothing, so it is safe to reach by
    # accident or through Siri.
    actions.append(
        act(
            "is.workflow.actions.choosefrommenu",
            UUID=next(i),
            GroupingIdentifier=g_menu,
            WFControlFlowMode=1,
            WFMenuItemTitle="Forget saved sign-in and school code",
        )
    )
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey="BrightwheelSessionToken",
            WFStoredContentGlobalValue=False,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey=CODE_SENT_KEY,
            WFStoredContentGlobalValue=False,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("Forgotten"),
            WFNotificationActionBody=ts("The next run signs in again and asks you to scan the school's code."),
        )
    )
    actions.append(act("is.workflow.actions.exit"))
    actions.append(
        act("is.workflow.actions.choosefrommenu", UUID=next(i), GroupingIdentifier=g_menu, WFControlFlowMode=2)
    )
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_valid, WFControlFlowMode=2)
    )

    # Wanted In is 1 for a check-in and 0 for a check-out, which later gets
    # compared against whether the child is already checked in.
    c_dir = actions.count_matches(var("Direction"), "^in$")
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Wanted In", WFInput=attach(out(c_dir, "Count")))
    )
    # One dictionary keyed by direction, instead of an If that set the same
    # three variables twice. The lookups are unconditional, so each value is a
    # named action output and needs no Set Variable to survive the branch it
    # used to be written in: eleven actions became four.
    #
    # All three are read as text — into the request body and into notification
    # wording — never as an If input, which is the case a Dictionary Value
    # cannot serve (see actions.count_matches()).
    actions.append(
        comment(
            "Set the wording and the value Brightwheel expects.\n"
            "- The key is the direction, so in and out read the same three fields\n"
            "- Checked In Value goes into the request as true or false\n"
            "- Verb and Already Word are only used in notifications"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.dictionary",
            UUID=u_words,
            WFItems=dict_field(
                [
                    kv_dict(
                        "in",
                        [
                            kv("value", ts("true")),
                            kv("verb", ts("checked in")),
                            kv("already", ts("already checked in")),
                        ],
                    ),
                    kv_dict(
                        "out",
                        [
                            kv("value", ts("false")),
                            kv("verb", ts("checked out")),
                            kv("already", ts("already checked out")),
                        ],
                    ),
                ]
            ),
        )
    )
    for u_w, field, outname in (
        (u_civ, "value", "Checked In Value"),
        (u_verb, "verb", "Verb"),
        (u_already, "already", "Already Word"),
    ):
        actions.append(
            act(
                "is.workflow.actions.getvalueforkey",
                UUID=u_w,
                CustomOutputName=outname,
                WFGetDictionaryValueType="Value",
                WFDictionaryKey=ts(var("Direction"), f".{field}"),
                WFInput=attach(out(u_words, "Dictionary")),
            )
        )

    actions.append(
        comment(
            "--- SETUP ---\n"
            "These three values are requested when the shortcut is imported. To change "
            "one later, edit the matching Text action, or re-import the shortcut."
        )
    )
    names = {"code": "Check-In Code", "email": "Account Email", "password": "Account Password"}
    for key, _kind, prompt, blurb, default, prompt_default in SETUP:
        param, ident = "WFTextActionText", "is.workflow.actions.gettext"
        if env is not None:
            # Debug build: bake the real value in and ask nothing at import.
            default = env[ENV_KEYS[key]]
        else:
            questions.append(
                import_question(
                    len(actions), param, f"{prompt} — {blurb}" + QUESTION_NOTES.get(key, ""), prompt_default
                )
            )
        actions.append(act(ident, UUID=u[key], CustomOutputName=names[key], **{param: default}))
    if questions:
        # Appended to whichever question comes last rather than written into
        # SETUP, so reordering the questions cannot leave the note stranded on
        # a page whose button still works.
        questions[-1]["Text"] += LAST_QUESTION_NOTE

    # ---- session token, with interactive sign-in on failure ----
    #
    # Sign-in is two steps and needs a 6-digit code (see README). A background
    # automation cannot answer the prompt, but this branch only runs when the
    # token has already expired, so that run was failing regardless. Running the
    # shortcut by hand afterward completes it.
    u_get_token, u_probe = next(i), next(i)
    u_start, u_code, u_sess = next(i), next(i), next(i)
    u_tmatch, u_tgrp, u_zero = next(i), next(i), next(i)
    g_loop, g_signin, g_code, g_got, g_fail = (next(i) for _ in range(5))

    actions.append(
        comment(
            "--- SESSION ---\n"
            "Use the token saved by the last sign-in. There is none the first time, "
            "so the first run signs in and saves one.\n\n"
            "The token is kept under this shortcut rather than in the shared store, "
            "so deleting the shortcut clears it and a re-import signs in again. The "
            "school's code is kept in the shared store instead, and outlives both."
        )
    )
    actions.append(
        act(
            "is.workflow.actions.getstoredcontent",
            UUID=u_get_token,
            WFStoredContentKey="BrightwheelSessionToken",
            WFStoredContentGlobalValue=False,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Session Token",
            WFInput=attach(out(u_get_token, "Stored Content")),
        )
    )

    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_probe,
            Advanced=True,
            ShowHeaders=False,
            WFURL=f"{BASE}/users/me",
            WFHTTPMethod="GET",
            WFHTTPHeaders=dict_field(
                [
                    kv("Accept", ts("application/json")),
                    kv("X-Parse-Session-Token", ts(var("Session Token"))),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
        )
    )
    # E1200 is what an expired or absent token returns.
    c_bad = actions.count_matches(out(u_probe, "Contents of URL"), "E1200")
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Needs Sign In", WFInput=attach(out(c_bad, "Count")))
    )

    # The guardian id is the top-level object_id of this same response, so it
    # costs no extra request. Read by key name off the parsed body, because
    # "object_id" appears four times in it — the photo, an auth method and a
    # school invite all have one — and only the top-level key is the guardian.
    #
    # This used to be a Match Text anchored with ^ to the front of the body, on
    # the reasoning that the wanted object_id is the first key. It is, on the
    # wire. But Get Contents of URL parses the response and coerces a
    # re-serialization in Shortcuts' own key order, where first place is not
    # the API's to promise — measured on device at position six for a body of
    # this shape. The anchor held until it did not, and an empty guardian id
    # builds a URL that 404s and reads downstream as an empty roster.
    #
    # On a run that has to sign in this response is the E1200 error body
    # instead, which has no object_id, so the read comes back empty and the
    # second one below fills it in.
    u_gv, u_gt = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_gv,
            CustomOutputName="Guardian Id Value",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="object_id",
            WFInput=attach(out(u_probe, "Contents of URL")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_gt,
            CustomOutputName="Guardian Id Text",
            WFTextActionText=ts(out(u_gv, "Guardian Id Value")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Guardian Id",
            WFInput=attach(out(u_gt, "Guardian Id Text")),
        )
    )

    # ---- was a code sent a few minutes ago? ----
    #
    # Canceling the code prompt to go and read the email leaves a code in the
    # inbox. The send time is stored beside the token, and a run within ten
    # minutes of it asks for that code instead of sending another. A scheduled
    # run with a dead token gets the same benefit: it sends, stores the time,
    # and dies at the prompt it cannot answer, and the run by hand that follows
    # goes straight to the prompt. Read here, outside the sign-in branch, so
    # the loop below sees one number rather than a stored value.
    u_sent, u_now, u_since = next(i), next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.getstoredcontent",
            UUID=u_sent,
            WFStoredContentKey=CODE_SENT_KEY,
            WFStoredContentGlobalValue=False,
        )
    )
    actions.append(act("is.workflow.actions.date", UUID=u_now))
    actions.append(
        act(
            "is.workflow.actions.gettimebetweendates",
            UUID=u_since,
            WFInput=ts(out(u_sent, "Stored Content")),
            WFTimeUntilFromDate=ts(out(u_now, "Current Date")),
            WFTimeUntilUnit="Minutes",
        )
    )
    c_recent = actions.count_matches(out(u_since, "Time Between Dates"), CODE_SENT_RECENT)
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Sent Recently", WFInput=attach(out(c_recent, "Count")))
    )

    # ---- a code already on the clipboard? ----
    #
    # Someone who canceled the prompt, copied the code out of the email and
    # ran again should only have to tap Done. Read once, and only on a run
    # that has to sign in: iOS can ask before a shortcut reads the clipboard,
    # and an ordinary run has no business touching it. Kept only when it is
    # exactly six digits, so a stray number is never offered.
    g_clip = next(i)
    actions.append(
        comment(
            "Look at the clipboard, only when a sign-in is needed.\n"
            "- Condition checks Needs Sign In\n"
            "- Prefill is the clipboard when it is exactly six digits, else empty"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_clip,
            WFControlFlowMode=0,
            WFCondition=GREATER_THAN,
            WFNumberValue="0",
            WFInput=cond_input(var("Needs Sign In")),
        )
    )
    u_clip, u_cm, u_ct = next(i), next(i), next(i)
    actions.append(act("is.workflow.actions.getclipboard", UUID=u_clip))
    actions.append(
        act(
            "is.workflow.actions.text.match",
            UUID=u_cm,
            WFMatchTextPattern=r"^[0-9]{6}$",
            text=ts(out(u_clip, "Clipboard")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_ct,
            CustomOutputName="Clipboard Code",
            WFTextActionText=ts(out(u_cm, "Matches")),
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Prefill", WFInput=attach(out(u_ct, "Clipboard Code")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_clip, WFControlFlowMode=2))

    actions.append(
        comment(
            "Sign in again, up to five times.\n"
            "- The first pass skips the send when a code went out within ten "
            "minutes, and asks for that one; every later pass sends a fresh code "
            "first\n"
            "- Leaving the box empty sends another code instead of trying to "
            "use what was typed\n"
            "- Canceling the code prompt stops the whole shortcut; the next run "
            "within ten minutes asks for the code without sending again\n"
            "- A pass that gets a token clears Needs Sign In, so later passes do "
            "nothing"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.repeat.count",
            UUID=next(i),
            GroupingIdentifier=g_loop,
            WFControlFlowMode=0,
            WFRepeatCount=5,
        )
    )
    actions.append(
        comment(
            "Only act while the session is still not usable.\n"
            "- Condition checks whether an earlier pass already signed in"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_signin,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(var("Needs Sign In")),
        )
    )
    actions.append(
        comment(
            "Send a code, unless one went out within the last ten minutes.\n"
            "- Condition checks Sent Recently, which only the first pass can have\n"
            "- The send stores its time, and the prompt says where the code went\n"
            "- Otherwise the prompt is the short one, for a code already in hand"
        )
    )
    g_send, g_addr = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_send,
            WFControlFlowMode=0,
            WFCondition=LESS_THAN,
            WFNumberValue="1",
            WFInput=cond_input(var("Sent Recently")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_start,
            Advanced=True,
            ShowHeaders=False,
            WFURL=f"{BASE}/sessions/start",
            WFHTTPMethod="POST",
            WFHTTPBodyType="JSON",
            WFHTTPHeaders=dict_field(
                [
                    kv("Accept", ts("application/json")),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
            WFJSONValues=dict_field(
                [
                    kv_dict(
                        "user",
                        [
                            kv("email", ts(out(u["email"], "Account Email"))),
                            kv("password", ts(out(u["password"], "Account Password"))),
                        ],
                    ),
                ]
            ),
        )
    )
    # Remember when the code went out, for the next run.
    u_now2, u_fmt = next(i), next(i)
    actions.append(act("is.workflow.actions.date", UUID=u_now2))
    actions.append(
        act(
            "is.workflow.actions.format.date",
            UUID=u_fmt,
            WFDate=ts(out(u_now2, "Current Date")),
            WFDateFormatStyle="Custom",
            WFDateFormat=CODE_SENT_FORMAT,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setstoredcontent",
            WFStoredContentKey=CODE_SENT_KEY,
            WFStoredContentGlobalValue=False,
            WFInput=ts(out(u_fmt, "Formatted Date")),
        )
    )
    # Where the code went. The start response lists the masked address it
    # was sent to, and the prompt names it so someone with two inboxes opens
    # the right one. Read into the prompt, never branched on directly: the
    # branch below is only "was there one", through a count.
    u_to = next(i)
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_to,
            CustomOutputName="Sent To",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="2fa_code_sent_to",
            WFInput=attach(out(u_start, "Contents of URL")),
        )
    )
    c_to = actions.count_matches(out(u_to, "Sent To"))
    actions.append(
        comment(
            "Name the inbox the code went to, when the response says.\n"
            "- Condition counts whether the start response listed an address\n"
            "- Otherwise the prompt says to check your email"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_addr,
            WFControlFlowMode=0,
            WFCondition=GREATER_THAN,
            WFNumberValue="0",
            WFInput=cond_input(out(c_to, "Count")),
        )
    )
    u_p_addr = next(i)
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_p_addr,
            WFTextActionText=ts(CODE_PROMPT_SENT[0], out(u_to, "Sent To"), CODE_PROMPT_SENT[1]),
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Prompt Text", WFInput=attach(out(u_p_addr, "Text")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_addr, WFControlFlowMode=1))
    u_p_none = next(i)
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_p_none,
            WFTextActionText=CODE_PROMPT_SENT[0] + CODE_PROMPT_SENT_NOWHERE + CODE_PROMPT_SENT[1],
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Prompt Text", WFInput=attach(out(u_p_none, "Text")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_addr, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_send, WFControlFlowMode=1))
    u_p_short = next(i)
    actions.append(act("is.workflow.actions.gettext", UUID=u_p_short, WFTextActionText=CODE_PROMPT))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Prompt Text", WFInput=attach(out(u_p_short, "Text")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_send, WFControlFlowMode=2))
    # Only the first pass may skip the send, and only the first pass is
    # offered the clipboard. A code that was not entered, or was rejected,
    # means the next pass sends a fresh one and asks with an empty field.
    u_zero_sent = next(i)
    actions.append(act("is.workflow.actions.number", UUID=u_zero_sent, WFNumberActionNumber="0"))
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Sent Recently",
            WFInput=attach(out(u_zero_sent, "Number")),
        )
    )

    # Two sheets, one of which is shown. With a code on the clipboard the
    # field is a plain text one with that code already in it, and a prompt
    # that says nothing about leading zeros, whatever this pass did: the
    # person only has to tap Done. A text
    # field is deliberate there — a number field formats its default through
    # the locale and shows 654,321, while the value it returns is intact
    # (measured); a text default is shown as given, leading zero and all.
    #
    # Otherwise a number field, so the number pad comes up rather than the
    # full keyboard and the sheet stays short enough to read an email around.
    # A numeric field drops a leading zero as the next digit is typed: 012345
    # displays and returns as 12345. The padding below puts it back.
    c_pre = actions.count_matches(var("Prefill"), r"^[0-9]{6}$")
    g_pre = next(i)
    actions.append(
        comment(
            "Ask for the code, offering the clipboard when it holds one.\n"
            "- Condition counts whether Prefill is six digits\n"
            "- A text field shows the pasted code as it is; the number field "
            "is for typing\n"
            "- Both leave the answer in Answer"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_pre,
            WFControlFlowMode=0,
            WFCondition=GREATER_THAN,
            WFNumberValue="0",
            WFInput=cond_input(out(c_pre, "Count")),
        )
    )
    u_p_pre, u_code_pre = next(i), next(i)
    actions.append(act("is.workflow.actions.gettext", UUID=u_p_pre, WFTextActionText=CODE_PROMPT_PASTED))
    actions.append(
        act(
            "is.workflow.actions.ask",
            UUID=u_code_pre,
            WFAskActionPrompt=ts(out(u_p_pre, "Text")),
            WFInputType="Text",
            WFAskActionDefaultAnswer=ts(var("Prefill")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Answer",
            WFInput=attach(out(u_code_pre, "Provided Input")),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_pre, WFControlFlowMode=1))
    actions.append(
        act(
            "is.workflow.actions.ask",
            UUID=u_code,
            WFAskActionPrompt=ts(var("Prompt Text")),
            WFInputType="Number",
            WFAskActionAllowsDecimalNumbers=False,
            WFAskActionAllowsNegativeNumbers=False,
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Answer", WFInput=attach(out(u_code, "Provided Input")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_pre, WFControlFlowMode=2))
    # A rejected clipboard code is not offered again: a zero is not six digits.
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Prefill", WFInput=attach(out(u_zero_sent, "Number")))
    )
    # Left-pad the answer to six digits and keep only a real code. Five zeros
    # go in front, not six, so an empty answer stays five characters and fails
    # the match, and empty remains "send me another". One to six digits pass
    # and come out as exactly six; seven or more fail, as a typo should.
    # Measured on an iOS 27 simulator: empty -> no match; 7 -> 000007; a typed
    # 012345 arrives as 12345 and matches as 012345; 1234567 -> no match.
    #
    # Only a real code is worth exchanging. Anything else skips the exchange,
    # so the next pass calls /sessions/start again and a new code is sent.
    # Posting junk instead would burn an attempt and risks the API treating it
    # as a failed sign-in.
    u_pad, u_pm, u_pg = next(i), next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_pad,
            CustomOutputName="Padded Code",
            WFTextActionText=ts("00000", var("Answer")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.text.match",
            UUID=u_pm,
            WFMatchTextPattern=r"^0*([0-9]{6})$",
            text=ts(out(u_pad, "Padded Code")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.text.match.getgroup",
            UUID=u_pg,
            CustomOutputName="Code",
            WFGroupIndex="1",
            matches=attach(out(u_pm, "Matches")),
        )
    )
    c_code = actions.count_matches(out(u_pg, "Code"))
    actions.append(
        comment(
            "Only try the code if one was actually entered.\n"
            "- Condition counts whether the padded answer made a six-digit code\n"
            "- Anything else falls through, and the next pass sends a new code"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_code,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_code, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_sess,
            Advanced=True,
            ShowHeaders=False,
            WFURL=f"{BASE}/sessions",
            WFHTTPMethod="POST",
            WFHTTPBodyType="JSON",
            WFHTTPHeaders=dict_field(
                [
                    kv("Accept", ts("application/json")),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
            WFJSONValues=dict_field(
                [
                    kv_dict(
                        "user",
                        [
                            kv("email", ts(out(u["email"], "Account Email"))),
                            kv("password", ts(out(u["password"], "Account Password"))),
                        ],
                    ),
                    kv("2fa_code", ts(out(u_pg, "Code"))),
                ]
            ),
        )
    )
    # The token comes back as a top-level "token", not "session_token".
    actions.append(
        act("is.workflow.actions.gettext", UUID=u_tmatch, WFTextActionText=ts(out(u_sess, "Contents of URL")))
    )
    u_tm2 = next(i)
    actions.append(
        act(
            "is.workflow.actions.text.match",
            UUID=u_tm2,
            WFMatchTextPattern=r'"token"\s*:\s*"([^"]+)"',
            text=ts(out(u_tmatch, "Text")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.text.match.getgroup",
            UUID=u_tgrp,
            WFGroupIndex="1",
            matches=attach(out(u_tm2, "Matches")),
        )
    )
    c_got = actions.count_matches(out(u_tgrp, "Matched Text Group"))
    actions.append(
        comment(
            "Keep the new token when the code was accepted.\n"
            "- Condition counts whether a token came back\n"
            "- A wrong or expired code returns none, and the next pass asks again"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_got,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_got, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setstoredcontent",
            WFStoredContentKey="BrightwheelSessionToken",
            WFStoredContentGlobalValue=False,
            WFInput=ts(out(u_tgrp, "Matched Text Group")),
        )
    )
    # The code is used up, so the next sign-in starts with a fresh send.
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey=CODE_SENT_KEY,
            WFStoredContentGlobalValue=False,
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Session Token",
            WFInput=attach(out(u_tgrp, "Matched Text Group")),
        )
    )
    actions.append(act("is.workflow.actions.number", UUID=u_zero, WFNumberActionNumber="0"))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Needs Sign In", WFInput=attach(out(u_zero, "Number")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_got, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_code, WFControlFlowMode=2))
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_signin, WFControlFlowMode=2)
    )
    actions.append(
        act("is.workflow.actions.repeat.count", UUID=next(i), GroupingIdentifier=g_loop, WFControlFlowMode=2)
    )

    actions.append(
        comment(
            "Give up after five attempts.\n"
            "- Condition checks whether the session is still unusable\n"
            "- Nothing has been sent to Brightwheel about the children yet"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_fail,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(var("Needs Sign In")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("Nobody ", out(u_verb, "Verb")),
            WFNotificationActionBody=ts(
                "Could not sign in after five tries, so nothing was sent. "
                "Run this shortcut by hand and enter a code, or leave the "
                "box empty to have another sent."
            ),
        )
    )
    actions.append(act("is.workflow.actions.exit"))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_fail, WFControlFlowMode=2))

    # A run that signed in read its guardian id off an E1200 body, so it has
    # none. Ask again now that there is a working token. Gated on emptiness so
    # the ordinary path still makes exactly one /users/me call.
    g_gid = next(i)
    c_nogid = actions.count_matches(var("Guardian Id"))
    actions.append(
        comment(
            "Fetch the guardian id if signing in meant the first read missed it.\n"
            "- Condition counts whether an id was found on the probe\n"
            "- The probe body is the auth error on a run that had to sign in"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_gid,
            WFControlFlowMode=0,
            WFCondition=0,
            WFNumberValue="1",
            WFInput=cond_input(out(c_nogid, "Count")),
        )
    )
    u_me2, u_gv2, u_gt2 = (next(i) for _ in range(3))
    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_me2,
            Advanced=True,
            ShowHeaders=False,
            WFURL=f"{BASE}/users/me",
            WFHTTPMethod="GET",
            WFHTTPHeaders=dict_field(
                [
                    kv("Accept", ts("application/json")),
                    kv("X-Parse-Session-Token", ts(var("Session Token"))),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_gv2,
            CustomOutputName="Guardian Id Retry Value",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="object_id",
            WFInput=attach(out(u_me2, "Contents of URL")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_gt2,
            CustomOutputName="Guardian Id Retry",
            WFTextActionText=ts(out(u_gv2, "Guardian Id Retry Value")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Guardian Id",
            WFInput=attach(out(u_gt2, "Guardian Id Retry")),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_gid, WFControlFlowMode=2))

    # Nothing below this works without an id, and an empty one is invisible: it
    # builds a perfectly well-formed URL that answers 404, whose body has no
    # students in it, which the roster guard then reports as Brightwheel having
    # returned no children. That misfiled a broken read as a broken API for a
    # whole morning. Guarded here, at the boundary it belongs to, so it says
    # what actually went wrong.
    #
    # Exit rather than a flag: this is still top level, before the attempt
    # Repeat, which is the only place this file uses Exit.
    g_hasgid = next(i)
    c_hasgid = actions.count_matches(var("Guardian Id"))
    actions.append(
        comment(
            "Stop if the account could not be identified.\n"
            "- Condition counts whether a guardian id was read\n"
            "- An empty one would 404 the roster call and read as an empty roster"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_hasgid,
            WFControlFlowMode=0,
            WFCondition=0,
            WFNumberValue="1",
            WFInput=cond_input(out(c_hasgid, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("Nobody ", out(u_verb, "Verb")),
            WFNotificationActionBody=ts(
                "Could not read which Brightwheel account this is, so "
                "nothing was sent. Run this shortcut by hand to try "
                "again."
            ),
        )
    )
    actions.append(act("is.workflow.actions.exit"))
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_hasgid, WFControlFlowMode=2)
    )

    # ---- attempt loop: school code, then every child ----
    #
    # Two passes. The first sends; if a child comes back with the school code
    # rejected, the stored code is deleted and a second pass is requested, which
    # finds nothing stored and scans a fresh one. "First run" and "the code
    # rotated" are therefore the same branch — deleting the stored code is what
    # turns one into the other.
    #
    # Children are a Repeat rather than an unrolled pair so the retry re-enters
    # the same actions instead of a second copy. Each Repeat Item is a whole
    # child record, so the name, the id and the room are read off it by key
    # path rather than looked up against anything.
    u_one, u_zero2 = next(i), next(i)
    u_gc, u_scan, u_cdict = next(i), next(i), next(i)
    u_csec, u_csid, u_st, u_si = (next(i) for _ in range(4))
    u_body, u_resp, u_rtext = next(i), next(i), next(i)
    g_attempt, g_doit, g_have, g_sigs = (next(i) for _ in range(4))
    g_kids, g_skip, g_res, g_stale = (next(i) for _ in range(4))

    actions.append(
        comment(
            "--- WHO TO SEND FOR ---\n"
            "Nobody is named in here. The children come from Brightwheel itself, a "
            "little further down, which is why adding or moving one needs no "
            "rebuild. What this sets is the flag that lets the whole send run a "
            "second time if the school's code turns out to be stale."
        )
    )
    actions.append(act("is.workflow.actions.number", UUID=u_one, WFNumberActionNumber="1"))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Send Needed", WFInput=attach(out(u_one, "Number")))
    )

    actions.append(
        comment(
            "Send, and send again once if the school code turned out to be stale.\n"
            "- Send Needed starts at yes, and is set again only by a rejected code\n"
            "- A child already in the right state is skipped, so a second pass "
            "cannot double up on anyone who already succeeded"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.repeat.count",
            UUID=next(i),
            GroupingIdentifier=g_attempt,
            WFControlFlowMode=0,
            WFRepeatCount=2,
        )
    )
    actions.append(
        comment(
            "Do nothing on the second pass unless one was asked for.\n"
            "- Condition checks whether a send is still outstanding\n"
            "- Only a rejected school code asks for another pass"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_doit,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(var("Send Needed")),
        )
    )
    actions.append(act("is.workflow.actions.number", UUID=u_zero2, WFNumberActionNumber="0"))
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Send Needed", WFInput=attach(out(u_zero2, "Number")))
    )

    # --- school code ---
    if env is not None:
        # Debug builds seed the store so a test import never has to scan.
        u_seed = next(i)
        actions.append(
            act(
                "is.workflow.actions.gettext",
                UUID=u_seed,
                CustomOutputName="Debug School Code",
                WFTextActionText=json.dumps(
                    {
                        "secret": env["BRIGHTWHEEL_SCHOOL_SECRET"],
                        "school_id": env["BRIGHTWHEEL_SCHOOL_ID"],
                        "signatures_enabled": False,
                    },
                    separators=(",", ":"),
                ),
            )
        )
        actions.append(
            act(
                "is.workflow.actions.setstoredcontent",
                WFStoredContentKey="BrightwheelSchoolCode",
                WFStoredContentGlobalValue=True,
                WFInput=ts(out(u_seed, "Debug School Code")),
            )
        )
    actions.append(
        act(
            "is.workflow.actions.getstoredcontent",
            UUID=u_gc,
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
        )
    )
    c_code = actions.count_matches(out(u_gc, "Stored Content"))
    actions.append(
        comment(
            "Scan the school's code when there is none saved.\n"
            "- Condition counts whether anything is stored\n"
            "- Nothing is stored on the first run, or after a stale code was "
            "forgotten below\n"
            "- Show Alert explains what to point the camera at before it opens"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_have,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_code, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable", WFVariableName="School Code", WFInput=attach(out(u_gc, "Stored Content"))
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_have, WFControlFlowMode=1))
    actions.append(
        act(
            "is.workflow.actions.alert",
            WFAlertActionTitle=ts("Brightwheel"),
            WFAlertActionMessage=ts(
                "The school's check-in QR code is needed. Point the camera at the code on the sign-in tablet."
            ),
            WFAlertActionCancelButtonShown=True,
        )
    )
    actions.append(act("is.workflow.actions.scanbarcode", UUID=u_scan, WFScanCodeActionMode=0))
    actions.append(
        act(
            "is.workflow.actions.setstoredcontent",
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
            WFInput=ts(out(u_scan, "QR/Barcodes")),
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="School Code", WFInput=attach(out(u_scan, "QR/Barcodes")))
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_have, WFControlFlowMode=2))

    # Pulled out with Match Text, not Detect Dictionary + Get Dictionary Value.
    # That pair was banned from the check path for never producing a working
    # branch, and was left here on the assumption it behaved on plain text. It
    # does not: school_id came back empty and Brightwheel answered E1205 "You
    # must specify the school".
    for key, varname, u_m, u_g, u_t in (
        ("secret", "School Secret", u_csec, u_cdict, u_st),
        ("school_id", "School Id", u_csid, u_si, next(i)),
    ):
        actions.append(
            act(
                "is.workflow.actions.text.match",
                UUID=u_m,
                WFMatchTextPattern=rf'"{key}"\s*:\s*"([^"]+)"',
                text=ts(var("School Code")),
            )
        )
        actions.append(
            act(
                "is.workflow.actions.text.match.getgroup",
                UUID=u_g,
                WFGroupIndex="1",
                matches=attach(out(u_m, "Matches")),
            )
        )
        actions.append(
            act("is.workflow.actions.gettext", UUID=u_t, WFTextActionText=ts(out(u_g, "Matched Text Group")))
        )
        actions.append(act("is.workflow.actions.setvariable", WFVariableName=varname, WFInput=attach(out(u_t, "Text"))))

    c_sigs = actions.count_matches(var("School Code"), r'"signatures_enabled"\s*:\s*(true|1)')
    actions.append(
        comment(
            "Warn if the school has started requiring signatures.\n"
            "- Condition counts whether the scanned code says signatures are on\n"
            "- Check-ins send no signature, so they would start failing"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_sigs,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_sigs, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("This school now requires signatures"),
            WFNotificationActionBody=ts("These shortcuts do not send one, so check-ins may start failing."),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_sigs, WFControlFlowMode=2))

    # --- who the children are, asked rather than baked in ---
    #
    # One call returns the roster and each child's current state together, so
    # they cannot disagree, and it replaces the per-child activities read.
    u_tz, u_rost, u_match = next(i), next(i), next(i)
    g_stale2, g_g1, g_g3, g_run = (next(i) for _ in range(4))
    g_rooms, g_room0, g_room2 = (next(i) for _ in range(3))

    # The device's own zone. time_zone is required by the endpoint, and while
    # its value looked inert in testing that test could not tell an inert
    # parameter from a day boundary nobody had crossed. UTC would put the
    # boundary in the middle of the afternoon; the local zone puts it at local midnight.
    actions.append(act("is.workflow.actions.date", UUID=u_tz))
    u_tzf, u_tzt = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.format.date",
            UUID=u_tzf,
            WFDate=ts(out(u_tz, "Current Date")),
            WFDateFormatStyle="Custom",
            WFDateFormat="VV",
        )
    )
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_tzt,
            CustomOutputName="Time Zone",
            WFTextActionText=ts(out(u_tzf, "Formatted Date")),
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Time Zone", WFInput=attach(out(u_tzt, "Time Zone")))
    )

    actions.append(
        comment(
            "Ask Brightwheel who the children are and where they stand.\n"
            "- One call returns the roster and each child's current state\n"
            "- Nothing about the family is built into this shortcut"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_rost,
            Advanced=True,
            ShowHeaders=False,
            WFHTTPMethod="GET",
            WFURL=ts(
                f"{BASE}/guardians/",
                var("Guardian Id"),
                "/students_for_checkin?school_id=",
                var("School Id"),
                "&secret=",
                var("School Secret"),
                "&time_zone=",
                var("Time Zone"),
            ),
            WFHTTPHeaders=dict_field(
                [
                    kv("Accept", ts("application/json")),
                    kv("X-Parse-Session-Token", ts(var("Session Token"))),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
        )
    )

    # The roster call is now the first thing to see a rotated school code, so
    # the rescan has to be triggered from here as well as from the check-in
    # response. Deleting the stored code is what turns the next pass into a
    # fresh scan; without it the second pass reads the same stale code back.
    c_stale2 = actions.count_matches(out(u_rost, "Contents of URL"), r'"secret"\s*:\s*"The given secret')
    actions.append(
        comment(
            "Rescan if the school's code was rotated.\n"
            "- Condition counts whether the roster call rejected the code\n"
            "- Forgetting it makes the second pass scan a fresh one"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_stale2,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_stale2, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Send Needed", WFInput=attach(out(u_one, "Number")))
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("The school's check-in code has changed"),
            WFNotificationActionBody=ts("Scanning the new one and trying again."),
        )
    )
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_stale2, WFControlFlowMode=2)
    )

    # The roster is read as a dictionary, not matched as text. Get Contents of
    # URL parses JSON, so what a Match Text would see is a re-serialization
    # whose key order is Shortcuts' own — measured on device, the student's
    # object_id came after the photo's. Key *names* survive that; positions and
    # adjacency do not, which is fatal to any pattern pairing several fields.
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_match,
            CustomOutputName="Students",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="students",
            WFInput=attach(out(u_rost, "Contents of URL")),
        )
    )
    u_cm = next(i)
    actions.append(
        act(
            "is.workflow.actions.count",
            UUID=u_cm,
            WFCountType="Items",
            WFInput=attach(out(u_match, "Students")),
            Input=attach(out(u_match, "Students")),
        )
    )

    # One guard still counts a key name in the body text, which is safe because
    # a single key-value pair does not depend on order. Each well-formed child
    # contributes exactly one "checked_in"; a room entry that has lost the key
    # is the one malformation the per-child loop below cannot see, because such
    # a child still has exactly one room.
    u_sm, u_cs = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.text.match",
            UUID=u_sm,
            WFMatchTextPattern=r'"checked_in"\s*:',
            text=ts(out(u_rost, "Contents of URL")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.count",
            UUID=u_cs,
            WFCountType="Items",
            WFInput=attach(out(u_sm, "Matches")),
            Input=attach(out(u_sm, "Matches")),
        )
    )

    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Roster OK", WFInput=attach(out(u_one, "Number")))
    )

    def guard(
        group: str, count_uuid: str, count_name: str, condition: int, number: str, message: str, blurb: str
    ) -> None:
        """Notify and clear Roster OK when a guard fires.

        A flag rather than Exit: this sits two blocks deep inside the attempt
        Repeat, and Exit is only ever used at the top level in this file.
        """
        actions.append(comment(blurb))
        actions.append(
            act(
                "is.workflow.actions.conditional",
                UUID=next(i),
                GroupingIdentifier=group,
                WFControlFlowMode=0,
                WFCondition=condition,
                WFNumberValue=number,
                WFInput=cond_input(out(count_uuid, count_name)),
            )
        )
        actions.append(
            act(
                "is.workflow.actions.notification",
                WFNotificationActionTitle=ts("Nobody ", out(u_verb, "Verb")),
                WFNotificationActionBody=ts(message),
            )
        )
        actions.append(
            act("is.workflow.actions.setvariable", WFVariableName="Roster OK", WFInput=attach(out(u_zero2, "Number")))
        )
        actions.append(
            act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=group, WFControlFlowMode=2)
        )

    guard(
        g_g1,
        u_cm,
        "Count",
        0,
        "1",
        "Brightwheel did not return a roster, so nothing was sent. Open the app to check, and run this by hand.",
        "Did Brightwheel list any children?\n"
        "- Condition counts the children in the reply\n"
        "- An error reply has none, and would read as nobody to send for",
    )

    def diff_guard(group: str, a_uuid: str, a_name: str, b_uuid: str, b_name: str, message: str, blurb: str) -> None:
        u = next(i)
        actions.append(
            act(
                "is.workflow.actions.math",
                UUID=u,
                WFInput=attach(out(a_uuid, a_name)),
                WFMathOperation="-",
                WFMathOperand=attach(out(b_uuid, b_name)),
            )
        )
        guard(group, u, "Calculation Result", 2, "0", message, blurb)

    diff_guard(
        g_g3,
        u_cm,
        "Count",
        u_cs,
        "Count",
        "A room in the roster has no check-in state, so nothing was sent rather than sending for only some of them.",
        "Does every room entry carry a state?\n"
        "- Condition counts children minus check-in states\n"
        "- A room that has lost the key reads as nobody to send for",
    )

    # Per child, exactly one room. The counts above compare totals, and two
    # children can cancel each other out: one with an empty room_states and one
    # listed in two rooms leaves every total correct while both children are
    # wrong. The first would be sent with an empty room id, the second with a
    # guess. Counting a child's own entries is the only check that cannot be
    # canceled by another child.
    #
    # A second pass rather than a test inside the sending loop, because the run
    # is all-or-nothing: by the child that looks wrong, the ones before it have
    # already been sent.
    #
    # Two conditions rather than one "is not equal to". Only less-than and
    # greater-than are proven here, and a multi-condition row imports empty
    # (see ARCHITECTURE.md). Two branches also carry two messages, which says
    # more than one about a count being wrong.
    actions.append(
        comment(
            "Look at each child's own rooms in turn.\n"
            "- One pass that sends nothing, so a bad child stops the whole run\n"
            "- Both checks below fire per child, so two bad children say so twice"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.repeat.each",
            UUID=next(i),
            GroupingIdentifier=g_rooms,
            WFControlFlowMode=0,
            WFInput=attach(out(u_match, "Students")),
        )
    )
    # "Repeat Item 2" for the same reason the sending loop uses it: one
    # enclosing Repeat, and the conditionals in between do not renumber.
    u_rooms, u_nr = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_rooms,
            CustomOutputName="Child Rooms",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="room_states",
            WFInput=attach(var("Repeat Item 2")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.count",
            UUID=u_nr,
            WFCountType="Items",
            WFInput=attach(out(u_rooms, "Child Rooms")),
            Input=attach(out(u_rooms, "Child Rooms")),
        )
    )
    guard(
        g_room0,
        u_nr,
        "Count",
        0,
        "1",
        "A child in the roster has no room, so nothing was sent rather than sending for only some of them.",
        "Does this child have a room at all?\n"
        "- Condition counts this child's own room entries\n"
        "- None means there is no room to send them to",
    )
    guard(
        g_room2,
        u_nr,
        "Count",
        2,
        "1",
        "A child is listed in more than one room, so nothing was sent rather than guessing which one to use.",
        "Is this child in more than one room?\n"
        "- Condition counts this child's own room entries\n"
        "- The room to send would be a guess, so nobody is",
    )
    actions.append(
        act("is.workflow.actions.repeat.each", UUID=next(i), GroupingIdentifier=g_rooms, WFControlFlowMode=2)
    )

    actions.append(
        comment(
            "Only send if every check above agreed.\n"
            "- Condition counts whether the roster survived all of them\n"
            "- Anything else has already said why, and sends nothing"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_run,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(var("Roster OK")),
        )
    )

    # --- one pass over the children ---
    actions.append(
        comment(
            "Work through the children in turn.\n"
            "- Each child arrives whole, so their name, id and room are read "
            "straight off them rather than looked up\n"
            "- Their current state is the checked_in flag on the room they are in, "
            "which came back with the roster and so cannot disagree with it"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.repeat.each",
            UUID=next(i),
            GroupingIdentifier=g_kids,
            WFControlFlowMode=0,
            WFInput=attach(out(u_match, "Students")),
        )
    )
    # "Repeat Item 2", not "Repeat Item": this loop sits inside the retry
    # Repeat, and a count-style outer loop shifts the numbering just as a
    # nested Repeat with Each does. Verified on device with a probe — with the
    # unnumbered name the item came back empty, the roster lookup found
    # nothing, and notifications showed a blank child name.
    #
    # Captured into Child Name here so the numbered variable appears exactly
    # once; if the nesting ever changes, this is the only line to revisit.
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Child Match", WFInput=attach(var("Repeat Item 2")))
    )

    # Read by key path off this child's entry. Array indices in a dot path are
    # 1-based, so room_states.1 is the first (and, per the guards above, only)
    # room entry.
    for varname, key, outname in (
        ("Child Id", "student.object_id", "Child Id Value"),
        ("Child Name", "student.first_name", "Child Name Value"),
        ("Child Room", "room_states.1.room.object_id", "Child Room Value"),
    ):
        u_v, u_t = next(i), next(i)
        actions.append(
            act(
                "is.workflow.actions.getvalueforkey",
                UUID=u_v,
                CustomOutputName=outname,
                WFGetDictionaryValueType="Value",
                WFDictionaryKey=key,
                WFInput=attach(var("Child Match")),
            )
        )
        actions.append(
            act(
                "is.workflow.actions.gettext",
                UUID=u_t,
                CustomOutputName=varname + " Text",
                WFTextActionText=ts(out(u_v, outname)),
            )
        )
        actions.append(
            act("is.workflow.actions.setvariable", WFVariableName=varname, WFInput=attach(out(u_t, varname + " Text")))
        )

    # The state is a boolean, and a boolean does not coerce to text. Read the
    # room entry it lives in — that does coerce, to {"checked_in":true,...} —
    # and match the one pair out of it, which no key order can disturb.
    u_rs, u_rst = next(i), next(i)
    actions.append(
        act(
            "is.workflow.actions.getvalueforkey",
            UUID=u_rs,
            CustomOutputName="Room State",
            WFGetDictionaryValueType="Value",
            WFDictionaryKey="room_states.1",
            WFInput=attach(var("Child Match")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_rst,
            CustomOutputName="Room State Text",
            WFTextActionText=ts(out(u_rs, "Room State")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.setvariable",
            WFVariableName="Room State Text",
            WFInput=attach(out(u_rst, "Room State Text")),
        )
    )

    # Is the child checked in right now? 1 or 0, same shape as Wanted In.
    # It has to be a count: the comparison below pastes two values and matches
    # ^(01|10)$, so the literal "true" would give "true1", never match, and
    # every child would be skipped in both directions while the notification
    # said "no change".
    c_in = actions.count_matches(var("Room State Text"), r'"checked_in"\s*:\s*true')
    # Two runtime numbers cannot be compared directly — an If tests a variable
    # against a literal, not against another variable. Pasting them together
    # gives 11 or 00 when they agree and 10 or 01 when they differ, which a
    # fixed pattern can match.
    u_pair = next(i)
    actions.append(
        act("is.workflow.actions.gettext", UUID=u_pair, WFTextActionText=ts(out(c_in, "Count"), var("Wanted In")))
    )
    c_send = actions.count_matches(out(u_pair, "Text"), "^(01|10)$")
    actions.append(
        comment(
            "Skip anyone who needs no change.\n"
            "- Condition counts whether their state and this run disagree\n"
            "- This is also what makes a second pass safe after a rescan\n"
            "- If the state cannot be read, the request is sent anyway"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_skip,
            WFControlFlowMode=0,
            WFCondition=0,
            WFNumberValue="1",
            WFInput=cond_input(out(c_send, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionBody=ts(var("Child Name"), " was ", out(u_already, "Already Word"), " — no change"),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_skip, WFControlFlowMode=1))

    actions.append(
        act(
            "is.workflow.actions.gettext",
            UUID=u_body,
            WFTextActionText=ts(
                '{"checkins":[{"actor":{"object_id":"',
                var("Guardian Id"),
                '"},"health_screen":{"questions":[]},"room":{"object_id":"',
                var("Child Room"),
                '"},"checked_in":',
                out(u_civ, "Checked In Value"),
                ",",
                '"target":{"object_id":"',
                var("Child Id"),
                '"},"note":""}],"school_id":"',
                var("School Id"),
                '","secret":"',
                var("School Secret"),
                '","checkin_code":"',
                out(u["code"], "Check-In Code"),
                '"}',
            ),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.downloadurl",
            UUID=u_resp,
            Advanced=True,
            ShowHeaders=False,
            WFURL=f"{BASE}/checkins/",
            WFHTTPMethod="POST",
            WFHTTPBodyType="File",
            WFRequestVariable=attach(out(u_body, "Text")),
            WFFormValues=dict_field([]),
            WFHTTPHeaders=dict_field(
                [
                    kv("Content-Type", ts("application/json")),
                    kv("Accept", ts("application/json")),
                    kv("X-Parse-Session-Token", ts(var("Session Token"))),
                    kv("X-Client-Name", ts(CLIENT_NAME)),
                    kv("X-Client-Version", ts(CLIENT_VERSION)),
                ]
            ),
        )
    )
    actions.append(
        act("is.workflow.actions.gettext", UUID=u_rtext, WFTextActionText=ts(out(u_resp, "Contents of URL")))
    )
    c_ok = actions.count_matches(out(u_resp, "Contents of URL"), '"event_date"')
    actions.append(
        comment(
            "Report the result.\n"
            "- Condition counts whether Brightwheel really recorded something\n"
            "- Otherwise branch works out whether the school code was the problem"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_res,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_ok, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionBody=ts("✅ ", var("Child Name"), " ", out(u_verb, "Verb")),
        )
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_res, WFControlFlowMode=1))
    c_stale = actions.count_matches(out(u_resp, "Contents of URL"), r'"secret"\s*:\s*"The given secret')
    actions.append(
        comment(
            "Tell a stale school code apart from anything else.\n"
            "- Condition counts whether Brightwheel rejected the school's code\n"
            "- Forgetting it makes this run scan a fresh one and try again\n"
            "- Otherwise branch just reports what Brightwheel said"
        )
    )
    actions.append(
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_stale,
            WFControlFlowMode=0,
            WFCondition=2,
            WFNumberValue="0",
            WFInput=cond_input(out(c_stale, "Count")),
        )
    )
    actions.append(
        act(
            "is.workflow.actions.deletestoredcontent",
            WFStoredContentKey="BrightwheelSchoolCode",
            WFStoredContentGlobalValue=True,
        )
    )
    actions.append(
        act("is.workflow.actions.setvariable", WFVariableName="Send Needed", WFInput=attach(out(u_one, "Number")))
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("The school's check-in code has changed"),
            WFNotificationActionBody=ts("Scanning the new one and trying again."),
        )
    )
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_stale, WFControlFlowMode=1)
    )
    actions.append(
        act(
            "is.workflow.actions.notification",
            WFNotificationActionTitle=ts("⚠️ ", var("Child Name"), " not ", out(u_verb, "Verb")),
            WFNotificationActionBody=ts(
                out(u_rtext, "Text"),
                "\n\nIf this says the session expired, run this shortcut by hand to sign in again.",
            ),
        )
    )
    actions.append(
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_stale, WFControlFlowMode=2)
    )
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_res, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_skip, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.repeat.each", UUID=next(i), GroupingIdentifier=g_kids, WFControlFlowMode=2))
    # closes the If that runs the loop only when all four guards agreed
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_run, WFControlFlowMode=2))
    actions.append(act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_doit, WFControlFlowMode=2))
    actions.append(
        act("is.workflow.actions.repeat.count", UUID=next(i), GroupingIdentifier=g_attempt, WFControlFlowMode=2)
    )

    return name, document(
        name, actions, glyph=glyph, color=color, questions=questions, input_classes=["WFStringContentItem"]
    )


def build_wrapper(direction: str) -> tuple[str, dict[str, Any]]:
    """A trigger carrier: a first-run setup guide, then the direction and the call.

    Direction lives here rather than in the shortcut that does the work, so it
    is structural — which wrapper ran decides it. Nothing infers it from the
    clock, so editing a trigger's time range on the device cannot make it wrong.

    Run Shortcut resolves its target by name: the workflowIdentifier below is
    freshly minted and matches nothing, and an imported copy still finds
    "Brightwheel Attendance" and passes its input. Verified on device.
    """
    checking_in = direction == "in"
    title = "Brightwheel Check In" if checking_in else "Brightwheel Check Out"
    word = "in" if checking_in else "out"
    # The trigger each wrapper wants, named rather than offered as a choice —
    # the bundled diagram rings this exact row in the action picker. Both want
    # Arrive: the school asks that children be checked out on the parents' way
    # in, so the check-out fires on arriving to pick up rather than on leaving
    # with them. The time range is what tells the two apart, and it lives on
    # the device — see the direction note below for why that is still safe.
    trigger = "Arrive"
    when = "drop-off time" if checking_in else "pickup time"
    # A plane arriving for in, departing for out: a matched pair that reads as
    # direction and nothing else. (Not 62466/62467, which are a plane on a
    # runway rather than the arriving/departing pair.) Sunrise/sunset was the other candidate and was
    # dropped — its arrow and its sun point opposite ways, so no assignment of
    # it is truthful. Green and red match Brightwheel's own colors. All four
    # values checked on a simulator; the palette numbers are keys, not RGB.
    glyph = 62022 if checking_in else 62021
    color = 4292093695 if checking_in else 4282601983

    i = random_uuids()
    u_text = next(i)
    u_seen, u_gt, u_gm, u_gc = next(i), next(i), next(i), next(i)
    u_b64, u_dec, u_mark = next(i), next(i), next(i)
    g_setup = next(i)

    # Each wrapper explains its own trigger, because each needs a different one
    # and because Brightwheel Attendance works perfectly well without either of
    # them. Setup instructions for an optional extra do not belong in the
    # shortcut that extra is optional to.
    diagram = Path(__file__).parent / "assets" / f"setup-check-{'in' if checking_in else 'out'}.png"
    diagram_b64 = base64.b64encode(diagram.read_bytes()).decode()

    actions = [
        comment(
            f"{title}\n\n"
            f"Attach the {trigger} trigger to this shortcut, set to {when}. "
            f"All it does is tell Brightwheel Attendance to check the children "
            f"{word}.\n\n"
            "Both wrappers arrive at the same school, so the time range is what "
            "tells them apart. Check out on the way in, not on the way out: the "
            "school asks that children be checked out as parents walk in, and an "
            "arrival fires while you are still parking.\n\n"
            "The direction lives here rather than in the shortcut that does the "
            "work, so which trigger fired decides it. Nothing is worked out from "
            "the time of day, which means changing a trigger's hours cannot make "
            "it send the wrong direction."
        ),
        # --- first run only: how to attach the trigger ---
        #
        # Scoped to this shortcut rather than the shared store, so Check In and
        # Check Out each explain themselves once and neither speaks for the
        # other. Stored content comes back empty the first time, and an empty
        # string still satisfies "has any value", so presence is measured with a
        # match count rather than tested.
        act(
            "is.workflow.actions.getstoredcontent",
            UUID=u_seen,
            WFStoredContentKey="BrightwheelSetupShown",
            WFStoredContentGlobalValue=False,
        ),
        act("is.workflow.actions.gettext", UUID=u_gt, WFTextActionText=ts(out(u_seen, "Stored Content"))),
        act("is.workflow.actions.text.match", UUID=u_gm, WFMatchTextPattern=r"\S", text=ts(out(u_gt, "Text"))),
        act(
            "is.workflow.actions.count",
            UUID=u_gc,
            WFCountType="Items",
            WFInput=attach(out(u_gm, "Matches")),
            Input=attach(out(u_gm, "Matches")),
        ),
        comment(
            "Show the setup guide the first time this shortcut runs.\n"
            "- Condition counts whether anything is stored yet\n"
            "- Nothing is stored until the guide has been shown once\n"
            "- Attaching the trigger is the one step that cannot be done for "
            "you, because the placemark only exists on your device"
        ),
        act(
            "is.workflow.actions.conditional",
            UUID=next(i),
            GroupingIdentifier=g_setup,
            WFControlFlowMode=0,
            WFCondition=0,
            WFNumberValue="1",
            WFInput=cond_input(out(u_gc, "Count")),
        ),
        act("is.workflow.actions.gettext", UUID=u_b64, CustomOutputName="Setup Diagram", WFTextActionText=diagram_b64),
        act(
            "is.workflow.actions.base64encode",
            UUID=u_dec,
            WFEncodeMode="Decode",
            WFInput=attach(out(u_b64, "Setup Diagram")),
        ),
        act("is.workflow.actions.showresult", Text=ts(out(u_dec, "Base64 Encoded"))),
        # Something has to follow Show Content. Left last, the image becomes the
        # shortcut's own output, and handing an image back to the caller needs
        # consent — "Allow ... to output 1 image?" on the very run that is
        # trying to be helpful.
        act("is.workflow.actions.nothing"),
        act("is.workflow.actions.gettext", UUID=u_mark, CustomOutputName="Setup Mark", WFTextActionText="shown"),
        act(
            "is.workflow.actions.setstoredcontent",
            WFStoredContentKey="BrightwheelSetupShown",
            WFStoredContentGlobalValue=False,
            WFInput=ts(out(u_mark, "Setup Mark")),
        ),
        act("is.workflow.actions.conditional", UUID=next(i), GroupingIdentifier=g_setup, WFControlFlowMode=2),
        comment(
            "Hand the direction to Brightwheel Attendance.\n"
            "- Attendance does all the work; this shortcut only says which way\n"
            "- Which trigger fired decides it, so editing a trigger's hours "
            "cannot make it send the wrong direction"
        ),
        act("is.workflow.actions.gettext", UUID=u_text, CustomOutputName="Direction", WFTextActionText=word),
        act(
            "is.workflow.actions.runworkflow",
            WFWorkflowName=ATTENDANCE,
            WFWorkflow={"isSelf": False, "workflowIdentifier": next(i), "workflowName": ATTENDANCE},
            WFInput=attach(out(u_text, "Direction")),
        ),
    ]
    return title, document(title, actions, glyph=glyph, color=color, input_classes=[])


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Generate, validate, and sign the Brightwheel Shortcuts.",
        epilog=(
            "examples:\n"
            "  %(prog)s dist\n"
            "  %(prog)s dist-debug --debug\n"
            "  %(prog)s dist-test --env-file tests/fixtures/test.env --api-base https://localhost:8788/api/v1"
        ),
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    ap.add_argument(
        "dest", nargs="?", default=Path(), type=Path, help="directory to write the .xml and .shortcut files into"
    )
    ap.add_argument("--debug", action="store_true", help="bake .env values in and emit no setup questions")
    ap.add_argument(
        "--env-file",
        metavar="PATH",
        type=Path,
        help="bake this env file in instead of .env; use for test builds so real credentials never reach the artifact",
    )
    ap.add_argument(
        "--api-base",
        metavar="URL",
        default=BASE,
        help="point the shortcuts at a different API root; used by the integration tests to reach the mock Brightwheel",
    )
    ap.add_argument("--unsigned", action="store_true", help="write and validate the XML but do not sign it")
    argcomplete.autocomplete(ap)
    return ap


def run(args: argparse.Namespace) -> None:
    """Build all three shortcuts into `args.dest`."""
    # build() reads BASE at call time, so setting it here is enough.
    global BASE
    BASE = args.api_base

    env = None
    if args.env_file:
        env = load_env(args.env_file)
        warning(f"BAKED BUILD from {args.env_file}")
    elif args.debug:
        env = load_env(Path(__file__).parent / ".env")
        warning("DEBUG BUILD — real credentials are baked in; do not commit or share")
    if BASE != DEFAULT_BASE:
        warning(f"API base overridden: {BASE}")

    shortcuts = []
    name, pl = build(env=env)
    shortcuts.append(Shortcut(name, pl))
    for d in ("in", "out"):
        name, pl = build_wrapper(d)
        shortcuts.append(Shortcut(name, pl))
    for sc in shortcuts:
        info(
            f"{sc.name}: {len(sc.document['WFWorkflowActions'])} actions, "
            f"{len(sc.document['WFWorkflowImportQuestions'])} setup questions"
        )

    # --mode anyone is what makes the artifact shareable: Apple signs it on its
    # server, and anybody can import it. The other mode, people-who-know-me,
    # embeds your contact card and works only for people who already have you
    # in Contacts — a release built that way would fail for every stranger and
    # succeed for you. The library defaults to anyone; it is named here so a
    # release never depends on a variable nobody remembers setting.
    build_all(args.dest, shortcuts, waived=WAIVED, mode="anyone", sign=not args.unsigned, on_step=info)


def main() -> None:
    try:
        run(build_parser().parse_args())
    except KeyboardInterrupt:
        pass
    except (CheckError, ValidationError, SigningError, ToolNotFoundError, OSError) as e:  # the CLI boundary
        error(str(e))
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()

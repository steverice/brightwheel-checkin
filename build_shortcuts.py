#!/usr/bin/env python3
"""Generate the two Brightwheel check-in/check-out Shortcuts plists.

Both shortcuts are structurally identical; only the `checked_in` literal,
the display wording, and the icon differ. README.md has the endpoint
contract.
"""
import argparse
import base64
import json
import plistlib
import subprocess
from pathlib import Path

OBJ = "￼"  # U+FFFC placeholder for an inline variable


BASE = "https://schools.mybrightwheel.com/api/v1"
# Kept so an overridden BASE can be reported as such. The integration tests
# point BASE at a mock; nothing else should.
DEFAULT_BASE = BASE
CLIENT_NAME = "ios"
CLIENT_VERSION = "3.103.0"


ENV_KEYS = {
    "school_id": "BRIGHTWHEEL_SCHOOL_ID",
    "code": "BRIGHTWHEEL_CHECKIN_CODE",
    "secret": "BRIGHTWHEEL_SCHOOL_SECRET",
    "email": "BRIGHTWHEEL_EMAIL",
    "password": "BRIGHTWHEEL_PASSWORD",
}


def load_env(path):
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


def uuids(n):
    out = subprocess.run(
        ["bash", "-c", f"for i in $(seq 1 {n}); do uuidgen; done"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return [u.upper() for u in out]


# --- serialization helpers ---------------------------------------------
def ts(*parts):
    """WFTextTokenString from interleaved literals and attachments."""
    s, att = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            att["{%d, 1}" % len(s)] = p
            s += OBJ
    return {
        "Value": {"attachmentsByRange": att, "string": s},
        "WFSerializationType": "WFTextTokenString",
    }


def out(uuid, name):
    return {"OutputUUID": uuid, "OutputName": name, "Type": "ActionOutput"}


def var(name):
    return {"Type": "Variable", "VariableName": name}


def attach(value):
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def cond_input(value):
    """If-condition input: Type=Variable wrapper around an attachment."""
    return {"Type": "Variable", "Variable": attach(value)}


def dict_field(items):
    return {
        "Value": {"WFDictionaryFieldValueItems": items},
        "WFSerializationType": "WFDictionaryFieldValue",
    }


def kv(key, value):
    return {"WFItemType": 0, "WFKey": ts(key), "WFValue": value}


def kv_dict(key, items):
    return {
        "WFItemType": 1,
        "WFKey": ts(key),
        "WFValue": {"Value": dict_field(items),
                    "WFSerializationType": "WFDictionaryFieldValue"},
    }


def act(identifier, **params):
    return {
        "WFWorkflowActionIdentifier": identifier,
        "WFWorkflowActionParameters": params,
    }


def comment(text):
    return act("is.workflow.actions.comment", WFCommentActionText=text)




# --- shortcut assembly --------------------------------------------------
# `checked_in` is the DESIRED state, not the child's current state.
#   checked_in: true  -> checks the child IN
#   checked_in: false -> checks the child OUT


# (key, kind, prompt, blurb, action_value, prompt_default)
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
    ("email", "text", "Brightwheel account email",
     "Used to sign in again when the session token stops working.",
     "not set", ""),
    ("password", "text", "Brightwheel account password",
     "Used together with the email, only when signing in again.",
     "not set", ""),
    ("code", "text", "Brightwheel check-in code",
     "Your 4-digit guardian check-in code.", "not set", ""),
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


def build(env=None):
    """The shortcut that does the work. Direction arrives as Shortcut Input."""
    name = "Brightwheel Attendance"
    # 62329 is a ring of petals — close to the Brightwheel logo — on pink.
    # Both taken from a share link's icon_glyph / a resolved palette value and
    # then checked on a simulator, because the bundled glyph names are wrong.
    glyph, color = 62329, 3980825855

    i = iter(uuids(260))
    U = {k: next(i) for k in ("code", "email", "password")}
    A = []
    questions = []

    def gate(src, name, pattern=r"\S"):
        """Append Text -> Match Text -> Count for a value, returning the Count
        action's UUID for use as a numeric If input.

        Two Shortcuts behaviors force this. A Dictionary Value compared
        directly in an If reads as blank and the branch never fires, and an
        empty string still satisfies "has any value", so presence has to be
        measured rather than tested. Counting matches handles both, and a
        pattern turns the same helper into "does this response say X".

        It is the branch that Get Dictionary Value cannot serve, not the read.
        The roster is read with it, and so is the wording dictionary below, but
        every one of those values is used as text. The moment a dictionary value
        has to decide a branch, it comes back through here.
        """
        t, m, c = next(i), next(i), next(i)
        # src may be an action UUID (with name), a variable name (name=None),
        # or a ready-made attachment value such as {"Type": "ExtensionInput"}.
        if isinstance(src, dict):
            source = src
        elif name is None:
            source = var(src)
        else:
            source = out(src, name)
        A.append(act("is.workflow.actions.gettext", UUID=t,
                     WFTextActionText=ts(source)))
        A.append(act("is.workflow.actions.text.match", UUID=m,
                     WFMatchTextPattern=pattern, text=ts(out(t, "Text"))))
        A.append(act("is.workflow.actions.count", UUID=c, WFCountType="Items",
                     WFInput=attach(out(m, "Matches")),
                     Input=attach(out(m, "Matches"))))
        return c

    A.append(comment(
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
    ))
    A.append(comment(
        "This shortcut saves the Brightwheel session token in its own on-device "
        "storage and refreshes it automatically, because a background automation "
        "cannot stop to ask you to paste a new one.\n\n"
        "The three values below are filled in when you import the shortcut, so "
        "neither your password nor your check-in code is stored in the shortcut "
        "file itself. Your session token is never in the file either: it is kept "
        "under this shortcut, so deleting the shortcut takes the token with it.\n\n"
        "The school's code is the one thing kept outside this shortcut, so that "
        "re-importing does not send you back for the QR code. \"Forget saved "
        "sign-in and school code\" in the menu clears both.\n\n"
        "If you ever share or export this shortcut, clear the three Text actions "
        "below first. The check-in code authenticates as you."
    ))

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
    U_WORDS, U_CIV, U_VERB, U_ALREADY = (next(i) for _ in range(4))
    G_VALID = next(i)
    EXT = {"Type": "ExtensionInput"}

    U_EXT, U_MIN, U_MOUT = next(i), next(i), next(i)
    G_MENU = next(i)

    C_VALID = gate(EXT, None, "^(in|out)$")
    A.append(comment(
        "Work out which direction this run goes.\n"
        "- Condition counts whether a direction was handed in\n"
        "- Brightwheel Check In and Check Out hand one in\n"
        "- Run on its own there is none, so it asks instead"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_VALID, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_VALID, "Count"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_EXT,
                 WFTextActionText=ts(EXT)))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Direction",
                 WFInput=attach(out(U_EXT, "Text"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_VALID, WFControlFlowMode=1))
    A.append(comment(
        "Ask, when nothing was handed in.\n"
        "- Each choice sets Direction to the same in or out a wrapper would\n"
        "- Canceling the menu stops the shortcut, so nothing is sent"
    ))
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=0,
                 WFMenuPrompt="Check the children in or out?",
                 WFMenuItems=["Check In", "Check Out",
                              "Show the school's code",
                              "Forget saved sign-in and school code"]))
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=1,
                 WFMenuItemTitle="Check In"))
    A.append(act("is.workflow.actions.gettext", UUID=U_MIN,
                 WFTextActionText="in"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Direction",
                 WFInput=attach(out(U_MIN, "Text"))))
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=1,
                 WFMenuItemTitle="Check Out"))
    A.append(act("is.workflow.actions.gettext", UUID=U_MOUT,
                 WFTextActionText="out"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Direction",
                 WFInput=attach(out(U_MOUT, "Text"))))
    # Puts the school's code back on screen as the QR it was scanned from.
    # What is stored is the scanned payload verbatim, so the image this draws is
    # the one taped up by the door — which is the point: a second phone can be
    # set up from it, and it is a way back into the Brightwheel app when the
    # shortcut is the thing misbehaving. It is not a secret being spread any
    # further than it already is; the same string sits in this shortcut's own
    # storage, which its owner can read. Sends nothing.
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=1,
                 WFMenuItemTitle="Show the school's code"))
    U_QRC = next(i)
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_QRC,
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    C_QRC = gate(U_QRC, "Stored Content")
    G_QR = next(i)
    A.append(comment(
        "Draw the stored code, or say there is none.\n"
        "- Condition counts whether anything is stored, because an empty "
        "string still satisfies \"has any value\"\n"
        "- Condition 2 against 0 is \"more than none\", so the first branch is "
        "the one where a code exists\n"
        "- Nothing is stored until the first run at the school has scanned it"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_QR, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_QRC, "Count"))))
    U_QRI = next(i)
    A.append(act("is.workflow.actions.generatebarcode", UUID=U_QRI,
                 CustomOutputName="School Code QR",
                 WFText=ts(out(U_QRC, "Stored Content"))))
    A.append(act("is.workflow.actions.previewdocument",
                 WFInput=attach(out(U_QRI, "School Code QR"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_QR, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("No school code saved yet"),
                 WFNotificationActionBody=ts(
                     "Run a check-in at the school once. It scans the code by "
                     "the door and keeps it.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_QR, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.exit"))

    # The only way to clear the school code from the device. Deleting the
    # shortcut takes the token with it, but the code lives in the shared store,
    # which outlives it — so the reinstall everyone reaches for first still
    # leaves a stale code behind. Sends nothing, so it is safe to reach by
    # accident or through Siri.
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=1,
                 WFMenuItemTitle="Forget saved sign-in and school code"))
    A.append(act("is.workflow.actions.deletestoredcontent",
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False))
    A.append(act("is.workflow.actions.deletestoredcontent",
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("Forgotten"),
                 WFNotificationActionBody=ts(
                     "The next run signs in again and asks you to scan the "
                     "school's code.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_VALID, WFControlFlowMode=2))

    # Wanted In is 1 for a check-in and 0 for a check-out, which later gets
    # compared against whether the child is already checked in.
    C_DIR = gate("Direction", None, "^in$")
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Wanted In",
                 WFInput=attach(out(C_DIR, "Count"))))
    # One dictionary keyed by direction, instead of an If that set the same
    # three variables twice. The lookups are unconditional, so each value is a
    # named action output and needs no Set Variable to survive the branch it
    # used to be written in: eleven actions became four.
    #
    # All three are read as text — into the request body and into notification
    # wording — never as an If input, which is the case a Dictionary Value
    # cannot serve (see gate() above).
    A.append(comment(
        "Set the wording and the value Brightwheel expects.\n"
        "- The key is the direction, so in and out read the same three fields\n"
        "- Checked In Value goes into the request as true or false\n"
        "- Verb and Already Word are only used in notifications"
    ))
    A.append(act("is.workflow.actions.dictionary", UUID=U_WORDS,
                 WFItems=dict_field([
                     kv_dict("in", [
                         kv("value", ts("true")),
                         kv("verb", ts("checked in")),
                         kv("already", ts("already checked in"))]),
                     kv_dict("out", [
                         kv("value", ts("false")),
                         kv("verb", ts("checked out")),
                         kv("already", ts("already checked out"))]),
                 ])))
    for u_w, field, outname in ((U_CIV, "value", "Checked In Value"),
                                (U_VERB, "verb", "Verb"),
                                (U_ALREADY, "already", "Already Word")):
        A.append(act("is.workflow.actions.getvalueforkey", UUID=u_w,
                     CustomOutputName=outname, WFGetDictionaryValueType="Value",
                     WFDictionaryKey=ts(var("Direction"), f".{field}"),
                     WFInput=attach(out(U_WORDS, "Dictionary"))))

    A.append(comment(
        "--- SETUP ---\n"
        "These three values are requested when the shortcut is imported. To change "
        "one later, edit the matching Text action, or re-import the shortcut."
    ))
    names = {"code": "Check-In Code",
             "email": "Account Email",
             "password": "Account Password"}
    for key, kind, prompt, blurb, default, prompt_default in SETUP:
        param, ident = "WFTextActionText", "is.workflow.actions.gettext"
        if env is not None:
            # Debug build: bake the real value in and ask nothing at import.
            default = env[ENV_KEYS[key]]
        else:
            questions.append({
                "ActionIndex": len(A),
                "Category": "Parameter",
                "DefaultValue": prompt_default,
                "ParameterKey": param,
                "Text": f"{prompt} — {blurb}" + QUESTION_NOTES.get(key, ""),
            })
        A.append(act(ident, UUID=U[key], CustomOutputName=names[key],
                     **{param: default}))
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
    U_GT, U_PROBE = next(i), next(i)
    U_START, U_CODE, U_SESS = next(i), next(i), next(i)
    U_TMATCH, U_TGRP, U_ZERO = next(i), next(i), next(i)
    G_LOOP, G_SIGNIN, G_CODE, G_GOT, G_FAIL = (next(i) for _ in range(5))

    A.append(comment(
        "--- SESSION ---\n"
        "Use the token saved by the last sign-in. There is none the first time, "
        "so the first run signs in and saves one.\n\n"
        "The token is kept under this shortcut rather than in the shared store, "
        "so deleting the shortcut clears it and a re-import signs in again. The "
        "school's code is kept in the shared store instead, and outlives both."
    ))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GT,
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U_GT, "Stored Content"))))

    A.append(act("is.workflow.actions.downloadurl", UUID=U_PROBE,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/users/me", WFHTTPMethod="GET",
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(var("Session Token"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))
    # E1200 is what an expired or absent token returns.
    C_BAD = gate(U_PROBE, "Contents of URL", "E1200")
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Needs Sign In",
                 WFInput=attach(out(C_BAD, "Count"))))

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
    A.append(act("is.workflow.actions.getvalueforkey", UUID=u_gv,
                 CustomOutputName="Guardian Id Value",
                 WFGetDictionaryValueType="Value", WFDictionaryKey="object_id",
                 WFInput=attach(out(U_PROBE, "Contents of URL"))))
    A.append(act("is.workflow.actions.gettext", UUID=u_gt,
                 CustomOutputName="Guardian Id Text",
                 WFTextActionText=ts(out(u_gv, "Guardian Id Value"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Guardian Id",
                 WFInput=attach(out(u_gt, "Guardian Id Text"))))

    A.append(comment(
        "Sign in again, up to five times.\n"
        "- Each pass asks Brightwheel to send a fresh code, then asks you for it\n"
        "- Leaving the box empty, or typing resend, sends another code instead "
        "of trying to use what was typed\n"
        "- Canceling the code prompt stops the whole shortcut\n"
        "- A pass that gets a token clears Needs Sign In, so later passes do "
        "nothing"
    ))
    A.append(act("is.workflow.actions.repeat.count", UUID=next(i),
                 GroupingIdentifier=G_LOOP, WFControlFlowMode=0,
                 WFRepeatCount=5))
    A.append(comment(
        "Only act while the session is still not usable.\n"
        "- Condition checks whether an earlier pass already signed in"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGNIN, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Needs Sign In"))))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_START,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/sessions/start", WFHTTPMethod="POST",
                 WFHTTPBodyType="JSON",
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ]),
                 WFJSONValues=dict_field([
                     kv_dict("user", [
                         kv("email", ts(out(U["email"], "Account Email"))),
                         kv("password", ts(out(U["password"], "Account Password"))),
                     ]),
                 ])))
    A.append(act("is.workflow.actions.ask", UUID=U_CODE,
                 WFAskActionPrompt="Enter the 6-digit code Brightwheel emailed. "
                                   "No code yet? Leave this empty, or type "
                                   "resend, and another will be sent. Cancel "
                                   "stops the shortcut.",
                 WFInputType="Text"))
    # Only a six-digit answer is worth exchanging. Empty, "resend", or a typo
    # all skip the exchange, so the next pass calls /sessions/start again and a
    # new code is sent. Posting a junk code instead would burn an attempt and
    # risks the API treating it as a failed sign-in.
    C_CODE = gate(U_CODE, "Provided Input", "^[0-9]{6}$")
    A.append(comment(
        "Only try the code if one was actually entered.\n"
        "- Condition counts whether the answer is six digits\n"
        "- Anything else falls through, and the next pass sends a new code"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_CODE, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_CODE, "Count"))))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_SESS,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/sessions", WFHTTPMethod="POST",
                 WFHTTPBodyType="JSON",
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ]),
                 WFJSONValues=dict_field([
                     kv_dict("user", [
                         kv("email", ts(out(U["email"], "Account Email"))),
                         kv("password", ts(out(U["password"], "Account Password"))),
                     ]),
                     kv("2fa_code", ts(out(U_CODE, "Provided Input"))),
                 ])))
    # The token comes back as a top-level "token", not "session_token".
    A.append(act("is.workflow.actions.gettext", UUID=U_TMATCH,
                 WFTextActionText=ts(out(U_SESS, "Contents of URL"))))
    U_TM2 = next(i)
    A.append(act("is.workflow.actions.text.match", UUID=U_TM2,
                 WFMatchTextPattern=r'"token"\s*:\s*"([^"]+)"',
                 text=ts(out(U_TMATCH, "Text"))))
    A.append(act("is.workflow.actions.text.match.getgroup", UUID=U_TGRP,
                 WFGroupIndex="1", matches=attach(out(U_TM2, "Matches"))))
    C_GOT = gate(U_TGRP, "Matched Text Group")
    A.append(comment(
        "Keep the new token when the code was accepted.\n"
        "- Condition counts whether a token came back\n"
        "- A wrong or expired code returns none, and the next pass asks again"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_GOT, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_GOT, "Count"))))
    A.append(act("is.workflow.actions.setstoredcontent",
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False,
                 WFInput=ts(out(U_TGRP, "Matched Text Group"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U_TGRP, "Matched Text Group"))))
    A.append(act("is.workflow.actions.number", UUID=U_ZERO,
                 WFNumberActionNumber="0"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Needs Sign In",
                 WFInput=attach(out(U_ZERO, "Number"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_GOT, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_CODE, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGNIN, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.repeat.count", UUID=next(i),
                 GroupingIdentifier=G_LOOP, WFControlFlowMode=2))

    A.append(comment(
        "Give up after five attempts.\n"
        "- Condition checks whether the session is still unusable\n"
        "- Nothing has been sent to Brightwheel about the children yet"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Needs Sign In"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("Nobody ",
                                              out(U_VERB, "Verb")),
                 WFNotificationActionBody=ts(
                     "Could not sign in after five tries, so nothing was sent. "
                     "Run this shortcut by hand and enter a code, or leave the "
                     "box empty to have another sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=2))

    # A run that signed in read its guardian id off an E1200 body, so it has
    # none. Ask again now that there is a working token. Gated on emptiness so
    # the ordinary path still makes exactly one /users/me call.
    G_GID = next(i)
    C_NOGID = gate("Guardian Id", None)
    A.append(comment(
        "Fetch the guardian id if signing in meant the first read missed it.\n"
        "- Condition counts whether an id was found on the probe\n"
        "- The probe body is the auth error on a run that had to sign in"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_GID, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="1",
                 WFInput=cond_input(out(C_NOGID, "Count"))))
    u_me2, u_gv2, u_gt2 = (next(i) for _ in range(3))
    A.append(act("is.workflow.actions.downloadurl", UUID=u_me2,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/users/me", WFHTTPMethod="GET",
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(var("Session Token"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=u_gv2,
                 CustomOutputName="Guardian Id Retry Value",
                 WFGetDictionaryValueType="Value", WFDictionaryKey="object_id",
                 WFInput=attach(out(u_me2, "Contents of URL"))))
    A.append(act("is.workflow.actions.gettext", UUID=u_gt2,
                 CustomOutputName="Guardian Id Retry",
                 WFTextActionText=ts(out(u_gv2, "Guardian Id Retry Value"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Guardian Id",
                 WFInput=attach(out(u_gt2, "Guardian Id Retry"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_GID, WFControlFlowMode=2))

    # Nothing below this works without an id, and an empty one is invisible: it
    # builds a perfectly well-formed URL that answers 404, whose body has no
    # students in it, which the roster guard then reports as Brightwheel having
    # returned no children. That misfiled a broken read as a broken API for a
    # whole morning. Guarded here, at the boundary it belongs to, so it says
    # what actually went wrong.
    #
    # Exit rather than a flag: this is still top level, before the attempt
    # Repeat, which is the only place this file uses Exit.
    G_HASGID = next(i)
    C_HASGID = gate("Guardian Id", None)
    A.append(comment(
        "Stop if the account could not be identified.\n"
        "- Condition counts whether a guardian id was read\n"
        "- An empty one would 404 the roster call and read as an empty roster"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HASGID, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="1",
                 WFInput=cond_input(out(C_HASGID, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("Nobody ",
                                              out(U_VERB, "Verb")),
                 WFNotificationActionBody=ts(
                     "Could not read which Brightwheel account this is, so "
                     "nothing was sent. Run this shortcut by hand to try "
                     "again.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HASGID, WFControlFlowMode=2))

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
    U_ONE, U_ZERO2 = next(i), next(i)
    U_GC, U_SCAN, U_CDICT = next(i), next(i), next(i)
    U_CSEC, U_CSID, U_ST, U_SI = (next(i) for _ in range(4))
    U_BODY, U_RESP, U_RTEXT = next(i), next(i), next(i)
    G_ATTEMPT, G_DOIT, G_HAVE, G_SIGS = (next(i) for _ in range(4))
    G_KIDS, G_SKIP, G_RES, G_STALE = (next(i) for _ in range(4))

    A.append(comment(
        "--- WHO TO SEND FOR ---\n"
        "Nobody is named in here. The children come from Brightwheel itself, a "
        "little further down, which is why adding or moving one needs no "
        "rebuild. What this sets is the flag that lets the whole send run a "
        "second time if the school's code turns out to be stale."
    ))
    A.append(act("is.workflow.actions.number", UUID=U_ONE,
                 WFNumberActionNumber="1"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Send Needed",
                 WFInput=attach(out(U_ONE, "Number"))))

    A.append(comment(
        "Send, and send again once if the school code turned out to be stale.\n"
        "- Send Needed starts at yes, and is set again only by a rejected code\n"
        "- A child already in the right state is skipped, so a second pass "
        "cannot double up on anyone who already succeeded"
    ))
    A.append(act("is.workflow.actions.repeat.count", UUID=next(i),
                 GroupingIdentifier=G_ATTEMPT, WFControlFlowMode=0,
                 WFRepeatCount=2))
    A.append(comment(
        "Do nothing on the second pass unless one was asked for.\n"
        "- Condition checks whether a send is still outstanding\n"
        "- Only a rejected school code asks for another pass"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_DOIT, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Send Needed"))))
    A.append(act("is.workflow.actions.number", UUID=U_ZERO2,
                 WFNumberActionNumber="0"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Send Needed",
                 WFInput=attach(out(U_ZERO2, "Number"))))

    # --- school code ---
    if env is not None:
        # Debug builds seed the store so a test import never has to scan.
        u_seed = next(i)
        A.append(act("is.workflow.actions.gettext", UUID=u_seed,
                     CustomOutputName="Debug School Code",
                     WFTextActionText=json.dumps(
                         {"secret": env["BRIGHTWHEEL_SCHOOL_SECRET"],
                          "school_id": env["BRIGHTWHEEL_SCHOOL_ID"],
                          "signatures_enabled": False},
                         separators=(",", ":"))))
        A.append(act("is.workflow.actions.setstoredcontent",
                     WFStoredContentKey="BrightwheelSchoolCode",
                     WFStoredContentGlobalValue=True,
                     WFInput=ts(out(u_seed, "Debug School Code"))))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GC,
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    C_CODE = gate(U_GC, "Stored Content")
    A.append(comment(
        "Scan the school's code when there is none saved.\n"
        "- Condition counts whether anything is stored\n"
        "- Nothing is stored on the first run, or after a stale code was "
        "forgotten below\n"
        "- Show Alert explains what to point the camera at before it opens"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_CODE, "Count"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Code",
                 WFInput=attach(out(U_GC, "Stored Content"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.alert",
                 WFAlertActionTitle=ts("Brightwheel"),
                 WFAlertActionMessage=ts(
                     "The school's check-in QR code is needed. Point the camera "
                     "at the code on the sign-in tablet."),
                 WFAlertActionCancelButtonShown=True))
    A.append(act("is.workflow.actions.scanbarcode", UUID=U_SCAN,
                 WFScanCodeActionMode=0))
    A.append(act("is.workflow.actions.setstoredcontent",
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True,
                 WFInput=ts(out(U_SCAN, "QR/Barcodes"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Code",
                 WFInput=attach(out(U_SCAN, "QR/Barcodes"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=2))

    # Pulled out with Match Text, not Detect Dictionary + Get Dictionary Value.
    # That pair was banned from the check path for never producing a working
    # branch, and was left here on the assumption it behaved on plain text. It
    # does not: school_id came back empty and Brightwheel answered E1205 "You
    # must specify the school".
    for key, varname, u_m, u_g, u_t in (
            ("secret", "School Secret", U_CSEC, U_CDICT, U_ST),
            ("school_id", "School Id", U_CSID, U_SI, next(i))):
        A.append(act("is.workflow.actions.text.match", UUID=u_m,
                     WFMatchTextPattern=r'"%s"\s*:\s*"([^"]+)"' % key,
                     text=ts(var("School Code"))))
        A.append(act("is.workflow.actions.text.match.getgroup", UUID=u_g,
                     WFGroupIndex="1", matches=attach(out(u_m, "Matches"))))
        A.append(act("is.workflow.actions.gettext", UUID=u_t,
                     WFTextActionText=ts(out(u_g, "Matched Text Group"))))
        A.append(act("is.workflow.actions.setvariable", WFVariableName=varname,
                     WFInput=attach(out(u_t, "Text"))))

    C_SIGS = gate("School Code", None, r'"signatures_enabled"\s*:\s*(true|1)')
    A.append(comment(
        "Warn if the school has started requiring signatures.\n"
        "- Condition counts whether the scanned code says signatures are on\n"
        "- Check-ins send no signature, so they would start failing"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGS, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_SIGS, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("This school now requires signatures"),
                 WFNotificationActionBody=ts(
                     "These shortcuts do not send one, so check-ins may start "
                     "failing.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGS, WFControlFlowMode=2))

    # --- who the children are, asked rather than baked in ---
    #
    # One call returns the roster and each child's current state together, so
    # they cannot disagree, and it replaces the per-child activities read.
    U_TZ, U_ROST, U_MATCH = next(i), next(i), next(i)
    G_STALE2, G_G1, G_G3, G_RUN = (next(i) for _ in range(4))
    G_ROOMS, G_ROOM0, G_ROOM2 = (next(i) for _ in range(3))

    # The device's own zone. time_zone is required by the endpoint, and while
    # its value looked inert in testing that test could not tell an inert
    # parameter from a day boundary nobody had crossed. UTC would put the
    # boundary in the middle of the afternoon; the local zone puts it at local midnight.
    A.append(act("is.workflow.actions.date", UUID=U_TZ))
    u_tzf, u_tzt = next(i), next(i)
    A.append(act("is.workflow.actions.format.date", UUID=u_tzf,
                 WFDate=ts(out(U_TZ, "Current Date")),
                 WFDateFormatStyle="Custom", WFDateFormat="VV"))
    A.append(act("is.workflow.actions.gettext", UUID=u_tzt,
                 CustomOutputName="Time Zone",
                 WFTextActionText=ts(out(u_tzf, "Formatted Date"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Time Zone",
                 WFInput=attach(out(u_tzt, "Time Zone"))))

    A.append(comment(
        "Ask Brightwheel who the children are and where they stand.\n"
        "- One call returns the roster and each child's current state\n"
        "- Nothing about the family is built into this shortcut"))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_ROST,
                 Advanced=True, ShowHeaders=False, WFHTTPMethod="GET",
                 WFURL=ts(f"{BASE}/guardians/", var("Guardian Id"),
                          "/students_for_checkin?school_id=", var("School Id"),
                          "&secret=", var("School Secret"),
                          "&time_zone=", var("Time Zone")),
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(var("Session Token"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))

    # The roster call is now the first thing to see a rotated school code, so
    # the rescan has to be triggered from here as well as from the check-in
    # response. Deleting the stored code is what turns the next pass into a
    # fresh scan; without it the second pass reads the same stale code back.
    C_STALE2 = gate(U_ROST, "Contents of URL", r'"secret"\s*:\s*"The given secret')
    A.append(comment(
        "Rescan if the school's code was rotated.\n"
        "- Condition counts whether the roster call rejected the code\n"
        "- Forgetting it makes the second pass scan a fresh one"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE2, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_STALE2, "Count"))))
    A.append(act("is.workflow.actions.deletestoredcontent",
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Send Needed",
                 WFInput=attach(out(U_ONE, "Number"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("The school's check-in code has changed"),
                 WFNotificationActionBody=ts(
                     "Scanning the new one and trying again.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE2, WFControlFlowMode=2))

    # The roster is read as a dictionary, not matched as text. Get Contents of
    # URL parses JSON, so what a Match Text would see is a re-serialization
    # whose key order is Shortcuts' own — measured on device, the student's
    # object_id came after the photo's. Key *names* survive that; positions and
    # adjacency do not, which is fatal to any pattern pairing several fields.
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_MATCH,
                 CustomOutputName="Students", WFGetDictionaryValueType="Value",
                 WFDictionaryKey="students",
                 WFInput=attach(out(U_ROST, "Contents of URL"))))
    u_cm = next(i)
    A.append(act("is.workflow.actions.count", UUID=u_cm, WFCountType="Items",
                 WFInput=attach(out(U_MATCH, "Students")),
                 Input=attach(out(U_MATCH, "Students"))))

    # One guard still counts a key name in the body text, which is safe because
    # a single key-value pair does not depend on order. Each well-formed child
    # contributes exactly one "checked_in"; a room entry that has lost the key
    # is the one malformation the per-child loop below cannot see, because such
    # a child still has exactly one room.
    u_sm, u_cs = next(i), next(i)
    A.append(act("is.workflow.actions.text.match", UUID=u_sm,
                 WFMatchTextPattern=r'"checked_in"\s*:',
                 text=ts(out(U_ROST, "Contents of URL"))))
    A.append(act("is.workflow.actions.count", UUID=u_cs, WFCountType="Items",
                 WFInput=attach(out(u_sm, "Matches")), Input=attach(out(u_sm, "Matches"))))

    A.append(act("is.workflow.actions.setvariable", WFVariableName="Roster OK",
                 WFInput=attach(out(U_ONE, "Number"))))

    def guard(group, count_uuid, count_name, condition, number, message, blurb):
        """Notify and clear Roster OK when a guard fires.

        A flag rather than Exit: this sits two blocks deep inside the attempt
        Repeat, and Exit is only ever used at the top level in this file.
        """
        A.append(comment(blurb))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=group, WFControlFlowMode=0,
                     WFCondition=condition, WFNumberValue=number,
                     WFInput=cond_input(out(count_uuid, count_name))))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts("Nobody ",
                                              out(U_VERB, "Verb")),
                     WFNotificationActionBody=ts(message)))
        A.append(act("is.workflow.actions.setvariable", WFVariableName="Roster OK",
                     WFInput=attach(out(U_ZERO2, "Number"))))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=group, WFControlFlowMode=2))

    guard(G_G1, u_cm, "Count", 0, "1",
          "Brightwheel did not return a roster, so nothing was sent. Open the "
          "app to check, and run this by hand.",
          "Did Brightwheel list any children?\n"
          "- Condition counts the children in the reply\n"
          "- An error reply has none, and would read as nobody to send for")

    def diff_guard(group, a_uuid, a_name, b_uuid, b_name, message, blurb):
        u = next(i)
        A.append(act("is.workflow.actions.math", UUID=u,
                     WFInput=attach(out(a_uuid, a_name)), WFMathOperation="-",
                     WFMathOperand=attach(out(b_uuid, b_name))))
        guard(group, u, "Calculation Result", 2, "0", message, blurb)

    diff_guard(G_G3, u_cm, "Count", u_cs, "Count",
               "A room in the roster has no check-in state, so nothing was "
               "sent rather than sending for only some of them.",
               "Does every room entry carry a state?\n"
               "- Condition counts children minus check-in states\n"
               "- A room that has lost the key reads as nobody to send for")

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
    A.append(comment(
        "Look at each child's own rooms in turn.\n"
        "- One pass that sends nothing, so a bad child stops the whole run\n"
        "- Both checks below fire per child, so two bad children say so twice"))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=G_ROOMS, WFControlFlowMode=0,
                 WFInput=attach(out(U_MATCH, "Students"))))
    # "Repeat Item 2" for the same reason the sending loop uses it: one
    # enclosing Repeat, and the conditionals in between do not renumber.
    u_rooms, u_nr = next(i), next(i)
    A.append(act("is.workflow.actions.getvalueforkey", UUID=u_rooms,
                 CustomOutputName="Child Rooms",
                 WFGetDictionaryValueType="Value",
                 WFDictionaryKey="room_states",
                 WFInput=attach(var("Repeat Item 2"))))
    A.append(act("is.workflow.actions.count", UUID=u_nr, WFCountType="Items",
                 WFInput=attach(out(u_rooms, "Child Rooms")),
                 Input=attach(out(u_rooms, "Child Rooms"))))
    guard(G_ROOM0, u_nr, "Count", 0, "1",
          "A child in the roster has no room, so nothing was sent rather than "
          "sending for only some of them.",
          "Does this child have a room at all?\n"
          "- Condition counts this child's own room entries\n"
          "- None means there is no room to send them to")
    guard(G_ROOM2, u_nr, "Count", 2, "1",
          "A child is listed in more than one room, so nothing was sent rather "
          "than guessing which one to use.",
          "Is this child in more than one room?\n"
          "- Condition counts this child's own room entries\n"
          "- The room to send would be a guess, so nobody is")
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=G_ROOMS, WFControlFlowMode=2))

    A.append(comment(
        "Only send if every check above agreed.\n"
        "- Condition counts whether the roster survived all of them\n"
        "- Anything else has already said why, and sends nothing"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_RUN, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Roster OK"))))

    # --- one pass over the children ---
    A.append(comment(
        "Work through the children in turn.\n"
        "- Each child arrives whole, so their name, id and room are read "
        "straight off them rather than looked up\n"
        "- Their current state is the checked_in flag on the room they are in, "
        "which came back with the roster and so cannot disagree with it"
    ))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=G_KIDS, WFControlFlowMode=0,
                 WFInput=attach(out(U_MATCH, "Students"))))
    # "Repeat Item 2", not "Repeat Item": this loop sits inside the retry
    # Repeat, and a count-style outer loop shifts the numbering just as a
    # nested Repeat with Each does. Verified on device with a probe — with the
    # unnumbered name the item came back empty, the roster lookup found
    # nothing, and notifications showed a blank child name.
    #
    # Captured into Child Name here so the numbered variable appears exactly
    # once; if the nesting ever changes, this is the only line to revisit.
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Child Match",
                 WFInput=attach(var("Repeat Item 2"))))

    # Read by key path off this child's entry. Array indices in a dot path are
    # 1-based, so room_states.1 is the first (and, per the guards above, only)
    # room entry.
    for varname, key, outname in (
            ("Child Id",   "student.object_id",              "Child Id Value"),
            ("Child Name", "student.first_name",             "Child Name Value"),
            ("Child Room", "room_states.1.room.object_id",   "Child Room Value")):
        u_v, u_t = next(i), next(i)
        A.append(act("is.workflow.actions.getvalueforkey", UUID=u_v,
                     CustomOutputName=outname, WFGetDictionaryValueType="Value",
                     WFDictionaryKey=key, WFInput=attach(var("Child Match"))))
        A.append(act("is.workflow.actions.gettext", UUID=u_t,
                     CustomOutputName=varname + " Text",
                     WFTextActionText=ts(out(u_v, outname))))
        A.append(act("is.workflow.actions.setvariable", WFVariableName=varname,
                     WFInput=attach(out(u_t, varname + " Text"))))

    # The state is a boolean, and a boolean does not coerce to text. Read the
    # room entry it lives in — that does coerce, to {"checked_in":true,...} —
    # and match the one pair out of it, which no key order can disturb.
    u_rs, u_rst = next(i), next(i)
    A.append(act("is.workflow.actions.getvalueforkey", UUID=u_rs,
                 CustomOutputName="Room State", WFGetDictionaryValueType="Value",
                 WFDictionaryKey="room_states.1", WFInput=attach(var("Child Match"))))
    A.append(act("is.workflow.actions.gettext", UUID=u_rst,
                 CustomOutputName="Room State Text",
                 WFTextActionText=ts(out(u_rs, "Room State"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Room State Text",
                 WFInput=attach(out(u_rst, "Room State Text"))))

    # Is the child checked in right now? 1 or 0, same shape as Wanted In.
    # It has to be a count: the comparison below pastes two values and matches
    # ^(01|10)$, so the literal "true" would give "true1", never match, and
    # every child would be skipped in both directions while the notification
    # said "no change".
    C_IN = gate("Room State Text", None, r'"checked_in"\s*:\s*true')
    # Two runtime numbers cannot be compared directly — an If tests a variable
    # against a literal, not against another variable. Pasting them together
    # gives 11 or 00 when they agree and 10 or 01 when they differ, which a
    # fixed pattern can match.
    u_pair = next(i)
    A.append(act("is.workflow.actions.gettext", UUID=u_pair,
                 WFTextActionText=ts(out(C_IN, "Count"), var("Wanted In"))))
    C_SEND = gate(u_pair, "Text", "^(01|10)$")
    A.append(comment(
        "Skip anyone who needs no change.\n"
        "- Condition counts whether their state and this run disagree\n"
        "- This is also what makes a second pass safe after a rescan\n"
        "- If the state cannot be read, the request is sent anyway"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SKIP, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="1",
                 WFInput=cond_input(out(C_SEND, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionBody=ts(
                     var("Child Name"), " was ",
                     out(U_ALREADY, "Already Word"), " — no change")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SKIP, WFControlFlowMode=1))

    A.append(act("is.workflow.actions.gettext", UUID=U_BODY,
                 WFTextActionText=ts(
                     '{"checkins":[{"actor":{"object_id":"',
                     var("Guardian Id"),
                     '"},"health_screen":{"questions":[]},'
                     '"room":{"object_id":"',
                     var("Child Room"),
                     '"},"checked_in":',
                     out(U_CIV, "Checked In Value"),
                     ',',
                     '"target":{"object_id":"',
                     var("Child Id"),
                     '"},"note":""}],"school_id":"',
                     var("School Id"),
                     '","secret":"',
                     var("School Secret"),
                     '","checkin_code":"',
                     out(U["code"], "Check-In Code"),
                     '"}')))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_RESP,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/checkins/", WFHTTPMethod="POST",
                 WFHTTPBodyType="File",
                 WFRequestVariable=attach(out(U_BODY, "Text")),
                 WFFormValues=dict_field([]),
                 WFHTTPHeaders=dict_field([
                     kv("Content-Type", ts("application/json")),
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(var("Session Token"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))
    A.append(act("is.workflow.actions.gettext", UUID=U_RTEXT,
                 WFTextActionText=ts(out(U_RESP, "Contents of URL"))))
    C_OK = gate(U_RESP, "Contents of URL", '"event_date"')
    A.append(comment(
        "Report the result.\n"
        "- Condition counts whether Brightwheel really recorded something\n"
        "- Otherwise branch works out whether the school code was the problem"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_RES, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_OK, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionBody=ts(
                     "✅ ", var("Child Name"), " ", out(U_VERB, "Verb"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_RES, WFControlFlowMode=1))
    C_STALE = gate(U_RESP, "Contents of URL",
                   r'"secret"\s*:\s*"The given secret')
    A.append(comment(
        "Tell a stale school code apart from anything else.\n"
        "- Condition counts whether Brightwheel rejected the school's code\n"
        "- Forgetting it makes this run scan a fresh one and try again\n"
        "- Otherwise branch just reports what Brightwheel said"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_STALE, "Count"))))
    A.append(act("is.workflow.actions.deletestoredcontent",
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Send Needed",
                 WFInput=attach(out(U_ONE, "Number"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("The school's check-in code has changed"),
                 WFNotificationActionBody=ts(
                     "Scanning the new one and trying again.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(
                     "⚠️ ", var("Child Name"), " not ", out(U_VERB, "Verb")),
                 WFNotificationActionBody=ts(
                     out(U_RTEXT, "Text"),
                     "\n\nIf this says the session expired, run this shortcut "
                     "by hand to sign in again.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_RES, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SKIP, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=G_KIDS, WFControlFlowMode=2))
    # closes the If that runs the loop only when all four guards agreed
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_RUN, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_DOIT, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.repeat.count", UUID=next(i),
                 GroupingIdentifier=G_ATTEMPT, WFControlFlowMode=2))

    return name, {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": glyph,
                           "WFWorkflowIconStartColor": color},
        "WFWorkflowImportQuestions": questions,
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": name,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }


def build_wrapper(direction):
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

    i = iter(uuids(24))
    u_text = next(i)
    u_seen, u_gt, u_gm, u_gc = next(i), next(i), next(i), next(i)
    u_b64, u_dec, u_mark = next(i), next(i), next(i)
    g_setup = next(i)

    # Each wrapper explains its own trigger, because each needs a different one
    # and because Brightwheel Attendance works perfectly well without either of
    # them. Setup instructions for an optional extra do not belong in the
    # shortcut that extra is optional to.
    diagram = (Path(__file__).parent / "assets" /
               f"setup-check-{'in' if checking_in else 'out'}.png")
    diagram_b64 = base64.b64encode(diagram.read_bytes()).decode()

    A = [
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
        act("is.workflow.actions.getstoredcontent", UUID=u_seen,
            WFStoredContentKey="BrightwheelSetupShown",
            WFStoredContentGlobalValue=False),
        act("is.workflow.actions.gettext", UUID=u_gt,
            WFTextActionText=ts(out(u_seen, "Stored Content"))),
        act("is.workflow.actions.text.match", UUID=u_gm,
            WFMatchTextPattern=r"\S", text=ts(out(u_gt, "Text"))),
        act("is.workflow.actions.count", UUID=u_gc, WFCountType="Items",
            WFInput=attach(out(u_gm, "Matches")),
            Input=attach(out(u_gm, "Matches"))),
        comment(
            "Show the setup guide the first time this shortcut runs.\n"
            "- Condition counts whether anything is stored yet\n"
            "- Nothing is stored until the guide has been shown once\n"
            "- Attaching the trigger is the one step that cannot be done for "
            "you, because the placemark only exists on your device"),
        act("is.workflow.actions.conditional", UUID=next(i),
            GroupingIdentifier=g_setup, WFControlFlowMode=0,
            WFCondition=0, WFNumberValue="1",
            WFInput=cond_input(out(u_gc, "Count"))),
        act("is.workflow.actions.gettext", UUID=u_b64,
            CustomOutputName="Setup Diagram", WFTextActionText=diagram_b64),
        act("is.workflow.actions.base64encode", UUID=u_dec,
            WFEncodeMode="Decode",
            WFInput=attach(out(u_b64, "Setup Diagram"))),
        act("is.workflow.actions.showresult",
            Text=ts(out(u_dec, "Base64 Encoded"))),
        # Something has to follow Show Content. Left last, the image becomes the
        # shortcut's own output, and handing an image back to the caller needs
        # consent — "Allow ... to output 1 image?" on the very run that is
        # trying to be helpful.
        act("is.workflow.actions.nothing"),
        act("is.workflow.actions.gettext", UUID=u_mark,
            CustomOutputName="Setup Mark", WFTextActionText="shown"),
        act("is.workflow.actions.setstoredcontent",
            WFStoredContentKey="BrightwheelSetupShown",
            WFStoredContentGlobalValue=False,
            WFInput=ts(out(u_mark, "Setup Mark"))),
        act("is.workflow.actions.conditional", UUID=next(i),
            GroupingIdentifier=g_setup, WFControlFlowMode=2),

        comment(
            "Hand the direction to Brightwheel Attendance.\n"
            "- Attendance does all the work; this shortcut only says which way\n"
            "- Which trigger fired decides it, so editing a trigger's hours "
            "cannot make it send the wrong direction"),
        act("is.workflow.actions.gettext", UUID=u_text,
            CustomOutputName="Direction", WFTextActionText=word),
        act("is.workflow.actions.runworkflow",
            WFWorkflowName="Brightwheel Attendance",
            WFWorkflow={"isSelf": False,
                        "workflowIdentifier": next(i),
                        "workflowName": "Brightwheel Attendance"},
            WFInput=attach(out(u_text, "Direction"))),
    ]
    return title, {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": glyph,
                           "WFWorkflowIconStartColor": color},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": title,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Generate the Brightwheel Shortcuts plists.")
    ap.add_argument("dest", nargs="?", default=".",
                    help="directory to write the .xml files into")
    ap.add_argument("--debug", action="store_true",
                    help="bake .env values in and emit no setup questions")
    ap.add_argument("--env-file", metavar="PATH",
                    help="bake this env file in instead of .env; use for test "
                         "builds so real credentials never reach the artifact")
    ap.add_argument("--api-base", metavar="URL", default=BASE,
                    help="point the shortcuts at a different API root; used by "
                         "the integration tests to reach the mock Brightwheel")
    args = ap.parse_args()

    # build() reads these at call time, so setting them here is enough.
    BASE = args.api_base

    env = None
    if args.env_file:
        env = load_env(args.env_file)
        print(f"BAKED BUILD from {args.env_file}")
    elif args.debug:
        env = load_env(Path(__file__).parent / ".env")
        print("DEBUG BUILD — real credentials are baked in; do not commit or share")
    if BASE != DEFAULT_BASE:
        print(f"API base overridden: {BASE}")

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    name, pl = build(env=env)
    (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
    print(f"{name}: {len(pl['WFWorkflowActions'])} actions, "
          f"{len(pl['WFWorkflowImportQuestions'])} setup questions")
    for d in ("in", "out"):
        name, pl = build_wrapper(d)
        (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
        print(f"{name}: {len(pl['WFWorkflowActions'])} actions, "
              f"{len(pl['WFWorkflowImportQuestions'])} setup questions")

#!/usr/bin/env python3
"""Generate the two Brightwheel check-in/check-out Shortcuts plists.

Both shortcuts are structurally identical; only the `checked_in` literal,
the display wording, and the icon differ. README.md has the endpoint
contract.
"""
import json
import plistlib
import subprocess
import sys
from pathlib import Path

OBJ = "￼"  # U+FFFC placeholder for an inline variable

# --- Static, non-secret identifiers -------------------------------------
ACTOR = "00000000-0000-0000-0000-000000000000"
CHILD_A = "00000000-0000-0000-0000-000000000000"
CHILD_B = "00000000-0000-0000-0000-000000000000"
ROOM = "00000000-0000-0000-0000-000000000000"
SCHOOL = "00000000-0000-0000-0000-000000000000"
# The school QR secret and the 4-digit guardian check-in code are deliberately
# NOT here. They are collected as import-time setup questions so that no live
# credential is ever written into this repo or into the signed .shortcut.

BASE = "https://schools.mybrightwheel.com/api/v1"
CLIENT_NAME = "ios"
CLIENT_VERSION = "3.103.0"

CHILDREN = [("First Child", CHILD_A), ("Second Child", CHILD_B)]


ENV_KEYS = {
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
# The activity feed's `state` field reports the result: "1" = in, "2" = out.
STATE_IN, STATE_OUT = "1", "2"


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


def build(direction, env=None):
    """direction: 'in' or 'out'."""
    checking_in = direction == "in"
    desired = checking_in                 # value sent as `checked_in`
    already = STATE_IN if checking_in else STATE_OUT   # skip when state == this
    verb = "checked in" if checking_in else "checked out"
    already_word = "already checked in" if checking_in else "already checked out"
    title_word = "Check In" if checking_in else "Check Out"
    name = f"Brightwheel {title_word}"
    glyph = 59692 if checking_in else 59707   # circledDownArrow / circledUpArrow
    color = 4292093695 if checking_in else 4251333119  # green / orange

    i = iter(uuids(180))
    U = {k: next(i) for k in ("code", "email", "password")}
    U_HOUR, U_DAY, U_AFT, U_BEF = next(i), next(i), next(i), next(i)
    U_WEM, U_WEC, U_HNM, U_HNC = next(i), next(i), next(i), next(i)
    G0, G0A, G0B, G0C = next(i), next(i), next(i), next(i)
    G2, G3 = next(i), next(i)
    kid = {c: {k: next(i) for k in
               ("act", "adict", "state", "stext", "gskip", "body", "resp",
                "rdict", "chk", "rtext", "gres")}
           for c, _ in CHILDREN}

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

        This is also why nothing here parses JSON with Detect Dictionary and Get
        Dictionary Value. That pair does not produce a working
        branch. Matching the raw response text is the primitive that works.
        """
        t, m, c = next(i), next(i), next(i)
        # name=None means src is a variable name rather than an action UUID.
        source = var(src) if name is None else out(src, name)
        A.append(act("is.workflow.actions.gettext", UUID=t,
                     WFTextActionText=ts(source)))
        A.append(act("is.workflow.actions.text.match", UUID=m,
                     WFMatchTextPattern=pattern, text=ts(out(t, "Text"))))
        A.append(act("is.workflow.actions.count", UUID=c, WFCountType="Items",
                     WFInput=attach(out(m, "Matches")),
                     Input=attach(out(m, "Matches"))))
        return c

    A.append(comment(
        f"Brightwheel — {title_word}\n\n"
        f"Checks First Child and Second Child {verb} at Your School (room Your Room) by "
        "talking to the Brightwheel API directly, without opening the app.\n\n"
        "Built to be run unattended by an arrival trigger, so it does not stop "
        "to ask anything on the normal path. Everything it needs is collected "
        "once, when you import it. When it should run is decided by the "
        "trigger's own time range, not by this shortcut.\n\n"
        "Each run:\n"
        "1. Checks the saved sign-in is still valid, and signs in again by itself "
        "if it has expired.\n"
        "2. Looks up whether each child is already checked in or out.\n"
        f"3. Skips any child who is {already_word}, so running it twice is "
        "harmless.\n"
        "4. Sends the request for everyone else and posts a notification per child."
    ))
    A.append(comment(
        "Generated by build_shortcuts.py in the brightwheel-checkin repo.\n\n"
        "The four Setup values below are meant to be changed — edit them here, or "
        "re-import the shortcut to be asked again. Any other edit is lost the next "
        "time the shortcut is rebuilt, so change the generator instead."
    ))
    A.append(comment(
        "ALLOW_TOKEN_FILE — this shortcut saves the Brightwheel session token in "
        "its own on-device storage and refreshes it automatically, because a "
        "background automation cannot stop to ask you to paste a new one.\n\n"
        "The four values below are filled in when you import the shortcut, so no "
        "password, school secret or check-in code is stored in the shortcut file "
        "itself. Your session token is never in the file either — it lives only in "
        "this device's storage, scoped to this shortcut and not synced to iCloud.\n\n"
        "If you ever share or export this shortcut, clear the four Text actions "
        "below first. The school secret is shared with every family at the center, "
        "and the check-in code authenticates as you."
    ))

    A.append(comment(
        "--- SETUP ---\n"
        "These four values are requested when the shortcut is imported. To change "
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
                "Text": f"{prompt} — {blurb}",
            })
        A.append(act(ident, UUID=U[key], CustomOutputName=names[key],
                     **{param: default}))

    # ---- session token, with interactive sign-in on failure ----
    #
    # Sign-in is two steps and needs a 6-digit code (see README). A background
    # automation cannot answer the prompt, but this branch only runs when the
    # token has already expired, so that run was failing regardless. Running the
    # shortcut by hand afterwards completes it.
    U_GT, U_PROBE = next(i), next(i)
    U_START, U_CODE, U_SESS = next(i), next(i), next(i)
    U_TMATCH, U_TGRP, U_ZERO = next(i), next(i), next(i)
    G_LOOP, G_SIGNIN, G_CODE, G_GOT, G_FAIL = (next(i) for _ in range(5))

    A.append(comment(
        "--- SESSION ---\n"
        "Use the token saved by the last sign-in. There is none the first time, "
        "so the first run signs in and saves one.\n\n"
        "The token is kept in the shared store rather than this shortcut's own, "
        "so signing in here also covers the other Brightwheel shortcut. That "
        "store syncs through iCloud."
    ))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GT,
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=True))
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

    A.append(comment(
        "Sign in again, up to five times.\n"
        "- Each pass asks Brightwheel to send a fresh code, then asks you for it\n"
        "- Leaving the box empty, or typing resend, sends another code instead "
        "of trying to use what was typed\n"
        "- Cancelling the code prompt stops the whole shortcut\n"
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
                 WFStoredContentGlobalValue=True,
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
        "Give up after three attempts.\n"
        "- Condition checks whether the session is still unusable\n"
        "- Nothing has been sent to Brightwheel about the children yet"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Needs Sign In"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     "Could not sign in after five tries, so nothing was sent. "
                     "Run this shortcut by hand and enter a code, or leave the "
                     "box empty to have another sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=2))

    # ---- school code: scanned once, then remembered ----
    #
    # The QR code holds {secret, school_id, signatures_enabled}. Both values the
    # request needs come from it, so it is stored whole and re-parsed rather than
    # split into two keys. Scanning is interactive and only happens when nothing
    # is stored, i.e. first run or after the school rotates the code — the same
    # shape as the sign-in prompt.
    U_GC, U_SCAN, U_CDICT = next(i), next(i), next(i)
    U_CSEC, U_CSID, U_ST, U_SI = (next(i) for _ in range(4))
    G_CODE_HAVE, G_SIGS = next(i), next(i)

    A.append(comment(
        "--- SCHOOL CODE ---\n"
        "The school's check-in QR code carries the secret this request needs. It "
        "is scanned once and remembered, so this only asks on the first run, or "
        "again if the school rotates the code."
    ))
    if env is not None:
        # Debug builds seed the store so a test import never has to scan.
        A.append(act("is.workflow.actions.gettext", UUID=next(i),
                     CustomOutputName="Debug School Code",
                     WFTextActionText=json.dumps(
                         {"secret": env["BRIGHTWHEEL_SCHOOL_SECRET"],
                          "school_id": SCHOOL, "signatures_enabled": False},
                         separators=(",", ":"))))
        A.append(act("is.workflow.actions.setstoredcontent",
                     WFStoredContentKey="BrightwheelSchoolCode",
                     WFStoredContentGlobalValue=True,
                     WFInput=ts(out(A[-1]["WFWorkflowActionParameters"]["UUID"],
                                    "Debug School Code"))))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GC,
                 WFStoredContentKey="BrightwheelSchoolCode",
                 WFStoredContentGlobalValue=True))
    C_CODE = gate(U_GC, "Stored Content")
    A.append(comment(
        "Scan the code the first time, and remember it.\n"
        "- Condition counts whether anything has been stored yet\n"
        "- Show Alert explains what to point the camera at before it opens\n"
        "- Otherwise branch reuses what was scanned before"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_CODE_HAVE, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_CODE, "Count"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Code",
                 WFInput=attach(out(U_GC, "Stored Content"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_CODE_HAVE, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.alert",
                 WFAlertActionTitle=ts("Brightwheel"),
                 WFAlertActionMessage=ts(
                     "The school's check-in QR code is needed once. Point the "
                     "camera at the code on the sign-in tablet."),
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
                 GroupingIdentifier=G_CODE_HAVE, WFControlFlowMode=2))

    A.append(act("is.workflow.actions.detect.dictionary", UUID=U_CDICT,
                 WFInput=ts(var("School Code"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_CSEC,
                 WFDictionaryKey="secret", WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_CDICT, "Dictionary"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_ST,
                 WFTextActionText=ts(out(U_CSEC, "Dictionary Value"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Secret",
                 WFInput=attach(out(U_ST, "Text"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_CSID,
                 WFDictionaryKey="school_id", WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_CDICT, "Dictionary"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_SI,
                 WFTextActionText=ts(out(U_CSID, "Dictionary Value"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Id",
                 WFInput=attach(out(U_SI, "Text"))))

    # Matched against the raw scanned text rather than an extracted value, so no
    # JSON boolean has to survive coercion.
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
                 WFNotificationActionTitle=ts("Brightwheel"),
                 WFNotificationActionBody=ts(
                     "This school now requires signatures at check-in. These "
                     "shortcuts do not send one, so check-ins may start "
                     "failing.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGS, WFControlFlowMode=2))

    # ---- per child: read state, skip if already there, otherwise send ----
    for cname, target in CHILDREN:
        p = kid[cname]
        A.append(comment(
            f"--- {title_word.upper()}: {cname.upper()} ---\n"
            f"Read {cname}'s most recent check-in event first, so running this "
            "twice in one morning does not record a second arrival. Brightwheel "
            "reports 1 for checked in and 2 for checked out, and the reply for a "
            "single event carries exactly one of them."
        ))
        A.append(act("is.workflow.actions.downloadurl", UUID=p["act"],
                     Advanced=True, ShowHeaders=False, WFHTTPMethod="GET",
                     WFURL=f"{BASE}/students/{target}/activities"
                           "?page_size=1&action_type=ac_checkin",
                     WFHTTPHeaders=dict_field([
                         kv("Accept", ts("application/json")),
                         kv("X-Parse-Session-Token", ts(var("Session Token"))),
                         kv("X-Client-Name", ts(CLIENT_NAME)),
                         kv("X-Client-Version", ts(CLIENT_VERSION)),
                     ])))
        C_SKIP = gate(p["act"], "Contents of URL",
                      '"state"\\s*:\\s*"%s"' % already)
        A.append(comment(
            f"Skip {cname} if nothing needs to change.\n"
            f"- Condition counts whether {cname} is already in the state this "
            "shortcut would produce\n"
            "- Otherwise branch sends the request and reports the result\n"
            "- If the state cannot be read, the request is sent anyway"
        ))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gskip"], WFControlFlowMode=0,
                     WFCondition=2, WFNumberValue="0",
                     WFInput=cond_input(out(C_SKIP, "Count"))))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts("Brightwheel"),
                     WFNotificationActionBody=ts(
                         f"• {cname} was {already_word} — no change")))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gskip"], WFControlFlowMode=1))

        A.append(act("is.workflow.actions.gettext", UUID=p["body"],
                     WFTextActionText=ts(
                         '{"checkins":[{"actor":{"object_id":"' + ACTOR + '"},'
                         '"health_screen":{"questions":[]},'
                         '"room":{"object_id":"' + ROOM + '"},'
                         '"checked_in":' + ("true" if desired else "false") + ','
                         '"target":{"object_id":"' + target + '"},'
                         '"note":""}],'
                         '"school_id":"',
                         var("School Id"),
                         '","secret":"',
                         var("School Secret"),
                         '","checkin_code":"',
                         out(U["code"], "Check-In Code"),
                         '"}')))
        A.append(act("is.workflow.actions.downloadurl", UUID=p["resp"],
                     Advanced=True, ShowHeaders=False,
                     WFURL=f"{BASE}/checkins/", WFHTTPMethod="POST",
                     WFHTTPBodyType="File",
                     # SKILL.md rule 9: WFRequestVariable is a variable-only
                     # parameter and takes a WFTextTokenAttachment. Serialised as
                     # a WFTextTokenString it sends an EMPTY body, and the API
                     # answers 422 "cannot process empty checkins" — which still
                     # contains "checkins", so it used to read as success.
                     WFRequestVariable=attach(out(p["body"], "Text")),
                     WFFormValues=dict_field([]),
                     WFHTTPHeaders=dict_field([
                         kv("Content-Type", ts("application/json")),
                         kv("Accept", ts("application/json")),
                         kv("X-Parse-Session-Token", ts(var("Session Token"))),
                         kv("X-Client-Name", ts(CLIENT_NAME)),
                         kv("X-Client-Version", ts(CLIENT_VERSION)),
                     ])))
        A.append(act("is.workflow.actions.gettext", UUID=p["rtext"],
                     WFTextActionText=ts(out(p["resp"], "Contents of URL"))))
        # "event_date" appears only when a record was really created. The
        # obvious test, "checkins", also matches the 422 empty-body error.
        C_OK = gate(p["resp"], "Contents of URL", '"event_date"')
        A.append(comment(
            f"Report the result for {cname}.\n"
            "- Condition counts whether Brightwheel echoed the check-in back\n"
            "- Otherwise branch shows Brightwheel's own error text"
        ))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gres"], WFControlFlowMode=0,
                     WFCondition=2, WFNumberValue="0",
                     WFInput=cond_input(out(C_OK, "Count"))))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts("Brightwheel"),
                     WFNotificationActionBody=ts(f"✅ {cname} {verb}")))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gres"], WFControlFlowMode=1))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts(f"⚠️ {cname} not {verb}"),
                     WFNotificationActionBody=ts(
                         out(p["rtext"], "Text"),
                         "\n\nIf this says the session expired, run this "
                         "shortcut by hand to sign in again.")))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gres"], WFControlFlowMode=2))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gskip"], WFControlFlowMode=2))

    return name, {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": glyph,
                           "WFWorkflowIconStartColor": color},
        "WFWorkflowImportQuestions": questions,
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": name,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }


# --- helper: read the school secret out of a scanned QR code ---------------
# iOS 27 has no action that decodes a QR back into a shortcut:
# `is.workflow.actions.scanbarcode` decodes an image but is macOS-only, and
# `com.apple.BarcodeScanner.BarcodeScannerIntent` ("Open Code Scanner") only
# launches the scanner app. So this helper parses the decoded text instead,
# defaulting to whatever Code Scanner left on the clipboard.
if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--debug"]
    env = load_env(Path(__file__).parent / ".env") if "--debug" in sys.argv else None
    dest = Path(argv[0] if argv else ".")
    dest.mkdir(parents=True, exist_ok=True)
    if env is not None:
        print("DEBUG BUILD — real credentials are baked in; do not commit or share")
    for d in ("in", "out"):
        name, pl = build(d, env)
        (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
        print(f"{name}: {len(pl['WFWorkflowActions'])} actions, "
              f"{len(pl['WFWorkflowImportQuestions'])} setup questions")

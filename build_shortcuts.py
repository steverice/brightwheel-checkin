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
    "token": "BRIGHTWHEEL_SESSION_TOKEN",
    "email": "BRIGHTWHEEL_EMAIL",
    "password": "BRIGHTWHEEL_PASSWORD",
    "start": "BRIGHTWHEEL_START_HOUR",
    "end": "BRIGHTWHEEL_END_HOUR",
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

# Default window bounds, offered as import-time defaults only — the real values
# are whatever gets typed at import. Start hour is inclusive, end hour is
# EXCLUSIVE, so the numbers entered match the times as spoken: 8 and 13 is
# "8:00 to 1:00".
WINDOW = {"in": (8, 13), "out": (13, 18)}

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
    ("token", "text", "Brightwheel session token",
     "The X-Parse-Session-Token for your account. Leave as 'not set' to sign in "
     "on the first run instead.", "not set", ""),
    ("email", "text", "Brightwheel account email",
     "Used to sign in again when the session token stops working.",
     "not set", ""),
    ("password", "text", "Brightwheel account password",
     "Used together with the email, only when signing in again.",
     "not set", ""),
    ("code", "text", "Brightwheel check-in code",
     "Your 4-digit guardian check-in code.", "not set", ""),
    ("secret", "text", "Brightwheel school QR secret",
     "The 'secret' value from your school's check-in QR code. It looks like "
     "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx. Run the Brightwheel Scan Code "
     "shortcut at the school to read it off the code and copy it.",
     "not set", ""),
    ("start", "number", "Earliest hour this may run",
     "A whole hour from 0 to 23, and the shortcut may run from this hour "
     "onwards. 8 means it can run from 8:00am.", None, None),
    ("end", "number", "Hour this stops running",
     "A whole hour from 0 to 23, and the shortcut stops running once this hour "
     "arrives. 13 means the last run is at 12:59pm.", None, None),
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
    U = {k: next(i) for k in ("code", "secret", "token", "email", "password",
                              "start", "end")}
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
        A.append(act("is.workflow.actions.gettext", UUID=t,
                     WFTextActionText=ts(out(src, name))))
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
        "Built to be run unattended by a location automation (for example, when "
        "you arrive at school in the morning), so it never stops to ask a "
        "question while it runs. Everything it needs is collected once, when you "
        "import it.\n\n"
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
    names = {"code": "Check-In Code", "secret": "School Secret",
             "token": "Saved Token", "email": "Account Email",
             "password": "Account Password",
             "start": "Start Hour", "end": "End Hour"}
    for key, kind, prompt, blurb, default, prompt_default in SETUP:
        if kind == "number":
            default = str(WINDOW[direction][0 if key == "start" else 1])
            prompt_default = default
            param, ident = "WFNumberActionNumber", "is.workflow.actions.number"
        else:
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

    # ---- time guard ----
    #
    # Two things here were learned the hard way and should not be "simplified":
    #
    # 1. The current time comes from a {Type: CurrentDate} magic token fed
    #    straight into Format Date, not from a Date action. A Date action with
    #    WFDateActionMode="Current Date" imported silently producing nothing, so
    #    the hour came out blank. That enum string is undocumented and the only
    #    observed sample uses "Specified Date". CurrentDate is a documented
    #    magic variable type.
    # 2. The bounds are compared with Math and two plain Ifs rather than one
    #    multi-condition If. Numeric rows inside a WFConditions table import
    #    with an empty, red comparison value, while string rows in the same
    #    table render fine.
    A.append(comment(
        "--- WHEN THIS IS ALLOWED TO RUN ---\n"
        "Only act on a weekday, between the two hours asked for at import. "
        "Every shortcut in your library can be started by saying its name to "
        "Siri, and there is no way to turn that off, so this window is what "
        "stops a misheard phrase or a stray tap from recording attendance at "
        "the wrong time.\n\n"
        "The weekday name is read in whatever language the phone is set to, and "
        "the check below expects the English names."
    ))
    A.append(act("is.workflow.actions.format.date", UUID=U_HOUR,
                 WFDate=ts({"Type": "CurrentDate"}),
                 WFDateFormatStyle="Custom", WFDateFormat="H"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Hour",
                 WFInput=attach(out(U_HOUR, "Formatted Date"))))
    A.append(act("is.workflow.actions.format.date", UUID=U_DAY,
                 WFDate=ts({"Type": "CurrentDate"}),
                 WFDateFormatStyle="Custom", WFDateFormat="EEEE"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Weekday",
                 WFInput=attach(out(U_DAY, "Formatted Date"))))

    A.append(act("is.workflow.actions.text.match", UUID=U_WEM,
                 WFMatchTextPattern="^(Saturday|Sunday)$",
                 text=ts(var("Weekday"))))
    A.append(act("is.workflow.actions.count", UUID=U_WEC,
                 WFCountType="Items",
                 WFInput=attach(out(U_WEM, "Matches")),
                 Input=attach(out(U_WEM, "Matches"))))
    A.append(comment(
        "Stop on a weekend.\n"
        "- Condition counts how many times today's name matched Saturday or "
        "Sunday\n"
        "- Nothing has been sent to Brightwheel before this point"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(U_WEC, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     "This only runs on weekdays, and today is ",
                     var("Weekday"), ". Nothing was sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0, WFControlFlowMode=2))

    # A plain "has any value" test is not enough: an empty string passes it, and
    # the run then fails the start-hour comparison and reports itself as too
    # early. Require the hour to actually be digits.
    A.append(act("is.workflow.actions.text.match", UUID=U_HNM,
                 WFMatchTextPattern="^[0-9]+$", text=ts(var("Hour"))))
    A.append(act("is.workflow.actions.count", UUID=U_HNC,
                 WFCountType="Items",
                 WFInput=attach(out(U_HNM, "Matches")),
                 Input=attach(out(U_HNM, "Matches"))))
    A.append(comment(
        "Stop when the clock cannot be read.\n"
        "- Condition counts whether the current hour came back as a number\n"
        "- Blocking here keeps an unreadable clock from quietly disabling the "
        "two window checks below, which would let every run through"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0C, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="1",
                 WFInput=cond_input(out(U_HNC, "Count"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     "The current hour did not come back as a number, so "
                     "nothing was sent. It read as '", var("Hour"),
                     "'. Check the Format Date actions at the top.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0C, WFControlFlowMode=2))

    # Hour minus the start hour: negative means too early.
    A.append(act("is.workflow.actions.math", UUID=U_AFT,
                 WFMathOperation="-",
                 WFInput=attach(var("Hour")),
                 WFMathOperand=attach(out(U["start"], "Start Hour"))))
    A.append(comment(
        "Stop before the window opens.\n"
        "- The calculation above is the current hour minus the earliest hour\n"
        "- A result below zero means it is still too early today"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0A, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="0",
                 WFInput=cond_input(out(U_AFT, "Calculation Result"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     "It is hour ", var("Hour"), ", earlier than the ",
                     out(U["start"], "Start Hour"),
                     ":00 start set when this was imported. Nothing was sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0A, WFControlFlowMode=2))

    # End hour minus hour: one or more means still inside, since end is exclusive.
    A.append(act("is.workflow.actions.math", UUID=U_BEF,
                 WFMathOperation="-",
                 WFInput=attach(out(U["end"], "End Hour")),
                 WFMathOperand=attach(var("Hour"))))
    A.append(comment(
        "Stop once the window has closed.\n"
        "- The calculation above is the finish hour minus the current hour\n"
        "- The finish hour is exclusive, so anything below one is too late"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0B, WFControlFlowMode=0,
                 WFCondition=0, WFNumberValue="1",
                 WFInput=cond_input(out(U_BEF, "Calculation Result"))))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     "It is hour ", var("Hour"), ", and this stops running at ",
                     out(U["end"], "End Hour"),
                     ":00 as set when it was imported. Nothing was sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0B, WFControlFlowMode=2))

    # ---- session token, with interactive sign-in on failure ----
    #
    # Sign-in is two steps and needs a 6-digit code (see README). A background
    # automation cannot answer the prompt, but this branch only runs when the
    # token has already expired, so that run was failing regardless. Running the
    # shortcut by hand afterwards completes it.
    U_GT, U_PROBE = next(i), next(i)
    U_START, U_CODE, U_SESS = next(i), next(i), next(i)
    U_TMATCH, U_TGRP, U_ZERO = next(i), next(i), next(i)
    G_HAVE, G_LOOP, G_SIGNIN, G_GOT, G_FAIL = (next(i) for _ in range(5))

    A.append(comment(
        "--- SESSION ---\n"
        "Use the saved token if there is one, otherwise the one entered at "
        "import. If it has expired, sign in again below."
    ))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GT,
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False))
    C_HAVE = gate(U_GT, "Stored Content")
    A.append(comment(
        "Prefer a token saved by an earlier sign-in.\n"
        "- Condition counts whether anything has been saved on this device yet\n"
        "- Otherwise branch falls back to the token entered at import"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(out(C_HAVE, "Count"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U_GT, "Stored Content"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U["token"], "Saved Token"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_HAVE, WFControlFlowMode=2))

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
        "Sign in again, up to three times.\n"
        "- Each pass asks Brightwheel to send a fresh code, then asks you for it\n"
        "- Cancelling the code prompt stops the whole shortcut\n"
        "- A pass that gets a token clears Needs Sign In, so later passes do "
        "nothing"
    ))
    A.append(act("is.workflow.actions.repeat.count", UUID=next(i),
                 GroupingIdentifier=G_LOOP, WFControlFlowMode=0,
                 WFRepeatCount=3))
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
                 WFAskActionPrompt="Brightwheel emailed a 6-digit code. Enter it "
                                   "here, or cancel to stop.",
                 WFInputType="Text"))
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
                     "Could not sign in, so nothing was sent. Run this shortcut "
                     "by hand to enter a fresh code.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=2))

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
                         '"school_id":"' + SCHOOL + '",'
                         '"secret":"',
                         out(U["secret"], "School Secret"),
                         '","checkin_code":"',
                         out(U["code"], "Check-In Code"),
                         '"}')))
        A.append(act("is.workflow.actions.downloadurl", UUID=p["resp"],
                     Advanced=True, ShowHeaders=False,
                     WFURL=f"{BASE}/checkins/", WFHTTPMethod="POST",
                     WFHTTPBodyType="File",
                     WFRequestVariable=ts(out(p["body"], "Text")),
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
        C_OK = gate(p["resp"], "Contents of URL", r'"checkins"\s*:')
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
def build_scanner():
    name = "Brightwheel Scan Code"
    i = iter(uuids(30))
    U_CLIP, U_ASK, U_DICT = next(i), next(i), next(i)
    U_SEC, U_SID, U_SIG = next(i), next(i), next(i)
    U_SECT, U_SIGT = next(i), next(i)
    G1, G2 = next(i), next(i)

    A = []
    A.append(comment(
        "Brightwheel — Scan Code\n\n"
        "Reads the school's check-in QR code and gives you the 'secret' value to "
        "paste into Brightwheel Check In and Brightwheel Check Out.\n\n"
        "You only need this if the school turns on Quick Scan Refresh, which "
        "rotates the secret every few hours, or if a check-in fails with "
        "'Problem scanning QR code'.\n\n"
        "How to use it, standing at the sign-in tablet:\n"
        "1. Open Code Scanner from Control Center and point it at the QR code.\n"
        "2. Copy the text it shows.\n"
        "3. Run this shortcut. The copied text is filled in for you, so you can "
        "usually just tap Done.\n\n"
        "iOS has no action that can read a QR code directly inside a shortcut, "
        "which is why the scanning step happens in Code Scanner rather than here."
    ))
    A.append(comment(
        "Generated by build_shortcuts.py in the brightwheel-checkin repo.\n\n"
        "Edits made here are lost the next time the shortcut is rebuilt, so change "
        "the generator instead."
    ))
    A.append(comment(
        "--- READ THE CODE ---\n"
        "The QR code is not a link. It holds a small block of text listing the "
        "school's secret, the school id, and whether signatures are required."
    ))
    A.append(act("is.workflow.actions.getclipboard", UUID=U_CLIP))
    A.append(act("is.workflow.actions.ask", UUID=U_ASK,
                 WFAskActionPrompt="QR code contents",
                 WFInputType="Text",
                 WFAskActionDefaultAnswer=ts(out(U_CLIP, "Clipboard"))))
    A.append(act("is.workflow.actions.detect.dictionary", UUID=U_DICT,
                 WFInput=ts(out(U_ASK, "Provided Input"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_SEC,
                 WFDictionaryKey="secret", WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_DICT, "Dictionary"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_SID,
                 WFDictionaryKey="school_id", WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_DICT, "Dictionary"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_SIG,
                 WFDictionaryKey="signatures_enabled",
                 WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_DICT, "Dictionary"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_SECT,
                 WFTextActionText=ts(out(U_SEC, "Dictionary Value"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="School Secret",
                 WFInput=attach(out(U_SECT, "Text"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_SIGT,
                 WFTextActionText=ts(out(U_SIG, "Dictionary Value"))))
    A.append(act("is.workflow.actions.setvariable",
                 WFVariableName="Signatures Flag",
                 WFInput=attach(out(U_SIGT, "Text"))))
    A.append(comment(
        "Copy the secret out, or explain what went wrong.\n"
        "- Condition checks whether a secret was found in the scanned text\n"
        "- Copy to Clipboard replaces the scanned block with just the secret\n"
        "- Otherwise branch shows what was actually read, to help spot a "
        "half-copied or wrong code"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G1, WFControlFlowMode=0, WFCondition=100,
                 WFInput=cond_input(var("School Secret"))))
    A.append(act("is.workflow.actions.setclipboard",
                 WFInput=ts(var("School Secret"))))
    A.append(comment(
        "Warn when the school starts requiring signatures.\n"
        "- Condition checks the signatures setting from the scanned code\n"
        "- Brightwheel reports 1 when signatures are required and 0 when not"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=0, WFCondition=4,
                 WFConditionalActionString="1",
                 WFInput=cond_input(var("Signatures Flag"))))
    A.append(act("is.workflow.actions.showresult",
                 Text=ts("Secret copied to the clipboard:\n\n",
                         var("School Secret"),
                         "\n\nSchool: ", out(U_SID, "Dictionary Value"),
                         "\n\nWARNING: this school now requires signatures at "
                         "check-in. The check-in and check-out shortcuts do not "
                         "send a signature, so they will probably stop working "
                         "until they are rebuilt to include one.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.showresult",
                 Text=ts("Secret copied to the clipboard:\n\n",
                         var("School Secret"),
                         "\n\nSchool: ", out(U_SID, "Dictionary Value"),
                         "\n\nPaste it into the 'Brightwheel school QR secret' "
                         "question when you re-import Brightwheel Check In and "
                         "Brightwheel Check Out, or into the School Secret text "
                         "action inside each one.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G1, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.showresult",
                 Text=ts("No secret found in that text.\n\nExpected a block "
                         "listing a secret and a school id. What was read:\n\n",
                         out(U_ASK, "Provided Input"),
                         "\n\nOpen Code Scanner from Control Center, point it at "
                         "the school's QR code, copy the text it shows, then run "
                         "this again.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G1, WFControlFlowMode=2))

    return name, {
        "WFWorkflowActions": A,
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59819,
                           "WFWorkflowIconStartColor": 463140863},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": name,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }


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
    name, pl = build_scanner()
    (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
    print(f"{name}: {len(pl['WFWorkflowActions'])} actions")

#!/usr/bin/env python3
"""Generate the two Brightwheel check-in/check-out Shortcuts plists.

Both shortcuts are structurally identical; only the `checked_in` literal,
the display wording, and the icon differ. README.md has the endpoint
contract.
"""
import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path

OBJ = "￼"  # U+FFFC placeholder for an inline variable

# --- Who this build is for --------------------------------------------
#
# Identifiers are not secret in the credential sense, but they do identify real
# children and a real school, so they live in a gitignored `roster.json` rather
# than in this file. `roster.example.json` shows the shape, and
# `--roster PATH` points at another one (the tests use a fixture full of
# obvious placeholders).
#
# These are filled in by load_roster() before build() runs.
ACTOR = ROOM = SCHOOL = None
SCHOOL_NAME = ROOM_NAME = None
CHILDREN = []

DEFAULT_ROSTER = Path(__file__).parent / "roster.json"


def load_roster(path=DEFAULT_ROSTER):
    """Read the roster and publish it as module state build() can see.

    `school_id` is only used to seed a baked build; on a normal run the school
    comes from the scanned QR code, which is why these shortcuts work at a
    different school without a rebuild.
    """
    global ACTOR, ROOM, SCHOOL, SCHOOL_NAME, ROOM_NAME, CHILDREN
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Copy roster.example.json to roster.json and "
            f"fill in your own guardian, room and children ids.")
    data = json.loads(path.read_text())
    missing = [k for k in ("guardian_id", "room_id", "children")
               if not data.get(k)]
    if missing:
        raise SystemExit(f"{path} is missing: {', '.join(missing)}")

    ACTOR = data["guardian_id"]
    ROOM = data["room_id"]
    SCHOOL = data.get("school_id", "")
    SCHOOL_NAME = data.get("school_name", "your school")
    ROOM_NAME = data.get("room_name", "")
    CHILDREN = [(c["name"], c["id"]) for c in data["children"]]
    return data


def child_list_phrase():
    """"A and B", or "A, B and C" — for the description comment."""
    names = [n for n, _ in CHILDREN]
    if len(names) <= 1:
        return names[0] if names else "nobody"
    return f"{', '.join(names[:-1])} and {names[-1]}"


BASE = "https://schools.mybrightwheel.com/api/v1"
# Kept so an overridden BASE can be reported as such. The integration tests
# point BASE at a mock; nothing else should.
DEFAULT_BASE = BASE
CLIENT_NAME = "ios"
CLIENT_VERSION = "3.103.0"


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


def build(direction=None, env=None):
    """The shortcut that does the work. Direction arrives as Shortcut Input."""
    name = "Brightwheel Attendance"
    # 62329 is a ring of petals — close to the Brightwheel logo — on pink.
    # Both taken from a share link's icon_glyph / a resolved palette value and
    # then checked on a simulator, because the bundled glyph names are wrong.
    glyph, color = 62329, 3980825855

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
        "Brightwheel — Check\n\n"
        f"Checks {child_list_phrase()} in or out at {SCHOOL_NAME}"
        f"{f' (room {ROOM_NAME})' if ROOM_NAME else ''} by "
        "talking to the Brightwheel API directly, without opening the app.\n\n"
        "Brightwheel Check In and Brightwheel Check Out start this one and tell "
        "it which direction to go; those are the two that carry the automation "
        "triggers. Run this on its own and it simply asks.\n\n"
        "Built to be run unattended by an arrival trigger, so it does not stop "
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

    # ---- which direction ----
    #
    # Direction comes from Shortcut Input, set by whichever wrapper started this.
    # It deliberately is NOT inferred from the clock: the trigger time ranges
    # live on the device and can be edited there, so a baked-in threshold would
    # silently disagree with them and send the wrong direction.
    #
    # Run with no input, this stops. That also makes the shortcut safe to have
    # in the library: saying its name to Siri cannot check anyone anywhere.
    U_DIRT, U_DIRF, U_VT, U_VF, U_AT, U_AF = (next(i) for _ in range(6))
    G_VALID, G_DIR = next(i), next(i)
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
        "- Cancelling the menu stops the shortcut, so nothing is sent"
    ))
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=0,
                 WFMenuPrompt="Check the children in or out?",
                 WFMenuItems=["Check In", "Check Out"]))
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
    A.append(act("is.workflow.actions.choosefrommenu", UUID=next(i),
                 GroupingIdentifier=G_MENU, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_VALID, WFControlFlowMode=2))

    # Wanted In is 1 for a check-in and 0 for a check-out, which later gets
    # compared against whether the child is already checked in.
    C_DIR = gate("Direction", None, "^in$")
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Wanted In",
                 WFInput=attach(out(C_DIR, "Count"))))
    A.append(comment(
        "Set the wording and the value Brightwheel expects.\n"
        "- Condition checks whether this run is a check-in\n"
        "- Checked In Value goes into the request as true or false\n"
        "- Verb and Already Word are only used in notifications"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_DIR, WFControlFlowMode=0,
                 WFCondition=2, WFNumberValue="0",
                 WFInput=cond_input(var("Wanted In"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_DIRT,
                 WFTextActionText="true"))
    A.append(act("is.workflow.actions.setvariable",
                 WFVariableName="Checked In Value",
                 WFInput=attach(out(U_DIRT, "Text"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_VT,
                 WFTextActionText="checked in"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Verb",
                 WFInput=attach(out(U_VT, "Text"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_AT,
                 WFTextActionText="already checked in"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Already Word",
                 WFInput=attach(out(U_AT, "Text"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_DIR, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.gettext", UUID=U_DIRF,
                 WFTextActionText="false"))
    A.append(act("is.workflow.actions.setvariable",
                 WFVariableName="Checked In Value",
                 WFInput=attach(out(U_DIRF, "Text"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_VF,
                 WFTextActionText="checked out"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Verb",
                 WFInput=attach(out(U_VF, "Text"))))
    A.append(act("is.workflow.actions.gettext", UUID=U_AF,
                 WFTextActionText="already checked out"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Already Word",
                 WFInput=attach(out(U_AF, "Text"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_DIR, WFControlFlowMode=2))

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
                 WFNotificationActionTitle=ts("Brightwheel — nobody ", var("Verb")),
                 WFNotificationActionBody=ts(
                     "Could not sign in after five tries, so nothing was sent. "
                     "Run this shortcut by hand and enter a code, or leave the "
                     "box empty to have another sent.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_FAIL, WFControlFlowMode=2))

    # ---- attempt loop: school code, then every child ----
    #
    # Two passes. The first sends; if a child comes back with the school code
    # rejected, the stored code is deleted and a second pass is requested, which
    # finds nothing stored and scans a fresh one. "First run" and "the code
    # rotated" are therefore the same branch — deleting the stored code is what
    # turns one into the other.
    #
    # Children are a Repeat rather than an unrolled pair so the retry re-enters
    # the same actions instead of a second copy. Repeat Item is the child's
    # name, and the id comes from a roster Dictionary looked up with a tokenized
    # key — the one wiring here with a verified example behind it. Get Item from
    # List and Split Text were the obvious alternatives and have no worked
    # example of their parameters anywhere.
    U_ROSTER, U_NAMES, U_ONE, U_ZERO2 = (next(i) for _ in range(4))
    U_GC, U_SCAN, U_CDICT = next(i), next(i), next(i)
    U_CSEC, U_CSID, U_ST, U_SI = (next(i) for _ in range(4))
    U_CHILD, U_CHILDT = next(i), next(i)
    U_ACT, U_BODY, U_RESP, U_RTEXT = (next(i) for _ in range(4))
    G_ATTEMPT, G_DOIT, G_HAVE, G_SIGS = (next(i) for _ in range(4))
    G_KIDS, G_SKIP, G_RES, G_STALE = (next(i) for _ in range(4))

    A.append(comment(
        "--- WHO TO SEND FOR ---\n"
        "This list drives the loop. Each name is matched to a Brightwheel id "
        "inside it, so the name shown in notifications and the id sent to "
        "Brightwheel always come from the same entry."
    ))
    A.append(act("is.workflow.actions.list", UUID=U_NAMES,
                 WFItems=[n for n, _ in CHILDREN]))
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
                          "school_id": SCHOOL, "signatures_enabled": False},
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
                 WFNotificationActionTitle=ts("Brightwheel"),
                 WFNotificationActionBody=ts(
                     "This school now requires signatures at check-in. These "
                     "shortcuts do not send one, so check-ins may start "
                     "failing.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SIGS, WFControlFlowMode=2))

    # --- one pass over the children ---
    A.append(comment(
        f"Work through the children in turn.\n"
        "- Child Name is the current child, taken from the list above\n"
        "- A short block below turns that name into their Brightwheel id\n"
        f"- Brightwheel reports 1 for checked in and 2 for checked out, and a "
        "reply for a single event carries exactly one of them"
    ))
    A.append(act("is.workflow.actions.repeat.each", UUID=next(i),
                 GroupingIdentifier=G_KIDS, WFControlFlowMode=0,
                 WFInput=attach(out(U_NAMES, "List"))))
    # "Repeat Item 2", not "Repeat Item": this loop sits inside the retry
    # Repeat, and a count-style outer loop shifts the numbering just as a
    # nested Repeat with Each does. Verified on device with a probe — with the
    # unnumbered name the item came back empty, the roster lookup found
    # nothing, and notifications showed a blank child name.
    #
    # Captured into Child Name here so the numbered variable appears exactly
    # once; if the nesting ever changes, this is the only line to revisit.
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Child Name",
                 WFInput=attach(var("Repeat Item 2"))))
    # One plain If per child rather than a dictionary lookup. Get Dictionary
    # Value returned nothing here too, leaving the target empty and every
    # check-in answered with E1204 "The requested resource could not be found".
    # Comparing a variable against a literal string is the primitive that works.
    for cname, target in CHILDREN:
        g_who, u_id = next(i), next(i)
        A.append(comment(
            f"Look up {cname}'s Brightwheel id.\n"
            "- Condition compares the current child's name with this one\n"
            "- Only the matching block sets Child Id"
        ))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g_who, WFControlFlowMode=0,
                     WFCondition=4, WFConditionalActionString=cname,
                     WFInput=cond_input(var("Child Name"))))
        A.append(act("is.workflow.actions.gettext", UUID=u_id,
                     WFTextActionText=target))
        A.append(act("is.workflow.actions.setvariable", WFVariableName="Child Id",
                     WFInput=attach(out(u_id, "Text"))))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=g_who, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_ACT,
                 Advanced=True, ShowHeaders=False, WFHTTPMethod="GET",
                 WFURL=ts(f"{BASE}/students/", var("Child Id"),
                          "/activities?page_size=1&action_type=ac_checkin"),
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(var("Session Token"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))
    # Is the child checked in right now? 1 or 0, same shape as Wanted In.
    C_IN = gate(U_ACT, "Contents of URL", '"state"\\s*:\\s*"1"')
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
                 WFNotificationActionTitle=ts("Brightwheel"),
                 WFNotificationActionBody=ts(
                     "• ", var("Child Name"), " was ",
                     var("Already Word"), " — no change")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_SKIP, WFControlFlowMode=1))

    A.append(act("is.workflow.actions.gettext", UUID=U_BODY,
                 WFTextActionText=ts(
                     '{"checkins":[{"actor":{"object_id":"' + ACTOR + '"},'
                     '"health_screen":{"questions":[]},'
                     '"room":{"object_id":"' + ROOM + '"},'
                     '"checked_in":',
                     var("Checked In Value"),
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
                 WFNotificationActionTitle=ts("Brightwheel"),
                 WFNotificationActionBody=ts(
                     "✅ ", var("Child Name"), " ", var("Verb"))))
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
                 WFNotificationActionTitle=ts("Brightwheel"),
                 WFNotificationActionBody=ts(
                     "The school's check-in code has changed. Scanning the new "
                     "one and trying again.")))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G_STALE, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(
                     "⚠️ ", var("Child Name"), " not ", var("Verb")),
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


# --- helper: read the school secret out of a scanned QR code ---------------
# iOS 27 has no action that decodes a QR back into a shortcut:
# `is.workflow.actions.scanbarcode` decodes an image but is macOS-only, and
# `com.apple.BarcodeScanner.BarcodeScannerIntent` ("Open Code Scanner") only
# launches the scanner app. So this helper parses the decoded text instead,
# defaulting to whatever Code Scanner left on the clipboard.
def build_wrapper(direction):
    """A trigger carrier. Two actions: the direction, and the call.

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
    # A plane arriving for in, departing for out: a matched pair that reads as
    # direction and nothing else. (Not 62466/62467, which are a plane on a
    # runway rather than the arriving/departing pair.) Sunrise/sunset was the other candidate and was
    # dropped — its arrow and its sun point opposite ways, so no assignment of
    # it is truthful. Green and red match Brightwheel's own colors. All four
    # values checked on a simulator; the palette numbers are keys, not RGB.
    glyph = 62022 if checking_in else 62021
    color = 4292093695 if checking_in else 4282601983

    i = iter(uuids(6))
    u_text = next(i)
    A = [
        comment(
            f"Brightwheel — {title}\n\n"
            f"Attach the arrival or departure trigger to this shortcut. All it "
            f"does is tell Brightwheel Attendance to check the children {word}.\n\n"
            "The direction lives here rather than in the shortcut that does the "
            "work, so which trigger fired decides it. Nothing is worked out from "
            "the time of day, which means changing a trigger's hours cannot make "
            "it send the wrong direction."
        ),
        comment(
            "Generated by build_shortcuts.py in the brightwheel-checkin repo.\n\n"
            "Edits made here are lost the next time the shortcut is rebuilt, so "
            "change the generator instead."
        ),
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
    ap.add_argument("--roster", metavar="PATH", default=DEFAULT_ROSTER,
                    help="who to check in: guardian, room and children ids "
                         "(default roster.json; see roster.example.json)")
    ap.add_argument("--api-base", metavar="URL", default=BASE,
                    help="point the shortcuts at a different API root; used by "
                         "the integration tests to reach the mock Brightwheel")
    args = ap.parse_args()

    # build() reads these at call time, so setting them here is enough.
    BASE = args.api_base
    load_roster(args.roster)

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

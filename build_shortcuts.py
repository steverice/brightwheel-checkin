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


def cond_row(code, inp, key=None, value=None):
    """One row of a multi-condition If."""
    row = {"WFCondition": code, "WFInput": cond_input(inp)}
    if key is not None:
        row[key] = value
    return row


def conditions(prefix, rows):
    """WFConditions table. prefix 0 = Any are true, 1 = All are true."""
    return {
        "Value": {"WFActionParameterFilterPrefix": prefix,
                  "WFActionParameterFilterTemplates": rows},
        "WFSerializationType": "WFContentPredicateTableTemplate",
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

# Weekday-only windows, as local clock hours. A run outside its window sends
# nothing, which is what stops a stray Siri phrase or a mistaken tap from
# recording real attendance. Hours are inclusive: 8..12 covers 08:00-12:59, so
# the two windows meet at 13:00 without overlapping.
# TEMPORARY: the check-in window is widened to 2:00pm for live testing. Revert
# to (8, 12, "8:00am and 1:00pm") when testing is done — while it is widened it
# overlaps the check-out window between 1:00pm and 2:00pm.
WINDOW = {"in": (8, 13, "8:00am and 2:00pm"),
          "out": (13, 17, "1:00pm and 6:00pm")}

# (key, prompt, blurb, action_value, prompt_default)
#
# action_value sits in the Text action. The validator rejects an empty one, and
# it is what gets sent if an import question is skipped, so it is a marker that
# fails loudly rather than something that looks like a real value.
# prompt_default pre-fills the import field: blank for anything secret, a format
# hint only where one genuinely helps.
SETUP = [
    ("email", "Brightwheel account email",
     "Email address you sign in to Brightwheel with.", "not set", ""),
    ("password", "Brightwheel account password",
     "Used only to refresh an expired session token.", "not set", ""),
    ("code", "Brightwheel check-in code",
     "Your 4-digit guardian check-in code.", "not set", ""),
    ("secret", "Brightwheel school QR secret",
     "The 'secret' value from your school's check-in QR code. It looks like "
     "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx. Run the Brightwheel Scan Code "
     "shortcut at the school to read it off the code and copy it.",
     "not set", ""),
]


def build(direction):
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

    i = iter(uuids(90))
    U = {k: next(i) for k in ("email", "password", "code", "secret")}
    U_NOW, U_HOUR, U_DAY, G0 = next(i), next(i), next(i), next(i)
    U_GT = next(i)
    U_PROBE, U_PDICT, U_PERR = next(i), next(i), next(i)
    U_LOGIN, U_LTXT, U_MATCH, U_GRP = next(i), next(i), next(i), next(i)
    G2, G3 = next(i), next(i)
    kid = {c: {k: next(i) for k in
               ("act", "adict", "state", "stext", "gskip", "body", "resp",
                "rdict", "chk", "rtext", "gres")}
           for c, _ in CHILDREN}

    A = []
    questions = []

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
    for key, prompt, blurb, default, prompt_default in SETUP:
        questions.append({
            "ActionIndex": len(A),
            "Category": "Parameter",
            "DefaultValue": prompt_default,
            "ParameterKey": "WFTextActionText",
            "Text": f"{prompt} — {blurb}",
        })
        A.append(act("is.workflow.actions.gettext", UUID=U[key],
                     CustomOutputName={"email": "Account Email",
                                       "password": "Account Password",
                                       "code": "Check-In Code",
                                       "secret": "School Secret"}[key],
                     WFTextActionText=default))

    # ---- time guard ----
    lo, hi, window_label = WINDOW[direction]
    A.append(comment(
        "--- WHEN THIS IS ALLOWED TO RUN ---\n"
        f"Only act on a weekday between {window_label}. Every shortcut in your "
        "library can be started by saying its name to Siri, and there is no way "
        "to turn that off, so this window is what stops a misheard phrase or a "
        "stray tap from recording attendance at the wrong time.\n\n"
        "The weekday name is read in whatever language the phone is set to, and "
        "the check below compares against the English names."
    ))
    # WFDateActionMode is a free-form string in ToolKit with no case list. The
    # one observed sample uses "Specified Date", so "Current Date" is the
    # matching UI label rather than a verified constant. Check that this action
    # reads "Current Date" in the editor after importing.
    A.append(act("is.workflow.actions.date", UUID=U_NOW,
                 WFDateActionMode="Current Date"))
    A.append(act("is.workflow.actions.format.date", UUID=U_HOUR,
                 WFDate=ts(out(U_NOW, "Date")),
                 WFDateFormatStyle="Custom", WFDateFormat="Custom",
                 WFDateFormatString="H"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Hour",
                 WFInput=attach(out(U_HOUR, "Formatted Date"))))
    A.append(act("is.workflow.actions.format.date", UUID=U_DAY,
                 WFDate=ts(out(U_NOW, "Date")),
                 WFDateFormatStyle="Custom", WFDateFormat="Custom",
                 WFDateFormatString="EEEE"))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Weekday",
                 WFInput=attach(out(U_DAY, "Formatted Date"))))
    A.append(comment(
        "Stop early when now is outside the window.\n"
        "- The block runs if any one of the four checks below is true\n"
        "- Hour is the current hour of the day, from 0 to 23\n"
        "- Weekday is the current day name\n"
        "- Nothing has been sent to Brightwheel before this point"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0, WFControlFlowMode=0,
                 WFConditions=conditions(0, [
                     cond_row(0, var("Hour"), "WFNumberValue", str(lo)),
                     cond_row(2, var("Hour"), "WFNumberValue", str(hi)),
                     cond_row(4, var("Weekday"), "WFConditionalActionString",
                              "Saturday"),
                     cond_row(4, var("Weekday"), "WFConditionalActionString",
                              "Sunday"),
                     # Fail closed. If the clock cannot be read, the three checks
                     # above would all quietly be false and the guard would let
                     # everything through while appearing to work.
                     cond_row(101, var("Hour")),
                 ])))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts(
                     f"Brightwheel — nobody {verb}"),
                 WFNotificationActionBody=ts(
                     f"This only runs on a weekday between {window_label}, so "
                     "nothing was sent. It is ", var("Weekday"), " at hour ",
                     var("Hour"), ". If the day and hour are blank above, the "
                     "Date action is misconfigured and needs to be set to "
                     "Current Date.")))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G0, WFControlFlowMode=2))

    # ---- session token ----
    A.append(comment(
        "--- VERIFY SESSION TOKEN ---\n"
        "Ask Brightwheel who the saved token belongs to. A valid token returns your "
        "account details; an expired one returns an error, which is what triggers "
        "the automatic sign-in below."
    ))
    A.append(act("is.workflow.actions.getstoredcontent", UUID=U_GT,
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_PROBE,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/users/me", WFHTTPMethod="GET",
                 WFHTTPHeaders=dict_field([
                     kv("Accept", ts("application/json")),
                     kv("X-Parse-Session-Token", ts(out(U_GT, "Stored Content"))),
                     kv("X-Client-Name", ts(CLIENT_NAME)),
                     kv("X-Client-Version", ts(CLIENT_VERSION)),
                 ])))
    A.append(act("is.workflow.actions.detect.dictionary", UUID=U_PDICT,
                 WFInput=ts(out(U_PROBE, "Contents of URL"))))
    A.append(act("is.workflow.actions.getvalueforkey", UUID=U_PERR,
                 WFDictionaryKey="error", WFGetDictionaryValueType="Value",
                 WFInput=ts(out(U_PDICT, "Dictionary"))))
    A.append(comment(
        "Sign in again only when the saved token has expired.\n"
        "- Condition checks whether the account lookup came back with an error\n"
        "- Get Contents of URL signs in with the email and password from Setup\n"
        "- Match Text pulls the new session token out of the reply\n"
        "- Session Token carries either the refreshed or the still-valid token"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=0, WFCondition=100,
                 WFInput=cond_input(out(U_PERR, "Dictionary Value"))))
    A.append(act("is.workflow.actions.downloadurl", UUID=U_LOGIN,
                 Advanced=True, ShowHeaders=False,
                 WFURL=f"{BASE}/sessions/", WFHTTPMethod="POST",
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
    A.append(act("is.workflow.actions.gettext", UUID=U_LTXT,
                 WFTextActionText=ts(out(U_LOGIN, "Contents of URL"))))
    A.append(act("is.workflow.actions.text.match", UUID=U_MATCH,
                 WFMatchTextPattern=r'"[sS]ession_?[tT]oken"\s*:\s*"([^"]+)"',
                 text=ts(out(U_LTXT, "Text"))))
    A.append(act("is.workflow.actions.text.match.getgroup", UUID=U_GRP,
                 WFGroupIndex="1", matches=attach(out(U_MATCH, "Matches"))))
    A.append(comment(
        "Save the refreshed token, or report why signing in did not work.\n"
        "- Condition checks whether a token was found in the sign-in reply\n"
        "- Store Content saves it on this device for next time\n"
        "- Notification repeats Brightwheel's own wording, so it is readable "
        "later even when the automation ran with the phone in a pocket"
    ))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G3, WFControlFlowMode=0, WFCondition=100,
                 WFInput=cond_input(out(U_GRP, "Matched Text Group"))))
    A.append(act("is.workflow.actions.setstoredcontent",
                 WFStoredContentKey="BrightwheelSessionToken",
                 WFStoredContentGlobalValue=False,
                 WFInput=ts(out(U_GRP, "Matched Text Group"))))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U_GRP, "Matched Text Group"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G3, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.notification",
                 WFNotificationActionTitle=ts("Brightwheel sign-in failed"),
                 WFNotificationActionBody=ts(
                     "Nobody was checked ", "in" if checking_in else "out",
                     ". Brightwheel said: ", out(U_LTXT, "Text"))))
    A.append(act("is.workflow.actions.exit"))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G3, WFControlFlowMode=2))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=1))
    A.append(act("is.workflow.actions.setvariable", WFVariableName="Session Token",
                 WFInput=attach(out(U_GT, "Stored Content"))))
    A.append(act("is.workflow.actions.conditional", UUID=next(i),
                 GroupingIdentifier=G2, WFControlFlowMode=2))

    # ---- per child: read state, skip if already there, otherwise send ----
    for cname, target in CHILDREN:
        p = kid[cname]
        state_var = f"{cname} State"
        A.append(comment(
            f"--- {title_word.upper()}: {cname.upper()} ---\n"
            f"Read {cname}'s most recent check-in event first, so that running "
            "this twice in one morning does not record a second arrival. "
            "Brightwheel reports 1 for checked in and 2 for checked out."
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
        A.append(act("is.workflow.actions.detect.dictionary", UUID=p["adict"],
                     WFInput=ts(out(p["act"], "Contents of URL"))))
        A.append(act("is.workflow.actions.getvalueforkey", UUID=p["state"],
                     WFDictionaryKey="activities.1.state",
                     WFGetDictionaryValueType="Value",
                     WFInput=ts(out(p["adict"], "Dictionary"))))
        A.append(act("is.workflow.actions.gettext", UUID=p["stext"],
                     WFTextActionText=ts(out(p["state"], "Dictionary Value"))))
        A.append(act("is.workflow.actions.setvariable", WFVariableName=state_var,
                     WFInput=attach(out(p["stext"], "Text"))))
        A.append(comment(
            f"Skip {cname} if nothing needs to change.\n"
            f"- Condition compares {cname}'s latest state with the one this "
            "shortcut would produce\n"
            f"- Otherwise branch sends the request and reports the result\n"
            "- If the state cannot be read, the request is sent anyway"
        ))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gskip"], WFControlFlowMode=0,
                     WFCondition=4, WFConditionalActionString=already,
                     WFInput=cond_input(var(state_var))))
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
        A.append(act("is.workflow.actions.detect.dictionary", UUID=p["rdict"],
                     WFInput=ts(out(p["resp"], "Contents of URL"))))
        A.append(act("is.workflow.actions.getvalueforkey", UUID=p["chk"],
                     WFDictionaryKey="checkins", WFGetDictionaryValueType="Value",
                     WFInput=ts(out(p["rdict"], "Dictionary"))))
        A.append(act("is.workflow.actions.gettext", UUID=p["rtext"],
                     WFTextActionText=ts(out(p["resp"], "Contents of URL"))))
        A.append(comment(
            f"Report the result for {cname}.\n"
            "- Condition checks whether Brightwheel echoed the check-in back\n"
            "- Otherwise branch shows Brightwheel's own error text"
        ))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gres"], WFControlFlowMode=0,
                     WFCondition=100,
                     WFInput=cond_input(out(p["chk"], "Dictionary Value"))))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts("Brightwheel"),
                     WFNotificationActionBody=ts(f"✅ {cname} {verb}")))
        A.append(act("is.workflow.actions.conditional", UUID=next(i),
                     GroupingIdentifier=p["gres"], WFControlFlowMode=1))
        A.append(act("is.workflow.actions.notification",
                     WFNotificationActionTitle=ts(f"⚠️ {cname} not {verb}"),
                     WFNotificationActionBody=ts(out(p["rtext"], "Text"))))
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
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    dest.mkdir(parents=True, exist_ok=True)
    for d in ("in", "out"):
        name, pl = build(d)
        (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
        print(f"{name}: {len(pl['WFWorkflowActions'])} actions, "
              f"{len(pl['WFWorkflowImportQuestions'])} setup questions")
    name, pl = build_scanner()
    (dest / f"{name}.xml").write_bytes(plistlib.dumps(pl, fmt=plistlib.FMT_XML))
    print(f"{name}: {len(pl['WFWorkflowActions'])} actions")

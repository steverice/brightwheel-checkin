# Brightwheel Check-In / Check-Out Shortcuts

Builds two signed iOS Shortcuts that check both children in or out of Brightwheel
without opening the app, designed to be fired unattended by location-and-time
automations:

- **Brightwheel Check In** — arrive at school in the morning
- **Brightwheel Check Out** — arrive at school in the afternoon

Plus a manual helper:

- **Brightwheel Scan Code** — recover the school secret from the check-in QR code

Requires iOS 27 (uses Store Content / Get Stored Content).

## Working in this repo

**Never edit a shortcut by hand.** `build_shortcuts.py` is the source of truth;
anything changed in the Shortcuts app or in `dist/` is overwritten by the next
build. The only values meant to be edited on the phone are the four Setup
answers.

The whole cycle is one command:

```bash
./build.sh           # normal build -> dist/, asks setup questions at import
./build.sh --debug   # debug build  -> dist-debug/, no setup questions
```

### Debug builds

Answering six setup questions on every test import gets old fast. Copy
`.env.example` to `.env`, fill it in, and `./build.sh --debug` bakes those values
straight into the shortcuts and emits **no import questions at all** — import and
run.

A debug build therefore contains a real password, the school-wide secret and your
check-in code. Both `.env` and `dist-debug/` are gitignored, and `--debug` writes
only to `dist-debug/`, never to `dist/`, so a debug artifact cannot be committed
by accident. Never AirDrop one to anyone else. Run `./build.sh` with no arguments
before committing.

which generates all three shortcuts, validates each against iOS 27, signs them,
and writes both the unsigned `.xml` and the signed `.shortcut` into `dist/`.
**Commit `dist/` alongside the generator change that produced it**, so the repo
always carries an installable build and the XML diff shows what actually
changed.

`build.sh` fails on any validator error except one deliberate waiver (below), so
a red build is a real problem.

To install a fresh build, AirDrop `dist/*.shortcut` to the phone. **Delete the
old shortcut first** — a same-name import is silently skipped, with no warning,
which looks exactly like a code change that did nothing.

### One waived validator rule

`validate-shortcut` reports `Second Comment missing required Shortcuts
Playground prompt text` for all three shortcuts. The attribution comment was
removed on purpose. That rule is a single string check on the second comment;
`build.sh` waives it by name and treats every other error as fatal.

### Build artifacts churn

Two things make committed artifacts noisier than they look:

- Every build calls `uuidgen` for each action, so a rebuild rewrites every UUID.
  The XMLs are otherwise byte-identical across runs — masking UUIDs makes two
  builds compare equal — but a no-op rebuild still shows ~64 changed lines in
  the smallest shortcut. Seeding UUIDs deterministically (e.g. `uuid5` over the
  shortcut name plus a slot key) would fix this if the noise becomes annoying.
- `shortcuts sign` is not deterministic: signing the same XML twice produces
  different bytes. The `.shortcut` blobs therefore always show as changed, and
  no amount of generator determinism avoids that.

So review the `.xml` diff, not the `.shortcut` diff.

## Setup values

Both shortcuts ask for six values **at import time**, so no credential is
stored in this repo or in the signed `.shortcut` file:

| Prompt | Field | Notes |
|---|---|---|
| Brightwheel account email | text | Used to sign in |
| Brightwheel account password | text | Same |
| Check-in code | text | 4-digit guardian code; authenticates as you |
| School QR secret | text | The `secret` value from the school's check-in QR |
| Earliest hour this may run | number | 0–23, inclusive |
| Hour this stops running | number | 0–23, **exclusive** |

**The session token is never asked for.** Nobody setting this up has one to hand.
The first run finds nothing stored, gets `E1200` from `/users/me`, signs in, and
saves the token to this shortcut's own on-device storage
(`WFStoredContentGlobalValue = false`, so not synced to iCloud). Later runs reuse
it until it expires, then sign in again.

### Sign-in is two steps and needs a 2FA code

Verified against a full login capture on 2026-08-19.

| Step | Request | Response |
|---|---|---|
| 1 | `POST /api/v1/sessions/start` with `{"user":{"email","password"}}` | `{"2fa_required": true, "2fa_code_sent_to": [...]}` |
| 2 | `POST /api/v1/sessions` with `{"user":{"email","password"}, "2fa_code":"123456"}` | `{"token": "…", "user": {…}, "csrf": "…"}` |

The token arrives as a **top-level `token`** key, 20 characters — not
`session_token`. Calling `/api/v1/sessions` on its own, without having called
`/start` and without a `2fa_code`, answers `403 "Please start over … we'll send a
new code"`, while a wrong password answers `401 E2053`.

Sign-in is a two-step flow and the token field is `token`. A shortcut cannot read a 6-digit code out
of email unattended, so **there is no auto-login**: the token is captured by hand
and used until it stops working, at which point requests return `E1200` and the
notification says to capture a fresh one and re-import.

Tokens appear long-lived; the one from the original capture was still valid hours
later. Note that first-attempt code delivery proved unreliable — the code only
arrived after using "resend", which is what `/sessions/start` being called twice
in the capture represents.

## Recovering the school secret

`Brightwheel Scan Code` exists for the case where the school enables Quick Scan
Refresh (which rotates the secret every ~3h) or a run fails with
`Problem scanning QR code`. It reads the decoded QR text, extracts `secret`,
copies it to the clipboard, and warns if `signatures_enabled` has flipped on.

**iOS cannot decode a QR code inside a shortcut.** There is no available action
for it:

- `is.workflow.actions.scanbarcode` decodes an image, but has no row in the
  iOS 27 ToolKit — it is macOS-only, and the validator rejects it for an iOS
  target.
- `com.apple.BarcodeScanner.BarcodeScannerIntent` is the iOS entry, but it is
  *Open Code Scanner*: a launcher whose only parameter is `target: launch`. It
  does not return the scanned text.

So the scan happens in Code Scanner (Control Center) and the helper parses what
you copied. The clipboard is prefilled as the default answer, so it is normally
one tap. The helper is deliberately interactive and makes no API calls — unlike
the two automation shortcuts, it is only ever run by hand at the sign-in tablet.

## Time guard

Each shortcut refuses to act outside a weekday window, and the window itself is
set at import rather than baked in. **Start is inclusive, end is exclusive**, so
the numbers typed match the times as spoken: `8` and `13` is "8:00 to 1:00", with
the last run at 12:59. Defaults are 8→13 for Check In and 13→18 for Check Out.

Outside the window nothing is sent and a notification says which bound was
missed. Everything happens before any network request.

This exists because **Siri invocation cannot be disabled**. Every shortcut in the
library can be started by saying its name, and there is no plist key or in-app
toggle to prevent it — the only surface controls in the file format are
`WFWorkflowTypes` and `WFQuickActionSurfaces`, neither of which covers Siri. The
idempotency check already makes a stray *repeat* harmless, but a stray *opposite*
direction would write real attendance, which is what the window prevents.

### Three things here were learned the hard way

Each of these imported cleanly, passed the validator, and was still wrong. Do not
"simplify" them back.

- **Format Date puts the pattern in `WFDateFormat`, not `WFDateFormatString`.**
  The working shape, taken from a real exported shortcut, is
  `WFDateFormatStyle="Custom"` plus `WFDateFormat="H"`, with no
  `WFDateFormatString` key at all. `BEST_PRACTICES.md` says to set
  `WFDateFormat="Custom"` and put the pattern in `WFDateFormatString`; doing that
  yields an **empty string**, which is worse than an error because it survives a
  "has any value" check. The validator only enforces its `WFDateFormatString`
  rules when `WFDateFormat == "Custom"`, so the correct shape passes cleanly.
- **The current time comes from a `{Type: CurrentDate}` magic token fed straight
  into Format Date, rather than a Date action.** `CurrentDate` is a documented
  variable type confirmed against 127 real shortcuts, whereas
  `WFDateActionMode="Current Date"` is an undocumented enum string with no
  ToolKit case list. Note the empty hour was caused by the format keys above, not
  by the Date action — both were changed at once during debugging, and the format
  keys were the actual fault.
- **The bounds are compared with Math plus plain Ifs, not one multi-condition
  If.** Numeric rows inside a `WFConditions` table import with an empty, red
  comparison value, while string rows in the same table render fine. There is no
  verified sample of a numeric row in that shape anywhere, and the template in
  `CONTROL_FLOW.md` appears to be wrong on this point.
- **The guard fails closed, and tests that the hour is a number.** A plain "has
  any value" check is not enough: an empty string passes it, then loses the
  start-hour comparison, so the run reports itself as *too early* and the real
  fault stays hidden. The check matches `^[0-9]+$` and blocks on no match, which
  catches empty, blank and non-numeric alike.

Because the bounds are runtime values, the hour test cannot be a regex built at
build time; it is `Hour − Start < 0` and `End − Hour < 1` via two Math actions,
each compared against a literal. Only the *upper* bound of an `is between`
condition may hold a variable, so that condition is unusable here.

### Responses are matched as text, not parsed as JSON

Nothing here uses **Detect Dictionary** or **Get Dictionary Value**. They were
used originally and never once produced a working branch — the `/users/me` probe
read its own 401 as "no error" and signed in never, which is what produced every
`token length 0` report. Presence is instead measured with **Match Text +
Count + a numeric If**, the primitive the weekday and hour checks already proved
out:

| Pattern | Means |
|---|---|
| `E1200` | token expired or absent |
| `"token"\s*:\s*"([^"]+)"` | sign-in succeeded (top-level `token`) |
| `"state"\s*:\s*"1"` / `"2"` | child is already checked in / out |
| `"checkins"\s*:` | the check-in was accepted |

The activities reply for a single event carries exactly one `state` field, so
that match is unambiguous.

### Gray input fields are normal

**Detect Dictionary** and **Get Dictionary Value** show a gray `Input` /
`Dictionary` chip rather than a colored variable token. That is Shortcuts
displaying an implicit connection to the previous action's output, not a broken
wire — every one of these actions is placed immediately after the action that
feeds it, so the implicit chain resolves to the intended source. The one thing to
watch: inserting an action between such a pair would silently redirect the input.

## Design constraints

Both shortcuts are triggered by **background** automations, which drives three
decisions:

1. **No interactive actions on the main path.** No Ask for Input, no Show Alert,
   no camera. A background automation cannot answer a prompt on a locked phone.
   That is why credentials are import-time setup questions rather than first-run
   prompts, and why failures report via notification.
2. **Idempotent.** Each run reads the child's most recent check-in event and
   skips anyone already in the target state, so a repeated trigger does not
   record a second arrival. If the state cannot be read, the request is sent
   anyway — failing open is safer than silently skipping a real arrival.
3. **Status codes are unavailable.** Shortcuts' Get Contents of URL does not
   expose the HTTP status code, so success is detected from the response body
   (`checkins` present) rather than `201`.

## API findings

The endpoints these shortcuts use.

### `checked_in` is the DESIRED state, not the current state

`checked_in` is the desired state, not the current one. `checked_in: true`
checks a child **in**; `checked_in: false` checks them **out**.

Evidence:

- `GET /students/{id}/activities` returns a `state` field per event:
  `1` = checked in, `2` = checked out.
- Weekday history shows ~09:00 drop-offs with `state: 1` and ~17:00 pickups with
  `state: 2` — in during the morning, out in the evening.
- The captured transactions: the one sending `checked_in: false` produced
  `state: 2` (out); the one sending `checked_in: true` produced `state: 1` (in).
- Confirmed in-app: both children read as checked **in** after the capture, which
  only holds under this reading.

### PerimeterX is not enforced

`X-PX-*` headers are not required. Plain requests carrying only
`X-Parse-Session-Token` reach the application layer on every route used,
including login.

### Endpoints

| Endpoint | Use |
|---|---|
| `GET /api/v1/users/me` | Clean auth probe. Valid token → 200 with the user object; invalid → 401 with a top-level `error` key |
| `POST /api/v1/sessions/` | Login. Body `{"user":{"email","password"}}` confirmed — bad credentials return `401 E2053`, not a 404 |
| `GET /api/v1/students/{id}/activities?page_size=1&action_type=ac_checkin` | Current state. `action_type` filters server-side |
| `POST /api/v1/checkins/` | The check-in itself |
| `GET /api/v1/guardians/{id}/students` | Roster. Confirms each child's `homeroom` matches the room id |

`GET /guardians/{id}/students_for_checkin` returns
`400 E2036 "Could not apply filter"` for every parameter combination tried, so
the activities endpoint is used for state instead.

The login response field carrying the token was never observed, since that would
require a real sign-in. The shortcut extracts it with the regex
`"[sS]ession_?[tT]oken"\s*:\s*"([^"]+)"`, which covers `session_token` and
`sessionToken`. If sign-in ever fails, the notification quotes Brightwheel's raw
reply so the real field name is visible.

### Error signatures

Used to tell failure modes apart from the response body:

| Condition | Body |
|---|---|
| Expired/invalid token | `{"error":"This resource requires authentication"}`, code `E1200` |
| Wrong check-in code | `{"checkin_code":"Incorrect checkin code"}`, code `E2004` |
| Bad/expired/missing school secret | `{"secret":"The given secret does not exist or is expired."}`, title `Problem scanning QR code` |
| Success | `{"checkins":[...]}` |

## What is deliberately not committed

Local working notes are gitignored.

`.env` and `dist-debug/` are excluded too — see Debug builds above.

`dist/` **is** committed. The built shortcuts carry no live credentials: the four
Setup values ship as placeholders (`you@example.com`, `PASTE PASSWORD AT
IMPORT`, `0000`, `PASTE SCHOOL SECRET AT IMPORT`) and are filled in at import
time on the device. Re-check this if the Setup defaults ever change.

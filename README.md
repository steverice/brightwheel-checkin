# Brightwheel Check-In / Check-Out Shortcuts

Signed iOS Shortcuts that check both children in or out of Brightwheel without
opening the app, fired unattended by arrival triggers:

- **Brightwheel Check In** — arriving at school in the morning
- **Brightwheel Check Out** — arriving at school in the afternoon
- **Brightwheel Scan Code** — manual helper; recovers the school secret from the
  check-in QR code

Requires iOS 27 (Store Content / Get Stored Content, and the iOS 27 trigger
model). `ARCHITECTURE.md` covers why the code looks the way it does, and what was
learned about building Shortcuts programmatically.

## Build

```bash
./build.sh           # -> dist/, asks Setup questions at import
./build.sh --debug   # -> dist-debug/, values baked in, asks nothing
```

Either command generates all three shortcuts, validates them against iOS 27,
signs them, and writes both the unsigned `.xml` and the signed `.shortcut` to the
target directory. It fails on any validator error except one named waiver, so a
red build is a real problem.

**Never edit a shortcut on the phone.** `build_shortcuts.py` is the source of
truth and the next build overwrites everything else. The only values meant to be
changed on the device are the Setup answers. **Commit `dist/` alongside the
generator change that produced it.**

### Debug builds

Answering four Setup questions on every test import gets old. Copy
`.env.example` to `.env`, fill it in, and `./build.sh --debug` bakes those values
straight in and emits no import questions.

A debug build therefore contains a real password, the school-wide secret, and
your check-in code. `.env` and `dist-debug/` are both gitignored and `--debug`
never writes to `dist/`, so a debug artifact cannot be committed by accident.
Don't AirDrop one to anyone else.

## Install

AirDrop `dist/*.shortcut` to the phone.

**Delete the old copy first.** A same-name import is silently skipped with no
warning, which looks exactly like a code change that did nothing.

Then answer four Setup questions:

| Prompt | Notes |
|---|---|
| Brightwheel account email | Used to sign in |
| Brightwheel account password | Same |
| Check-in code | 4-digit guardian code; authenticates as you |
| School QR secret | The `secret` from the school's check-in QR |

**No session token is asked for** — nobody setting this up has one. The first run
finds nothing stored, gets `E1200` from `/users/me`, signs in, and saves the
token to the shared stored-content namespace, so **one sign-in covers both
shortcuts**. Later runs reuse it until it expires.

**Sign-in is interactive**, so run one shortcut by hand once before relying on a
trigger — a background run cannot answer the code prompt.

### Triggers

In iOS 27 a shortcut carries its own triggers, so add an **Arrive** trigger with
a time range to each shortcut rather than creating a separate automation.

- **Attach triggers last.** Re-importing a rebuilt shortcut loses them, and they
  cannot be generated into the file.
- Set Location Services → Shortcuts to **Always**, or the geofence silently never
  fires.
- Choose **Run Immediately**, not "Run After Confirmation".

## Signing in

Sign-in is two steps and needs a 6-digit code:

| Step | Request | Response |
|---|---|---|
| 1 | `POST /api/v1/sessions/start` `{"user":{"email","password"}}` | `{"2fa_required": true, "2fa_code_sent_to": [...]}` |
| 2 | `POST /api/v1/sessions` `{"user":{…}, "2fa_code":"123456"}` | `{"token": "…", "user": {…}, "csrf": "…"}` |

The token is a **top-level `token`**, 20 characters. Calling `/sessions` without
`/start` and without a code answers `403 "Please start over"`; a wrong password
answers `401 E2053`.

**Code delivery is unreliable** — the first `/sessions/start` often sends
nothing, and only a resend arrives. The loop runs five passes and calls
`/sessions/start` each time, so the prompt doubles as a resend control: **leave
the box empty, or type `resend`**, and another code is sent. Only a strict
`^[0-9]{6}$` answer is exchanged, so a typo or padded paste also falls through to
a resend. Cancelling stops the shortcut.

## Recovering the school secret

Run **Brightwheel Scan Code** if the school enables Quick Scan Refresh (which
rotates the secret every few hours) or a run fails with `Problem scanning QR
code`. It opens Code Scanner, waits at an alert while you scan and choose Copy,
then extracts the `secret`, copies it to the clipboard, and warns if
`signatures_enabled` has flipped on — which would break check-ins, since the
payload sends no signature.

iOS has no action that decodes a QR code inside a shortcut, which is why the
scanning itself happens in Code Scanner.

## Brightwheel API

The endpoints these shortcuts use.

| Endpoint | Use |
|---|---|
| `GET /users/me` | Auth probe. Invalid token → 401 with `E1200` |
| `POST /sessions/start` | Sign-in step 1; sends the 2FA code |
| `POST /sessions` | Sign-in step 2; returns a top-level `token` |
| `GET /students/{id}/activities?page_size=1&action_type=ac_checkin` | Current state. `action_type` filters server-side |
| `POST /checkins/` | The check-in itself |
| `GET /guardians/{id}/students` | Roster; confirms each child's `homeroom` |

**`checked_in` is the desired state, not the current one.** `checked_in: true`
checks a child **in**; `false` checks them **out**. The activity feed's `state`
field reports the result: `1` = in, `2` = out.

**PerimeterX is not enforced.** Plain requests carrying only
`X-Parse-Session-Token` reach the application layer on every route used,
including sign-in.

`GET /guardians/{id}/students_for_checkin` returns `400 E2036` for every
parameter combination tried, so the activities endpoint is used for state.

### Error signatures

Failure modes are told apart from the response body, since Shortcuts cannot see
HTTP status codes:

| Condition | Body |
|---|---|
| Expired / absent token | `{"error":"This resource requires authentication"}`, `E1200` |
| Empty request body | `{"checkins":"cannot process empty checkins"}`, `E2001` |
| Wrong check-in code | `{"checkin_code":"Incorrect checkin code"}`, `E2004` |
| Bad or expired secret | `{"secret":"The given secret does not exist or is expired."}` |
| Success | `{"checkins":[{… "event_date": …}]}` |

Success is detected by **`event_date`**, not `"checkins"` — the empty-body error
contains `"checkins"` too, and testing for it once reported success while nothing
was posted.

`dist/` **is** committed. The built shortcuts carry no live credentials: every
Setup value ships as the placeholder `not set` and is filled in at import. Re-check
that if the Setup defaults ever change.

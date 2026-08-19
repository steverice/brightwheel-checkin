# Brightwheel Check-In / Check-Out Shortcuts

Builds two signed iOS Shortcuts that check both children in or out of Brightwheel
without opening the app, designed to be fired unattended by location-and-time
automations:

- **Brightwheel Check In** — arrive at school in the morning
- **Brightwheel Check Out** — arrive at school in the afternoon

Plus a manual helper:

- **Brightwheel Scan Code** — recover the school secret from the check-in QR code

```bash
python3 build_shortcuts.py out/
validate-shortcut "out/Brightwheel Check In.xml" --target-macos 27 --target-platform ios
sign-shortcut "out/Brightwheel Check In.xml" --name "Brightwheel Check In"
```

Requires iOS 27 (uses Store Content / Get Stored Content).

## Setup values

Both shortcuts ask for four values **at import time**, so no credential is
stored in this repo or in the signed `.shortcut` file:

| Prompt | Notes |
|---|---|
| Brightwheel account email | Used only to refresh an expired session token |
| Brightwheel account password | Same |
| Check-in code | 4-digit guardian code; authenticates as you |
| School QR secret | The `secret` value from the school's check-in QR |

The session token is never entered by hand. It is fetched on first run, saved to
this shortcut's own on-device storage (`WFStoredContentGlobalValue = false`, so
not synced to iCloud), and refreshed automatically when it expires.

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

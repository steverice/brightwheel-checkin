# Brightwheel Check-In / Check-Out Shortcuts

Signed iOS Shortcuts that check your children in or out of Brightwheel without
opening the app, fired unattended by location triggers.

> ## Requires iOS 27
>
> These do not work on iOS 26 or earlier. The shortcut stops at its first
> action, with "the shortcut could not be run because an action could not be
> found". Store Content and the live `Scan Code` action were both added in
> iOS 27, and only from iOS 27 does a shortcut carry its own triggers.
> `TESTING.md` has the version-by-version support matrix.

The three shortcuts:

- **Brightwheel Attendance** — does the work. Run it by hand and it asks which way.
- **Brightwheel Check In** — carries the Arrive trigger; calls Attendance with `in`
- **Brightwheel Check Out** — carries the Leave trigger; calls Attendance with `out`

The two small ones exist so that **which trigger fired decides the direction**.
Nothing works it out from the time of day, so editing a trigger's hours on the
device cannot make it send the wrong direction.

Run Attendance on its own — from the app, the Home Screen, or Siri — and it asks
whether to check in or out. Canceling the menu sends nothing. A third item,
"Forget saved sign-in and school code", clears what the shortcut has stored and
is the only way to do so.

`ARCHITECTURE.md` covers why it is built this way.

> **Setup questions are broken on current iOS 27 betas.** From `24A5408d`
> onwards, answering the import questions and tapping **Add Shortcut** does
> nothing — no install, no error. It is an iOS regression, not a problem with
> this build: it reproduces with a two-action shortcut, and the same file
> installs correctly on iOS 26.5 and on iOS 27 beta `24A5355p`. Until it is
> fixed, either tap **Skip Setup** and fill the three Text actions in yourself,
> or install a `--debug` build, which asks nothing.

## Build

Nothing to configure first — the shortcut asks Brightwheel who your children
are when it runs.

```bash
./build.sh           # -> dist/, asks Setup questions at import
./build.sh --debug   # -> dist-debug/, values baked in, asks nothing
```

Either command generates all three shortcuts, validates them against iOS 27,
signs them, and writes both the unsigned `.xml` and the signed `.shortcut` to the
target directory. It fails on any validator error except the waivers named in
`build.sh`, each of which is listed there with the reason it is waived, so a red
build is a real problem.

**Never edit a shortcut on the phone.** `build_shortcuts.py` is the source of
truth and the next build overwrites everything else. The only values meant to be
changed on the device are the Setup answers.

### Debug builds

Answering three Setup questions on every test import gets old. Copy
`.env.example` to `.env`, fill it in, and `./build.sh --debug` bakes those values
straight in and emits no import questions.

A debug build therefore contains a real password, the school-wide secret, and
your check-in code. `.env` and `dist-debug/` are both gitignored and `--debug`
never writes to `dist/`, so a debug artifact cannot be committed by accident.
Don't AirDrop one to anyone else.

## Testing

`./test.sh` runs all three shortcuts on an iOS 27 simulator against a mock
Brightwheel and asserts on the requests they send. It covers the skip path, a
genuine write in both directions, a stale school code, and recovering from an
expired token — including error paths that cannot be produced on demand against
the real API.

A test build is generated with fake credentials and its API base pointed at the
mock, so **no test can reach the real Brightwheel** or check a real child in.

`TESTING.md` covers what it needs, what it cannot cover — the QR scanning path
needs a real camera — and what had to be worked out to drive the simulator.

## Install

AirDrop `dist/*.shortcut` to the phone. **Order does not matter** — Run Shortcut
resolves its target by name when it runs, not when it is imported, so a wrapper
imported on its own works as soon as Brightwheel Attendance exists. All three
have to be there before a trigger fires.

**Delete the old copies first.** A same-name import is silently skipped with no
warning, which looks exactly like a code change that did nothing.

Only **Brightwheel Attendance** asks anything. Answer its three Setup questions:

| Prompt | Notes |
|---|---|
| Brightwheel account email | Used to sign in |
| Brightwheel account password | Same |
| Brightwheel check-in code | 4-digit guardian code; authenticates as you |

### Nothing about your family is in the build

The children, the room each is in, and the guardian recorded as checking them
in are all read from Brightwheel on every run — from `students_for_checkin`,
plus the `/users/me` call the shortcut already makes to check its token. A built
shortcut is therefore generic: the same file works for any family, and a new
sibling or a room move needs no rebuild.

It also means the run stops rather than guessing. If the roster comes back
empty, in a shape it cannot read, missing a child's room, or listing a child in
two rooms, nothing is sent and a notification says which. A partial check-in is
worse than none, because you would believe it worked.

**No session token is asked for** — nobody setting this up has one. The first run
finds nothing stored, gets `E1200` from `/users/me`, signs in, and saves the
token under Brightwheel Attendance, which is the only shortcut that signs in.
Later runs reuse it until it expires. **Deleting Attendance takes the token with
it**, so a re-import signs in again; the school's code is kept separately and
survives, because re-scanning it means being back at the school.

**Sign-in is interactive**, so run one shortcut by hand once before relying on a
trigger — a background run cannot answer the code prompt.

### Triggers

In iOS 27 a shortcut carries its own triggers, so add them to **Brightwheel Check
In** and **Brightwheel Check Out** — not to Brightwheel Attendance, which has no
direction of its own.

Any pair of triggers works, as long as one means "going in" and the other "going
out": arriving in the morning and leaving in the afternoon, or arriving twice
with different time ranges. The shortcut does not care which; it only knows which
wrapper called it.

**Each wrapper shows you how, once.** The first time you run Brightwheel Check
In or Check Out by hand, it displays a diagram pointing at the action it needs —
Arrive for one, Leave for the other — and then never shows it again.

- **Attach triggers last.** Re-importing a rebuilt shortcut loses them, and they
  cannot be generated into the file.
- Set Location Services → Shortcuts to **Always**, or the geofence silently never
  fires.
- **Confirm Before Run** already defaults to off, which is what lets a trigger
  run unattended — so there is nothing to change. iOS 27 has no "Run
  Immediately" button; it is a toggle on the trigger, and turning it on means a
  prompt you have to answer before anything is sent.

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
a resend. Canceling stops the shortcut.

## The school code

Nothing about the school is baked into the build: `secret` and `school_id` both
come from the scanned QR code, so these shortcuts would work at a different
school without a rebuild.


The school's check-in QR code carries the `secret` and `school_id` the request
needs. **It is not a Setup question.** The first run finds nothing stored, shows
an alert explaining what to point the camera at, scans the code, and remembers
it — so this is asked once, not every import. The whole scanned blob is stored
and re-parsed, so both values come from one source of truth.

If the scan says `signatures_enabled` is on, you get a warning: check-ins send no
signature and would start failing.

**A rotated code recovers within the same run.** If the school enables Quick Scan
Refresh, the stored code eventually goes stale and Brightwheel rejects it with
its own distinct error. That is detected, the stored code is forgotten, and the
run makes a second pass — which finds nothing stored, asks you to scan the new
code, and sends again. Anyone already checked in on the first pass is skipped, so
nobody is recorded twice.

## Brightwheel API

The endpoints these shortcuts use.

| Endpoint | Use |
|---|---|
| `GET /users/me` | Auth probe. Invalid token → 401 with `E1200` |
| `POST /sessions/start` | Sign-in step 1; sends the 2FA code |
| `POST /sessions` | Sign-in step 2; returns a top-level `token` |
| `GET /students/{id}/activities?page_size=1&action_type=ac_checkin` | Not used. The roster call reports each child's state, so this read was dropped |
| `POST /checkins/` | The check-in itself |
| `GET /guardians/{id}/students_for_checkin` | **The roster.** Each child's id, name, room, and whether they are checked in right now. Needs `school_id`, `secret` and `time_zone` |
| `GET /guardians/{id}/students` | A fuller roster. Not used — it carries addresses and medical notes this has no business seeing |

**`checked_in` is the desired state, not the current one.** `checked_in: true`
checks a child **in**; `false` checks them **out**. The activity feed's `state`
field reports the result: `1` = in, `2` = out.

**PerimeterX is not enforced.** Plain requests carrying only
`X-Parse-Session-Token` reach the application layer on every route used,
including sign-in.

`students_for_checkin` fails in three distinguishable ways, which is what makes
its failures recoverable rather than silent:

| condition | response |
|---|---|
| a required parameter missing | `400` `E2036` |
| unknown guardian id | `404` `E1204` |
| wrong or rotated `secret` | `403` `E2038`, body `{"secret":"The given secret does not exist or is expired"…}` |

That last body has **no trailing period** where the `/checkins/` version does.
The shortcut matches the prefix, so both work — tightening the pattern to the
whole sentence would break the rotated-code recovery on this endpoint.

`time_zone` is required but its value has never been seen to change the answer.
The shortcut sends the device's own zone rather than a constant, so the day
boundary lands at local midnight rather than in the middle of pickup.

### Error signatures

Failure modes are told apart from the response body, since Shortcuts cannot see
HTTP status codes:

| Condition | Body |
|---|---|
| Expired / absent token | `{"error":"This resource requires authentication"}`, `E1200` |
| Empty request body | `{"checkins":"cannot process empty checkins"}`, `E2001` |
| Wrong check-in code | `{"checkin_code":"Incorrect checkin code"}`, `E2004` |
| Stale school code | `{"secret":"The given secret does not exist or is expired."}` — triggers a re-scan |
| Success | `{"checkins":[{… "event_date": …}]}` |

Success is detected by **`event_date`**, not `"checkins"` — the empty-body error
contains `"checkins"` too, so matching on it reports success while nothing was
posted.

## Disclaimer

**Use at your own risk.** This is a personal project. It is not affiliated with,
endorsed by, or supported by Brightwheel.

It works by calling Brightwheel's private API — the one their own app uses,
which is undocumented and can change or stop working without notice. No
representation is made that using it is permitted under Brightwheel's terms of
service, your school's policies, or any agreement you have with either. Check
for yourself before you run it.

Attendance records are the school's record of where your child is. You are
responsible for anything these shortcuts record on your behalf, including
anything they record wrongly.

## License

MIT — see [LICENSE](LICENSE).

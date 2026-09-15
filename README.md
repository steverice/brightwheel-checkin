# Brightwheel Check-In / Check-Out Shortcuts

Signed iOS Shortcuts that check your children in or out of Brightwheel without
opening the app, fired unattended by location triggers.

## Just want it on your phone?
**[Start
here](https://steverice.github.io/brightwheel-checkin/)**.

The short version:

1. **Check your iPhone is on iOS 27.** Settings → General → About → Software
   Version. On iOS 26 or earlier these do not run at all; see the note below.
2. **On your iPhone, tap the three download buttons on [the
   page](https://steverice.github.io/brightwheel-checkin/).** Each one opens
   straight in Shortcuts.
3. **Answer the three questions** Brightwheel Attendance asks — your Brightwheel
   email, your password, and your own 4-digit check-in code — and finish with
   **Skip Setup**, not Add Shortcut. See the warning below for why.
4. **Run Brightwheel Check In by hand once, at the school**, so it can sign in
   and scan the school's code. Then add an Arrive trigger to Check In and to
   Check Out.

Prefer files? The [latest
release](https://github.com/steverice/brightwheel-checkin/releases/latest) has
all three in a zip. Unzip it and open each file, and **keep the file names as
they are**. A shortcut is named after the file it came from, and the three find
each other by name.

You need a Brightwheel account of your own; the download contains nothing about
anybody's children and asks Brightwheel who yours are every time it runs.

> ### Requires iOS 27
>
> These do not work on iOS 26 or earlier. The shortcut stops at its first
> action, with "the shortcut could not be run because an action could not be
> found". Store Content and the live `Scan Code` action were both added in
> iOS 27, and only from iOS 27 does a shortcut carry its own triggers.
> `TESTING.md` has the version-by-version support matrix.

## How it is put together

The three shortcuts:

- **Brightwheel Attendance** — does the work. Run it by hand and it asks which way.
- **Brightwheel Check In** — carries a morning Arrive trigger; calls Attendance with `in`
- **Brightwheel Check Out** — carries an afternoon Arrive trigger; calls Attendance with `out`

The two small ones exist so that **which trigger fired decides the direction**.
Nothing works it out from the time of day, so editing a trigger's hours on the
device cannot make it send the wrong direction.

Run Attendance on its own — from the app, the Home Screen, or Siri — and it asks
whether to check in or out. Canceling the menu sends nothing. Two more items sit
below those:

- **Show the school's code** draws the stored school code back as the QR it was
  scanned from. What is stored is the scanned payload verbatim, so the image is
  the one taped up by the door: a second phone can be set up from it without a
  trip to the school, and it is a way back into the Brightwheel app when the
  shortcut is the thing misbehaving. With nothing stored yet it offers to scan
  the code rather than reporting that there is none, so it is also how a second
  phone is primed at the school: that scan sends nothing to Brightwheel, needs
  no sign-in, and checks nobody in. The alert it opens with can be canceled,
  which is the answer anywhere the code is not on the wall in front of you.
  Verified by decoding the rendered image and comparing it to the stored
  string — see `TESTING.md`.
- **Forget saved sign-in and school code** clears what the shortcut has stored,
  and is the only way to do so.

`ARCHITECTURE.md` covers why it is built this way.

> **On iOS 27, finish the setup questions with Skip Setup, not Add Shortcut.**
> From `24A5408d` through the 27.0 release candidate `24A434`, answering an
> import question and tapping **Add Shortcut** does nothing at all — no install,
> no error, nothing logged. Tapping **Skip Setup** on that same page installs
> the shortcut *and* keeps every answer you typed, which its name gives you no
> reason to expect. The last question says so on screen.
>
> It is an iOS regression, not a problem with this build: it reproduces with a
> two-action shortcut carrying one question, and the same file installs
> correctly on iOS 26.5 and on iOS 27 beta `24A5355p`. Verified on the release
> candidate by reading all three answers back out of the installed shortcut —
> see the support matrix in `TESTING.md`. A `--debug` build, which asks nothing,
> sidesteps the whole flow.

## Build

Nothing to configure first — the shortcut asks Brightwheel who your children
are when it runs.

```bash
./build.sh           # -> dist/, asks Setup questions at import
./build.sh --debug   # -> dist-debug/, values baked in, asks nothing
```

Either command generates all three shortcuts, validates them against iOS 27,
signs them, and writes both the unsigned `.xml` and the signed `.shortcut` to the
target directory. It fails on any structural check, and on any validator error
except the waivers named in `build_shortcuts.py`, each of which is listed there
with the reason it is waived, so a red build is a real problem.

The plist primitives, the checks, the validate-and-sign pipeline, and the
simulator harness come from [shortcut-forge](../shortcut-forge), which
`pyproject.toml` takes as an editable path dependency from the sibling checkout.
`uv sync --dev` sets that up; every script here runs Python through `uv run`.
Validating and signing still need the shortcuts-playground plugin's
`validate-shortcut` and `sign-shortcut` on `PATH`.

**Never edit a shortcut on the phone.** `build_shortcuts.py` is the source of
truth and the next build overwrites everything else. The only values meant to be
changed on the device are the Setup answers.

### Refreshing the site's iCloud links

`docs/index.html` links each shortcut by iCloud link, and an iCloud link is a
snapshot taken at the moment you share — it never follows a rebuild. There is no
API for minting one: the `shortcuts` CLI has `run`, `list`, `view` and `sign` and
nothing else, and the AppleScript dictionary exposes only `run`. The action
exists only inside Shortcuts itself.

So `tools/build_publisher.py` generates **Brightwheel Share Links**, a shortcut
that finds each of the three in your library, calls
`com.apple.shortcuts.CreateShortcutiCloudLinkAction` on it, and puts the result
on the clipboard as the three `<li>` lines the page wants.

```bash
uv run python tools/build_publisher.py     # -> dist-tools/, validated and signed
uv run pytest tests/test_publisher.py
```

Import it once on iOS 27 or macOS 27. There is nothing to pick: it finds each of
the three by name every time it runs, so after a release it simply finds the new
copies. A picker couldn't do that. It stores a workflow identifier, which every
re-import replaces, and it can't be pre-seeded by name (shortcut-forge's
`docs/simulator-harness.md` has the measurements).

> ### Mint links from a library that holds none of your own copies
>
> Use a Mac with **Shortcuts' iCloud sync turned off**, holding fresh imports of
> `dist/` and nothing you have set up. Never the phone you check in with.
>
> The publisher links whatever it finds under each name, and it cannot tell a
> clean build from the copy you use every day — the one carrying your Brightwheel
> password, baked in if it is a debug build and stored as your answers if it is
> not. In a synced library that is the copy it would find, and every check would
> pass. The confirmation tap would be the only thing between your password and
> the internet, and a link cannot be revoked afterward (shortcut-forge's
> `docs/simulator-harness.md`).
>
> Sync off also keeps a release import from landing on your phone beside your own
> copies, where the wrappers find Brightwheel Attendance by name. If sync is ever
> turned back on, delete the Mac's Brightwheel shortcuts first.

**Delete the old copies before importing the new ones, and on a Mac don't choose
Replace.** A plain second import installs a numbered copy (`Brightwheel
Attendance 1`). Replace hides the old copy from the app without removing it.
Either way the publisher stops with a notification rather than guessing which
copy is the new build. It also refuses a copy whose only name is numbered, since
parents would install it under that name and the wrappers call Brightwheel
Attendance by its exact name. And it stops if a shortcut is missing. Every check
runs before the first link is minted, because each link is public the moment it
exists. If it says there's more than one copy but you see only one, delete that
one: the hidden copy reappears, and you delete it too.

`tests/test_publisher.py` pins that structure down. Run it with the file named.
A bare `pytest tests/` also collects `test_attendance.py`, whose custom `s`
argument reads to pytest as a missing fixture.

`release.sh` asks for them as its last step, and **verifies them before writing
them**:

```bash
uv run python tools/verify_links.py --clipboard --erase   # imports each on a simulator
uv run python tools/update_links.py --clipboard           # then writes them into the page
```

Verification exists because a link can arrive without its setup questions. It
happened once here and nobody has explained it — see shortcut-forge's
`docs/simulator-harness.md`. The failure
is silent: the shortcut installs in one tap, looks correct, and leaves `not set`
in the email, password and check-in code actions, so the first sign of trouble is
somebody whose check-in never works. `verify_links.py` imports each link on an
iOS 27 simulator and compares what lands against `dist/<name>.xml` — same action
identifiers in the same order, same number of import questions — and refuses the
lot if any of them disagrees.

It writes nothing unless all three are present and start with
`https://www.icloud.com/shortcuts/`, so a half-finished paste cannot leave the
page pointing two ways at once. Links are matched by the name next to them
rather than by position, so a reordered paste fails instead of silently swapping
two shortcuts.

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

The release tools have a plain pytest file that needs no simulator, and
`pyproject.toml` keeps pytest away from `test_attendance.py`:

```bash
uv run pytest
```

`TESTING.md` covers what it needs, what it cannot cover — the QR scanning path
needs a real camera — and what had to be worked out to drive the simulator.

## Install

AirDrop `dist/*.shortcut` to the phone, without renaming them: an imported
shortcut is named after its file. **Order does not matter** — Run Shortcut
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

iOS asks them **one at a time**. The button reads **Next** on the first two
pages and **Add Shortcut** on the last — and on iOS 27 that last button does
nothing, so finish with **Skip Setup**. See the warning at the top.

**Watch the first letter of the password.** The answer field autocapitalizes,
so a password typed as `hunter2` is stored as `Hunter2` and the sign-in fails
with credentials that look right. Nothing the build can switch off — import
questions expose no autocapitalization setting — so the password question warns
about it on screen instead. The email is likelier to survive
it — most services fold email case, though that is not something this project
has tested against Brightwheel — but fix both. The check-in code is digits and
cannot be affected.

### Nothing about your family is in the build

The children, the room each is in, and the guardian recorded as checking them
in are all read from Brightwheel on every run — from `students_for_checkin`,
plus the `/users/me` call the shortcut already makes to check its token. A built
shortcut is therefore generic: the same file works for any family, and a new
sibling or a room move needs no rebuild.

It also means the run stops rather than guessing. If it cannot tell which
Brightwheel account this is, or the roster comes back empty, in a shape it
cannot read, missing a child's room, or listing a child in two rooms, nothing is
sent and a notification says which. A partial check-in is
worse than none, because you would believe it worked.

**No session token is asked for** — nobody setting this up has one. The first run
finds nothing stored, gets `E1200` from `/users/me`, signs in, and saves the
token under Brightwheel Attendance, which is the only shortcut that signs in.
Later runs reuse it until it expires. **Deleting Attendance takes the token with
it**, so a re-import signs in again; the school's code is kept separately and
survives, because re-scanning it means being back at the school.

**Sign-in is interactive**, so run one shortcut by hand once before relying on a
trigger — a background run cannot answer the code prompt. It does still send
the code, and the shortcut remembers that for ten minutes, so the run by hand
that follows goes straight to the prompt instead of sending another.

### Triggers

In iOS 27 a shortcut carries its own triggers, so add them to **Brightwheel Check
In** and **Brightwheel Check Out** — not to Brightwheel Attendance, which has no
direction of its own.

**Use Arrive for both**, on two different time ranges — arriving at the school in
the morning and arriving again in the afternoon. Checking out on the way in is
what the school asks for: children should be checked out as parents walk in, and
an arrival fires while you are still parking, so they are already checked out by
the time you reach the room. A Leave trigger only sends once you are driving away
with them.

Any pair still works, as long as one means "going in" and the other "going out" —
leaving in the afternoon is a supported alternative, and the shortcut cannot tell
the difference. It only knows which wrapper called it.

**Each wrapper shows you how, once.** The first time you run Brightwheel Check
In or Check Out by hand, it displays a diagram pointing at the action it needs —
the same Arrive row for both, differing in the time range to set — and then never
shows it again.

- **Attach triggers last.** Re-importing a rebuilt shortcut loses them, and they
  cannot be generated into the file.
- **Do not let the two ranges overlap.** With both wrappers watching the same
  arrival, the hours are the only thing telling them apart. A bad range fires a
  run at the wrong time — never in the wrong direction, which stays structural.
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
the box empty and tap Done**, and another code is sent.

**Canceling is how you go and read the email.** The prompt is a sheet over
whatever you were doing, and Mail is awkward to reach underneath it. So a run
that sends a code also stores when, and the next run within ten minutes asks
for the code without sending another: cancel, open the email, come back, run
again, type the code. A run that has just sent says which address to check,
taken from the start response; a run that is reusing a recent send shows the
shorter prompt. Once a code is accepted the send time is forgotten, and
"Forget saved sign-in and school code" clears it too. The window is a guess at
how long Brightwheel's codes stay valid; a code that has expired is simply
rejected, and the next pass sends a fresh one.

**The prompt is a number pad.** It keeps the sheet short, but a numeric field
drops a leading zero as the next digit is typed, so `012345` shows as `12345`.
The shortcut left-pads the answer to six digits before exchanging it, which is
why the prompt says leading zeros are optional. Only an answer that pads to
exactly six digits is exchanged; seven or more, like a typo, fall through to a
resend.

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

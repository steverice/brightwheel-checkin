# Architecture

## Overview

This repo does not contain shortcuts. It contains a **generator** that emits
them. `build_shortcuts.py` builds the Shortcuts plist for each shortcut as a
Python data structure and hands the three to
[shortcut-forge](../shortcut-forge), which checks, validates, signs, and stages
them; `build.sh` is the one-line entry point. The `.shortcut` files in `dist/`
are gitignored build output.

That indirection exists because a Shortcuts plist is a poor thing to edit by
hand. Variable references are UUID-keyed, string parameters carry
`attachmentsByRange` offsets that must line up with U+FFFC placeholders
character-for-character, and control flow is expressed as flat runs of actions
sharing a `GroupingIdentifier`. Generating it means offsets are computed rather
than counted, the two wrappers cannot drift apart, and a change is a diff in one
Python file instead of an untracked edit on a phone.

The cost is that **the phone is downstream**. Anything edited in the Shortcuts
app is overwritten by the next build, and the only values meant to be changed on
the device are the import-time Setup answers. When a device-side edit is worth
keeping, it gets folded back into the generator — see *Ground truth beats
documentation* in shortcut-forge's `docs/building-shortcuts.md` for how to
extract one.

## Directory structure

```
build_shortcuts.py   Generator. Builds all three shortcuts as plist dicts, then
                     checks, validates, and signs them through shortcut-forge.
build.sh             Entry point: picks the output directory, runs the generator.
pyproject.toml       The uv project: shortcut-forge as an editable sibling checkout.
dist/                Gitignored build output: unsigned .xml + signed .shortcut.
assets/              The first-run setup diagrams, and the screenshots behind them.
make_diagrams.py     Redraws those diagrams. Needed only to regenerate them.
dist-debug/          Gitignored. Same shortcuts with credentials baked in.
dist-test/           Gitignored. Test builds, pointed at the mock API.
test.sh              Pipeline: build against a mock -> install -> run -> assert.
tests/               The integration suite and the mock Brightwheel. The
                     simulator harness itself is shortcut-forge's.
.env.example         Template for the debug build's credential file.
README.md            What this is and how to set it up.
ARCHITECTURE.md      This file: why it looks the way it does.
TESTING.md           How the simulator suite works, and what it cannot cover.
```

Inside `build_shortcuts.py`:

| Symbol | Role |
|---|---|
| `ts()` / `attach()` / `var()` / `out()` | Serialization helpers, from `shortcut_forge_lib.plist`. `ts()` computes `attachmentsByRange` offsets so placeholders always line up. |
| `dict_field()` / `kv()` / `kv_dict()` | `WFDictionaryFieldValue` builders for HTTP headers and JSON bodies, also from the library. |
| `act()` / `comment()` | Bare action constructors. |
| `A.count_matches()` | Text → Match Text → Count, on an `ActionList`. The presence primitive; see below. Takes an attachment value: `out()`, `var()`, or `EXTENSION_INPUT`. |
| `WAIVED` | The validator rules this build waives, each with its reason. |
| `SETUP` / `ENV_KEYS` | The import-time values, and their `.env` names for debug builds. |
| `guard()` / `diff_guard()` | Emit one roster check: notify, clear `Roster OK`, and let the run fall through without sending. `guard()` also serves the per-child pass. |
| `build()` | `Brightwheel Attendance` — everything except the direction. |
| `build_wrapper()` | The two trigger carriers, parameterized by direction. Each also carries its own first-run setup guide. |
| `--api-base` / `--env-file` | Overrides used only by the integration tests, so a test build cannot reach the real API. See `TESTING.md`. |

## Data flow

**Build:** `build.sh` runs the generator into a target directory. The generator
hands its three documents to shortcut-forge's `build_all()`, which runs the
structural checks, writes each XML, runs `validate-shortcut` against iOS 27, and
signs with `sign-shortcut --mode anyone` into the same directory. Any check
failure, or any validator error except the waivers named in
`build_shortcuts.py`, aborts the build before anything is signed.

**Runtime**, for `Brightwheel Attendance`:

```
Shortcut Input is "in" or "out"?
  ├─ yes ──> Direction                  (a wrapper handed it in)
  └─ no  ──> Choose from Menu           (run by hand)
Direction  ->  Wanted In, Checked In Value, Verb, Already Word

Get Stored Content (shared)  ->  Session Token
GET /users/me
  └─ body contains E1200 ──> sign-in loop, up to 5 passes:
         POST /sessions/start          (skipped on the first pass if a code went out within
                                        ten minutes; stores the send time; later passes resend)
         Ask for the 6-digit code      (a six-digit clipboard is prefilled in a text field; else a
                                        number pad; left-padded to six digits; empty falls through)
         POST /sessions with 2fa_code  -> token -> Store Content

Repeat 2, but only while Send Needed:
    Get Stored Content: school code
      └─ nothing stored ──> alert, Scan Code, store it
    Match Text  ->  School Secret, School Id
    Repeat for each child name:
        Repeat Item 2 -> Child Match -> student.object_id, student.first_name,
                                        room_states.1.room.object_id
          └─ already the way this run wants ──> notify "no change", send nothing
          └─ otherwise ──> POST /checkins/
                 ├─ event_date        ──> notify success
                 ├─ stale school code ──> forget it, ask for another pass
                 └─ anything else     ──> notify the API's own error
```

## Key design decisions

**Nothing branches on a dictionary value.** Fed a Get Contents of URL output,
Detect Dictionary and Get Dictionary Value never once produced a working
condition — the `/users/me` probe read its own 401 as "no error" and so never
signed in. Presence is measured instead with `count_matches()`: Text → Match
Text → Count → numeric If. Counting rather than testing also sidesteps empty strings
satisfying "has any value".

| Pattern | Means |
|---|---|
| `E1200` | token expired or absent |
| `"token"\s*:\s*"([^"]+)"` | sign-in succeeded |
| `"checked_in"\s*:\s*true` | child is checked in right now |
| `"event_date"` | a check-in record was really created |
| `"secret"\s*:\s*"The given secret` | the stored school code has gone stale |
| `"secret"\s*:\s*"([^"]+)"` / `"school_id"…` | pulling those out of the scanned code |

**Reading a value is a different thing, and it works.** The built shortcut
holds nine `Get Dictionary Value` actions and one `Dictionary`: the roster is
parsed with them, and so is the in/out wording. Every value read that way is
used as *text* — pasted into a request body, into a notification, or into a
`count_matches()` pattern. The moment one has to decide a branch it goes back
through `count_matches()`.

That line is where it sits, and finding it was expensive. Branching on a
dictionary value broke four separate things: the auth probe read its own 401 as
"no error"; the success test reported a check-in that never happened; the school
code yielded an empty `school_id` and `E1205 "You must specify the school"`; and
the roster lookup yielded an empty target and `E1204 "The requested resource
could not be found"`.

Each time it was removed from the place that had just failed and left where it
seemed harmless, which is why the rule has to name the operation rather than the
action. The failure mode is what makes it dangerous: it does not error, it
returns empty, and the empty value travels until something far away complains
about the wrong thing.

**The school code is scanned, not typed.** `scanbarcode` returns decoded text
in-process, so the QR code became something the check shortcuts read directly
rather than a separate helper shortcut feeding the clipboard. The whole scanned
blob is stored under one key and re-parsed on later runs, so `secret` and
`school_id` share a single source and one code path extracts both. Scanning is
interactive, so it sits behind an "is anything stored" check — the same shape as
the sign-in prompt, and for the same reason. The menu's "Show the school's code"
sits behind the same check and scans when it finds nothing to draw, so a phone
can be primed at the school without a check-in. Both sites write the one key,
so a code scanned from either is the code every later run reads.

A rotated code self-heals **within the run**. Brightwheel rejects a stale secret
with a distinct error, matched against the response text; the stored code is
deleted and a second pass requested. The next pass finds nothing stored and
scans, so **"first run" and "the code rotated" are the same branch** — deleting
the stored code is what turns one into the other. The pattern
(`"secret"\s*:\s*"The given secret`) was checked against live responses for a
stale secret, a wrong check-in code, an empty body and an expired token, and
matches only the first.

**Idempotency.** Each pass reads the `checked_in` flag on the room the child is
in — it arrives with the roster, so it cannot disagree with it — and skips
anyone already in the state this run wants, so a repeated trigger cannot record
a second arrival. A state that cannot be read at all no longer sends anyway: a
room entry missing the key trips a guard and stops the whole run, because the
same reply would have to be trusted for every other child too.

Comparing "already checked in?" against "want them checked in?" needs two
*runtime* numbers compared, and an If tests a variable against a literal, never
against another variable. So the two are pasted into one string: `11` or `00`
when they agree, `01` or `10` when they differ, matched with the fixed pattern
`^(01|10)$`.

That check is also what makes the retry safe. A second pass re-runs every child,
and anyone who already succeeded is skipped — so the retry needs no memory of who
was done.

**Children are a Repeat, not an unrolled pair.** They were unrolled originally,
which was simpler while there was nothing to retry. Looping means the retry
re-enters the *same* actions rather than a second copy: the built shortcut
contains exactly one `POST /checkins/`. It was also smaller at the time — 138 actions
unrolled against 125 looped, retry included.

The loop item is **`Repeat Item 2`**, not `Repeat Item`, because this loop sits
inside the retry Repeat — and a *count*-style outer loop shifts the numbering
exactly as a nested Repeat with Each does. `BEST_PRACTICES.md` documents the
nested-each case only; the count case was confirmed on device with a probe. With
the unnumbered name the item came back empty, the roster lookup found nothing,
and notifications showed a blank child name.

It is captured immediately in each of the two loops — into `Child Match` in the
sending pass, read straight in the guard pass — so the numbered variable appears
exactly twice. Change the nesting and those are the only lines to revisit.

Each loop item is a whole child record, so the id, the name and the room all
come from **`Get Dictionary Value` by key path** off it: `student.object_id`,
`student.first_name`, `room_states.1.room.object_id`. An earlier attempt at a
Dictionary read returned nothing and answered every check-in with `E1204 "The
requested resource could not be found"` — that was a Dictionary built from
matched text, not a parsed URL response, which is the distinction that makes the
current one work.

Carrying name and id together in one list item would have been tidier, but
`Get Item from List` and `Split Text` both appear in the golden shortcuts with
*empty* parameters — no worked example of how to index or separate anything — so
neither has a verified shape to copy.

**No interactive actions on the happy path.** These run from background arrival
triggers, which cannot answer a prompt on a locked phone. Credentials are
import-time Setup questions rather than first-run prompts, and every outcome is a
notification.

Three prompts exist, and each is reachable only when the run would otherwise fail
or was started by hand: the 2FA code (the token has already expired), the QR scan
(no school code is stored — whether a check attempt found none or the menu's
"Show the school's code" did), and the direction menu (no input, so a person is
driving). An earlier blanket ban on interactive actions was too broad — the rule
belongs on the happy path, not the recovery paths.

**No time logic.** iOS 27 attaches triggers to the shortcut itself, and the
trigger carries its own time range, so a weekday-and-hour guard inside the
shortcut was redundant and was removed. The trade-off: that guard also covered
Siri, which cannot be disabled and which a trigger's time range does not
constrain. A stray *repeat* is still harmless thanks to idempotency, but a stray
run of the *opposite* direction now writes real attendance.

**The token is scoped, the school code is shared.** `Brightwheel Attendance` is
the only shortcut that signs in, so the token has nothing to be shared with:
`BrightwheelSessionToken` uses `WFStoredContentGlobalValue = false` and is
cleared when the shortcut is deleted. `BrightwheelSchoolCode` stays global on
purpose, because re-establishing it means being back in front of the school's QR
code, while re-establishing the token only means typing a password.

The global flag was set on both when Check In and Check Out were each full
shortcuts and whichever signed in first covered the other. That reason went away
when the logic collapsed into one shortcut; the flag outlived it.

**Direction is structural, carried by two thin wrappers.** `Brightwheel Attendance`
holds all the logic and takes its direction from Shortcut Input; `Brightwheel
Check In` and `Brightwheel Check Out` do two things — say which way, and call
Attendance — and are what the triggers attach to. They are 17 actions rather
than 2 only because each also carries the first-run setup guide described
below; the working part is still a Text action and a Run Shortcut.

The alternatives were worse. A location trigger reports **no output**
(`outputTypeIdentifiers: ["none"]`, against a catalog that records real outputs
for 13 other triggers), so a single shortcut cannot tell which trigger woke it.
Inferring from the clock would put the deciding threshold in the actions while
the trigger's time range lives on the device, editable — two sources of truth for
one fact, and the failure is a silent wrong-direction write. Toggling from
current state needs no clock but destroys idempotency: a second arrival would
check the children out.

Run Shortcut resolves its target **by name**. The generated `workflowIdentifier`
is freshly minted and matches nothing on any device; a probe pair confirmed on
device that an imported copy still finds its target and passes input. So the
wrappers need no on-device picking, unlike a trigger's placemark.

Resolution happens when the shortcut **runs**, not when it is imported, so the
three can be installed in any order — a wrapper imported first is not broken, it
just has nothing to call until `Brightwheel Attendance` arrives.

Run on its own, `Brightwheel Attendance` has no input and asks with a Choose from
Menu instead, whose two options set the same `in` / `out` a wrapper would. So the
same shortcut serves the automations and manual use, with one direction path
rather than two.

That keeps it Siri-safe without refusing to run. Saying its name cannot check
anyone anywhere by itself — it can only open a menu, and canceling sends
nothing. Neither item below the two directions sends anything either: "Show the
school's code" draws the stored code, and scans one when there is none to draw,
but it talks only to the camera; "Forget saved sign-in and school code" is the
only way to clear the stored school code from the device. A wrapper never
reaches the menu, because it hands in a direction.

**Debug builds are isolated by construction.** `./build.sh --debug` bakes `.env`
values in and emits no import questions. Such a build contains a real password,
so `--debug` writes only to `dist-debug/`, and both that directory and `.env` are
gitignored. The isolation is structural rather than a reminder.

**`dist/` is gitignored**, and not for privacy — a build carries no children.
Every build mints fresh UUIDs and `shortcuts sign` is not deterministic, so all
six files change completely on a no-op rebuild and nothing ever deltas. The
built shortcuts ship as release assets instead.

---

# Building Shortcuts programmatically

The platform findings that used to live here — where the bundled docs are
wrong, actions with no verified example, loops, menus, everything measured on
a simulator, the silent failures to design against, iOS 27 automations, icons,
showing an image, and the practical notes — moved to
[shortcut-forge](../shortcut-forge/docs/building-shortcuts.md) along with the
code that embodies them. This file keeps what is about these shortcuts.

## The roster is read, not built in

`Brightwheel Attendance` contains no children. It asks
`GET /guardians/{id}/students_for_checkin`, which returns every child's id,
name, room and current state in one call — replacing both a baked-in roster and
a per-child activities read. The guardian id comes from the `/users/me` probe
that already runs, read by key name off it; the time zone comes from the device.

**Read as a dictionary, not matched as text.** `Get Contents of URL` parses
JSON, so the text a pattern would see is a re-serialization in Shortcuts' own
key order — see the silent failures below. `Get Dictionary Value` addresses keys
by name, which survives that, so the loop walks the `students` list and reads
`student.object_id`, `student.first_name` and `room_states.1.room.object_id`
off each item. The one exception is the current state: a boolean does not
coerce to text, so `room_states.1` is read instead and the single
`"checked_in":true` pair matched out of it, which no key order can disturb.

### Guards, all before anything is sent

One of these counts key names in the body text, which stays safe because a
single key-value pair does not depend on order, and compares the result against
the length of the `students` list. Another counts that list itself. The last two
count one child's own rooms.

| guard | fires when |
|---|---|
| any children at all | the list is empty — an error body looks like this |
| every room entry carries a state | `"checked_in"` count is short: a room lost the key |
| this child has a room | their own `room_states` is empty or absent |
| this child is in one room | their own `room_states` holds more than one |

One more sits above all of these, back where the guardian id is read: an empty
id builds a `/guardians//…` URL that answers 404 with no students in the body,
which arrives here indistinguishable from a school with nobody enrolled. Every
guard in this table would have blamed the API for a bad read, and for one
morning the first of them did. It stops the run where the id is read instead.

The last two run in **their own pass** over the children, before the sending
pass. A check inside the sending loop cannot be all-or-nothing: by the child
that looks wrong, the ones ahead of it have already been sent.

They are per-child because totals can cancel. One child with an empty
`room_states` and one listed in two rooms leaves the children, the
`"room_states"` keys and the `"checked_in"` keys all totaling the same, so
every whole-roster comparison agrees while both children are wrong — the first
would be sent with an empty room id, the second into a guessed room. Two
earlier guards subtracted totals in each direction and neither could see it.
`tests/mock_brightwheel.py` has the shape as `canceling`, ordered so the
two-room child is sent first; reverse the two and the run dies on the empty
child's failed lookup instead, which passes for the wrong reason.

Counting a child's own entries subsumed both of those subtractions, so they are
gone: a child whose `room_states` key was renamed away counts zero and fires
the same guard as a child whose array is empty.

All of them **stop the run**. A partial check-in is worse than none, because
the parent believes it worked.

### Order matters inside the pass

A rotated school code makes the roster call fail, and that same body also fails
the first guard. So the stale-code branch runs **first**, deletes the stored
code, asks for a second pass, and ends that pass — it does not exit the
shortcut, or the retry it just requested would never happen.

## Icons

What an icon is, why the glyph mapping cannot be trusted, and how to read a
number off a device are in shortcut-forge's `docs/building-shortcuts.md`.
What these shortcuts use:

What the shortcuts use today, all measured on a simulator rather than taken
from a name: Attendance is `62329` — a ring of petals, close to the Brightwheel
logo — on pink, `3980825855`. The wrappers are a plane arriving, `62022` on green `4292093695`, for Check In,
and departing, `62021` on red `4282601983`, for Check Out — Brightwheel's own
colors. (Not `62466`/`62467`, which are a plane on a runway, a heavier and
less legible pair.) Both numbers are waived in `build_shortcuts.py`: the validator checks
against a 507-entry mapping and the device's picker offers far more, so it
rejects real glyphs. The waiver names the two numbers rather than disabling the
rule. Chevrons (`59692` / `59707`) were the semantically purest pair but too
insubstantial to read as deliberate at tile size.

Sunrise (`62020`) and sunset (`62019`) were the other candidate and were
rejected on a subtlety worth recording: each carries **two signals that point
opposite ways**. Sunrise draws an up arrow, so it reads as *out* graphically
while meaning *morning* semantically. No assignment of that pair is truthful in
both registers. Time-of-day iconography is the wrong register here anyway,
for the same reason the direction is never inferred from the clock.

## Showing an image, and the first-run guide

Each wrapper shows a diagram the first time it runs, explaining how to attach
its own trigger — an **Arrive** trigger for both, separated by their time
ranges. Check Out used to point at Leave; it points at a second arrival now
because the school asks that children be checked out as parents walk in, and an
arrival fires while you are still parking rather than once you are driving away
with them. The guide lives in the wrappers rather than in Attendance on purpose:
**Attendance is complete on its own**, and the wrappers are optional extras that
automate it, so setup instructions for an optional extra do not belong in the
shortcut that extra is optional to. Two wrappers also means two different
guides, which is what the job actually needs.

Both guides therefore ring the same row in one shared picker screenshot, and it
is the configured panel and step 4 that differ — `capture-arrive-set.png` at
drop-off time against `capture-arrive-pm-set.png` at pickup time.

The school those captures name, **Washington Elementary School, is a stand-in**
chosen because it could be any of dozens of them, and the times with it are
invented. It is not anybody's school, so it is not something to scrub. The
location in a trigger is the one part of these screenshots that could identify a
family, and the diagrams ship inside the wrappers, so a recapture must keep
using a stand-in — `make_diagrams.py` says so where the crops are described.

Two arrivals at one school move all of the load onto the **time ranges, which
must not overlap**. They are now the only thing telling the triggers apart, and
they live on the device where the build cannot check them. What that cannot
break is direction: the wrapper still decides it, so a badly set range makes a
run fire at the wrong *time*, never in the wrong direction — the failure it can
produce is a check-out in the morning, not a check-in that reports a departure.
Arriving twice inside one window is separately harmless, because the
idempotency guard above skips anyone already in the state the run wants.

The mechanics — a PNG carried as base64 in a Text action, decoded at run
time, shown with Show Content, and the three things that are easy to get
wrong — are in shortcut-forge's `docs/building-shortcuts.md`, "Showing an
image". Quantized to 64 colors the diagrams are ~104 KB each, ~138 KB as
base64, which takes a wrapper from 4 KB to about 149 KB. Flat UI art loses
nothing at 64 colors.

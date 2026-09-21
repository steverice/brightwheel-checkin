# Testing

`./test.sh` runs the real shortcuts, in the real Shortcuts app, on an iOS 27
simulator, against a fake Brightwheel — and asserts on the HTTP traffic they
produce.

Until this existed the only way to test a change was to AirDrop a build to a
phone, stand in a doorway, and see what happened. Error paths were effectively
untestable: you cannot ask a school to rotate its QR code on demand, and an
expired session token arrives when it arrives.

## Running it

```bash
./test.sh                 # everything
./test.sh check_out       # only tests whose name contains this
./test.sh --erase         # wipe the simulator's library first
```

First run takes a few minutes; later runs skip the install. Use `--erase` when
you have changed the generator, because **a shortcut already in the library is
not replaced** — the harness skips installing over it.

### What you need

- **An iOS 27 iPhone simulator.** Xcode → Settings → Components. The harness
  picks a booted one, or boots the first it finds.
- **`idb`**, on `PATH`: `brew trust facebook/fb && brew install
  facebook/fb/idb`. No Accessibility permission, and no window: the device is
  driven headless, and do not quit Device Hub while a run is going — quitting
  it shuts down every booted simulator.
- Nothing else. The certificate, the mock, and the test build are all generated.

### Driven through idb, not a window

`idb` injects touches and reads the accessibility tree over its own connection
to the simulator, so there is no window to find, no screen to map, and no
Accessibility permission to grant. What that took to measure, and why the
harness never opens Device Hub, is in shortcut-forge's
[`docs/simulator-harness.md`](../shortcut-forge/docs/simulator-harness.md).
Other devices can stay booted; a person running Device Hub alongside does no
harm, since idb's touches do not care whether a window is showing the device.

**The simulator cannot open files from `~/Desktop`.** `xcrun simctl openurl
<udid> file:///Users/…/Desktop/X.shortcut` fails with "Operation not permitted":
the Desktop is one of macOS's privacy-protected folders, and the simulator
process has no grant for it. The harness opens files from the repo and from temp
paths for that reason. `~/Documents` and `~/Downloads` are protected the same
way and are likely to fail too, though neither has been tried.

### When a run goes wrong

Three failures that read as flakiness and are not. shortcut-forge's
`docs/simulator-harness.md` has a Troubleshooting table with the rest.

**The five clipboard tests fail as a block, everything else passes.** A device
that has been up a long time stops accepting pasteboard syncs: `simctl pbsync`
prints the byte count it resolved and "Sync complete" while the device's
pasteboard stays empty, the same report-success-copy-nothing behavior
`simctl pbcopy` has. Restarting `com.apple.coredevice.dtpasteboardd` does not
fix it; shutting the device down and booting it does. This is the first thing
to try when those five fail together, and only then — a device has gone four
full suites since a reboot with the pasteboard still landing, so rebooting
before every run buys nothing. `set_pasteboard()` says which half failed, so
read its message before guessing.

**A test says the shortcut never asked for something, and the request list is
empty.** A runner dialog whose position the harness has not measured is a
silent timeout, not an error: the settle loop polls for the *absence* of a
dialog, so an unrecognized consent looks exactly like a run that never
started. Open the screenshot the run left in `tests/artifacts/` before calling
it flaky. The fix is a row in `SEEDS` in shortcut-forge's `harness.py`, and
since these dialogs are neither centered nor anchored to an edge, a consent
with different wording is a new row rather than an offset on an existing one.

**`idb` commands fail with `Failed to connect to companion`.** A stale
registration for that device: `idb disconnect <udid>` clears it and the next
command spawns a fresh companion. **Never `idb kill`** — it SIGKILLs every
companion on the machine, including ones for other devices and other people's
sessions.

## It cannot reach the real Brightwheel

Test builds are generated with `--api-base` pointing at the local mock, plus
`--env-file tests/fixtures/test.env`, which bakes in obviously fake
credentials. The children are no longer a build input at all — the shortcut
asks the mock who they are. There is no code path from a test to the live API, and so no way
for a test run to check a real child in or out, or to make Brightwheel send a
real 2FA code. The suite refuses to be useful for that on purpose.

Confirm it for yourself after a build:

```bash
grep -c schools.mybrightwheel.com "dist-test/Brightwheel Attendance.xml"   # 0
```

## What the tests cover

| Test | What would break without it |
|---|---|
| `skips_children_already_in_the_wanted_state` | The idempotency guard. Asserts **no** POST reaches `/checkins/` |
| `checks_both_children_in` | The happy path, plus the exact body: `checked_in`, both targets, secret, `school_id`, `checkin_code` |
| `check_out_sends_checked_in_false` | Direction is structural — the wrapper decides it, not the clock |
| `stale_school_code_causes_a_second_pass` | A rejected secret is recognized and retried instead of reported as a plain failure |
| `expired_token_signs_in_again` | `E1200` → two-step 2FA → token stored → the run recovers and still sends. The code has a leading zero, which the number pad drops and the shortcut pads back |
| `an_empty_code_answer_sends_another` | Empty is the only resend control now. Done on an empty field reaches `/sessions/start` again and never `/sessions` |
| `a_code_sent_minutes_ago_is_not_sent_again` | Cancel the prompt, run again within ten minutes: the prompt comes back with no second send, and the send time is cleared once the code is accepted |
| `a_code_on_the_clipboard_is_offered` | A six-digit clipboard prefills the prompt, and Done alone exchanges it. A pasted value carries provenance, so iOS asks before sending it ("send 1 text item to localhost?"), and the token it earns carries it on: four prompts in that run, one on the next, none on the one after, measured from an erased device. The test clears them and asserts the second follow-up run asks nothing. Every sign-in test sets the simulator's pasteboard first, by way of the Mac's own (`simctl pbcopy` copies nothing under Xcode 27; `pbsync` does), and the suite restores the Mac's clipboard when it finishes |
| `test_setup_questions_commit_their_answers` | The import-question mechanism `dist/` depends on. A canary: broken from iOS 27.0 beta 5 through the 27.0 release, passing again on 27.2 beta 1 — see the support matrix |
| `a_day_that_is_not_a_school_day_sends_nothing` | The school-days guard: with every day but today in the store, Check In makes no request at all |
| `a_school_day_named_by_three_letters_runs` | The match is on the first three letters: a stored `Fri` on a Friday checks both children in |
| `nothing_stored_means_monday_to_friday` | The default when "Set school days" has never run: a weekday runs, a weekend day does not. The one test whose expectation depends on the calendar |
| `a_snooze_until_tomorrow_sends_nothing` | The snooze guard: a stored date of tomorrow makes no request |
| `a_snooze_until_today_has_ended` | The snooze date is the first day back: a stored date of today runs today |

The schedule tests write the shared store through a probe shortcut
(`build_store_probe()`), the way "Set school days" and "Snooze until a date"
do, and run the real Check In. The suite stores every day as a school day at
setup, and each test puts that back, so nothing else depends on the calendar.

Assertions are on **recorded traffic**, not on notifications. "Nobody was
checked in" is exactly "no POST reached `/checkins/`", which is a fact the mock
holds; a notification only reports what the shortcut believes happened. The
shortcut's own state is checked by reading Store Content back off the device.

**The QR the menu draws is the one that was scanned.** Not judged by eye: the
rendered image was pulled off the simulator and decoded with CoreImage's
`CIDetectorTypeQRCode`, and the result compared to the `BrightwheelSchoolCode`
string in the device's own store. Byte-identical, which is what makes
re-seeding a second phone from it equivalent to standing at the door.

That check is a one-off rather than a suite test, because Quick Look has no
on-disk state to assert against — the evidence is pixels, and the decode has to
happen outside the device.

**A dismissed scan is guarded but unmeasured.** The menu's scan branch counts
its result before drawing, so an empty scan reaches the "Still no school code
saved" notification instead of Generate QR Code. Whether iOS returns an empty
string or stops the run outright when the scanner is dismissed has not been
checked on a phone: the guard covers the first, and the second needs no cover.

## Importing on a Mac, iCloud links, and the publisher

How a Mac import and an iCloud link preserve a build, why a picked target
goes stale every release, and how looking the shortcut up at run time avoids
the picker — all measured with these shortcuts — moved to shortcut-forge's
`docs/simulator-harness.md` with the publisher and the link checker. What is
specific to this project stays here.

**The shipping publisher now works this way.** `tools/build_publisher.py`
generates Brightwheel Share Links around the lookup. For each of the three
targets it stops, with a notification, on more than one copy, on a copy whose
only name is numbered, or on a missing shortcut. All nine checks run before the
first link is minted, and `tests/test_publisher.py` pins that structure down.
Measured on 2026-09-11:

| step | result |
|---|---|
| import on an iOS 27 simulator | 99 actions; all three links still hold the `Repeat Item` variable |
| Mac run, the current `dist/` builds imported fresh | three links, which `verify_links.py` accepted: same actions, and Attendance's 3 questions |
| a kept second copy of Check In | "More than one Brightwheel Check In", no link confirmation (the user's report) |
| only `Brightwheel Check In 1` left | "…has a number after its name", no link confirmation (the user's report) |
| Check Out renamed `Brightwheel Check Out x` | "…is not in your library", no link confirmation (the user's report) |

The clipboard sentinel that would have independently confirmed the refusals
copied nothing was overwritten by an unrelated copy during those runs. So the
refusal rows rest on what was on screen: each notification appeared, and no link
confirmation did.


## The roster fixtures

The shortcut reads its roster at run time, so the mock has to be able to answer
badly as well as well. **None of these shapes occurs in any capture** — the
evidence base is one family, one day, with both children in the same room, so
mis-pairing and multi-room are invisible in real data. They exist here or
nowhere.

| fixture | what it proves |
|---|---|
| an error body | a failed roster stops loudly instead of doing nothing quietly |
| a restructured child shape | a child whose `room_states` key was renamed away has no room to send to |
| a child with empty `room_states` | one child with no room stops the run rather than half-running it |
| a child in two rooms | the room to send would be a guess, so nothing is sent |
| a room entry with no `checked_in` | the one malformation a per-child room count cannot see |
| one child empty, one in two rooms | every whole-roster total agrees; only the per-child count catches it |
| a rotated secret | the re-scan still fires when the roster call is what sees the rotation |

Every roster shape carries the same two children. Nothing yet varies the roster
*length*, so "nothing assumes two" is not among the things this proves.

Two fixtures earn their place by being wrong in a way that looks right: an
error body and a restructured reply both drive every count the guards derive to
zero, and zero equals zero.

The mock's `/users/me` deliberately returns **several** `object_id` values, so
an unanchored guardian-id pattern fails the suite instead of passing it.

## Support matrix

Established by running the suite and targeted probes against each runtime, not
inferred from release notes.

| Runtime | Shortcuts run? | Setup questions? | Checked on |
|---|---|---|---|
| iOS 26.5 `23F77` (release) and earlier | **No** | yes | simulator |
| iOS 27.0 beta 1 `24A5355p` | yes | yes | simulator |
| iOS 27.0 beta 5 `24A5408d` | yes | **No** | simulator |
| iOS 27.0 beta 6 `24A5423a` | yes | **No** | simulator |
| iOS 27.0 beta 7 `24A5424a` | yes | **No** | **device** |
| iOS 27.0 RC `24A434` | yes | **No** — but Skip Setup commits them | simulator |
| iOS 27.2 beta 1 `24B5084k` | yes | **yes** — Add Shortcut commits them again | simulator |

Beta 7 is a device result because Apple has published no simulator runtime for
it — the downloadable index stops at beta 6. That makes it the better data
point of the set: the regression is not a simulator artifact.

The release candidate has not been checked on a device. Everything in the
`24A434` row was measured on a simulator, so the Skip Setup workaround is worth
confirming on a phone before anyone relies on it.

Betas 2, 3 and 4 (`24A5370g`, `24A5380g`/`24A5380i`, `24A5390f`) have not been
tried, so the regression landed somewhere between beta 1 and beta 5. Narrowing
it would cost an 8 GB runtime download per beta and would not change what the
build has to do.

**iOS 27 is genuinely required to run these shortcuts.** On iOS 26.5 every test
fails at the first action with "the shortcut could not be run because an action
could not be found". Probed individually, both `is.workflow.actions.getstoredcontent`
(Store Content) and `is.workflow.actions.scanbarcode` (Scan Code) fail there on
their own, so this is not something a workaround in the generator could fix —
the shortcut is built on two iOS 27 actions.

**Setup questions regressed during the iOS 27 beta cycle.** Answering them and
tapping "Add Shortcut" does nothing at all: no install, no error, nothing in the
log — while the tap is delivered (`UIEvent` dispatched, gesture actions sent).
It reproduces with a **two-action** shortcut carrying one question, so it is
nothing to do with this project's build. Skip Setup still installs, and
importing a shortcut that merely *has* questions is fine; it is committing the
*answers* that is inert.

The regression is **iOS only**: the same build imports on macOS 27 on the first
click of Add Shortcut — see "Importing on a Mac" in shortcut-forge's
`docs/simulator-harness.md`.

That is what `test_setup_questions_commit_their_answers` watches. It is marked
`expected_broken`, so it reports **KNOWN** in yellow and does not fail the
suite — and the day a release fixes it, it reports **FIXED** and tells you to
drop the marker.

**iOS 27.2 beta 1 (`24B5084k`) commits the answer again.** Measured 2026-09-18
with shortcut-forge's two-action `setup_probe`, driven through idb on a
simulator, with the `24A434` release run through the identical procedure as the
control: on 27.0 the typed answer plus Add Shortcut installed nothing, and on
27.2 beta 1 the installed shortcut held the typed answer, read out of
`Shortcuts.sqlite`. Skip Setup left the placeholder on both. Two things sit
between the typing and the tap and are worth knowing for anyone repeating it:
the software keyboard covers Add Shortcut after typing, so a tap at the button's
frame lands on the keys, and on a fresh device dismissing the keyboard raises a
first-run typing tip whose Continue brings the keyboard back. The full suite has
not run on 27.2 beta 1, so the `expected_broken` marker stays until it does. The
matrix already holds a beta that got this right before a later beta broke it,
so this is a beta-1 row, not a 27.2 row.

**When 27.2 ships, update the text that steers around the bug.** Once the
canary passes on the release build: the README's install step that says to
finish with Skip Setup and the warning block below it; the note carried in the
last setup question in `build_shortcuts.py`, which says the same; and the
`expected_broken` marker on `test_setup_questions_commit_their_answers`. Users
on 27.0 still need the workaround, so the wording becomes "on 27.2 or later, Add
Shortcut works; on 27.0, use Skip Setup" rather than a plain removal.

### Three checks, three different questions

This regression is the clearest case of why the three checks are not
interchangeable, since it passes one of them while breaking the thing that one
appears to be about.

| check | what it proves | what it cannot see |
|---|---|---|
| `tools/verify_links.py` | The **link** carries the plist the build produced: same name, same action identifiers in order, same import questions, none answered. It fetches the record iCloud shares, so it is sound against a stale or mis-minted link | Anything that happens after an import. The device is never involved |
| shortcut-forge's setup canary | The **import path**: whether answering a setup question configures the shortcut at all. A two-action shortcut whose only value comes from a question, read back off the device | Anything about these shortcuts — that is the point. It carries none of this project's machinery |
| `./test.sh` | What the built shortcuts **do once installed**, in requests to the mock | Whether they would have installed that way from a link, and anything nobody wrote a test for — see "What the suite does not cover" |

The iOS 27.0 behavior above sits exactly in the link check's blind spot: the
plist is right, the link is right, `verify_links.py` passes, and the device
still installs a shortcut whose answer was never committed. Reading a green
link check as covering the import path is the mistake this table exists to
prevent.

The canary lives in shortcut-forge, because it is about iOS rather than about
Brightwheel. Run it against a booted device from that repo:

```bash
xcrun simctl boot <udid>
SHORTCUT_FORGE_SIM_UDID=<udid> make test-integ    # in ~/code/shortcut-forge
```

`tests/test_sim_canary.py` there branches on the measured difference, and its
docstring says to update the note above when 27.2 ships.

One thing to get right when you run the link check: it compares each link
against `dist/`, so build at the release those links were minted from. A
`dist/` built from a `main` that has moved on reports differences that belong
to the build, not to the link.

**Skip Setup commits the answers, so `dist/` is installable after all.** Found
on the `24A434` release candidate, and it is the reason the last setup question
now carries a note saying so. Three probes, each read back off the device rather
than judged from the screen:

| what was done | what the installed shortcut held |
|---|---|
| answer typed, **Add Shortcut** tapped (twice, 5s apart) | nothing installed at all |
| answer typed, **Skip Setup** tapped | installed, holding the typed answer |
| nothing typed, **Skip Setup** tapped | installed, holding the `not set` placeholder |

The second and third rows together are the proof: the field is genuinely read,
rather than Skip Setup applying something unconditionally. Repeated against the
real 238-action build with all three questions answered — every answer reached
its own `gettext` action, in order, and no action was left holding `not set`.

Two details of the multi-question flow that the one-question canary cannot see.
iOS asks the questions **one at a time**, and the button reads **Next** until
the last page; Next works normally, so it is specifically the final commit that
is dead. And **the answer field autocapitalizes** — `pw9swordfish` is stored as
`Pw9swordfish`. Digits are immune, which is why the canary types digits.

## How it fits together

```
build_shortcuts.py --api-base https://localhost:8788/api/v1 --env-file …
        │
        ├─ sign-shortcut ──> dist-test/*.shortcut
        │
        ▼
 simctl openurl file://…            ← install, one tap on "Add Shortcut"
 simctl openurl shortcuts://run-shortcut?name=…
        │
        ▼
 Shortcuts.app ──HTTPS──> mock Brightwheel (localhost, trusted CA)
        │                        │
        │                        └─ records every request  ─┐
        └─ Store Content ──> Shortcuts.sqlite ──────────────┤
                                                            ▼
                                                       assertions
```

| File | Role |
|---|---|
| `test.sh` | Entry point |
| `tests/test_attendance.py` | The scenarios, and the runner |
| `shortcut_forge_lib.sim.harness` | Driving the simulator: install, run, tap, type, read state (in shortcut-forge) |
| `tests/mock_brightwheel.py` | The fake API and its scenario knobs |
| `shortcut_forge_lib.sim.certs` | Throwaway CA so the simulator trusts localhost (in shortcut-forge) |
| `tests/testbuild.py` | Builds and signs the shortcuts under test, and the setup canary |
| `tests/fixtures/test.env` | Deliberately fake credentials |
| `tests/fixtures/roster.json` | Deliberately fake children — "Alpha" and "Beta" at "Test School". No longer a build input: it is the dataset the mock serves |

`dist-test/`, `tests/tls/` and `tests/artifacts/` are generated and gitignored.
A failing test saves a screenshot into `tests/artifacts/`.

## What had to be worked out

Installing by host file URL, finding a button by label and confirming it with
a hit test before tapping, typing with a single `idb ui text` call, HTTPS
through a throwaway CA, reading Store Content off disk, consent prompts, focus
before typing, autocapitalization, and dropped run URLs: all in
shortcut-forge's `docs/simulator-harness.md`.

## What the suite does not cover

Worth stating so nobody reads a green run as broader than it is. What the suite
covers against what the link check and the setup canary cover is "Three checks,
three different questions" above.

**The first-run setup guide.** Every test's first assertion is that a run
reached the mock, and the priming run absorbs the diagram sheet along with the
consent prompts — so a regression that stopped the guide appearing, or made it
appear on *every* run, would pass. Both behaviors were checked by hand on a
simulator instead. The harness can automate it (`prompt_up()` is how the sheet
would be detected); nobody has written the test.

**The two menu items themselves, and a run from the menu ignoring the
schedule.** The guard applies only to a run that was handed a direction, so
Attendance's own Check In on a day off still checks in; choosing it means
tapping a row of an action sheet, which the harness has no way to find. The
date picker and the tick list behind "Snooze until a date" and "Set school
days" are sheets too. All three were checked by hand on a simulator: the
picker's date reached the store as `yyyy-MM-dd` once it went through a Text
action (fed to Format Date directly it came back empty); the toggle sheet
opened with 🏫 on every stored day and nothing on the rest, ticking Saturday
from Monday to Friday stored Monday to Saturday and brought the sheet back
with Saturday marked, Done with nothing ticked closed it, and ticking all five
school days raised the "no school days" alert, brought the sheet back, and
changed nothing; and
Check In chosen from the menu with every day but today stored posted both
check-ins.

**Icons.** Nothing asserts a glyph or color. That is deliberate — an icon
regression is visible the moment the app opens, and pinning glyph numbers in a
test would only restate the generator.

## What the simulator cannot tell you

- **Scan Code does not exist there**, and **triggers are not exercised**; see
  shortcut-forge's `docs/simulator-harness.md`. Test builds seed the school
  code so the run never takes the scan branch, which means **neither QR scanning
  path is covered by these tests** — not the one at the top of a check attempt,
  and not the one the menu's "Show the school's code" takes when nothing is
  stored. Both still need a device.
- **The real API contract.** The mock reproduces the response *shapes* recorded
  in README.md. If Brightwheel changes them, every test here still passes. The
  mock is only as true as the day it was written against the live API.


The two dead ends and the troubleshooting table are in
shortcut-forge's `docs/simulator-harness.md` as well.

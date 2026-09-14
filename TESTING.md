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
- **Accessibility permission** for the terminal running the tests: System
  Settings → Privacy & Security → Accessibility. Taps are synthesized as real
  mouse events, so without this the cursor moves and nothing is pressed.
- Nothing else. The certificate, the mock, and the test build are all generated.

### Simulator.app, or Device Hub

The harness detects which one is installed and drives either; what differs,
what is harder under Device Hub, and how the screen is measured inside the
bezel are in shortcut-forge's
[`docs/simulator-harness.md`](../shortcut-forge/docs/simulator-harness.md).
Other devices can stay booted; leave this device's window alone while the
suite runs.

**The simulator cannot open files from `~/Desktop`.** `xcrun simctl openurl
<udid> file:///Users/…/Desktop/X.shortcut` fails with "Operation not permitted":
the Desktop is one of macOS's privacy-protected folders, and the simulator
process has no grant for it. The harness opens files from the repo and from temp
paths for that reason. `~/Documents` and `~/Downloads` are protected the same
way and are likely to fail too, though neither has been tried.

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
| `expired_token_signs_in_again` | `E1200` → two-step 2FA → token stored → the run recovers and still sends |
| `test_setup_questions_commit_their_answers` | The import-question mechanism `dist/` depends on. Currently a **known-broken canary** — see the support matrix |

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

Installing by host file URL, tapping, finding the blue button, typing by
keycode, HTTPS through a throwaway CA, reading Store Content off disk, consent
prompts, which window, focus before typing, autocapitalization, and dropped
run URLs: all in shortcut-forge's `docs/simulator-harness.md`.

## What the suite does not cover

Worth stating so nobody reads a green run as broader than it is.

**The first-run setup guide.** Every test's first assertion is that a run
reached the mock, and the priming run absorbs the diagram sheet along with the
consent prompts — so a regression that stopped the guide appearing, or made it
appear on *every* run, would pass. Both behaviors were checked by hand on a
simulator instead. The harness can automate it (`blue_buttons()` is how the
sheet was detected); nobody has written the test.

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

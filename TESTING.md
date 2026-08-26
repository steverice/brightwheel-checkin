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

Leave the Simulator window alone while the suite runs — the taps go to real
screen coordinates.

## It cannot reach the real Brightwheel

Test builds are generated with `--api-base` pointing at the local mock, plus
`--env-file tests/fixtures/test.env` and `--roster tests/fixtures/roster.json`,
which bake in obviously fake credentials and a fake roster. There is no code path from a test to the live API, and so no way
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
| `setup_questions_commit_their_answers` | The import-question mechanism `dist/` depends on. Currently a **known-broken canary** — see the support matrix |

Assertions are on **recorded traffic**, not on notifications. "Nobody was
checked in" is exactly "no POST reached `/checkins/`", which is a fact the mock
holds; a notification only reports what the shortcut believes happened. The
shortcut's own state is checked by reading Store Content back off the device.

## Support matrix

Established by running the suite and targeted probes against each runtime, not
inferred from release notes.

| Runtime | Shortcuts run? | Setup questions? |
|---|---|---|
| iOS 26.5 `23F77` (release) and earlier | **No** | yes |
| iOS 27.0 beta `24A5355p` | yes | yes |
| iOS 27.0 beta `24A5408d` | yes | **No** |
| iOS 27.0 beta `24A5423a` | yes | **No** |

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

That is what `test_setup_questions_commit_their_answers` watches. It is marked
`expected_broken`, so it reports **KNOWN** in yellow and does not fail the
suite — and the day a beta fixes it, it reports **FIXED** and tells you to drop
the marker. Until then, `dist/` cannot be installed through its documented
setup flow, which is why the debug build (no questions) is the practical path.

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
| `tests/simharness.py` | Driving the simulator: install, run, tap, type, read state |
| `tests/mock_brightwheel.py` | The fake API and its scenario knobs |
| `tests/certs.py` | Throwaway CA so the simulator trusts localhost |
| `tests/testbuild.py` | Builds and signs the shortcuts under test |
| `tests/fixtures/test.env` | Deliberately fake credentials |
| `tests/fixtures/roster.json` | Deliberately fake children — "Alpha" and "Beta" at "Test School" |

`dist-test/`, `tests/tls/` and `tests/artifacts/` are generated and gitignored.
A failing test saves a screenshot into `tests/artifacts/`.

## What had to be worked out

None of this is documented by Apple, and most of it failed silently first.

**Installing.** `shortcuts://import-shortcut?url=…` only accepts iCloud links —
it rejects anything else *before* fetching it, so a local file cannot be
imported that way. What does work is opening the file as a **host** file URL:
simulator processes are ordinary Mac processes and see the Mac's filesystem, so
`simctl openurl "file:///Users/…/Brightwheel Check In.shortcut"` opens the
normal import sheet. **The library name comes from the filename**, not from
`WFWorkflowName`, so the signed files have to be named exactly what the
wrappers call.

**Tapping.** A synthesized click needs a `MouseMoved` event first *and*
`kCGMouseEventClickState` set; with either missing the cursor moves to the right
place and nothing is pressed. Coordinates are exact once the Simulator is set to
*Window → Point Accurate* with *Show Device Bezels* off: the device screen then
starts at the window origin plus a 52pt title bar, at 3 device pixels per point.
The harness sets both itself.

**Finding the button.** In every prompt shape the button we want is the
bottom-most iOS-blue one — "Allow", "Always Allow", "Add Shortcut" — so the
harness finds blue rectangles by color and taps the lowest. No text
recognition, and it survives light and dark mode. Tall blue shapes are filtered
out because the shortcut tile on the import sheet is also blue.

**One trap in that rule:** the *Ask for Input* dialog's **Done** button is blue
too, so blind-tapping submits it empty — which this shortcut reads as "resend me
a code", five times over, and then gives up. Tests that expect a prompt use
`answer_prompt()` and stop the generic clearing once traffic has started.

**Typing.** `CGEventKeyboardSetUnicodeString` does nothing here. The Simulator
passes raw HID keycodes through to the guest, so every character arrives as
whatever keycode 0 is: typing `123456` puts `Aaaaaa` in the field. Real US
virtual keycodes are the only thing that works.

**HTTPS.** `simctl keychain <udid> add-root-cert` installs a CA into the
device's trust store, which is what lets Get Contents of URL talk to the mock.
iOS rejects leaf certificates without a `subjectAltName`, without
`extendedKeyUsage = serverAuth`, or valid for much beyond a year, so
`tests/certs.py` sets all three. **Erasing the device drops the trust**, so the
harness re-adds it after an erase.

**Reading Store Content.** It is two hops on disk. `ZSTOREDVALUE` in
`Shortcuts.sqlite` holds the key in `ZDISPLAYNAME` and a keyed archive naming a
file; the value itself lives in `Library/Shortcuts/PersistentStorage/<uuid>`.
Reading only the row gives you a key and no value, which is misleading rather
than obviously empty. The payload sits under `NS.string` or, when it came out of
an action, under a `WFObjectRepresentation`'s `object`.

**Consent prompts** are per capability per shortcut — running another shortcut,
network access, the clipboard — and persist until the device is erased. The
suite absorbs them in one throwaway priming run.

**Which window.** More than one simulator can be booted, and AppleScript's
"window 1" is then whichever is frontmost. Taps silently go to the wrong device
— or, if the window sizes differ, to a computed point off-screen entirely. The
harness matches the window by title (device name + OS version) and raises it
before measuring or clicking.

**Focus before typing.** Neither the Ask for Input dialog nor the setup wizard
focuses its text field, so typing straight into either goes nowhere and leaves
an empty answer. Tap the field first. The software keyboard being visible is
*not* a sign that the hardware keyboard is disconnected — it can be connected
and showing anyway, so that is not a useful diagnostic.

**Autocapitalisation is on**, so `zzemail` arrives as `Zzemail`. The canary
types digits to sidestep it; anything asserting on typed letters has to account
for it or turn it off on the device.

**A run URL can be dropped** if it arrives while Shortcuts is still shutting
down — nothing happens and no error is raised. The runner re-issues the run
once if no traffic has appeared and no prompt is on screen.

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

- **Scan Code does not exist there.** A simulator has no camera, and a shortcut
  reaching `is.workflow.actions.scanbarcode` dies with "an action could not be
  found". Test builds seed the school code so the run never takes that branch,
  which means **the QR scanning path is not covered by these tests** and still
  needs a device.
- **Triggers are not exercised.** The tests start a run by URL. `simctl
  location` could drive a geofence, but a trigger cannot be generated into a
  shortcut in the first place, so there is nothing built to test.
- **The real API contract.** The mock reproduces the response *shapes* recorded
  in README.md. If Brightwheel changes them, every test here still passes. The
  mock is only as true as the day it was written against the live API.

## Two dead ends, so nobody spends the afternoon again

**Writing rows into `Shortcuts.sqlite` directly.** It gets tantalisingly close —
`siriactionsd` picks the row up and hashes it — but library membership lives in
a CRDT blob in `ZLIBRARY.ZDATA` (it starts with the magic `crdt`), so an
injected shortcut never appears and cannot be run by name. Use the import sheet.

**`shortcuts://import-shortcut`.** iCloud URLs only, as above. It is not even a
verb in `WorkflowKit`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `no Add Shortcut button appeared` | Accessibility permission, or the Simulator window is off-screen or obscured |
| Taps land in the wrong place | Something re-enabled device bezels or changed the window scale; rerun, the harness resets both |
| A test hangs then fails to settle | Look at `tests/artifacts/` — a prompt shape the harness did not recognize |
| Everything fails after an erase | The CA is re-added automatically, but only on the run that erased |

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

> **Xcode 27 breaks this harness.** It removed `Simulator.app` — the whole of
> `Xcode.app/Contents/Developer/Applications/` is gone — and replaced it with
> **Device Hub** (`Xcode.app/Contents/Applications/DeviceHub.app`, process
> `DeviceHub`), which hosts simulators and real devices in one sidebar window.
> `simharness.py` drives `process "Simulator"` throughout, so every AppleScript
> in it now finds nothing. Two more things it depends on are gone with it:
> `Show Device Bezels` and `Point Accurate`, which is what made `_mapping()`'s
> window-frame arithmetic exact. Bezels are always drawn now and there is no 1:1
> zoom, so the device screen has to be *measured* inside the window instead —
> and neither the sidebar nor the device screen appears in the accessibility
> tree, so there is no way around clicking coordinates.
>
> `xcrun simctl` is untouched, including `simctl io <udid> screenshot`, so every
> assertion the suite makes about on-disk state still works headlessly. Until
> the port lands, run the suite under Xcode 26.

**Other simulators can stay booted.** The harness brings its own device's
window to the front and verifies it arrived, because every Simulator menu it
touches applies to the frontmost window. A visionOS device in front has no
"Show Device Bezels" item at all, which used to kill the run on a missing menu
item rather than on anything real. Window settings are now applied only if the
frontmost window offers them; the hardware keyboard is not optional, so a
missing one there is still an error, and it names the likely cause.

Leave the Simulator window alone while the suite runs — the taps go to real
screen coordinates.

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

Beta 7 is a device result because Apple has published no simulator runtime for
it — the downloadable index stops at beta 6. That makes it the better data
point of the set: the regression is not a simulator artifact.

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
| `tests/fixtures/roster.json` | Deliberately fake children — "Alpha" and "Beta" at "Test School". No longer a build input: it is the dataset the mock serves |

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

**Autocapitalization is on**, so `zzemail` arrives as `Zzemail`. The canary
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

**Writing rows into `Shortcuts.sqlite` directly.** It gets tantalizingly close —
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

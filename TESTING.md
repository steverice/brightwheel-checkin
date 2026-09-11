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

Xcode 27 deleted `Simulator.app` and replaced it with **Device Hub**
(`Xcode.app/Contents/Applications/DeviceHub.app`, process `DeviceHub`), which
shows simulators and real devices together in one window with a sidebar. The
harness detects which one is installed and drives either; everything that
differs lives in the two `_Host` classes in `simharness.py`. `xcrun simctl` is
untouched under both, so every assertion about on-disk state is headless
regardless.

Three things are genuinely harder under Device Hub, and are worth knowing when
a run misbehaves:

- **Selecting the device is a click, not a command.** There is one window and it
  shows whichever device the sidebar has selected, so "no window for this
  device" is the ordinary state. Nothing in the sidebar reaches the
  accessibility tree — the split view reports *zero* children — so the harness
  filters the sidebar by typing the device name and clicks rows by position,
  checking the window title after each until it matches. Never send ⌘A hoping to
  clear that filter: ⌘A is Select All for the *device list*, and its modifier
  leaks into the clicks that follow, which quietly gathers up a multi-selection.
- **The mapping is measured, not computed.** `Point Accurate` and
  `Show Device Bezels` are both gone, bezels are always drawn, and there is no
  1:1 zoom, so `_screen_box` finds the screen in pixels: it is the widest gap
  between the two walls of dark either side of it. The bezel reads dark in both
  system appearances, and in dark mode so does the window background — which
  costs nothing, because merging the two only thickens the wall and never moves
  its inner edge. It runs once per window, on the home screen, since a dimmed
  backdrop behind a sheet swallows the screen's own edges. A shape check against
  the device's real aspect ratio turns that into an error rather than a bad
  mapping, and the failing screenshot is kept as
  `artifacts/measure-failed.png`.
- **Points and pixels are not the same number.** `screencapture -R` takes a rect
  in points and writes pixels, so on a Retina display the image is twice the
  size of the window it captured, while Quartz click coordinates stay in points.
  Every measurement divides by that scale. Also clamp the capture *width* to the
  display, not only its origin: a window can start at a negative x, and clamping
  one without the other runs the region past the far edge, where whatever window
  sits behind gets measured as bezel.

**Other devices can stay booted.** The harness brings its own device's window to
the front and verifies it arrived, because every menu it touches applies to the
frontmost window. A visionOS device in front has no "Show Device Bezels" item at
all, which used to kill the run on a missing menu item rather than on anything
real. Display settings are applied only if the frontmost window offers them; the
hardware keyboard is not optional, so a missing one there is still an error, and
it names the likely cause.

Leave the device window alone while the suite runs — the taps go to real screen
coordinates.

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

## Importing on a Mac

Worth knowing before minting iCloud links, since the link is a snapshot of
whatever the sharing device holds: **a Mac import is lossless.** The shipping
Attendance build was signed under a throwaway name, imported into the macOS 27
Shortcuts library, and read back out of `~/Library/Shortcuts/Shortcuts.sqlite`,
which has the same schema as the simulator's:

| | source | after a Mac import |
|---|---|---|
| actions | 251 | 251 |
| identifier sequence | — | identical |
| parameters differing | — | none |
| action UUIDs (164 of them) | — | all preserved |

Nothing is rewritten, dropped, or reminted on the way in.

**And nothing is rewritten on the way out either.** That copy was exported from
the Mac's Shortcuts (File → Export, *For: Anyone*, which re-signs through
iCloud), the exported file imported on an iOS 27 simulator, and the actions read
back off the device: 251 actions again, identifier sequence identical, and after
canonicalising Shortcuts' own text-token form the *only* three differences were
the setup answers, which Skip Setup had emptied during the import.

The action that made this worth testing survives exactly:

| | `is.workflow.actions.scanbarcode` |
|---|---|
| source | `{'WFScanCodeActionMode': 0}` |
| after macOS export → iOS import | `{'WFScanCodeActionMode': 0}` |

No `imageFile` appears and the live-scanner mode is intact, so the macOS variant
of that action does not get substituted in passing. That was the plausible
silent failure — a shortcut that imports cleanly, looks right, and has no camera
at the school door.

### iCloud links carry setup questions — but check every one

The **actions** survive every route tested: 251 actions, identical identifiers,
zero differences after canonicalising text tokens, `scanbarcode` intact. Only
`WFWorkflowImportQuestions` ever went missing, and only once.

Everything measured, each count read out of `ZSHORTCUT.ZIMPORTQUESTIONSDATA`
rather than inferred from what a sheet offered:

| | questions |
|---|---|
| the built file | 3 |
| macOS library copy, imported with Add Shortcut and no values filled | **3** |
| the same copy after a File → Export | **3** (export changes nothing) |
| macOS export → file → iOS import | **3** |
| link minted on an **iPhone** → iOS import | **3** |
| link minted on the **Mac** from that copy → iOS import | **3** |
| link minted on the Mac from the *first* probe → iOS import | **0** |

So iCloud links carry questions, from either platform, and "Add Shortcut with
the fields left empty" on macOS behaves like iOS's Skip Setup — the copy keeps
its questions.

**The last row is unexplained.** Three theories were tried and all three are
dead: an iCloud link does not strip questions (five links say otherwise);
completing setup does not consume them (the macOS copy kept all three); and
exporting does not clear them (measured before and after). The only other
difference between that probe and the rest is that its clicks were synthesized
rather than made by a person, which is not a mechanism, just the remaining
variable.

A note on what sent that investigation wrong twice: every `Brightwheel*` shortcut
in the macOS library reports zero import questions, which looked like strong
evidence that something was stripping them. They are **debug builds**, which are
generated with no import questions at all. They were never evidence.

Since the cause is unknown, rely on the check rather than the rule. It is free:
open the link yourself before sending it to anyone. A sheet offering **Set Up
Shortcut** has the questions; one offering **Add Shortcut** does not, and will
install in one tap leaving `not set` in the email, password and check-in code
actions — no error, no prompt, and a shortcut that cannot sign in.

Two limits worth keeping on the file result. It establishes that the
round-tripped shortcut is structurally identical to the build the suite
exercises, not that it was separately run end to end. And a shortcut with no
questions still installs and runs — it simply has no credentials in it.

### The publisher's pickers go stale every release

`Create iCloud Link for Shortcut` takes a workflow reference, and the picker
stores both an identifier and a display name:

```
identifier : 5427F12F-…      the target's ZWORKFLOWID
title      : { key: "ZZ Target" }
subtitle   : { key: "ZZ Target" }
image      : { uri: intents-remote-image-proxy:… }
```

Delete the target and import it again — which is exactly what shipping a new
build requires, since iOS skips a same-name import and macOS installs a numbered
second copy beside it — and it comes back with a fresh `ZWORKFLOWID`. The picker keeps the old one. Measured: stored
`5427F12F-…` against a live `28E1CE52-…`.

**The editor gives no sign of it.** It goes on displaying the target's name and
renders it as a resolved token, because the name is stored beside the identifier
as display metadata. Nothing is marked broken.

**And it is fatal: the picker does not fall back to the name.** Run on the Mac
with iCloud access allowed, after deleting and re-importing the target, the probe
stops and asks for a shortcut to be picked again. (An earlier attempt was
confounded — the probe had been denied iCloud access, and failed on permissions
before reaching the question.) So `Run Shortcut`'s behavior does not carry over:
it resolves its `workflowIdentifier` by name, and this picker does not.

The consequence worth guarding against is still the quiet one. If the old
copy is still in the library — renamed, or simply not cleared out — a picker
holding its identifier would mint a link for the **previous build** with nothing
on screen to say so. `verify_links.py` catches that for free, since a link to a
stale build fails the comparison against `dist/<name>.xml`; a second reason it
earns its place.

**Nor can it be pre-seeded by name**, which is how the wrappers get away with
never knowing Brightwheel Attendance's identifier. `Run Shortcut` has a name field
the runtime reads — `WFWorkflowName`, with a `workflowIdentifier` that matches
nothing — and the picker has no equivalent. Three shapes were generated, imported,
and run on the Mac with the target present and iCloud access allowed:

| pre-seeded with | on run |
|---|---|
| a fresh UUID that matches nothing, plus the name | asks for a shortcut |
| the name only, no identifier | asks for a shortcut |
| the name in the identifier slot | asks for a shortcut |

All three validate, import, and display the target's name in the editor, so
nothing short of running one reveals the problem. The identifier must be a real,
live workflow id, and no such id exists before import — the generator cannot
supply one, and neither can any build step.

So `Brightwheel Share Links` saves the three trips through the share sheet but
not the picking, and the picking has to be redone every release.

### Looking the shortcut up at run time avoids the picker entirely

Storing no reference is what fixes it. A probe ran **Get My Shortcuts**, walked
the result with **Repeat with Each**, kept the item whose name matched, and passed
that item to Create iCloud Link as a *variable* instead of a picked value. Each
step was checked on its own:

- **The variable survives import.** Read back off an iOS 27 simulator, the
  `shortcut` parameter still holds
  `{"Value": {"VariableName": "Repeat Item", "Type": "Variable"}, …}` rather than
  being reset to an empty picker.
- **The lookup matches.** On the Mac the match branch ran, and the result was the
  iCloud page for the target.
- **The output is a usable URL.** Building Share Links' exact
  `<li><a href="…">` markup and copying it gave
  `<li><a href="https://www.icloud.com/shortcuts/686774d5…">ZZ Target</a></li>`
  on the clipboard, which is exactly what `update_links.py` reads.

A shortcut coerced to text gives its name, so the match needs no property
lookup: a Text action holding the Repeat Item, then Match Text and Count, the
same pattern `gate()` uses in `build_shortcuts.py`. `Get Shortcut Attributes`
looks like the obvious tool and is not: its `attribute` enum covers only
toggles — share sheet, Apple Watch, menu bar, running when locked — and has no
name.

**Reading a Mac-run shortcut's output: use the clipboard, primed.** A shortcut
run on the Mac can only report back through the screen or the clipboard, and the
screen lies — see the Show Content note below. So have the probe copy its result,
put a sentinel on the clipboard before the run (`printf SENTINEL | pbcopy`), and
read it with `pbpaste` afterwards. The sentinel is what makes a run that copied
nothing look different from a result. And nobody may copy anything between the
run and the read: pasting a note into chat once overwrote a result, which the
sentinel check caught.

One thing looked like a bug and was not. Put the link straight into **Show
Content** and it reads "minted: Shortcuts", followed by the rendered page. That
is only a rich preview. The link's text form is the URL, as the clipboard shows.
Check the clipboard, not what the screen displays.

A fresh import is simply found again on the next run, so nothing needs
re-picking. The one hazard left is duplicates. Re-importing without deleting
leaves `Brightwheel Attendance 1` beside the original, and an unanchored name
match takes both. Anchor the match to the exact name, and treat more than one
match as an error rather than guessing which copy is the new build.



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
click of Add Shortcut — see "Importing on a Mac" below.

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
place and nothing is pressed. Under Simulator.app coordinates are exact once it
is set to *Window → Point Accurate* with *Show Device Bezels* off: the device
screen then starts at the window origin plus a 52pt title bar, at 3 device
pixels per point, and the harness sets both itself. Device Hub offers neither,
so there the screen is measured inside the bezel — see above.

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

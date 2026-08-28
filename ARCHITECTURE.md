# Architecture

## Overview

This repo does not contain shortcuts. It contains a **generator** that emits
them. `build_shortcuts.py` builds the Shortcuts plist for each shortcut as a
Python data structure, and `build.sh` validates, signs, and stages the result.
The `.shortcut` files in `dist/` are build output that happens to be committed.

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
documentation* below for how to extract one.

## Directory structure

```
build_shortcuts.py   Generator. Builds all three shortcuts as plist dicts.
build.sh             Pipeline: generate -> validate -> sign -> stage.
dist/                Gitignored build output: unsigned .xml + signed .shortcut.
roster.json          Gitignored. Who this build is for: guardian, room, children.
roster.example.json  The shape of a roster, with placeholder ids.
assets/              The first-run setup diagrams, and the screenshots behind them.
make_diagrams.py     Redraws those diagrams. Needed only to regenerate them.
dist-debug/          Gitignored. Same shortcuts with credentials baked in.
dist-test/           Gitignored. Test builds, pointed at the mock API.
test.sh              Pipeline: build against a mock -> install -> run -> assert.
tests/               The integration suite and the simulator harness.
.env.example         Template for the debug build's credential file.
README.md            What this is and how to set it up.
ARCHITECTURE.md      This file: why it looks the way it does.
TESTING.md           How the simulator suite works, and what it cannot cover.
```

Inside `build_shortcuts.py`:

| Symbol | Role |
|---|---|
| `ts()` / `attach()` / `var()` / `out()` | Serialization helpers. `ts()` computes `attachmentsByRange` offsets so placeholders always line up. |
| `dict_field()` / `kv()` / `kv_dict()` | `WFDictionaryFieldValue` builders for HTTP headers and JSON bodies. |
| `act()` / `comment()` | Bare action constructors. |
| `gate()` | Text → Match Text → Count. The presence primitive; see below. Pass `name=None` to read a named variable instead of an action output. |
| `SETUP` / `ENV_KEYS` | The import-time values, and their `.env` names for debug builds. |
| `load_roster()` | Reads `roster.json` into module state before `build()` runs. Identifiers live there rather than in the source. |
| `build()` | `Brightwheel Attendance` — everything except the direction. |
| `build_wrapper()` | The two trigger carriers, parameterised by direction. Each also carries its own first-run setup guide. |
| `--api-base` / `--env-file` | Overrides used only by the integration tests, so a test build cannot reach the real API. See `TESTING.md`. |

## Data flow

**Build:** `build.sh` runs the generator into a target directory, then for each
shortcut runs `validate-shortcut` against iOS 27, signs it with `sign-shortcut`,
and copies the signed file back beside its XML. Any validator error except two
named waivers aborts the build.

**Runtime**, for `Brightwheel Attendance`:

```
Shortcut Input is "in" or "out"?
  ├─ yes ──> Direction                  (a wrapper handed it in)
  └─ no  ──> Choose from Menu           (run by hand)
Direction  ->  Wanted In, Checked In Value, Verb, Already Word

Get Stored Content (shared)  ->  Session Token
GET /users/me
  └─ body contains E1200 ──> sign-in loop, up to 5 passes:
         POST /sessions/start          (sends, and on later passes resends, a code)
         Ask for the 6-digit code      (empty or "resend" falls through to the next pass)
         POST /sessions with 2fa_code  -> token -> Store Content

Repeat 2, but only while Send Needed:
    Get Stored Content: school code
      └─ nothing stored ──> alert, Scan Code, store it
    Match Text  ->  School Secret, School Id
    Repeat for each child name:
        Repeat Item 2 -> Child Name -> (one If per child) -> Child Id
        GET /students/{id}/activities?page_size=1&action_type=ac_checkin
          └─ already the way this run wants ──> notify "no change", send nothing
          └─ otherwise ──> POST /checkins/
                 ├─ event_date        ──> notify success
                 ├─ stale school code ──> forget it, ask for another pass
                 └─ anything else     ──> notify the API's own error
```

## Key design decisions

**Responses are matched as text, not parsed as JSON.** Nothing on the check path
uses Detect Dictionary or Get Dictionary Value. Fed a Get Contents of URL output
they never once produced a working branch — the `/users/me` probe read its own
401 as "no error" and so never signed in. Presence is measured instead with
`gate()`: Text → Match Text → Count → numeric If. Counting rather than testing
also sidesteps empty strings satisfying "has any value".

| Pattern | Means |
|---|---|
| `E1200` | token expired or absent |
| `"token"\s*:\s*"([^"]+)"` | sign-in succeeded |
| `"state"\s*:\s*"1"` / `"2"` | child is already checked in / out |
| `"event_date"` | a check-in record was really created |
| `"secret"\s*:\s*"The given secret` | the stored school code has gone stale |
| `"secret"\s*:\s*"([^"]+)"` / `"school_id"…` | pulling those out of the scanned code |

There is now **no Detect Dictionary, Get Dictionary Value, or Dictionary action
anywhere in the shortcut** — verified on the built output.

That pair broke four separate things before it was fully removed: the auth probe
read its own 401 as "no error"; the success test reported a check-in that never
happened; the school code yielded an empty `school_id` and `E1205 "You must
specify the school"`; and the roster lookup yielded an empty target and `E1204
"The requested resource could not be found"`.

Each time it was removed from the place that had just failed and left where it
seemed harmless. The rule is **never**, not "not there". Its failure mode is what
makes it dangerous: it does not error, it returns empty, and the empty value
travels until something far away complains about the wrong thing.

**The school code is scanned, not typed.** `scanbarcode` returns decoded text
in-process, so the QR code became something the check shortcuts read directly
rather than a separate helper shortcut feeding the clipboard. The whole scanned
blob is stored under one key and re-parsed on later runs, so `secret` and
`school_id` share a single source and one code path extracts both. Scanning is
interactive, so it sits behind an "is anything stored" check — the same shape as
the sign-in prompt, and for the same reason.

A rotated code self-heals **within the run**. Brightwheel rejects a stale secret
with a distinct error, matched against the response text; the stored code is
deleted and a second pass requested. The next pass finds nothing stored and
scans, so **"first run" and "the code rotated" are the same branch** — deleting
the stored code is what turns one into the other. The pattern
(`"secret"\s*:\s*"The given secret`) was checked against live responses for a
stale secret, a wrong check-in code, an empty body and an expired token, and
matches only the first.

**Idempotency, failing open.** Each pass reads the child's latest check-in event
and skips anyone already in the state this run wants, so a repeated trigger cannot record
a second arrival. If the state cannot be read at all the request is sent anyway:
a duplicate event is recoverable, a silently skipped arrival is not.

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

It is captured into `Child Name` immediately, so the numbered variable appears
exactly once in the whole shortcut. Change the nesting and that is the only line
to revisit.

The child's name is the loop item; the id comes from **one plain If per child**
comparing that name against a literal. A roster Dictionary read with
`Get Dictionary Value` was tried first and returned nothing, leaving the target
empty and every check-in answered `E1204 "The requested resource could not be
found"`.

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
(no school code is stored), and the direction menu (no input, so a person is
driving). An earlier blanket ban on interactive actions was too broad — the rule
belongs on the happy path, not the recovery paths.

**No time logic.** iOS 27 attaches triggers to the shortcut itself, and the
trigger carries its own time range, so a weekday-and-hour guard inside the
shortcut was redundant and was removed. The trade-off: that guard also covered
Siri, which cannot be disabled and which a trigger's time range does not
constrain. A stray *repeat* is still harmless thanks to idempotency, but a stray
run of the *opposite* direction now writes real attendance.

**One shared session token.** Stored content uses
`WFStoredContentGlobalValue = true`, so whichever shortcut signs in first covers
the other. With per-shortcut storage each needed its own interactive sign-in, and
because a background trigger cannot answer the code prompt, the second could only
be seeded by running it by hand at a moment its action happened to be wanted. The
shared store syncs through iCloud; what it holds is a session token, not the
password.

**Direction is structural, carried by two thin wrappers.** `Brightwheel Attendance`
holds all the logic and takes its direction from Shortcut Input; `Brightwheel
Check In` and `Brightwheel Check Out` do two things — say which way, and call
Attendance — and are what the triggers attach to. They are 18 actions rather
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
anyone anywhere by itself — it can only open a menu, and cancelling sends
nothing. A wrapper never reaches the menu, because it hands in a direction.

**Debug builds are isolated by construction.** `./build.sh --debug` bakes `.env`
values in and emits no import questions. Such a build contains a real password,
so `--debug` writes only to `dist-debug/`, and both that directory and `.env` are
gitignored. The isolation is structural rather than a reminder.

**`dist/` is committed** so a checkout always carries an installable build and
the XML diff shows what a generator change did. Two caveats: every build mints
fresh UUIDs, so a no-op rebuild still churns; and `shortcuts sign` is not
deterministic, so the signed blobs always differ. Review the `.xml` diff, never
the `.shortcut`.

---

# Building Shortcuts programmatically

Notes that outlived this project. The toolchain is the **shortcuts-playground**
skill, which supplies `validate-shortcut`, `sign-shortcut`, `resolve-icon`, a
bundled ToolKit snapshot, and a library of real shortcuts under
`golden-shortcuts/`.

## Ground truth beats documentation

The single most useful technique found here, and the one to reach for first.

**An iCloud share link exposes an unsigned plist.** Ask for a link to a working
shortcut and read it directly:

```bash
curl -s https://www.icloud.com/shortcuts/api/records/<id> -o rec.json
# fields.shortcut.value.downloadURL      -> unsigned binary plist, readable
# fields.signedShortcut.value.downloadURL -> AEA1, encrypted, not readable
# fields.icon_glyph / fields.icon_color   -> right there in the JSON
python3 -c "import plistlib;print(plistlib.load(open('shared.bin','rb')))"
```

Signed `.shortcut` files really are opaque — `AEA1` magic bytes, and `aa extract`
fails with "Failed to create decryption stream" — so a shared *file* is useless.
A shared *link* is not.

Every serialization bug in this project's history was a case where a verified
sample would have given the answer in one step instead of a test-on-device round
trip. When a plist shape is uncertain, ask for a link before inferring.

The `golden-shortcuts/` library is the other source of truth, and it is worth
grepping before trusting prose: if no golden shortcut uses a pattern, treat that
pattern as unproven.

## Where the bundled docs are wrong

Each of these passes the validator, imports cleanly, and then misbehaves.

| Topic | Documented | Actually works |
|---|---|---|
| Format Date custom pattern | `WFDateFormat="Custom"` + pattern in `WFDateFormatString` | Pattern goes **in `WFDateFormat`**, with `WFDateFormatStyle="Custom"` and no `WFDateFormatString` |
| `WFRequestVariable` | ACTIONS.md File Body example shows `WFTextTokenString` | Must be a `WFTextTokenAttachment` (SKILL.md rule 9 is the correct one) |
| Multi-condition If | CONTROL_FLOW.md shows numeric rows with `WFNumberValue` | Numeric rows import empty and red; use Match Text + Count + a numeric If |
| "Open Code Scanner" | Grounding catalog lists `com.apple.BarcodeScanner.BarcodeScannerIntent` under that display name | Plain `is.workflow.actions.openapp` with `WFAppIdentifier` + `WFSelectedApp`. (This project no longer opens the app at all — `scanbarcode` replaced it — but the lesson stands.) |
| Glyph numbers | `shortcuts-official-glyph-mapping.json` | Right for many entries, but `59692` documented as `circledDownArrow` renders as a checkmark |
| `scanbarcode` | macOS-only, and requires `imageFile` | Works on iOS 27 as a **live scanner**: `WFScanCodeActionMode = 0`, no image input, output named `QR/Barcodes` |

The Format Date one is the nastiest: the validator only enforces its
`WFDateFormatString` rules when `WFDateFormat == "Custom"`, so the wrong shape is
precisely the shape it never checks, and it yields an **empty string** rather than
an error.

The `scanbarcode` row is the clearest case of the bundled ToolKit snapshot being
*incomplete* rather than wrong: `toolkit-v78-ios27-tool-ids.json` has no entry,
so the validator concludes macOS-only, and the documented `imageFile` parameter
describes the macOS scan-an-image variant. The iOS action is a live camera
scanner that returns decoded text directly. Absence from the snapshot is not
evidence of absence on the device.

**AppIntents need an `AppIntentDescriptor`**, and no verified example of one
exists in the catalogs or the golden library. An invented descriptor does not
degrade gracefully — it makes the *entire shortcut* fail to import with "contains
features not supported on this device". Prefer an ordinary
`is.workflow.actions.*` action whenever one exists, even when an AppIntent shares
its display name.

## Actions with no verified example

Treat these as unproven until a sample turns up. Each appears in the golden
library with **empty parameters**, or not at all, so their wiring is guesswork:

| Action | Status |
|---|---|
| `Get Item from List` | present with no parameters — no example of `WFItemSpecifier` or any index |
| `Split Text` | present with no parameters — no example of a separator in use |
| `Run Shortcut` | no golden example; shape recovered from a user's share link |
| AppIntent `AppIntentDescriptor` | no example anywhere; an invented one breaks the whole import |
| Multi-condition `WFConditions` numeric rows | no example; imports red and empty |

Working around a missing shape is usually cheap. Carrying a name and an id
through a loop *looks* like it needs Split Text; one plain If per item does the
same job with primitives that are proven.

## Loops

- **Nested loops renumber the item.** A Repeat with Each inside a `Repeat 2`
  exposes its item as **`Repeat Item 2`**, not `Repeat Item` — a *count*-style
  outer loop shifts the numbering just as a nested Repeat with Each does, which
  `BEST_PRACTICES.md` does not say. Confirmed on device with a probe. Getting it
  wrong fails silently: the item reads empty and everything derived from it is
  empty. Capture the numbered variable into a named one immediately so it appears
  exactly once.
- **An If compares a variable to a *literal*, never to another variable.** To
  compare two runtime values, paste them into one string and match a fixed
  pattern — `11`/`00` versus `01`/`10` for a pair of booleans.
- **Idempotency makes retries free.** If each iteration already skips work that
  is done, an outer retry loop needs no memory of what succeeded.

## Choose from Menu

`WFMenuItems` on the start action and the `WFMenuItemTitle` of each case must
match exactly, and there must be one case per item. A mismatch imports without
complaint and misroutes at runtime.

## Measured on a simulator

Findings from device probes, recorded here because a claim that lives only in a
design document cannot be checked by anyone else.

- **`Get Group` works for `WFGroupIndex` 1 through 5**, and returns **one value
  per match** — a list, newline-joined when coerced to text — not just the first
  match's group. Group 4 returning different values for two students is what
  proves it tracks matches rather than merely accepting the index.
- **`Match Text` honors `^` as start-of-string.** An anchored pattern returned 1
  match where the unanchored one returned 4 on the same body.
- **`Repeat with Each` iterates a `Matches` output**, and the repeat item coerces
  to the matched substring, so `Match Text` *on the item* isolates one record.
- **`Get Group` against a single repeat item does not work.** It stores nothing
  and raises no error.
- **`is.workflow.actions.math` subtracts two runtime values**: `WFInput` and
  `WFMathOperand` both as action-output attachments, `WFMathOperation` `-`, read
  back as `Calculation Result`. This is how to compare two counts; the
  paste-and-match trick below only works for single digits.
- **A numeric `If` accepts a Math output directly.** Feeding
  `Calculation Result` to `WFCondition=2, WFNumberValue="0"` branches correctly:
  a difference of 1 took the greater-than branch, 0 took the else branch. So two
  runtime counts can be compared as Count -> Count -> Math subtract -> If, with
  no intermediate `gate()`.
- **Format Date emits an IANA timezone name.** `WFDateFormatStyle` `Custom` with
  the pattern in **`WFDateFormat`**: `VV` gives `America/Los_Angeles`, `VVVV`
  gives `Los Angeles Time`, `zzzz` gives `Pacific Daylight Time`, `ZZZZZ` gives
  `-07:00`. `DATE_TIME.md` says to set `WFDateFormat` to `Custom` and put the
  pattern in `WFDateFormatString`; that shape returns **empty**, silently.

## Silent failures to design against

Shortcuts rarely errors. It does the wrong thing quietly, so build checks that
distinguish "worked" from "looked like it worked".

- **An empty string satisfies "has any value."** A blank hour passed the guard's
  emptiness check, lost the following numeric comparison, and reported itself as
  "too early" — pointing at the wrong cause entirely. Measure presence with a
  match count.
- **A success test can match its own error body.** Testing for `"checkins"`
  reported both children checked out while nothing was posted, because the
  empty-body error is `422 {"checkins":"cannot process empty checkins"}`. Pick a
  token that appears *only* on success and verify it against real error bodies.
- **Global stored content outlives the shortcut that wrote it.** Deleting a
  shortcut leaves everything it put in the shared store behind; six throwaway
  probes were deleted and all fourteen of their global keys survived. "Delete
  and re-import to reset" is therefore not true for anything stored globally,
  which is where the session token and the school code live.
- **A same-name import is silently skipped.** iOS keeps the old version with no
  warning, which is indistinguishable from a code change that did nothing. Delete
  before importing.
- **Get Contents of URL exposes no HTTP status code.** Success has to be
  determined from the body.
- **Handing off to another app lets the run continue.** A clipboard read after an
  `Open App` sees stale content; a blocking **Show Alert** immediately after the
  hand-off holds the run until the user returns. Worth knowing generally, though
  this project no longer needs it — `scanbarcode` returns the decoded text
  in-process, so the hand-off went away entirely.
- **Gray input chips are normal.** An action showing a gray `Input` chip rather
  than a colored token is displaying an implicit connection to the previous
  action, not a broken wire. Inserting an action between such a pair silently
  redirects the input.
- **A control-flow block must be closed by its own action identifier.** A
  `repeat.each` opened with `WFControlFlowMode=0` and closed with a
  `conditional` at mode 2 does not error, does not warn, and does not run its
  body even once — the actions between the markers are simply skipped. It looks
  exactly like a loop whose collection was empty. Closing a Repeat with a
  Repeat, and an If with an If, is the rule; `build_shortcuts.py:901` is the
  worked example.
- **Dictionary actions return empty rather than failing.** See the prohibition
  above; this is the specific reason they cost four separate debugging rounds.

## iOS 27 automations

Automations are no longer separate objects: a shortcut carries one or more
triggers at the top, which is why the Automation tab's "+" now starts a shortcut.
Triggers are added to the shortcut itself.

They cannot be generated, and a placeholder is not worth emitting. Every
variant of the location triggers requires the placemark — the ToolKit catalog
lists `WFArriveLocation` as the first parameter of both
`enter_location` and `enter_location_between`, and `WFLeaveLocation` likewise —
and it is a `redacted-local-location-token`, a device-specific value only the
on-device picker can produce.

A share link shows the split: both triggers came through in `WFWorkflowTriggers`
with their `WFArriveStartTime` / `WFArriveEndTime` and `WFArriveTimeRange`
intact and **without** any `WFArriveLocation`. The time range travels, the
placemark does not.

**Verified on iOS 27** by building that exact shape — an arrival trigger with a
time range and no placemark — signing it and importing it on a simulator: the
shortcut imports cleanly and **the trigger is silently discarded**.
`ZTRIGGERCOUNT` is 0, `ZTRIGGER` and `ZUNIFIEDTRIGGER` are empty, and the
Automation list says "No Automations". It does not import as a broken
automation you could then fix; there is simply no automation, so a generated
stub would save nobody a step. So **attach triggers last**: re-importing a
rebuilt shortcut replaces it and loses them.

A trigger stub is only possible at all for the 4 of 42 catalogued triggers that
take no parameters — external drive connected, file modified, folder changed,
and Wi-Fi disconnect-from-any. Nothing location- or time-based is among them.

Triggers also report **no output** (`outputTypeIdentifiers: ["none"]`), so a
shortcut cannot tell which one woke it. That is a real absence, not missing
metadata: 13 of the 42 catalogued triggers *do* declare an output, including
message, email, notification and file triggers.

Arrival triggers also require Settings → Privacy & Security → Location Services →
Shortcuts set to **Always**. "While Using the App" makes a geofence silently
never fire.

## Icons

An icon is a glyph number plus a color, and **nothing else**. There is no
custom-image escape hatch: `WFWorkflowIconImageData` exists as a key in
WorkflowKit, but setting it does nothing. With a glyph number alongside it the
glyph wins; with the image alone the shortcut imports with `ZGLYPHNUMBER = 0`
and draws an empty tile. The `ZSHORTCUTICON` table has columns for background
color, glyph number and the owning shortcut — there is nowhere for an image to
go.

There is also no authored description. The whole `WFWorkflow*` key set has no
Description, Subtitle or Summary; the "About This Shortcut" block on the import
sheet is derived from the actions, which is where "Can Run When Locked" comes
from. So the import sheet shows a name, an icon, and the setup questions —
nothing else you can write.

`data/shortcuts-official-glyph-mapping.json` is not trustworthy: it calls
`59692` `circledDownArrow`, and it renders as a chevron. Rather than guess,
measure — install one shortcut per candidate number on a simulator and look.
This run of ten, read off an iOS 27 library:

| Number | Renders as | | Number | Renders as |
|---|---|---|---|---|
| 59690 | ✓ checkmark | | 59695 | ⏩ fast-forward |
| 59691 | $ in a circle | | 59696 | ‹ chevron-left |
| 59692 | ⌄ chevron-down | | 59697 | i info |
| 59693 | ⤓ download tray | | 59698 | π |
| 59694 | € euro | | 59699 | ▶ play |

Consecutive numbers are unrelated to each other, so there is no neighbourhood to
search — sweeping to find a *specific* idea is wasteful. To pick a particular
icon, choose it in the on-device icon picker and then read
`ZSHORTCUTICON.ZGLYPHNUMBER` straight out of `Shortcuts.sqlite`; an iCloud share
link works too, since the record JSON exposes `icon_glyph` without a download.

What the shortcuts use today, all measured on a simulator rather than taken
from a name: Attendance is `62329` — a ring of petals, close to the Brightwheel
logo — on pink, `3980825855`. The wrappers are a plane arriving, `62022` on green `4292093695`, for Check In,
and departing, `62021` on red `4282601983`, for Check Out — Brightwheel's own
colors. (Not `62466`/`62467`, which are a plane on a runway, a heavier and
less legible pair.) Both numbers are waived in `build.sh`: the validator checks
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

Color values are palette keys, not RGB: `4292093695` renders green, not the
yellow or magenta its bytes suggest. `resolve-icon --color <name>` maps a name
to the right integer, and it has been right every time it was checked — pink,
green and red all rendered as named.

**The fastest way to choose an icon is the device's own picker**, not a sweep.
Open a shortcut → the name control → Choose Icon: it has a "Search Symbols"
field over the whole catalog and the 15 colors. Pick one, then read
`ZSHORTCUTICON.ZGLYPHNUMBER` out of `Shortcuts.sqlite` to learn its number. That
is how `62020` (sunrise) and `62019` (sunset) were identified — searching for
them by name through `resolve-icon` finds nothing, because its vocabulary is
much smaller than the picker's.

## Showing an image, and the first-run guide

Each wrapper shows a diagram the first time it runs, explaining how to attach
its own trigger — Arrive for Check In, Leave for Check Out. It lives in the
wrappers rather than in Attendance on purpose: **Attendance is complete on its
own**, and the wrappers are optional extras that automate it, so setup
instructions for an optional extra do not belong in the shortcut that extra is
optional to. Two wrappers also means two different guides, which is what the
job actually needs.

There is no image parameter on any alert. Show Alert takes a title and a
message and nothing else. What works is carrying the PNG as base64 in a Text
action and decoding it at runtime:

    Text (base64)  ->  Base64 Encode [mode: Decode]  ->  Show Content

Three things that are easy to get wrong:

- **Use Show Content (`is.workflow.actions.showresult`), not Quick Look.**
  Quick Look renders the image but titles the sheet with the raw base64 string.
  Show Content has no title bar at all.
- **Something must follow Show Content.** Left as the last action, the image
  becomes the shortcut's own output, and handing an image back to the caller
  needs consent — *"Allow … to output 1 image?"* — on the very run that is
  trying to be helpful. Any following action displaces it;
  `is.workflow.actions.nothing` says so explicitly.
- **Store Content's `WFInput` must be a `WFTextTokenString` carrying exactly
  one object placeholder.** A literal string imports as an empty Content
  parameter, so the marker that records "already shown" has to come from a Text
  action rather than being written inline. Silent if you get it wrong: the gate
  never closes and the guide shows every run.

The marker is stored with `WFStoredContentGlobalValue: False`, scoping it to
the shortcut, so each wrapper explains itself once and neither speaks for the
other. Presence is measured with a match count, because an empty string still
satisfies "has any value".

Quantized to 64 colors the diagrams are ~85 KB each, ~110 KB as base64, which
takes a wrapper from 4 KB to about 113 KB. Flat UI art loses nothing at 64
colors.

## Practical notes

- **Comment discipline is enforced.** The validator requires a Comment
  immediately before every control-flow start. A helper that appends actions must
  be called *before* the comment, not between it and the `If`.
- **UUIDs must look random.** Repeating-hex placeholders are a hard error. Mint
  them with `uuidgen`.
- **Waive validator rules by name, never wholesale.** `build.sh` waives two, each
  with its reason recorded: the removed attribution comment, and both
  `scanbarcode` complaints, which a device-exported shortcut disproves.
  Everything else stays fatal. That guard has caught real regressions, including a
  malformed control-flow block and a genuinely empty parameter.
- **Signing is flaky, not broken.** `shortcuts sign` intermittently returns
  "Failed to modify some records" or a 500; the wrapper retries after converting
  to a binary plist. Re-run before investigating the plist.

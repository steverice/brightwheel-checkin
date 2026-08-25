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
than counted, the two check shortcuts cannot drift apart, and a change is a diff
in one Python file instead of an untracked edit on a phone.

The cost is that **the phone is downstream**. Anything edited in the Shortcuts
app is overwritten by the next build, and the only values meant to be changed on
the device are the import-time Setup answers. When a device-side edit is worth
keeping, it gets folded back into the generator — see *Ground truth beats
documentation* below for how to extract one.

## Directory structure

```
build_shortcuts.py   Generator. Builds both shortcuts as plist dicts.
build.sh             Pipeline: generate -> validate -> sign -> stage.
dist/                Committed build output: unsigned .xml + signed .shortcut.
dist-debug/          Gitignored. Same shortcuts with credentials baked in.
.env.example         Template for the debug build's credential file.
README.md            What this is and how to set it up.
ARCHITECTURE.md      This file: why it looks the way it does.
```

Inside `build_shortcuts.py`:

| Symbol | Role |
|---|---|
| `ts()` / `attach()` / `var()` / `out()` | Serialization helpers. `ts()` computes `attachmentsByRange` offsets so placeholders always line up. |
| `dict_field()` / `kv()` / `kv_dict()` | `WFDictionaryFieldValue` builders for HTTP headers and JSON bodies. |
| `act()` / `comment()` | Bare action constructors. |
| `gate()` | Text → Match Text → Count. The presence primitive; see below. Pass `name=None` to read a named variable instead of an action output. |
| `SETUP` / `ENV_KEYS` | The import-time values, and their `.env` names for debug builds. |
| `build()` | Both shortcuts, parameterised by direction. |

## Data flow

**Build:** `build.sh` runs the generator into a target directory, then for each
shortcut runs `validate-shortcut` against iOS 27, signs it with `sign-shortcut`,
and copies the signed file back beside its XML. Any validator error except two
named waivers aborts the build.

**Runtime**, for a check shortcut:

```
Get Stored Content (shared namespace)  ->  Session Token
GET /users/me
  └─ body contains E1200 ──> sign-in loop, up to 5 passes:
         POST /sessions/start           (sends, and on later passes resends, a code)
         Ask for the 6-digit code       (empty or "resend" falls through to the next pass)
         POST /sessions with 2fa_code   -> token -> Store Content
Repeat 2, but only while Send Needed:
    Get Stored Content: school code
      └─ nothing stored ──> alert, Scan Code, store it
    Detect Dictionary  ->  School Secret, School Id
    Repeat for each child name:
        roster lookup: name -> Child Id
        GET /students/{id}/activities?page_size=1&action_type=ac_checkin
          └─ already in the target state ──> notify "no change", send nothing
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

The same actions work fine on the school code, where Detect Dictionary parses the
**text** handed back by Scan Code. The failure is specific to an already-parsed
HTTP response, not to the actions themselves.

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
contains exactly one `POST /checkins/`. It also came out smaller — 138 actions
unrolled, 125 looped, retry included.

`Repeat Item` is the child's **name**, and the id comes from a roster Dictionary
looked up with a **tokenized `WFDictionaryKey`** bound to Repeat Item. That is
the one shape here with a verified example behind it, found in the golden
library. `Get Item from List` and `Split Text` were the obvious alternatives for
carrying a name and id together, and both appear in the golden shortcuts with
*empty* parameters — no worked example of how to index or separate anything. That
is the whole reason the roster is a dictionary keyed by name rather than a list
of `name|id` strings.

**No interactive actions on the happy path.** These run from background arrival
triggers, which cannot answer a prompt on a locked phone. Credentials are
import-time Setup questions rather than first-run prompts, and every outcome is
a notification. The one prompt — the 2FA code — sits on the recovery branch,
which only runs once the token has already expired, i.e. when that run was
failing regardless. An earlier blanket ban on interactive actions was too broad;
the rule belongs on the happy path, not the recovery path.

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
Check In` and `Brightwheel Check Out` are four actions each — a Text action and a
Run Shortcut — and are what the triggers attach to.

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

Running `Brightwheel Attendance` on its own stops with a notification, since its input
is neither `in` nor `out`. That also makes it safe to have in the library: saying
its name to Siri cannot check anyone anywhere.

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
| "Open Code Scanner" | Grounding catalog lists `com.apple.BarcodeScanner.BarcodeScannerIntent` under that display name | Plain `is.workflow.actions.openapp` with `WFAppIdentifier` + `WFSelectedApp` |
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
- **Gray input chips are normal.** Detect Dictionary and Get Dictionary Value show
  a gray `Input` / `Dictionary` chip for an implicit connection to the previous
  action, not a broken wire. Inserting an action between such a pair silently
  redirects the input.

## iOS 27 automations

Automations are no longer separate objects: a shortcut carries one or more
triggers at the top, which is why the Automation tab's "+" now starts a shortcut.
Triggers are added to the shortcut itself.

They cannot be generated. `WFArriveLocationTrigger` is authorable and its
`enter_location_between` variant takes `WFArriveLocation`, `WFArriveStartTime`
and `WFArriveEndTime`, but `WFArriveLocation` is a
`redacted-local-location-token` — a device-specific placemark that has to come
from the on-device picker — and an incomplete trigger header imports as an
*invalid* automation. So **attach triggers last**: re-importing a rebuilt
shortcut replaces it and loses them.

Arrival triggers also require Settings → Privacy & Security → Location Services →
Shortcuts set to **Always**. "While Using the App" makes a geofence silently
never fire.

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

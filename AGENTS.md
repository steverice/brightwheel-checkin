# AGENTS.md

Instructions for AI coding agents working on this codebase.

## Project structure

```
build_shortcuts.py     The generator: Brightwheel Attendance and the two wrappers, as plist dicts;
                       checks, validates, and signs them through shortcut-forge.
build.sh / release.sh  Build into dist/; cut a GitHub release with the zip.
test.sh                The integration suite: real shortcuts on a simulator against a mock API.
tools/                 build_publisher.py (Brightwheel Share Links), verify_links.py, update_links.py.
tests/                 test_attendance.py (the runner), mock_brightwheel.py, testbuild.py, fixtures/,
                       test_publisher.py (plain pytest).
make_*.py              Redraw the site's icons, the setup diagrams, and the link-preview card.
docs/                  The GitHub Pages site.
assets/                The setup diagrams the wrappers carry, and the screenshots behind them.
```

This is a flat script layout on purpose: the scripts are the documented
interface (`./build.sh`, `uv run python tools/verify_links.py`), named by the
README, the release script, and the site. Do not move them into a package.

## Key conventions

**Nothing branches on a dictionary value.** Every branch goes through
`ActionList.count_matches()` (Text → Match Text → Count → numeric If). Reading a
dictionary value as text is fine; deciding a branch on one is not. The reasons
are in `ARCHITECTURE.md`, "Key design decisions", and cost four debugging rounds.

**Only `LESS_THAN` and `GREATER_THAN` conditions.** Other comparisons are not
proven on device. Two runtime numbers are compared by pasting them together and
matching a fixed pattern, or with a Math subtract.

**A Comment before every control-flow start**, and every block closed by the
same kind of action that opened it. `shortcut_forge_lib.checks` refuses the
second; the validator refuses the first.

**Exit only at the top level.** Inside the attempt Repeat, guards set a flag
(`Roster OK`, `Send Needed`) and let the run fall through.

**Waive validator rules by name, never wholesale**, in `WAIVED` in
`build_shortcuts.py`, each with its reason. A red build is a real problem.

**Every outcome is a notification.** A missed check-in has consequences at the
school, so the shortcut never fails silently.

**Terminal output** from the scripts goes through the shared Rich console in
`console.py`, never `print()`. The generator's functions stay output-free; only
the `__main__` blocks and the tools speak.

## Architecture rules

- **The phone is downstream.** Never edit a shortcut on the device; the next
  build overwrites it. A device-side finding gets folded into the generator.
- **Direction is structural.** Which wrapper ran decides in or out. Nothing
  infers it from the clock, so a trigger's hours can be edited on the device
  without sending the wrong direction.
- **The roster is read at run time.** No child, room, school, or credential is
  in a clean build. `--debug` and `--env-file` builds bake credentials in and
  go only to gitignored directories.
- **A test build cannot reach Brightwheel.** `--api-base` points at the mock,
  `--env-file tests/fixtures/test.env` bakes fake values. Never add a code path
  from a test to the live API.
- **Delete before importing.** iOS silently skips a same-name import; macOS
  installs a numbered second copy. Both look like a change that did nothing.
- **Mint iCloud links only from a library with none of your own copies.** iCloud
  sync for Shortcuts stays off on the release Mac. A link cannot be revoked.

## Code quality standards

Per the `project-conventions` skill's python layer, with these project
relaxations in `pyproject.toml`: `S603`/`S607` (fixed argv to tools found by
name), `INP001` (flat scripts, not packages), and the usual test relaxations.
Do not add more without raising it.

## Testing

```
make test          # tests/test_publisher.py; no simulator, no network
make test-integ    # ./test.sh — needs an iOS 27 simulator and Accessibility permission
./test.sh --erase  # after changing the generator: an installed shortcut is never replaced
```

The suite asserts on the mock's recorded traffic, not on notifications.
`test_setup_questions_commit_their_answers` is a known-broken canary for an iOS
27 regression and reports KNOWN rather than failing; drop its marker the day it
passes. Before changing anything that affects the built plist, capture a baseline
build and compare with UUIDs normalized; every build mints fresh UUIDs, so a
plain diff is noise.

## Commits and releases

Conventional commits + gitmoji; the hook adds the emoji, so never type it. Run
`make check` before every commit and `make test-integ` after a generator change.
Never bump the version locally. `./release.sh` drafts by default; publishing is
public and hard to take back.

## External tool dependencies

| Tool | Purpose | Required |
|---|---|---|
| `validate-shortcut`, `sign-shortcut` | from the shortcuts-playground plugin, via shortcut-forge | for any build |
| `xcrun simctl`, Device Hub | the integration suite | for `test.sh` and `verify_links.py` |
| `gh` | creating the release | for `release.sh` |
| `shortcuts` (macOS) | running Brightwheel Share Links headless | for refreshing the page's links |

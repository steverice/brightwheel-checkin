#!/usr/bin/env python3
"""Refuse to mint iCloud links from a library that would publish the wrong thing.

Run this before "Brightwheel Share Links", and let a non-zero exit stop the
release:

    uv run python tools/check_library.py
    uv run python tools/check_library.py --dist dist --database ~/Library/Shortcuts/Shortcuts.sqlite

The publisher already refuses a duplicate, a numbered copy, or a missing name.
It cannot see the two things that actually cost something, because both look
perfectly well-formed to it:

**A configured copy.** The publisher links whatever it finds under each name
and cannot tell a clean build from the one you use every day — the one holding
your Brightwheel password, baked in if it is a debug build and stored as your
setup answers if it is not. Every check would pass, and a link cannot be
revoked afterward.

**A stale build.** An iCloud link is a snapshot, so the point of re-minting is
to carry the new code. Nothing downstream compares the installed copy to what
was built, and when v1.5.0 was cut the library was still holding v1.4.0 — 317
actions where the new build has 339. The page would have been updated with
three fresh links to the bug the release fixed.

So this reads the Shortcuts database rather than the screen, and compares what
is installed against the plists in `dist/`. It reports every problem it finds,
not just the first, because whoever is about to fix one wants to know about the
rest before importing again.
"""

from __future__ import annotations

import argparse
import plistlib
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import argcomplete

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from console import error, info, success  # noqa: E402
from update_links import NAMES  # noqa: E402

DATABASE = Path.home() / "Library" / "Shortcuts" / "Shortcuts.sqlite"
PREFIX = "Brightwheel"

# An address is the one credential with a shape worth matching. A password is
# just text, but no clean build carries an email anywhere, so finding one means
# either an answered setup question or a debug build's baked-in values.
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# What an *unanswered* import question carries, measured against a clean import
# on 2026-09-15. An answer arrives as an extra key, and Apple's name for it is
# not documented — so treat any key outside this set as the answer rather than
# guessing which one it is.
QUESTION_KEYS = frozenset({"ActionIndex", "Category", "DefaultValue", "ParameterKey", "Text"})

# `Brightwheel Attendance 1`, which is what a second import leaves behind.
NUMBERED = re.compile(r"^(?P<base>.+?) (?P<n>\d+)$")


@dataclass(frozen=True)
class Installed:
    """One shortcut as the library holds it."""

    name: str
    action_count: int
    answered_questions: int
    credential_text: bool


def _count_actions(blob: bytes | None) -> int:
    if not blob:
        return 0
    try:
        parsed = plistlib.loads(blob)
    except (plistlib.InvalidFileException, ValueError):
        return 0
    if isinstance(parsed, dict):
        return len(parsed.get("WFWorkflowActions", []))
    return len(parsed) if isinstance(parsed, list) else 0


def _count_answers(blob: bytes | None) -> int:
    if not blob:
        return 0
    try:
        parsed = plistlib.loads(blob)
    except (plistlib.InvalidFileException, ValueError):
        return 0
    if not isinstance(parsed, list):
        return 0
    return sum(1 for q in parsed if isinstance(q, dict) and any(v for k, v in q.items() if k not in QUESTION_KEYS))


def read_library(database: Path | str, prefix: str = PREFIX) -> list[Installed]:
    """Every shortcut whose name starts with `prefix`, as the database has it.

    Opened read-only and through a URI, so a running Shortcuts.app is neither
    disturbed nor able to disturb the read.
    """
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ZSHORTCUT.ZNAME, ZSHORTCUT.ZIMPORTQUESTIONSDATA, ZSHORTCUTACTIONS.ZDATA "
            "FROM ZSHORTCUT LEFT JOIN ZSHORTCUTACTIONS ON ZSHORTCUTACTIONS.Z_PK = ZSHORTCUT.ZACTIONS "
            "WHERE ZSHORTCUT.ZNAME LIKE ? ORDER BY ZSHORTCUT.ZNAME",
            (f"{prefix}%",),
        ).fetchall()
    finally:
        con.close()

    found = []
    for name, questions, actions in rows:
        blobs = [b for b in (questions, actions) if b]
        found.append(
            Installed(
                name=name,
                action_count=_count_actions(actions),
                answered_questions=_count_answers(questions),
                credential_text=any(EMAIL.search(b) for b in blobs),
            )
        )
    return found


def expected_counts(dist: Path, names: list[str]) -> dict[str, int]:
    """How many actions each built plist has, by name."""
    counts = {}
    for name in names:
        path = dist / f"{name}.xml"
        if path.exists():
            counts[name] = _count_actions(path.read_bytes())
    return counts


def problems(installed: list[Installed], expected: dict[str, int]) -> list[str]:
    """Every reason this library must not be published from."""
    found: list[str] = []
    seen = Counter(i.name for i in installed)

    for name in expected:
        if seen[name] == 0:
            found.append(f"{name} is missing from the library")
        elif seen[name] > 1:
            found.append(f"{name} has {seen[name]} copies; the publisher cannot tell which is the new build")

    for shortcut in installed:
        numbered = NUMBERED.match(shortcut.name)
        if numbered and numbered.group("base") in expected:
            found.append(
                f"{shortcut.name} is a numbered copy left by a second import; delete it and the one it shadows"
            )
        if shortcut.name not in expected:
            continue
        if shortcut.answered_questions:
            found.append(
                f"{shortcut.name} has {shortcut.answered_questions} answered setup question(s), "
                f"so it is a configured copy and may carry a password"
            )
        if shortcut.credential_text:
            found.append(
                f"{shortcut.name} carries an email address, so it is a configured or debug copy, not a clean build"
            )
        want = expected[shortcut.name]
        if shortcut.action_count != want:
            found.append(
                f"{shortcut.name} has {shortcut.action_count} actions but the build in dist/ has {want}; "
                f"the library is holding a different build"
            )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", type=Path, default=DATABASE, help="the Shortcuts database to read")
    parser.add_argument("--dist", type=Path, default=REPO / "dist", help="the build to compare the library against")
    parser.add_argument("--prefix", default=PREFIX, help="only look at shortcuts whose name starts with this")
    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if not args.database.exists():
        error(f"no Shortcuts database at {args.database}")
        return 1

    expected = expected_counts(args.dist, NAMES)
    missing = [n for n in NAMES if n not in expected]
    if missing:
        error(f"{args.dist} has no build for: {', '.join(missing)} — run ./build.sh first")
        return 1

    installed = read_library(args.database, prefix=args.prefix)
    info(f"library holds {len(installed)} shortcut(s) named {args.prefix}*; comparing against {args.dist}")
    for shortcut in installed:
        info(f"  {shortcut.name}: {shortcut.action_count} actions, {shortcut.answered_questions} answered")

    found = problems(installed, expected)
    if found:
        for problem in found:
            error(problem)
        error("not safe to mint links from this library")
        return 1

    success(f"library holds exactly the build in {args.dist}, with nothing configured — safe to mint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

**A dev build.** `./build.sh --debug` bakes `.env` straight in and emits no
setup questions at all, which is the whole reason a clean build carries no
credentials: it asks instead. So the import-question count tells the two apart —
three on a clean Attendance, zero on a debug one — and comparing it is what
makes "never share a dev build" an assertion rather than a rule to remember.

That same count is the only thing that can see a copy which **lost** its
questions on import. `shortcut-forge/docs/simulator-harness.md` records a link
that arrived with zero where its siblings had three, the one difference being
that its clicks were synthesized rather than made by a person. Such a copy
installs in one tap and leaves `not set` in the email, password and check-in
code, with no error and no prompt; its action count is unchanged, so nothing
else here would notice.

So this reads the Shortcuts database rather than the screen, and compares what
is installed against the plists in `dist/`. It reports every problem it finds,
not just the first, because whoever is about to fix one wants to know about the
rest before importing again.

**Everything here fails closed.** A build missing from `dist/` is refused rather
than skipped, and a blob that will not parse is refused rather than counted as
zero — zero actions and zero questions are what a *wrong* build looks like, so
neither may be what "I could not read it" produces. The first version of this
file got both wrong, and reported a clean library for a stale, configured,
credential-bearing copy.
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
    action_count: int = 0
    question_count: int = 0
    answered_questions: int = 0
    credential_text: bool = False
    # A blob that would not parse. It must never read as "zero of everything":
    # zero actions and zero questions are what a *wrong* build looks like, so
    # failing to read has to be its own refusal rather than a quiet count.
    unreadable: bool = False


@dataclass(frozen=True)
class Expected:
    """What the built plist in `dist/` says a clean copy should look like."""

    actions: int
    questions: int


class UnreadableError(Exception):
    """A blob that is not a plist this tool understands."""


def _load(blob: bytes) -> object:
    try:
        return plistlib.loads(blob)
    except (plistlib.InvalidFileException, ValueError, EOFError, TypeError) as exc:
        raise UnreadableError(str(exc)) from exc


def _count_actions(blob: bytes | None) -> int:
    if not blob:
        return 0
    parsed = _load(blob)
    if isinstance(parsed, dict):
        return len(parsed.get("WFWorkflowActions", []))
    return len(parsed) if isinstance(parsed, list) else 0


def _count_questions(blob: bytes | None) -> int:
    """How many setup questions the copy carries, answered or not.

    A clean Attendance has three. A `--debug` build has none, because it bakes
    the credentials in instead of asking — so this is what tells a dev build
    from a release one. It is also the only thing that can see a copy that lost
    its questions on import, since that leaves the action count untouched.
    """
    if not blob:
        return 0
    parsed = _load(blob)
    return len(parsed) if isinstance(parsed, list) else 0


def _count_answers(blob: bytes | None) -> int:
    if not blob:
        return 0
    parsed = _load(blob)
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
        credential = any(EMAIL.search(b) for b in blobs)
        try:
            found.append(
                Installed(
                    name=name,
                    action_count=_count_actions(actions),
                    question_count=_count_questions(questions),
                    answered_questions=_count_answers(questions),
                    credential_text=credential,
                )
            )
        except UnreadableError:
            found.append(Installed(name=name, credential_text=credential, unreadable=True))
    return found


def expected_builds(dist: Path, names: list[str]) -> dict[str, Expected]:
    """What each built plist says a clean copy looks like, by name.

    A name whose plist is absent is skipped rather than guessed at; `problems`
    is what refuses the gap. Nothing here may invent an expectation, because an
    invented one is indistinguishable from a met one.
    """
    built = {}
    for name in names:
        path = dist / f"{name}.xml"
        if not path.exists():
            continue
        raw = path.read_bytes()
        doc = _load(raw)
        questions = doc.get("WFWorkflowImportQuestions", []) if isinstance(doc, dict) else []
        built[name] = Expected(actions=_count_actions(raw), questions=len(questions))
    return built


def problems(installed: list[Installed], expected: dict[str, Expected], wanted: list[str]) -> list[str]:
    """Every reason this library must not be published from.

    `wanted` is the set of names that must be accounted for, and is why an
    absent build refuses instead of passing: with no expectation to compare
    against there is nothing to say about a copy, and "nothing to say" must
    never come out as "nothing wrong".
    """
    found: list[str] = []
    seen = Counter(i.name for i in installed)

    for name in wanted:
        if name not in expected:
            found.append(f"{name} has no build in dist/ to compare against; run ./build.sh first")
        if seen[name] == 0:
            found.append(f"{name} is missing from the library")
        elif seen[name] > 1:
            found.append(f"{name} has {seen[name]} copies; the publisher cannot tell which is the new build")

    for shortcut in installed:
        numbered = NUMBERED.match(shortcut.name)
        if numbered and numbered.group("base") in wanted:
            found.append(
                f"{shortcut.name} is a numbered copy left by a second import; delete it and the one it shadows"
            )
        if shortcut.name not in wanted:
            continue
        if shortcut.unreadable:
            found.append(f"{shortcut.name} could not be read out of the library, so nothing about it can be checked")
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
        want = expected.get(shortcut.name)
        if want is None:
            continue
        if shortcut.action_count != want.actions:
            found.append(
                f"{shortcut.name} has {shortcut.action_count} actions but the build in dist/ has {want.actions}; "
                f"the library is holding a different build"
            )
        if shortcut.question_count != want.questions:
            found.append(
                f"{shortcut.name} has {shortcut.question_count} setup question(s) but the build in dist/ has "
                f"{want.questions}; a debug build has none, and a copy that lost its questions on import installs "
                f"in one tap leaving the credentials unset"
            )
    return found


def gate(database: Path, dist: Path, prefix: str = PREFIX) -> list[str]:
    """Every reason not to mint from the library at `database`, given the build in `dist`.

    The whole check from two paths, for a minting rig that copies a guest's
    database out and wants a verdict: `functools.partial(gate, dist=dist)` is
    the `Callable[[Path], list[str]]` it takes. The copy has to carry its `-wal`
    and `-shm` files, or it is the library as it stood before the latest imports.

    A missing database is a refusal rather than an exception, and a missing
    build is left to `problems` rather than short-circuited here: a guard that
    lives only in `main` is what once let an empty expectation set read as a
    clean library.
    """
    if not database.exists():
        return [f"no Shortcuts database at {database}"]
    return problems(read_library(database, prefix=prefix), expected_builds(dist, NAMES), NAMES)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", type=Path, default=DATABASE, help="the Shortcuts database to read")
    parser.add_argument("--dist", type=Path, default=REPO / "dist", help="the build to compare the library against")
    parser.add_argument("--prefix", default=PREFIX, help="only look at shortcuts whose name starts with this")
    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if args.database.exists():
        installed = read_library(args.database, prefix=args.prefix)
        info(f"library holds {len(installed)} shortcut(s) named {args.prefix}*; comparing against {args.dist}")
        for shortcut in installed:
            if shortcut.unreadable:
                info(f"  {shortcut.name}: could not be read")
                continue
            info(
                f"  {shortcut.name}: {shortcut.action_count} actions, "
                f"{shortcut.question_count} question(s), {shortcut.answered_questions} answered"
            )

    found = gate(args.database, args.dist, prefix=args.prefix)
    if found:
        for problem in found:
            error(problem)
        error("not safe to mint links from this library")
        return 1

    success(f"library holds exactly the build in {args.dist}, with nothing configured — safe to mint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

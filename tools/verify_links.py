#!/usr/bin/env python3
"""Check that an iCloud link actually delivers the shortcut you built.

A link is a snapshot of whatever was in the sharing device's library, and it can
come out missing its setup questions — once, in testing, for reasons nobody has
explained. The failure is silent: the shortcut installs in one tap, looks right,
and leaves `not set` in the credential actions, so the first sign it went wrong
is a parent whose check-in never works.

So every link gets imported on a simulator and compared against the build it is
supposed to be carrying, before it goes anywhere near the page.

    python3 tools/verify_links.py --clipboard        # what Share Links copied
    python3 tools/verify_links.py --erase            # wipe the library first

Checks per link: the installed shortcut has the expected name, the same action
identifiers in the same order as `dist/<name>.xml`, and the same number of
import questions.
"""
import argparse
import plistlib
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simharness import Simulator, HOST                    # noqa: E402
from update_links import NAMES, PREFIX, from_clipboard    # noqa: E402


def questions(sim, name):
    """Import questions on the installed copy, straight out of its database."""
    con = sqlite3.connect(f"file:{sim._db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT ZIMPORTQUESTIONSDATA FROM ZSHORTCUT "
                          "WHERE ZNAME = ? AND ZTOMBSTONED = 0", (name,)).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return 0
    return len(plistlib.loads(bytes(row[0])))


def install(sim, link, timeout=45):
    """Open a link and take whichever import path the sheet offers.

    A sheet with questions offers Set Up Shortcut and then needs Skip Setup,
    which is the only way to finish that keeps the questions. One without
    questions installs on the first tap. Both are handled, because which one
    appears is the thing being measured.
    """
    sim.terminate_shortcuts()
    time.sleep(1.2)
    subprocess.run(["xcrun", "simctl", "openurl", sim.udid, link], check=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        if sim.blue_buttons():
            break
    else:
        raise RuntimeError("no import sheet appeared — is the link still live?")
    sim.tap_affirmative()
    time.sleep(4)
    boxes = sim.blue_buttons()
    if boxes:                       # the questions page: Skip Setup sits below
        img = sim.image()
        w, h = img.size
        _, _, _, y1 = max(boxes, key=lambda b: (b[3], b[2]))
        sim.tap(w // 2, int(y1 + h * 0.045), device_size=(w, h))
        time.sleep(6)


def check(sim, name, link, dist):
    xml = dist / f"{name}.xml"
    if not xml.exists():
        return [f"no {xml} to compare against — build first"]
    want = plistlib.load(xml.open("rb"))
    if name in sim.library():
        return [f"{name!r} is already installed, so the import would be "
                f"silently skipped. Re-run with --erase."]

    install(sim, link)

    problems = []
    if name not in sim.library():
        return [f"{name!r} did not install"]
    got = sim.shortcut_actions(name) or []
    want_ids = [a["WFWorkflowActionIdentifier"] for a in want["WFWorkflowActions"]]
    got_ids = [a["WFWorkflowActionIdentifier"] for a in got]
    if got_ids != want_ids:
        problems.append(f"actions differ: {len(got_ids)} installed, "
                        f"{len(want_ids)} built")
    want_q = len(want["WFWorkflowImportQuestions"])
    got_q = questions(sim, name)
    if got_q != want_q:
        problems.append(f"import questions: {got_q} on the link, {want_q} built. "
                        f"Anyone installing this is never asked for credentials.")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clipboard", action="store_true",
                    help="read the links from the clipboard (the default)")
    ap.add_argument("--erase", action="store_true",
                    help="wipe the simulator's library first")
    ap.add_argument("--dist", default=str(REPO / "dist"), type=Path,
                    help="where the built .xml files are")
    args = ap.parse_args()

    links = from_clipboard()
    missing = [n for n in NAMES if n not in links]
    if missing:
        print(f"No link on the clipboard for: {', '.join(missing)}", file=sys.stderr)
        return 1
    bad = [n for n in NAMES if not links[n].startswith(PREFIX)]
    if bad:
        print(f"Not iCloud links: {', '.join(bad)}", file=sys.stderr)
        return 1

    sim = Simulator.find(runtime="iOS 27")
    if args.erase:
        print("erasing the simulator's library")
        sim.erase()
    sim.prepare_window()
    print(f"verifying on {sim.udid}\n")

    failed = False
    for name in NAMES:
        problems = check(sim, name, links[name], args.dist)
        if problems:
            failed = True
            print(f"  FAIL  {name}")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"  ok    {name}")
    print()
    if failed:
        print("Do not publish these links.", file=sys.stderr)
        return 1
    print("All three links deliver the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

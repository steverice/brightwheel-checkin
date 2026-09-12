#!/usr/bin/env python3
"""Check that an iCloud link actually delivers the shortcut you built.

A link is a snapshot of whatever was in the sharing device's library, and it can
come out missing its setup questions — once, in testing, for reasons nobody has
explained. The failure is silent: the shortcut installs in one tap, looks right,
and leaves `not set` in the credential actions, so the first sign it went wrong
is a parent whose check-in never works.

So every link gets imported on a simulator and compared against the build it is
supposed to be carrying, before it goes anywhere near the page.

    uv run python tools/verify_links.py --clipboard        # what Share Links copied
    uv run python tools/verify_links.py --erase            # wipe the library first

Checks per link (`shortcut_forge_lib.sim.links.check_link`): the installed shortcut
has the expected name, the same action identifiers in the same order as
`dist/<name>.xml`, and the same number of import questions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import argcomplete
from shortcut_forge_lib.sim.harness import Simulator
from shortcut_forge_lib.sim.links import check_link

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from console import error, info  # noqa: E402
from update_links import NAMES, PREFIX, from_clipboard  # noqa: E402


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        epilog="examples:\n  %(prog)s --clipboard --erase",
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    ap.add_argument("--clipboard", action="store_true", help="read the links from the clipboard (the default)")
    ap.add_argument("--erase", action="store_true", help="wipe the simulator's library first")
    ap.add_argument("--dist", default=REPO / "dist", type=Path, help="where the built .xml files are")
    argcomplete.autocomplete(ap)
    return ap


def main() -> int:
    args = build_parser().parse_args()

    links = from_clipboard()
    missing = [n for n in NAMES if n not in links]
    if missing:
        error(f"No link on the clipboard for: {', '.join(missing)}")
        return 1
    bad = [n for n in NAMES if not links[n].startswith(PREFIX)]
    if bad:
        error(f"Not iCloud links: {', '.join(bad)}")
        return 1

    sim = Simulator.find(runtime="iOS 27")
    if args.erase:
        info("erasing the simulator's library")
        sim.erase()
    sim.prepare_window()
    info(f"verifying on {sim.udid}\n")

    failed = False
    for name in NAMES:
        problems = check_link(sim, name, links[name], args.dist / f"{name}.xml")
        if problems:
            failed = True
            error(f"  FAIL  {name}")
            for p in problems:
                error(f"        {p}")
        else:
            info(f"  ok    {name}")
    info("")
    if failed:
        error("Do not publish these links.")
        return 1
    info("All three links deliver the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

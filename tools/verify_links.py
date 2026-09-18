#!/usr/bin/env python3
"""Check that an iCloud link actually delivers the shortcut you built.

A link is a snapshot of whatever was in the sharing library, so it can carry the
wrong build, or come out missing its setup questions — once, in testing, for
reasons nobody has explained. Both failures are silent: the shortcut installs in
one tap and looks right, and the first sign anything went wrong is a parent
whose check-in never works. So every link is compared against the build it is
supposed to carry before it goes anywhere near the page.

    uv run python tools/verify_links.py --clipboard                  # what Share Links copied
    uv run python tools/verify_links.py --clipboard --simulator --erase

By default each link is checked through the iCloud records API
(`shortcut_forge_lib.records.check_record`), which serves the unsigned plist
that was shared, with no device: the record's name, the action identifiers in
order, and every import question's `ActionIndex`, `ParameterKey` and
`Category` against `dist/<name>.xml`, and a refusal if any question carries an
answer. That is what verified the v1.5.0 links on 2026-09-18.

`--simulator` instead imports each link on an iOS 27 simulator
(`shortcut_forge_lib.sim.links.check_link`), which also sees what an import
does to the copy. It cannot run under Xcode 27, whose Device Hub exposes
nothing to drive, until the harness moves to idb.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import argcomplete
from shortcut_forge_lib.records import RecordError, check_record

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
        epilog="examples:\n  %(prog)s --clipboard",
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    ap.add_argument("--clipboard", action="store_true", help="read the links from the clipboard (the default)")
    ap.add_argument("--dist", default=REPO / "dist", type=Path, help="where the built .xml files are")
    ap.add_argument("--simulator", action="store_true", help="import each link on a simulator instead")
    ap.add_argument("--erase", action="store_true", help="with --simulator, wipe the simulator's library first")
    argcomplete.autocomplete(ap)
    return ap


def by_record(link: str, name: str, xml: Path) -> list[str]:
    """A record that cannot be fetched or read is a problem with the link, not a pass."""
    try:
        return check_record(link, name, xml)
    except RecordError as exc:
        return [str(exc)]


def by_records(links: dict[str, str], dist: Path) -> dict[str, list[str]]:
    return {name: by_record(links[name], name, dist / f"{name}.xml") for name in NAMES}


def by_simulator(links: dict[str, str], dist: Path, *, erase: bool) -> dict[str, list[str]]:
    # Imported here: the harness needs Quartz and a simulator, and the default
    # path needs neither.
    from shortcut_forge_lib.sim.harness import Simulator
    from shortcut_forge_lib.sim.links import check_link

    sim = Simulator.find(runtime="iOS 27")
    if erase:
        info("erasing the simulator's library")
        sim.erase()
    sim.prepare_window()
    info(f"verifying on {sim.udid}\n")
    return {name: check_link(sim, name, links[name], dist / f"{name}.xml") for name in NAMES}


def main() -> int:
    args = build_parser().parse_args()
    if args.erase and not args.simulator:
        error("--erase wipes a simulator's library, so it needs --simulator")
        return 1

    links = from_clipboard()
    missing = [n for n in NAMES if n not in links]
    if missing:
        error(f"No link on the clipboard for: {', '.join(missing)}")
        return 1
    bad = [n for n in NAMES if not links[n].startswith(PREFIX)]
    if bad:
        error(f"Not iCloud links: {', '.join(bad)}")
        return 1

    if args.simulator:
        results = by_simulator(links, args.dist, erase=args.erase)
    else:
        info("checking each link's record against the build\n")
        results = by_records(links, args.dist)

    failed = False
    for name in NAMES:
        if results[name]:
            failed = True
            error(f"  FAIL  {name}")
            for p in results[name]:
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

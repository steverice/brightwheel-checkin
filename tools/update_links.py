#!/usr/bin/env python3
"""Put fresh iCloud links into docs/index.html.

An iCloud link is frozen at the moment you share it, so the page goes stale the
moment you rebuild. "Brightwheel Share Links" (see tools/build_publisher.py)
mints three new ones and copies them out; this drops them into the page.

    uv run python tools/update_links.py                 # asks for each of the three
    uv run python tools/update_links.py --clipboard     # takes what Share Links copied

Nothing is written unless all three are present and look like iCloud links, so
a half-finished paste cannot leave the page pointing two ways at once.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import argcomplete

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from console import error, info  # noqa: E402

PAGE = REPO / "docs" / "index.html"
NAMES = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]
PREFIX = "https://www.icloud.com/shortcuts/"


def anchor(name: str) -> re.Pattern[str]:
    """The page's own link for one shortcut, matched on its visible text."""
    return re.compile(r'(<a href=")([^"]*)("\s*>\s*' + re.escape(name) + r"\s*</a>)")


def from_clipboard() -> dict[str, str]:
    """Pull the three out of whatever Share Links put on the clipboard.

    It copies the finished markup, so the hrefs are already in there in page
    order — but read them by name rather than by position, so a reordered or
    partial paste fails loudly instead of silently swapping two shortcuts.
    """
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    found = {}
    for name in NAMES:
        m = anchor(name).search(text)
        if m:
            found[name] = m.group(2)
    return found


def ask() -> dict[str, str]:
    info("Paste each iCloud link. In Shortcuts, run Brightwheel Share Links,")
    info("or share each shortcut and choose Copy iCloud Link.\n")
    found = {}
    for name in NAMES:
        value = input(f"  {name}: ").strip()
        if value:
            found[name] = value
    return found


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        epilog="examples:\n  %(prog)s --clipboard",
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    ap.add_argument("--clipboard", action="store_true", help="read the links from the clipboard instead of asking")
    argcomplete.autocomplete(ap)
    return ap


def main() -> int:
    args = build_parser().parse_args()

    page = PAGE.read_text(encoding="utf-8")
    links = from_clipboard() if args.clipboard else ask()

    missing = [n for n in NAMES if n not in links]
    if missing:
        where = "on the clipboard" if args.clipboard else "given"
        error(f"\nNo link {where} for: {', '.join(missing)}. Page unchanged.")
        return 1

    bad = {n: u for n, u in links.items() if not u.startswith(PREFIX)}
    if bad:
        error("\nThese do not look like iCloud shortcut links, so nothing was written:")
        for n, u in bad.items():
            error(f"  {n}: {u}")
        error(f"An iCloud link starts with {PREFIX}")
        return 1

    changed = []
    for name in NAMES:
        pattern = anchor(name)
        match = pattern.search(page)
        if not match:
            error(f"\n{PAGE.name} has no link for {name}. Page unchanged.")
            return 1
        was = match.group(2)
        page = pattern.sub(lambda m, name=name: m.group(1) + links[name] + m.group(3), page, count=1)
        changed.append((name, was, links[name]))

    PAGE.write_text(page, encoding="utf-8")
    info(f"\nUpdated {PAGE}:")
    for name, was, now in changed:
        info(f"  {name}\n    was {was}\n    now {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generate "Brightwheel Share Links": a shortcut that shares the shortcuts.

An iCloud link is a snapshot taken when you share, so every release needs three
fresh ones. The `shortcuts` CLI has no share command and the AppleScript
dictionary exposes only `run`, so the minting has to happen *inside* a shortcut
— but `run` is all it takes to drive one. This builds a shortcut that mints all
three and puts them on the clipboard as the exact markup `docs/index.html`
wants, which is what makes refreshing the page's links a scripted step rather
than a chore.

The shortcut itself is `shortcut_forge_lib.publisher.share_links_shortcut`, which
finds each target **by name, every time it runs**, rather than through a
picker, and refuses to mint anything if a name is duplicated, numbered, or
missing. See `TESTING.md` for how that was measured.

Run it headless:

    shortcuts run "Brightwheel Share Links"

It exits 0 with the three links already on the clipboard — no taps, no phone,
no Shortcuts window. Then `verify_links.py --clipboard --erase` and
`update_links.py --clipboard` finish the job; `release.sh` offers to run both
right after cutting a release, and declining that offer is what leaves the
published page handing out the previous build's links.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import argcomplete
from shortcut_forge_lib.build import Shortcut, build_all
from shortcut_forge_lib.checks import CheckError
from shortcut_forge_lib.publisher import share_links_shortcut
from shortcut_forge_lib.toolchain import SigningError, ToolNotFoundError, ValidationError

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from console import error, success  # noqa: E402
from update_links import NAMES  # noqa: E402

NAME = "Brightwheel Share Links"
TARGETS = NAMES

# Validator complaints that are expected here. Anything else is real.
#   The prompt-comment rule, waived in build_shortcuts.py for the same reason:
#   the Shortcuts Playground attribution comment was removed on purpose, so
#   the second action is not a prompt block.
WAIVED = ["Second action must be the prompt Comment block"]


def build() -> dict[str, Any]:
    """The publisher, with this project's three targets and the page's markup."""
    return share_links_shortcut(
        NAME,
        TARGETS,
        line_format='<li><a href="{link}">{name}</a></li>\n',
        done_message="Copied three links. Now run: uv run python tools/verify_links.py --clipboard --erase",
    )


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        epilog="examples:\n  %(prog)s\n  %(prog)s /tmp/publisher",
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    ap.add_argument("dest", nargs="?", default=REPO / "dist-tools", type=Path, help="directory to build into")
    argcomplete.autocomplete(ap)
    return ap


def run(args: argparse.Namespace) -> None:
    plist = build()
    built = build_all(args.dest, [Shortcut(NAME, plist)], waived=WAIVED, mode="anyone", known_shortcuts=TARGETS)
    success(f"{NAME}: {len(plist['WFWorkflowActions'])} actions, validated, signed -> {built[NAME]}")


def main() -> None:
    try:
        run(build_parser().parse_args())
    except KeyboardInterrupt:
        pass
    except (CheckError, ValidationError, SigningError, ToolNotFoundError, OSError) as e:  # the CLI boundary
        error(str(e))
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build and sign the shortcuts the integration suite installs.

Two things separate a test build from `./build.sh`:

  * `--env-file tests/fixtures/test.env` bakes obviously-fake credentials in
    and drops the import questions, so the sheet is a single "Add Shortcut"
    tap. It also seeds the school code, which is what keeps the run away from
    Scan Code — an action the simulator does not have. No children are baked
    in; the roster comes from the mock, which serves
    `tests/fixtures/roster.json`.
  * `--api-base` points every request at the mock, so a test can never reach
    the real Brightwheel and can never check a real child in.

The signed file's *name on disk* becomes its name in the library, so the
generator names the files after the shortcuts, which is what the wrappers call.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from shortcut_forge import toolchain
from shortcut_forge.plist import write_xml
from shortcut_forge.sim import probes

REPO = Path(__file__).resolve().parent.parent
TEST_ENV = Path(__file__).parent / "fixtures" / "test.env"
OUT = REPO / "dist-test"
NAMES = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]


def build(api_base, dest=OUT):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # The generator validates and signs by itself; the signed files land in dest.
    subprocess.run(
        [sys.executable, str(REPO / "build_shortcuts.py"), str(dest), "--env-file", str(TEST_ENV), "--api-base", api_base],
        check=True,
        capture_output=True,
        text=True,
    )

    paths = {}
    for name in NAMES:
        signed = dest / f"{name}.shortcut"
        if not signed.exists():
            raise SystemExit(f"generator did not produce {signed}")
        paths[name] = signed
    return paths


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8788/api/v1"
    for name, path in build(base).items():
        print(f"{name}: {path}")


SETUP_PROBE_PLACEHOLDER = probes.SETUP_PROBE_PLACEHOLDER


def build_setup_probe(name, dest=OUT):
    """A two-action shortcut whose only value comes from an import question.

    Small on purpose: when the setup flow breaks, this says so without any of
    the Brightwheel machinery being involved.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xml = write_xml(probes.setup_probe(name), dest / f"{name}.xml")
    return toolchain.sign(xml, name=name)

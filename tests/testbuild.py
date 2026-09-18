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

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from shortcut_forge_lib import checks, toolchain
from shortcut_forge_lib.plist import act, attach, document, out, ts, write_xml
from shortcut_forge_lib.sim import probes
from shortcut_forge_lib.uuids import random_uuids

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import build_shortcuts  # noqa: E402
from console import info  # noqa: E402

TEST_ENV = Path(__file__).parent / "fixtures" / "test.env"
OUT = REPO / "dist-test"
NAMES = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]


def build(api_base: str, dest: str | Path = OUT) -> dict[str, Path]:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # The generator validates and signs by itself; the signed files land in dest.
    subprocess.run(
        [
            sys.executable,
            str(REPO / "build_shortcuts.py"),
            str(dest),
            "--env-file",
            str(TEST_ENV),
            "--api-base",
            api_base,
        ],
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
        info(f"{name}: {path}")


SETUP_PROBE_PLACEHOLDER = probes.SETUP_PROBE_PLACEHOLDER


def build_setup_probe(name: str, dest: str | Path = OUT) -> Path:
    """A two-action shortcut whose only value comes from an import question.

    Small on purpose: when the setup flow breaks, this says so without any of
    the Brightwheel machinery being involved.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xml = write_xml(probes.setup_probe(name), dest / f"{name}.xml")
    return toolchain.sign(xml, name=name)


# --- probes the schedule tests drive the device with ------------------------
#
# A test cannot reach into Shortcuts' store or hand a shortcut its input, so
# each gets a tiny shortcut of its own: one that writes the shared store the
# way "Set school days" would, and one that runs a target with a direction the
# way a wrapper does. A copy of Attendance built with other baked-in settings
# is the third, for a build whose settings are Text actions.


def build_store_probe(values: dict[str, str | None], dest: str | Path = OUT) -> tuple[str, Path]:
    """A shortcut that writes each value to the shared store, or deletes it for None.

    The name carries a digest of the values: an installed shortcut is never
    replaced, so a new value has to be a new shortcut.
    """
    digest = hashlib.sha1(json.dumps(values, sort_keys=True).encode()).hexdigest()[:8]  # noqa: S324 - a name, not security
    name = f"Store Probe {digest}"
    i = random_uuids()
    actions = []
    for key, value in values.items():
        if value is None:
            actions.append(
                act("is.workflow.actions.deletestoredcontent", WFStoredContentKey=key, WFStoredContentGlobalValue=True)
            )
            continue
        u = next(i)
        actions.append(act("is.workflow.actions.gettext", UUID=u, WFTextActionText=value))
        actions.append(
            act(
                "is.workflow.actions.setstoredcontent",
                WFStoredContentKey=key,
                WFStoredContentGlobalValue=True,
                WFInput=ts(out(u, "Text")),
            )
        )
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xml = write_xml(document(name, actions, glyph=59692, color=4292093695, input_classes=[]), dest / f"{name}.xml")
    return name, toolchain.sign(xml, name=name)


def build_run_probe(target: str, direction: str, dest: str | Path = OUT) -> tuple[str, Path]:
    """A shortcut that runs `target` with `direction` as its input, as a wrapper does."""
    name = f"Run {target} {direction}"
    i = random_uuids()
    u = next(i)
    actions = [
        act("is.workflow.actions.gettext", UUID=u, CustomOutputName="Direction", WFTextActionText=direction),
        act(
            "is.workflow.actions.runworkflow",
            WFWorkflowName=target,
            WFWorkflow={"isSelf": False, "workflowIdentifier": next(i), "workflowName": target},
            WFInput=attach(out(u, "Direction")),
        ),
    ]
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xml = write_xml(document(name, actions, glyph=59692, color=4292093695, input_classes=[]), dest / f"{name}.xml")
    return name, toolchain.sign(xml, name=name)


def build_attendance_copy(name: str, api_base: str, overrides: dict[str, str], dest: str | Path = OUT) -> Path:
    """Brightwheel Attendance built against the mock with `overrides` on top of the test env, signed as `name`.

    The library name is the file's, so the copy lives beside the real one and
    a run probe can target it by that name. Checked like a real build; not
    validated, because the build the suite installed already was.
    """
    build_shortcuts.BASE = api_base
    env = {**build_shortcuts.load_env(TEST_ENV), **overrides}
    _real_name, doc = build_shortcuts.build(env=env)
    checks.check_all(doc)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xml = write_xml(doc, dest / f"{name}.xml")
    return toolchain.sign(xml, name=name)

#!/usr/bin/env python3
"""Build and sign the shortcuts the integration suite installs.

Two things separate a test build from `./build.sh`:

  * `--env-file tests/fixtures/test.env` bakes obviously-fake credentials in
    and drops the import questions, so the sheet is a single "Add Shortcut"
    tap. It also seeds the school code, which is what keeps the run away from
    Scan Code — an action the simulator does not have.
  * `--api-base` points every request at the mock, so a test can never reach
    the real Brightwheel and can never check a real child in.

The signed file's *name on disk* becomes its name in the library, so the
filenames here have to match what the wrappers call.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEST_ENV = Path(__file__).parent / "fixtures" / "test.env"
OUT = REPO / "dist-test"
NAMES = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]


def build(api_base, dest=OUT):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    subprocess.run(
        [sys.executable, str(REPO / "build_shortcuts.py"), str(dest),
         "--env-file", str(TEST_ENV), "--api-base", api_base],
        check=True, capture_output=True, text=True)

    signed_dir = Path(os.environ.get(
        "CLAUDE_PLUGIN_OPTION_OUTPUT_DIR",
        Path.home() / "Documents" / "Shortcuts Playground"))

    paths = {}
    for name in NAMES:
        xml = dest / f"{name}.xml"
        if not xml.exists():
            raise SystemExit(f"generator did not produce {xml}")
        subprocess.run(["sign-shortcut", str(xml), "--name", name],
                       check=True, capture_output=True, text=True)
        target = dest / f"{name}.shortcut"
        shutil.copy(signed_dir / f"{name}.shortcut", target)
        paths[name] = target
    return paths


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8788/api/v1"
    for name, path in build(base).items():
        print(f"{name}: {path}")


SETUP_PROBE_PLACEHOLDER = "not set"


def build_setup_probe(name, dest=OUT):
    """A two-action shortcut whose only value comes from an import question.

    Small on purpose: when the setup flow breaks, this says so without any of
    the Brightwheel machinery being involved.
    """
    import plistlib
    import uuid

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    u = str(uuid.uuid4()).upper()
    plist = {
        "WFWorkflowActions": [
            {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
             "WFWorkflowActionParameters": {
                 "UUID": u, "CustomOutputName": "Answer",
                 "WFTextActionText": SETUP_PROBE_PLACEHOLDER}},
            {"WFWorkflowActionIdentifier": "is.workflow.actions.setclipboard",
             "WFWorkflowActionParameters": {
                 "WFInput": {"Value": {"OutputName": "Answer", "OutputUUID": u,
                                       "Type": "ActionOutput"},
                             "WFSerializationType": "WFTextTokenAttachment"}}},
        ],
        "WFWorkflowClientVersion": "2700.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59692,
                           "WFWorkflowIconStartColor": 4292093695},
        "WFWorkflowImportQuestions": [{
            "ActionIndex": 0,
            "Category": "Parameter",
            "DefaultValue": "",
            "ParameterKey": "WFTextActionText",
            "Text": "Setup canary — type the digits shown by the test",
        }],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowName": name,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
    }
    xml = dest / f"{name}.xml"
    xml.write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_XML))
    subprocess.run(["sign-shortcut", str(xml), "--name", name],
                   check=True, capture_output=True, text=True)
    signed_dir = Path(os.environ.get(
        "CLAUDE_PLUGIN_OPTION_OUTPUT_DIR",
        Path.home() / "Documents" / "Shortcuts Playground"))
    target = dest / f"{name}.shortcut"
    shutil.copy(signed_dir / f"{name}.shortcut", target)
    return target

"""The gate that stands between the Mac's library and an unrevocable link.

Two failures motivate every case here, and both are ones the publisher's own
checks pass without complaint. It cannot tell a clean build from the copy you
use every day, so it would mint a link to your Brightwheel password. And it
cannot tell one build from another, so it would mint links to last release's
code — which is what the library was actually holding when v1.5.0 was cut.

Run with the file named: pytest tests/test_check_library.py
"""

from __future__ import annotations

import plistlib
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import check_library  # noqa: E402
from check_library import Installed, problems  # noqa: E402

EXPECTED = {"Brightwheel Attendance": 339, "Brightwheel Check In": 17, "Brightwheel Check Out": 17}


def clean() -> list[Installed]:
    """What a library holding exactly the fresh build looks like."""
    return [
        Installed(name="Brightwheel Attendance", action_count=339, answered_questions=0, credential_text=False),
        Installed(name="Brightwheel Check In", action_count=17, answered_questions=0, credential_text=False),
        Installed(name="Brightwheel Check Out", action_count=17, answered_questions=0, credential_text=False),
    ]


def swap(installed: list[Installed], name: str, **changes: object) -> list[Installed]:
    """The same library with one shortcut altered."""
    import dataclasses

    return [dataclasses.replace(i, **changes) if i.name == name else i for i in installed]  # type: ignore[arg-type]


def test_a_library_holding_exactly_the_fresh_build_passes():
    assert problems(clean(), EXPECTED) == []


def test_a_missing_shortcut_is_refused():
    only_two = [i for i in clean() if i.name != "Brightwheel Check Out"]
    found = problems(only_two, EXPECTED)
    assert any("Brightwheel Check Out" in p and "missing" in p for p in found), found


def test_a_second_copy_under_the_same_name_is_refused():
    twice = [*clean(), clean()[0]]
    found = problems(twice, EXPECTED)
    assert any("Brightwheel Attendance" in p and "2" in p for p in found), found


def test_a_numbered_copy_is_refused():
    """`Brightwheel Attendance 1` is what a second import leaves behind."""
    numbered = [*clean(), Installed("Brightwheel Attendance 1", 339, 0, False)]
    found = problems(numbered, EXPECTED)
    assert any("Brightwheel Attendance 1" in p for p in found), found


def test_an_answered_setup_question_is_refused():
    """The password hazard: answers mean this is somebody's configured copy."""
    configured = swap(clean(), "Brightwheel Attendance", answered_questions=3)
    found = problems(configured, EXPECTED)
    assert any("Brightwheel Attendance" in p and "answer" in p.lower() for p in found), found


def test_credential_shaped_text_is_refused():
    baked = swap(clean(), "Brightwheel Attendance", credential_text=True)
    found = problems(baked, EXPECTED)
    assert any("Brightwheel Attendance" in p for p in found), found


def test_a_stale_build_is_refused():
    """v1.4.0's Attendance is 317 actions where v1.5.0's is 339."""
    stale = swap(clean(), "Brightwheel Attendance", action_count=317)
    found = problems(stale, EXPECTED)
    assert any("317" in p and "339" in p for p in found), found


def test_every_problem_is_reported_not_just_the_first():
    broken = swap(
        swap(clean(), "Brightwheel Attendance", action_count=317), "Brightwheel Check In", answered_questions=1
    )
    assert len(problems(broken, EXPECTED)) >= 2


def _fixture_db(path: Path, rows: list[tuple[str, bytes | None, bytes | None]]) -> None:
    """The subset of the Shortcuts schema this tool reads."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ZSHORTCUTACTIONS (Z_PK INTEGER PRIMARY KEY, ZDATA BLOB)")
    con.execute(
        "CREATE TABLE ZSHORTCUT (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZIMPORTQUESTIONSDATA BLOB, ZACTIONS INTEGER)"
    )
    for index, (name, questions, actions) in enumerate(rows, start=1):
        con.execute("INSERT INTO ZSHORTCUTACTIONS (Z_PK, ZDATA) VALUES (?, ?)", (index, actions))
        con.execute(
            "INSERT INTO ZSHORTCUT (Z_PK, ZNAME, ZIMPORTQUESTIONSDATA, ZACTIONS) VALUES (?, ?, ?, ?)",
            (index, name, questions, index),
        )
    con.commit()
    con.close()


def _actions_blob(count: int, text: str = "not set") -> bytes:
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {"WFTextActionText": text},
    }
    return plistlib.dumps({"WFWorkflowActions": [action] * count})


def test_read_library_counts_actions_and_unanswered_questions(tmp_path):
    questions = plistlib.dumps([{"ParameterKey": "WFTextActionText", "Text": "Your email", "DefaultValue": "not set"}])
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(db, [("Brightwheel Attendance", questions, _actions_blob(339))])

    found = {i.name: i for i in check_library.read_library(db, prefix="Brightwheel")}

    assert found["Brightwheel Attendance"].action_count == 339
    assert found["Brightwheel Attendance"].answered_questions == 0
    assert found["Brightwheel Attendance"].credential_text is False


def test_read_library_spots_an_answered_question(tmp_path):
    answered = plistlib.dumps(
        [{"ParameterKey": "WFTextActionText", "Text": "Your email", "ActualValue": "someone@example.invalid"}]
    )
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(db, [("Brightwheel Attendance", answered, _actions_blob(339))])

    found = check_library.read_library(db, prefix="Brightwheel")[0]

    assert found.answered_questions == 1
    assert found.credential_text is True


def test_read_library_spots_a_credential_baked_into_the_actions(tmp_path):
    """A debug build carries the address in the actions, with no question at all."""
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(db, [("Brightwheel Attendance", None, _actions_blob(339, text="parent@example.invalid"))])

    found = check_library.read_library(db, prefix="Brightwheel")[0]

    assert found.answered_questions == 0
    assert found.credential_text is True


def test_expected_counts_reads_the_built_plists(tmp_path):
    xml = tmp_path / "Brightwheel Check In.xml"
    xml.write_bytes(_actions_blob(17))

    assert check_library.expected_counts(tmp_path, ["Brightwheel Check In"]) == {"Brightwheel Check In": 17}

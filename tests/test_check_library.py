"""The gate that stands between the Mac's library and an unrevocable link.

Every case here is a way the publisher's own checks say yes to something they
should not. It cannot tell a clean build from a dev build or from the copy you
use every day, and it cannot tell one release from another.

Three of these were found by review on 2026-09-15 after the first version
shipped, and all three failed *open* — the gate reported a clean library:

- a target with no build in `dist/` left nothing to compare, and an empty
  expectation set produced an empty problem list
- a blob that would not parse counted as zero actions and zero answers
- nothing compared import questions, so a debug build (which bakes credentials
  in and emits no questions) and a shortcut that silently lost its questions on
  import both read as fine

Run with the file named: pytest tests/test_check_library.py
"""

from __future__ import annotations

import dataclasses
import plistlib
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import check_library  # noqa: E402
from check_library import Expected, Installed, problems  # noqa: E402

WANTED = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]
EXPECTED = {
    "Brightwheel Attendance": Expected(actions=339, questions=3),
    "Brightwheel Check In": Expected(actions=17, questions=0),
    "Brightwheel Check Out": Expected(actions=17, questions=0),
}


def clean() -> list[Installed]:
    """What a library holding exactly the fresh build looks like."""
    return [
        Installed("Brightwheel Attendance", action_count=339, question_count=3, answered_questions=0),
        Installed("Brightwheel Check In", action_count=17, question_count=0, answered_questions=0),
        Installed("Brightwheel Check Out", action_count=17, question_count=0, answered_questions=0),
    ]


def swap(installed: list[Installed], name: str, **changes: object) -> list[Installed]:
    """The same library with one shortcut altered."""
    return [dataclasses.replace(i, **changes) if i.name == name else i for i in installed]  # type: ignore[arg-type]


def test_a_library_holding_exactly_the_fresh_build_passes():
    assert problems(clean(), EXPECTED, WANTED) == []


def test_a_missing_shortcut_is_refused():
    only_two = [i for i in clean() if i.name != "Brightwheel Check Out"]
    found = problems(only_two, EXPECTED, WANTED)
    assert any("Brightwheel Check Out" in p and "missing" in p for p in found), found


def test_a_second_copy_under_the_same_name_is_refused():
    twice = [*clean(), clean()[0]]
    found = problems(twice, EXPECTED, WANTED)
    assert any("Brightwheel Attendance" in p and "2" in p for p in found), found


def test_a_numbered_copy_is_refused():
    """`Brightwheel Attendance 1` is what a second import leaves behind."""
    numbered = [*clean(), Installed("Brightwheel Attendance 1", action_count=339, question_count=3)]
    found = problems(numbered, EXPECTED, WANTED)
    assert any("Brightwheel Attendance 1" in p for p in found), found


def test_an_answered_setup_question_is_refused():
    """The password hazard: answers mean this is somebody's configured copy."""
    configured = swap(clean(), "Brightwheel Attendance", answered_questions=3)
    found = problems(configured, EXPECTED, WANTED)
    assert any("Brightwheel Attendance" in p and "answer" in p.lower() for p in found), found


def test_credential_shaped_text_is_refused():
    baked = swap(clean(), "Brightwheel Attendance", blobs=(b"<string>parent@example.invalid</string>",))
    found = problems(baked, EXPECTED, WANTED)
    assert any("Brightwheel Attendance" in p for p in found), found


def test_a_stale_build_is_refused():
    """v1.4.0's Attendance is 317 actions where v1.5.0's is 339."""
    stale = swap(clean(), "Brightwheel Attendance", action_count=317)
    found = problems(stale, EXPECTED, WANTED)
    assert any("317" in p and "339" in p for p in found), found


# -- the three that failed open -------------------------------------------


def test_a_target_with_no_build_to_compare_against_is_refused():
    """The gate must never read "nothing to compare" as "nothing wrong".

    `expected_builds` skips a name whose plist is absent, so pointing the gate
    at a directory with no `.xml` files used to yield an empty expectation set
    — and a library holding a stale, configured, credential-bearing copy then
    produced no problems at all.
    """
    rotten = [Installed("Brightwheel Attendance", action_count=317, question_count=0, answered_questions=3)]
    found = problems(rotten, {}, WANTED)
    assert found, "an empty expectation set must refuse, not pass"
    assert any("Brightwheel Attendance" in p and "dist" in p for p in found), found


def test_an_unreadable_shortcut_is_refused():
    """A blob that will not parse is a refusal, not a zero."""
    broken = swap(clean(), "Brightwheel Attendance", unreadable=True)
    found = problems(broken, EXPECTED, WANTED)
    assert any("Brightwheel Attendance" in p and "read" in p.lower() for p in found), found


def test_a_debug_build_is_refused():
    """`./build.sh --debug` bakes credentials in and emits no setup questions.

    The question count is the discriminator: a clean Attendance has 3, a debug
    build has 0. This is what makes "never share a dev build" an assertion.
    """
    debug = swap(clean(), "Brightwheel Attendance", question_count=0, action_count=341)
    found = problems(debug, EXPECTED, WANTED)
    assert any("question" in p.lower() and "Brightwheel Attendance" in p for p in found), found


def test_a_shortcut_that_lost_its_setup_questions_on_import_is_refused():
    """The measured silent failure, and the reason this check exists.

    `shortcut-forge/docs/simulator-harness.md` records a link that arrived with
    zero import questions where its siblings had three; the only difference was
    that its clicks were synthesized rather than made by a person. Such a copy
    installs in one tap and leaves `not set` in the email, password and
    check-in code, with no error. Its action count is unchanged, so only the
    question count can see it.
    """
    lost = swap(clean(), "Brightwheel Attendance", question_count=0)
    found = problems(lost, EXPECTED, WANTED)
    assert any("question" in p.lower() for p in found), found


def test_every_problem_is_reported_not_just_the_first():
    broken = swap(
        swap(clean(), "Brightwheel Attendance", action_count=317), "Brightwheel Check In", answered_questions=1
    )
    assert len(problems(broken, EXPECTED, WANTED)) >= 2


# -- a real database -------------------------------------------------------
# The reader and its own tests live in `shortcut_forge_lib.library`. These
# helpers build the subset of the schema it reads, for the gate tests below.


def _fixture_db(path: Path, rows: list[tuple[str, bytes | None, bytes | None]]) -> None:
    """The subset of the Shortcuts schema this tool reads."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ZSHORTCUTACTIONS (Z_PK INTEGER PRIMARY KEY, ZDATA BLOB)")
    con.execute(
        "CREATE TABLE ZSHORTCUT (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZTOMBSTONED INTEGER DEFAULT 0, "
        "ZIMPORTQUESTIONSDATA BLOB, ZACTIONS INTEGER)"
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


def _questions_blob(count: int, *, answered: bool = False) -> bytes:
    q: dict[str, object] = {"ParameterKey": "WFTextActionText", "Text": "Your email", "DefaultValue": "not set"}
    if answered:
        q["ActualValue"] = "someone@example.invalid"
    return plistlib.dumps([q] * count)


# -- the gate a minting rig calls -------------------------------------------


def _fixture_dist(path: Path) -> Path:
    """A dist/ holding the three builds in `EXPECTED`."""
    for name, want in EXPECTED.items():
        doc = plistlib.loads(_actions_blob(want.actions))
        doc["WFWorkflowImportQuestions"] = [{"ParameterKey": "WFTextActionText"}] * want.questions
        (path / f"{name}.xml").write_bytes(plistlib.dumps(doc))
    return path


def test_gate_passes_a_library_holding_exactly_the_build(tmp_path):
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(
        db,
        [
            (name, _questions_blob(want.questions) if want.questions else None, _actions_blob(want.actions))
            for name, want in EXPECTED.items()
        ],
    )

    assert check_library.gate(db, _fixture_dist(tmp_path)) == []


def test_gate_refuses_a_stale_library(tmp_path):
    """The mint26 base on 2026-09-18: v1.4.0 copies against a v1.5.0 build."""
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(
        db,
        [
            ("Brightwheel Attendance", _questions_blob(3), _actions_blob(317)),
            ("Brightwheel Check In", None, _actions_blob(17)),
            ("Brightwheel Check Out", None, _actions_blob(17)),
        ],
    )

    found = check_library.gate(db, _fixture_dist(tmp_path))

    assert len(found) == 1
    assert "317 actions" in found[0]


def test_gate_refuses_a_missing_database_instead_of_raising(tmp_path):
    """A rig that copied the database to the wrong place gets a refusal it can print."""
    found = check_library.gate(tmp_path / "nowhere.sqlite", _fixture_dist(tmp_path))

    assert len(found) == 1
    assert "no Shortcuts database" in found[0]


def test_gate_refuses_a_copy_carrying_an_email(tmp_path):
    """The library reads the blobs; searching them for a credential is this gate's policy."""
    db = tmp_path / "Shortcuts.sqlite"
    _fixture_db(
        db,
        [
            ("Brightwheel Attendance", None, _actions_blob(339, text="parent@example.invalid")),
            ("Brightwheel Check In", None, _actions_blob(17)),
            ("Brightwheel Check Out", None, _actions_blob(17)),
        ],
    )

    found = check_library.gate(db, _fixture_dist(tmp_path))

    assert any("carries an email address" in f for f in found)

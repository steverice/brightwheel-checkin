"""Structural tests for Brightwheel Share Links. No simulator, no network.

The publisher can only really run on a device with iCloud, so these pin down the
properties that make it safe: nothing stored that can go stale, nothing minted
until every check has passed, and no numbered copy ever shared.

Run with the file named: pytest tests/test_publisher.py
"""
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import build_publisher as P  # noqa: E402
from update_links import NAMES  # noqa: E402

LINK = P.ICLOUD_LINK
REPEAT = "is.workflow.actions.repeat.each"
EXIT = "is.workflow.actions.exit"


def actions():
    return P.build()["WFWorkflowActions"]


def ident(a):
    return a["WFWorkflowActionIdentifier"]


def params(a):
    return a["WFWorkflowActionParameters"]


def positions(acts, identifier):
    return [n for n, a in enumerate(acts) if ident(a) == identifier]


def test_targets_are_the_names_the_page_uses():
    assert P.TARGETS == NAMES


def test_target_names_need_no_regex_escaping():
    # The patterns interpolate the names raw, which is safe only while they
    # hold nothing but letters and spaces.
    for t in P.TARGETS:
        assert re.fullmatch(r"[A-Za-z ]+", t), t


def test_every_link_is_found_at_run_time_not_picked():
    links = [a for a in actions() if ident(a) == LINK]
    assert len(links) == 3
    for a in links:
        assert params(a)["shortcut"] == {
            "Value": {"Type": "Variable", "VariableName": "Repeat Item"},
            "WFSerializationType": "WFTextTokenAttachment",
        }


def test_links_are_minted_inside_the_single_library_walk():
    acts = actions()
    opens = [n for n in positions(acts, REPEAT) if params(acts[n])["WFControlFlowMode"] == 0]
    closes = [n for n in positions(acts, REPEAT) if params(acts[n])["WFControlFlowMode"] == 2]
    assert len(opens) == 1 and len(closes) == 1
    assert all(opens[0] < n < closes[0] for n in positions(acts, LINK))


def test_nothing_is_minted_until_every_check_has_passed():
    acts = actions()
    exits = positions(acts, EXIT)
    assert len(exits) == 3 * len(P.TARGETS)  # more-than-one, numbered, missing
    assert max(exits) < min(positions(acts, LINK))


def test_each_target_is_checked_and_matched_by_name():
    pats = [params(a)["WFMatchTextPattern"] for a in actions()
            if ident(a) == "is.workflow.actions.text.match"]
    for t in P.TARGETS:
        assert f"(?m)^{t}( \\d+)?$" in pats   # more than one copy
        assert f"(?m)^{t} \\d+$" in pats      # the only copy is numbered
        assert f"(?m)^{t}$" in pats           # missing
        assert f"^{t}$" in pats               # the walk: exact name only


def test_patterns_mean_what_the_checks_claim():
    # Python's re agrees with ICU on these constructs; TESTING.md records the
    # Mac measurement ("A whole library coerces to its names, one per line").
    for t in P.TARGETS:
        lib = f"Other\n{t}\n{t} 1\nBrightwheel Share Links"
        assert len(re.findall(f"(?m)^{t}( \\d+)?$", lib)) == 2
        assert len(re.findall(f"(?m)^{t} \\d+$", lib)) == 1
        assert len(re.findall(f"(?m)^{t}$", lib)) == 1
        assert re.search(f"^{t}$", t) and not re.search(f"^{t}$", f"{t} 1")
        # macOS can also leave a second copy under the exact same name
        # (TESTING.md, "A duplicate isn't always numbered").
        same_name = f"Other\n{t}\n{t}\nBrightwheel Share Links"
        assert len(re.findall(f"(?m)^{t}( \\d+)?$", same_name)) == 2


def test_more_than_one_explains_the_copy_you_cannot_see():
    # On the Mac, choosing Replace leaves the old copy hidden from the app but
    # counted here, so the refusal has to say how to bring it back into view
    # (TESTING.md, "Replace hides the old copy from the app, and nowhere else").
    notes = {params(a)["WFNotificationActionTitle"]["Value"]["string"]:
             params(a)["WFNotificationActionBody"]["Value"]["string"]
             for a in actions() if ident(a) == "is.workflow.actions.notification"}
    for t in P.TARGETS:
        assert "Replace" in notes[f"More than one {t}"]


def test_clipboard_gets_the_markup_update_links_reads():
    acts = actions()
    markup = [a for a in acts if params(a).get("CustomOutputName") == "Markup"]
    assert len(markup) == 1
    token = params(markup[0])["WFTextActionText"]["Value"]
    for t in NAMES:
        assert f'">{t}</a></li>' in token["string"]
    used = {v.get("VariableName") for v in token["attachmentsByRange"].values()}
    assert used == {f"{t} Link" for t in P.TARGETS}
    assert len(positions(acts, "is.workflow.actions.setclipboard")) == 1


def test_control_flow_is_balanced():
    opened, closed = {}, {}
    for a in actions():
        p = params(a)
        g = p.get("GroupingIdentifier")
        if g is None:
            continue
        if p["WFControlFlowMode"] == 0:
            opened[g] = opened.get(g, 0) + 1
        elif p["WFControlFlowMode"] == 2:
            closed[g] = closed.get(g, 0) + 1
    assert opened == closed
    assert all(n == 1 for n in opened.values())


def test_it_asks_no_setup_questions():
    assert P.build()["WFWorkflowImportQuestions"] == []

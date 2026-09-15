"""Brightwheel Share Links: the parts that are this project's, not the library's.

The publisher's structure — nothing stored that can go stale, nothing minted
until every check has passed, no numbered copy ever shared — is tested in
shortcut-forge. What is pinned here is that it targets the three names the
page uses and hands back the markup `update_links.py` reads.

Run with the file named: pytest tests/test_publisher.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import build_publisher as publisher  # noqa: E402
from update_links import NAMES, RELEASES, with_version  # noqa: E402


def actions():
    return publisher.build()["WFWorkflowActions"]


def ident(a):
    return a["WFWorkflowActionIdentifier"]


def params(a):
    return a["WFWorkflowActionParameters"]


def test_targets_are_the_names_the_page_uses():
    assert publisher.TARGETS == NAMES


def test_target_names_need_no_regex_escaping():
    # The patterns interpolate the names raw, which is safe only while they
    # hold nothing but letters and spaces.
    for t in publisher.TARGETS:
        assert re.fullmatch(r"[A-Za-z ]+", t), t


def test_clipboard_gets_the_markup_update_links_reads():
    acts = actions()
    markup = [a for a in acts if params(a).get("CustomOutputName") == "Markup"]
    assert len(markup) == 1
    token = params(markup[0])["WFTextActionText"]["Value"]
    for t in NAMES:
        assert f'">{t}</a></li>' in token["string"]
    used = {v.get("VariableName") for v in token["attachmentsByRange"].values()}
    assert used == {f"{t} Link" for t in publisher.TARGETS}
    assert sum(1 for a in acts if ident(a) == "is.workflow.actions.setclipboard") == 1


def test_it_says_what_to_run_next():
    last = actions()[-1]
    assert ident(last) == "is.workflow.actions.showresult"
    assert "verify_links.py --clipboard --erase" in params(last)["Text"]["Value"]["string"]


def test_it_asks_no_setup_questions():
    assert publisher.build()["WFWorkflowImportQuestions"] == []
    assert publisher.build()["WFWorkflowName"] == publisher.NAME


def test_the_version_badge_takes_a_new_tag_in_both_places():
    page = f'<h3>Add all three <a class="version" href="{RELEASES}v1.4.0">v1.4.0</a></h3>'
    out = with_version(page, "v1.5.0")
    assert out == f'<h3>Add all three <a class="version" href="{RELEASES}v1.5.0">v1.5.0</a></h3>'
    assert with_version("<h3>Add all three</h3>", "v1.5.0") == "<h3>Add all three</h3>"


def test_the_shipped_page_has_exactly_one_badge():
    page = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    matches = re.findall(r'<a class="version" href="' + re.escape(RELEASES) + r'([^"]*)">([^<]*)</a>', page)
    assert len(matches) == 1
    assert matches[0][0] == matches[0][1]

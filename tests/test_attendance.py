#!/usr/bin/env python3
"""Integration tests: real shortcuts, real Shortcuts.app, fake Brightwheel.

These run the shipping shortcut on an iOS 27 simulator against a mock API and
assert on the HTTP traffic it produced. Traffic is the honest signal — "nobody
was checked in" is exactly "no POST reached /checkins/", whereas a notification
only says what the shortcut believes.

Run with ./test.sh. See TESTING.md for what the harness had to work around.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from shortcut_forge_lib.sim.certs import ensure_certs
from shortcut_forge_lib.sim.harness import Simulator

if TYPE_CHECKING:
    from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

import testbuild  # noqa: E402
from build_shortcuts import DAY_NAMES, SCHOOL_DAYS_KEY, SNOOZE_KEY  # noqa: E402
from console import error, info, success, warning  # noqa: E402
from mock_brightwheel import MockBrightwheel, Scenario  # noqa: E402

# The throwaway CA the simulator is told to trust. Disposable and gitignored.
TLS_DIR = Path(__file__).parent / "tls"

PORT = 8788
CHECK_IN = "Brightwheel Check In"
CHECK_OUT = "Brightwheel Check Out"
ATTENDANCE = "Brightwheel Attendance"
# The suite runs against a fixture roster of obvious placeholders, so nothing
# here names a real child or school. Once the shortcut reads its roster at
# runtime this file is no longer a build input — it is the dataset the mock
# serves, which is why every Scenario below passes `roster=ROSTER_ROWS`.
ROSTER = json.loads((Path(__file__).parent / "fixtures" / "roster.json").read_text())
CHILD_A, CHILD_B = (c["id"] for c in ROSTER["children"])
SCHOOL_ID = ROSTER["school_id"]
ROOM_ID = ROSTER["room_id"]
ROSTER_ROWS = [(c["id"], c["name"], ROOM_ID) for c in ROSTER["children"]]
# What the mock serves as the guardian's own object_id, and so what the
# roster URL must carry.
GUARDIAN_ID = Scenario().guardian_id
ARTIFACTS = Path(__file__).parent / "artifacts"
# What the suite keeps in the shared store between schedule tests: every day
# a school day and no snooze, so nothing else here depends on the calendar.
EVERY_DAY = {SCHOOL_DAYS_KEY: " ".join(DAY_NAMES), SNOOZE_KEY: None}


class Suite:
    """Shared, expensive setup: one build, one install, many scenarios."""

    def __init__(self, erase: bool = False, runtime: str = "iOS 27") -> None:
        self.erase = erase
        self.runtime = runtime

    def setup(self) -> None:
        ca, server = ensure_certs(TLS_DIR, ca_name="Brightwheel Test CA")
        self.sim = Simulator.find(runtime=self.runtime, artifacts=ARTIFACTS)
        info(f"  runtime {self.runtime}")
        info(f"  simulator {self.sim.udid}")
        if self.erase:
            info("  erasing device for a clean library")
            self.sim.erase()
        self.sim.prepare()
        self.sim.add_root_cert(ca)

        self.mock = MockBrightwheel(PORT, server).start()
        info(f"  mock Brightwheel on {self.mock.base}")

        paths = testbuild.build(self.mock.base)
        info(f"  built and signed {len(paths)} shortcuts against the mock")

        # Order is irrelevant — Run Shortcut resolves by name at run time,
        # not at import — but all three must exist before anything runs.
        for name in (ATTENDANCE, CHECK_IN, CHECK_OUT):
            fresh = self.sim.install(paths[name], expect_name=name)
            info(f"  {'installed' if fresh else 'already present'}: {name}")

        # The sign-in tests set the device's pasteboard with `set_pasteboard`, which
        # goes by way of the Mac's own, so remember what was on it and put it back.
        self.host_clipboard = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout

        # The schedule lives in the shared store, and nothing stored means
        # Monday to Friday, which would make the suite fail on a weekend.
        self.set_store(EVERY_DAY)
        self._prime()

    def set_store(self, values: dict[str, str | None], timeout: float = 45) -> None:
        """Write (or delete, for None) shared stored values through a probe shortcut, and wait until they read back."""
        name, path = testbuild.build_store_probe(values)
        self.sim.install(path, expect_name=name)
        self.sim.terminate_shortcuts()
        time.sleep(1.2)
        self.sim.run_shortcut(name)
        deadline = time.time() + timeout
        while time.time() < deadline:
            stored = self.sim.stored_content()
            if all(stored.get(k) == v for k, v in values.items()):
                return
            time.sleep(1.0)
        raise AssertionError(f"the store never showed {values}; it holds {self.sim.stored_content()}")

    def _prime(self) -> None:
        """One throwaway run so the consent prompts are answered up front."""
        self.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "in", CHILD_B: "in"}))
        self.run_and_settle(CHECK_IN, timeout=90)
        info(f"  consent primed ({len(self.mock.requests)} requests)")

    def teardown(self) -> None:
        self.mock.stop()
        subprocess.run(["pbcopy"], input=self.host_clipboard, text=True, check=False)

    # -- the run loop ----------------------------------------------------
    def run_and_settle(self, shortcut: str, timeout: float = 180, quiet: float = 10.0, min_wait: float = 18.0) -> None:
        """Start a shortcut, clear any prompts it raises, wait for it to stop.

        A consent prompt blocks the run and produces no traffic, so "no new
        requests" is the cue to look for a button, not to give up. min_wait
        exists because the first prompt takes a few seconds to appear, and an
        eager quiet-exit would end the run before it had begun.
        """
        self.sim.terminate_shortcuts()
        time.sleep(1.2)
        started = time.time()
        self.sim.run_shortcut(shortcut)
        seen, stable = len(self.mock.requests), time.time()
        self.taps = 0
        relaunched = False
        while time.time() - started < timeout:
            time.sleep(0.8)
            # A run URL delivered while Shortcuts is still shutting down is
            # silently dropped; nothing happens and no error is raised.
            if not relaunched and seen == 0 and time.time() - started > 20 and not self.sim.prompt_up():
                self.sim.run_shortcut(shortcut)
                relaunched = True
                stable = time.time()
                continue
            now = len(self.mock.requests)
            if now != seen:
                seen, stable = now, time.time()
                continue
            cleared = self.sim.clear_prompts()
            if cleared:
                self.taps += len(cleared)
                stable = time.time()
                continue
            # The runner's "One-time automation setup" sheet — shown once ever
            # per device, the first time any shortcut is run by URL — carries
            # its own Done at a seed of its own (SEEDS in the forge worktree),
            # well below the Ask for Input dialog's. clear_prompts() never
            # presses Done by default — that guard exists so a live code
            # prompt is never submitted empty — so this sheet needs its own
            # explicit, narrowly-scoped press. Safe here because run_and_settle
            # is never used while a code prompt is up (those tests drive the
            # Ask dialog by hand), so the only Done a hit test can find here is
            # this sheet's own.
            if self.sim.find_button("Done") is not None:
                self.sim.press("Done")
                self.taps += 1
                stable = time.time()
                continue
            if time.time() - started >= min_wait and time.time() - stable >= quiet:
                return
        raise AssertionError(f"{shortcut} did not settle within {timeout}s")

    # -- assertion helpers ----------------------------------------------
    def targets_of(self, posts: list[dict[str, Any]]) -> list[str | None]:
        out = []
        for r in posts:
            out.extend((entry.get("target") or {}).get("object_id") for entry in (r["body"] or {}).get("checkins", []))
        return out

    def unique_name(self, base: str) -> str:
        """A library name not already taken, so a re-run is still meaningful."""
        existing = set(self.sim.library())
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"


# ---------------------------------------------------------------------------
# Tests. Each gets a fresh scenario; the installed shortcuts never change.
# ---------------------------------------------------------------------------


def test_skips_children_already_in_the_wanted_state(s):
    """The idempotency guard: no POST at all when nothing needs changing."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "in", CHILD_B: "in"}))
    s.run_and_settle(CHECK_IN)

    assert _roster_requests(s), "the run should have read the roster to learn the current state"
    assert s.mock.checkins == [], f"expected no check-in to be sent, got {len(s.mock.checkins)}"


def test_checks_both_children_in(s):
    """The happy path, including the exact body Brightwheel is sent."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)

    posts = s.mock.checkins
    assert len(posts) == 2, f"expected 2 check-ins, got {len(posts)}"
    assert sorted(s.targets_of(posts)) == sorted([CHILD_A, CHILD_B]), f"wrong targets: {s.targets_of(posts)}"
    for r in posts:
        body = r["body"]
        entry = body["checkins"][0]
        assert entry["checked_in"] is True, f"checked_in should be true for a check-in, got {entry['checked_in']!r}"
        assert body["secret"] == "test-school-secret", f"school secret not sent: {body.get('secret')!r}"
        assert body["school_id"] == SCHOOL_ID, f"school_id not sent: {body.get('school_id')!r}"
        assert body["checkin_code"] == "1234", f"checkin_code not sent: {body.get('checkin_code')!r}"
        assert entry["target"]["object_id"], "target object_id was empty"


def test_check_out_sends_checked_in_false(s):
    """Direction is structural: the wrapper decides, not the clock."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "in", CHILD_B: "in"}))
    s.run_and_settle(CHECK_OUT)

    posts = s.mock.checkins
    assert len(posts) == 2, f"expected 2 check-outs, got {len(posts)}"
    for r in posts:
        entry = r["body"]["checkins"][0]
        assert entry["checked_in"] is False, f"checked_in should be false for a check-out, got {entry['checked_in']!r}"


def test_stale_school_code_causes_a_second_pass(s):
    """A rejected secret must be retried, not reported as a plain failure.

    On a real device the retry rescans the QR code. A test build seeds the
    school code at the top of every attempt instead — Scan Code does not exist
    on the simulator — so the fresh-code half is simulated by the mock
    accepting the same secret on the later attempt. What this proves is the
    part that lives in the shortcut: the stale reply is recognized, and a
    second pass happens.
    """
    s.mock.load(
        Scenario(
            roster=ROSTER_ROWS,
            states={CHILD_A: "out", CHILD_B: "out"},
            checkin_outcomes=["stale_secret", "stale_secret", "ok"],
        )
    )
    s.run_and_settle(CHECK_IN, timeout=120)

    posts = s.mock.checkins
    assert len(posts) > 2, f"expected a retry beyond the first pass, got {len(posts)} check-ins"
    stored = s.sim.stored_content()
    assert "BrightwheelSchoolCode" in stored, f"school code should be stored again after the retry, saw {list(stored)}"
    assert "test-school-secret" in (stored["BrightwheelSchoolCode"] or ""), (
        f"stored school code looks wrong: {stored['BrightwheelSchoolCode']!r}"
    )


def test_expired_token_signs_in_again(s):
    """E1200 sends the run down the two-step 2FA branch, and it recovers.

    Deliberately not using run_and_settle: this run raises an Ask for Input
    dialog whose Done button is iOS blue, and the generic prompt-clearing would
    submit it empty — which the shortcut treats as "send me another code".

    The code starts with a zero on purpose. The prompt is a number field, which
    drops a leading zero as the next digit is typed, and the shortcut pads the
    answer back out to six digits before exchanging it. This is the case that
    proves the padding, not just the happy path.
    """
    scenario = Scenario(
        token_valid=False, two_fa_code="012345", roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}
    )
    s.mock.load(scenario)
    _start_sign_in(s, CHECK_IN)

    time.sleep(4)
    s.sim.screenshot("sent-code-prompt.png")
    assert s.sim.answer_prompt(scenario.two_fa_code, expect="12345"), "no code prompt appeared to answer"

    s.mock.quiet_for(6, timeout=120)
    s.sim.clear_prompts()  # a pending output sheet makes the next test's first run do nothing

    exchanges = _exchanges(s)
    assert exchanges, "the code was never exchanged for a token"
    assert exchanges[0]["body"]["2fa_code"] == scenario.two_fa_code, (
        f"wrong code sent: {exchanges[0]['body'].get('2fa_code')!r}"
    )
    assert s.mock.checkins, "sign-in recovered but nobody was checked in"

    stored = s.sim.stored_content()
    assert stored.get("BrightwheelSessionToken") == scenario.issued_token, (
        f"the new token should have been stored, saw {stored}"
    )


def test_an_empty_code_answer_sends_another(s):
    """Leaving the code box empty is the resend control, and posts nothing.

    The number pad has no way to type "resend", so empty is the only path to a
    second code. Done with nothing in the field must reach /sessions/start
    again without ever reaching /sessions — a junk exchange would burn an
    attempt at Brightwheel.
    """
    scenario = Scenario(token_valid=False, roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"})
    s.mock.load(scenario)
    _start_sign_in(s, CHECK_IN)

    time.sleep(4)
    assert s.sim.answer_prompt(""), "no code prompt appeared to leave empty"

    # The second pass sends again and raises the prompt again.
    deadline = time.time() + 60
    while time.time() < deadline and len(s.mock.matching("POST", "/sessions/start")) < 2:
        time.sleep(1.0)
    starts = s.mock.matching("POST", "/sessions/start")
    assert len(starts) == 2, f"an empty answer should have sent a second code, saw {len(starts)} start(s)"
    assert not _exchanges(s), f"an empty answer must not be exchanged, saw {_exchanges(s)[0]['body']}"

    # Answer the second prompt properly so the run ends signed in, not stuck.
    time.sleep(4)
    assert s.sim.answer_prompt(scenario.two_fa_code), "no second code prompt appeared"
    s.mock.quiet_for(6, timeout=120)
    s.sim.clear_prompts()  # a pending output sheet makes the next test's first run do nothing
    exchanges = _exchanges(s)
    assert len(exchanges) == 1, f"expected one exchange after the real answer, saw {len(exchanges)}"
    assert exchanges[0]["body"]["2fa_code"] == scenario.two_fa_code
    assert s.mock.checkins, "sign-in recovered but nobody was checked in"


def test_rejected_credentials_stop_without_prompting(s):
    """A wrong email or password stops the run instead of asking for a code.

    Brightwheel only sends a code on a successful /sessions/start, so a run
    that gets E2053 has nothing to ask for. Asking anyway is what made a
    rejected password look like a code that never arrived.

    One start is the whole assertion. The prompt is also the resend control —
    an empty answer sends another code — so a run that still raised it would be
    answered empty by run_and_settle and reach five starts, not one.

    The send time matters as much as the prompt: stored on a rejected start, it
    would make the next run within ten minutes skip the send entirely and ask
    for a code that was never sent.
    """
    before = s.sim.stored_content().get("BrightwheelCodeSentAt")
    assert not before, f"a previous test left a send time stored, so this one cannot tell: {before!r}"

    scenario = Scenario(
        token_valid=False, credentials_valid=False, roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}
    )
    s.mock.load(scenario)
    s.sim.set_pasteboard("nothing to paste")
    s.run_and_settle(CHECK_IN, timeout=120)

    starts = s.mock.matching("POST", "/sessions/start")
    assert len(starts) == 1, f"a rejected sign-in should be tried once and not prompted, saw {len(starts)} start(s)"
    assert not _exchanges(s), f"nothing should be exchanged when no code was sent, saw {_exchanges(s)[0]['body']}"
    assert not s.mock.checkins, f"a run that never signed in must send nothing, saw {s.targets_of(s.mock.checkins)}"

    stored = s.sim.stored_content()
    assert not stored.get("BrightwheelCodeSentAt"), (
        f"a rejected start must not record a send time, saw {stored.get('BrightwheelCodeSentAt')!r}"
    )


def test_a_code_sent_minutes_ago_is_not_sent_again(s):
    """Cancel the prompt, read the email, run again: no second code is sent.

    The first run sends a code and stores when. The second run, minutes later,
    must raise the prompt without touching /sessions/start, exchange the code
    it is given, and forget the send time once the code is used up.

    Runs last among the sign-in tests on purpose: a failure here can leave a
    send time stored, and any sign-in test within ten minutes of it would then
    skip its send and time out waiting for one.
    """
    scenario = Scenario(token_valid=False, roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"})
    s.mock.load(scenario)
    _start_sign_in(s, CHECK_IN)

    time.sleep(4)
    assert s.sim.cancel_prompt(), "no code prompt appeared to cancel"
    s.mock.quiet_for(5, timeout=60)
    assert len(s.mock.matching("POST", "/sessions/start")) == 1, "the canceled run should have sent exactly one code"
    assert not _exchanges(s), "a canceled prompt must not exchange anything"
    assert s.sim.stored_content().get("BrightwheelCodeSentAt"), "the send time should have been stored"

    # Run again, as someone who went to read the email would.
    s.mock.load(scenario)
    s.sim.terminate_shortcuts()
    time.sleep(1.2)
    s.sim.run_shortcut(CHECK_IN)
    deadline = time.time() + 75
    while time.time() < deadline and s.sim.find_button("Done") is None:
        time.sleep(1.0)
    assert s.sim.find_button("Done") is not None, "the second run never raised the code prompt"
    s.sim.screenshot("remembered-send-prompt.png")
    assert not s.mock.matching("POST", "/sessions/start"), "a code sent minutes ago was sent again"

    time.sleep(1.5)
    assert s.sim.answer_prompt(scenario.two_fa_code), "could not answer the remembered prompt"
    s.mock.quiet_for(6, timeout=120)
    s.sim.clear_prompts()  # a pending output sheet makes the next test's first run do nothing
    exchanges = _exchanges(s)
    assert len(exchanges) == 1, f"expected one exchange, saw {len(exchanges)}"
    assert exchanges[0]["body"]["2fa_code"] == scenario.two_fa_code
    assert s.mock.checkins, "sign-in recovered but nobody was checked in"
    stored = s.sim.stored_content()
    assert stored.get("BrightwheelSessionToken") == scenario.issued_token, f"token not stored, saw {stored}"
    assert not stored.get("BrightwheelCodeSentAt"), (
        f"the send time should be cleared once the code is used, saw {stored}"
    )


def test_a_code_on_the_clipboard_is_offered(s):
    """Copy the code, run again, tap Done: the pasted code is what gets exchanged.

    The clipboard is read only on a run that has to sign in, and only a
    six-digit value is offered. Nothing is typed here on purpose — Done alone
    must submit what the sheet was prefilled with.
    """
    scenario = Scenario(
        token_valid=False, two_fa_code="654321", roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}
    )
    s.mock.load(scenario)
    _start_sign_in(s, CHECK_IN, clipboard=scenario.two_fa_code)

    deadline = time.time() + 40
    while time.time() < deadline and s.sim.find_button("Done") is None:
        time.sleep(1.0)
    time.sleep(1.5)
    s.sim.screenshot("clipboard-prefilled-prompt.png")
    s.sim.press("Done")  # the answer is already in the field, from the clipboard

    # A value that came off the clipboard gets its own consent the first time
    # it is sent anywhere: "Allow ... to send 1 text item to localhost?", with
    # Always Allow as the bottom button. The primed consents do not cover it.
    taps, seen, stable = 0, len(s.mock.requests), time.time()
    deadline = time.time() + 150
    while time.time() < deadline:
        time.sleep(0.8)
        now = len(s.mock.requests)
        if now != seen:
            seen, stable = now, time.time()
            continue
        cleared = s.sim.clear_prompts()
        if cleared:
            taps += len(cleared)
            s.sim.screenshot(f"clipboard-consent-{taps}.png")
            stable = time.time()
            continue
        if time.time() - stable >= 8:
            break
    info(f"    consents cleared after the pasted code: {taps}")
    exchanges = _exchanges(s)
    assert len(exchanges) == 1, f"expected one exchange, saw {len(exchanges)}"
    assert exchanges[0]["body"]["2fa_code"] == scenario.two_fa_code, (
        f"the pasted code was not what got sent: {exchanges[0]['body'].get('2fa_code')!r}"
    )
    assert s.mock.checkins, "sign-in recovered but nobody was checked in"
    s.sim.clear_prompts()  # a pending output sheet makes the next test's first run do nothing

    # The token a pasted code earned is stored, and it carries the clipboard's
    # provenance with it: on a fresh device the run after this one asked once
    # more, then no run asked again. Two follow-up runs, and the last must be
    # silent — that is what "Always Allow" has to mean for this to be usable.
    later = []
    for _ in range(2):
        s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
        s.run_and_settle(CHECK_IN)
        assert s.mock.checkins, "a later run with the earned token checked nobody in"
        later.append(s.taps)
    info(f"    consents on the two runs after it: {later}")
    assert later[-1] == 0, f"a token earned with a pasted code kept asking permission: {later} prompt(s) on later runs"


def _start_sign_in(s, shortcut: str, clipboard: str = "nothing to paste") -> None:
    """Run a shortcut whose token is dead, and return once a code has been sent.

    Consent prompts are cleared only while nothing has been sent yet; once
    traffic starts, the next blue button belongs to the code prompt, and
    tapping it would submit an empty answer.

    The clipboard is set first, because the shortcut offers a six-digit
    clipboard as the answer. A leftover code from an earlier test would turn a
    typed answer into twelve digits, so every sign-in starts from a known one.
    """
    s.sim.set_pasteboard(clipboard)
    s.sim.terminate_shortcuts()
    time.sleep(1.2)
    s.sim.run_shortcut(shortcut)
    deadline = time.time() + 75
    while time.time() < deadline:
        if s.mock.matching("POST", "/sessions/start"):
            return
        if not s.mock.requests:
            s.sim.clear_prompts()
        time.sleep(1.0)
    raise AssertionError("shortcut never asked Brightwheel to send a code")


def _exchanges(s) -> list[dict[str, Any]]:
    """Every POST /sessions, the step that trades a code for a token."""
    return [r for r in s.mock.requests if r["method"] == "POST" and r["path"].endswith("/sessions")]


def test_setup_questions_commit_their_answers(s):
    """Canary: does answering an import question actually configure the shortcut?

    This is the mechanism `dist/` relies on — the shipping build ships
    placeholders and expects setup to fill them in. It is deliberately tiny, so
    when it fails it is saying something about iOS and nothing about Brightwheel.

    Known broken from iOS 27 beta 24A5408d through the 27.0 release candidate
    24A434: the wizard collects the answers and "Add Shortcut" then does nothing
    at all, with no error logged. Beta 7 (24A5424a) was checked on a physical
    iPhone because Apple published no simulator runtime past beta 6 — so this is
    not a simulator artifact.
    Verified working on iOS 26.5 (23F77) and on iOS 27 beta 24A5355p, so the
    question shape is right and this is a regression to wait out. When this
    starts passing, drop its entry from KNOWN_BROKEN.

    This still tests "Add Shortcut", deliberately, because that is the button
    whose repair we are waiting on. Skip Setup does commit the answers on
    24A434, and that is what the shipping build now tells the user to reach
    for — see TESTING.md — but a canary that tapped Skip Setup would go green
    while the actual bug was still there.
    """
    name = s.unique_name("Setup Canary")
    path = testbuild.build_setup_probe(name)
    marker = "246813"  # digits: immune to the keyboard's autocapitalization

    s.sim.terminate_shortcuts()
    time.sleep(1.2)
    subprocess.run(["xcrun", "simctl", "openurl", s.sim.udid, "file://" + urllib.parse.quote(str(path))], check=True)
    time.sleep(4)

    s.sim.press("Set Up Shortcut")
    time.sleep(3)
    assert s.sim.fill(marker) == marker
    s.sim.confirm("Add Shortcut", "Next")
    time.sleep(4)

    assert name in s.sim.library(), "answering the setup question left the shortcut uninstalled"
    actions = s.sim.shortcut_actions(name)
    value = actions[0]["WFWorkflowActionParameters"]["WFTextActionText"]
    assert value == marker, f"setup answer did not reach the action: {value!r} (wanted {marker!r})"


# Keyed by function rather than stashed as an attribute on it, so a type
# checker can see the mapping's shape instead of a dynamic attribute nothing
# declares.
KNOWN_BROKEN = {
    test_setup_questions_commit_their_answers: (
        "iOS 27 regression: Add Shortcut is inert once a question is answered "
        "(works on iOS 26.5 and on iOS 27 beta 24A5355p; still broken on a device "
        "at beta 7, 24A5424a, and on the 27.0 release candidate, 24A434). "
        "Skip Setup commits the answers and is the documented way through."
    ),
}


def _roster_requests(s: Suite) -> list[dict[str, Any]]:
    return s.mock.matching(contains="students_for_checkin")


def test_reads_the_roster_at_runtime(s):
    """The shortcut asks the API who the children are, instead of being told.

    This is the whole point of the change: a build with no children in it.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert _roster_requests(s), "the shortcut never asked for a roster"
    posts = s.mock.checkins
    assert sorted(s.targets_of(posts)) == sorted([CHILD_A, CHILD_B]), (
        f"expected both children from the roster, got {s.targets_of(posts)}"
    )


def test_the_roster_call_carries_the_guardian_id(s):
    """The id must survive being read out of GET /users/me.

    It is read by key name off the parsed body. An earlier version matched it
    with a pattern anchored to the front of the response, which held only for
    as long as Shortcuts happened to re-serialize object_id back into first
    place. When that stopped, the roster URL lost its guardian segment, 404'd,
    and the run announced that Brightwheel had returned no children — a broken
    read filed as a broken API. The mock serves object_id out of first place,
    so a positional read cannot pass here.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    calls = _roster_requests(s)
    assert calls, "the shortcut never asked for a roster"
    assert f"/guardians/{GUARDIAN_ID}/" in calls[0]["path"], f"the roster call lost its guardian id: {calls[0]['path']}"


def test_an_unreadable_guardian_id_stops_loudly(s):
    """No id means no roster call at all, rather than one that 404s.

    An empty guardian id still builds a well-formed URL, and Brightwheel
    answers it 404 with no students in the body — indistinguishable downstream
    from a school with nobody enrolled. So the run has to stop at the read.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, guardian_id_readable=False, states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "an unidentified account must not check anybody in"
    assert not _roster_requests(s), "the run should stop at the guardian id, not ask for a roster without one"


def test_a_failed_roster_stops_loudly(s):
    """An error body must not read as an empty roster.

    Every count the guards derive is zero on a failed call, so without a
    positive test the run does nothing at all and says nothing — which looks
    exactly like the trigger never firing.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="error", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "a failed roster call must not check anybody in"


def test_a_restructured_roster_stops(s):
    """Students present, child shape changed: nobody has a room to send to."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="restructured", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "a roster whose child shape changed must not check anybody in"


def test_a_child_in_two_rooms_stops(s):
    """The extraction takes room_states[0], so multiplicity is refused.

    Proceeding would check the child into the room they are not in, which the
    right teacher sees as an absence.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="two_rooms", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "a child with two room_states must not be checked into a guessed room"


def test_a_dropped_child_stops(s):
    """One child with no room means the run stops, rather than half-runs."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="empty_room_states", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "a child dropped from the parse must stop the run"


def test_a_room_with_no_state_stops(s):
    """A room entry that lost its `checked_in` key must stop the run.

    The one malformation the per-child room count cannot see: the child still
    has exactly one room, so both per-child guards pass. Only the whole-roster
    comparison of children against `"checked_in"` keys catches it, and this is
    the only test that exercises that guard.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="unreadable_state", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "a room with no check-in state must not check anybody in"


def test_two_children_canceling_out_stops(s):
    """One child in two rooms and one with no room must still stop the run.

    The case every whole-roster count agrees on. Two children, two
    "room_states" keys and two "checked_in" keys, so children-minus-states and
    states-minus-children are both zero, and no comparison of totals can see
    that both children are wrong.

    The two-room child is first and needs sending, so without a per-child check
    the run reaches a real POST into a guessed room before it ever looks at the
    child who has none. That POST is what this asserts against: reversing the
    two children ends the run on a failed lookup instead, which is the right
    outcome for the wrong reason and proves nothing.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="canceling", states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, "two children failing in opposite directions must stop the run"


def test_a_rotated_secret_on_the_roster_call_recovers(s):
    """The roster call now sees the rotation before any POST does.

    Detection lived only on the check-in response, so without a branch here the
    self-heal dies silently and the run looks like a dead trigger.
    """
    # The school has rotated: the code the shortcut holds is no longer the one
    # the API accepts, so the roster call is the first thing to be rejected.
    sc = Scenario(
        roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}, required_secret="a-freshly-rotated-secret"
    )
    s.mock.load(sc)
    s.run_and_settle(CHECK_IN, timeout=120)
    assert len(_roster_requests(s)) > 1, "a rotated secret should make the run ask for the roster again"


# ---------------------------------------------------------------------------
# The schedule. The settings live in the shared store, where the menu's
# "Set school days" and "Snooze until a date" put them, so a test writes the
# store through a probe shortcut and runs the real Check In. Each puts the
# suite's every-day setting back, whatever happened.
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.date.today().strftime("%A")


def _run_with_store(s: Suite, values: dict[str, str | None]) -> None:
    try:
        s.set_store(values)
        s.run_and_settle(CHECK_IN)
    finally:
        s.set_store(EVERY_DAY)


def test_a_day_that_is_not_a_school_day_sends_nothing(s):
    """Every day but today is stored as a school day, so today's automation must stop before any request."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    others = " ".join(d for d in DAY_NAMES if d != _today())
    _run_with_store(s, {SCHOOL_DAYS_KEY: others})
    assert not s.mock.checkins, f"a day off must not check anybody in, saw {s.targets_of(s.mock.checkins)}"
    assert not _roster_requests(s), "a day off should stop before the roster is even asked for"
    assert not s.mock.requests, f"a day off should make no request at all, saw {len(s.mock.requests)}"


def test_a_school_day_named_by_three_letters_runs(s):
    """Only today is stored, and only by its first three letters: the run goes ahead."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    _run_with_store(s, {SCHOOL_DAYS_KEY: _today()[:3]})
    assert sorted(s.targets_of(s.mock.checkins)) == sorted([CHILD_A, CHILD_B]), (
        f"a school day stored as {_today()[:3]!r} should check both children in, got {s.targets_of(s.mock.checkins)}"
    )


def test_nothing_stored_means_monday_to_friday(s):
    """With no school days ever set, a weekday runs and a weekend day does not."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    _run_with_store(s, {SCHOOL_DAYS_KEY: None})
    weekday = datetime.date.today().weekday() < 5
    if weekday:
        assert s.mock.checkins, f"a {_today()} with nothing stored should run as a school day, and sent nothing"
    else:
        assert not s.mock.checkins, f"a {_today()} with nothing stored should be a day off, saw a check-in"


def test_a_snooze_until_tomorrow_sends_nothing(s):
    """Snoozed until tomorrow: today stops before any request."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    _run_with_store(s, {SNOOZE_KEY: tomorrow})
    assert not s.mock.checkins, f"a snoozed run must not check anybody in, saw {s.targets_of(s.mock.checkins)}"
    assert not s.mock.requests, f"a snoozed run should make no request at all, saw {len(s.mock.requests)}"


def test_a_snooze_until_today_has_ended(s):
    """The snooze date is the first day back, so a snooze until today runs today."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    _run_with_store(s, {SNOOZE_KEY: datetime.date.today().isoformat()})
    assert sorted(s.targets_of(s.mock.checkins)) == sorted([CHILD_A, CHILD_B]), (
        f"a snooze that ends today should check both children in, got {s.targets_of(s.mock.checkins)}"
    )


TESTS = [
    test_skips_children_already_in_the_wanted_state,
    test_checks_both_children_in,
    test_check_out_sends_checked_in_false,
    test_stale_school_code_causes_a_second_pass,
    test_expired_token_signs_in_again,
    test_an_empty_code_answer_sends_another,
    test_rejected_credentials_stop_without_prompting,
    test_a_code_sent_minutes_ago_is_not_sent_again,
    test_a_code_on_the_clipboard_is_offered,
    test_setup_questions_commit_their_answers,
    test_reads_the_roster_at_runtime,
    test_the_roster_call_carries_the_guardian_id,
    test_an_unreadable_guardian_id_stops_loudly,
    test_a_failed_roster_stops_loudly,
    test_a_restructured_roster_stops,
    test_a_child_in_two_rooms_stops,
    test_a_dropped_child_stops,
    test_a_room_with_no_state_stops,
    test_two_children_canceling_out_stops,
    test_a_rotated_secret_on_the_roster_call_recovers,
    test_a_day_that_is_not_a_school_day_sends_nothing,
    test_a_school_day_named_by_three_letters_runs,
    test_nothing_stored_means_monday_to_friday,
    test_a_snooze_until_tomorrow_sends_nothing,
    test_a_snooze_until_today_has_ended,
]


def _audit_tests() -> None:
    """Fail before the simulator boots if a test cannot fail.

    A test whose body was lost to an edit still prints PASS, and a test written
    but never added to TESTS never runs at all. Both read as green. This walks
    this file's own syntax tree and refuses to start on either.
    """
    import ast

    tree = ast.parse(pathlib.Path(__file__).read_text())
    defined = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    registered = {t.__name__ for t in TESTS}

    problems = [f"{name} is defined but not in TESTS, so it never runs" for name in sorted(defined.keys() - registered)]
    problems.extend(f"{name} is in TESTS but not defined in this file" for name in sorted(registered - defined.keys()))
    problems.extend(
        f"{name} has no assert, so it cannot fail"
        for name in sorted(registered & defined.keys())
        if not any(isinstance(s, ast.Assert) for s in ast.walk(defined[name]))
    )
    if problems:
        raise SystemExit("test suite is not sound:\n  " + "\n  ".join(problems))


def main(argv: list[str]) -> int:
    _audit_tests()
    runtime = "iOS 27"
    if "--runtime" in argv:
        i = argv.index("--runtime")
        runtime = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    only = [a for a in argv if not a.startswith("-")]
    suite = Suite(erase="--erase" in argv, runtime=runtime)
    info("setup:")
    suite.setup()

    chosen = [t for t in TESTS if not only or any(o in t.__name__ for o in only)]
    info(f"\nrunning {len(chosen)} test(s):\n")
    failures, known = [], []
    for t in chosen:
        label = t.__name__.replace("_", " ")
        info(f"  … {label}")
        broken = KNOWN_BROKEN.get(t)
        try:
            t(suite)
            if broken:
                success(
                    f"[green]FIXED[/green] {label}\n        this was expected to fail — drop it from KNOWN_BROKEN\n"
                )
            else:
                success(f"[green]PASS[/green] {label}\n")
        except Exception as exc:  # noqa: BLE001 - a failing test must not stop the suite
            shot = suite.sim.screenshot(f"FAIL-{t.__name__}.png")
            if broken:
                warning(f"[yellow]KNOWN[/yellow] {label}\n        {broken}\n        (failed as expected: {exc})\n")
                known.append(t.__name__)
            else:
                error(f"[red]FAIL[/red] {label}\n        {exc}\n        screenshot: {shot}\n")
                failures.append(t.__name__)
    suite.teardown()

    info("-" * 60)
    if known:
        info(f"{len(known)} known-broken (not counted as failures): {', '.join(known)}")
    if failures:
        error(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    success(f"all {len(chosen) - len(known)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

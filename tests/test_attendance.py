#!/usr/bin/env python3
"""Integration tests: real shortcuts, real Shortcuts.app, fake Brightwheel.

These run the shipping shortcut on an iOS 27 simulator against a mock API and
assert on the HTTP traffic it produced. Traffic is the honest signal — "nobody
was checked in" is exactly "no POST reached /checkins/", whereas a notification
only says what the shortcut believes.

Run with ./test.sh. See TESTING.md for what the harness had to work around.
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

import build_shortcuts as gen                      # noqa: E402
from certs import ensure_certs                     # noqa: E402
from mock_brightwheel import MockBrightwheel, Scenario  # noqa: E402
from simharness import Simulator                   # noqa: E402
import testbuild                                   # noqa: E402

PORT = 8788
CHECK_IN = "Brightwheel Check In"
CHECK_OUT = "Brightwheel Check Out"
ATTENDANCE = "Brightwheel Attendance"
KIDS = {name: oid for name, oid in gen.CHILDREN}
CHILD_A, CHILD_B = gen.CHILD_A, gen.CHILD_B
ARTIFACTS = Path(__file__).parent / "artifacts"


class Suite:
    """Shared, expensive setup: one build, one install, many scenarios."""

    def __init__(self, erase=False):
        self.erase = erase

    def setup(self):
        ca, server = ensure_certs()
        self.sim = Simulator.find(artifacts=ARTIFACTS)
        print(f"  simulator {self.sim.udid}")
        if self.erase:
            print("  erasing device for a clean library")
            self.sim.erase()
        self.sim.prepare_window()
        self.sim.add_root_cert(ca)

        self.mock = MockBrightwheel(PORT, server).start()
        print(f"  mock Brightwheel on {self.mock.base}")

        paths = testbuild.build(self.mock.base)
        print(f"  built and signed {len(paths)} shortcuts against the mock")

        # Attendance first: the wrappers call it by name.
        for name in (ATTENDANCE, CHECK_IN, CHECK_OUT):
            fresh = self.sim.install(paths[name], expect_name=name)
            print(f"  {'installed' if fresh else 'already present'}: {name}")

        self._prime()

    def _prime(self):
        """One throwaway run so the consent prompts are answered up front."""
        self.mock.load(Scenario(states={CHILD_A: "in", CHILD_B: "in"}))
        self.run_and_settle(CHECK_IN, timeout=90)
        print(f"  consent primed ({len(self.mock.requests)} requests)")

    def teardown(self):
        self.mock.stop()

    # -- the run loop ----------------------------------------------------
    def run_and_settle(self, shortcut, timeout=150, quiet=6.0, min_wait=14.0):
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
        while time.time() - started < timeout:
            time.sleep(0.8)
            now = len(self.mock.requests)
            if now != seen:
                seen, stable = now, time.time()
                continue
            if self.sim.tap_affirmative():
                stable = time.time()
                continue
            if (time.time() - started >= min_wait
                    and time.time() - stable >= quiet):
                return
        raise AssertionError(f"{shortcut} did not settle within {timeout}s")

    # -- assertion helpers ----------------------------------------------
    def targets_of(self, posts):
        out = []
        for r in posts:
            for entry in (r["body"] or {}).get("checkins", []):
                out.append((entry.get("target") or {}).get("object_id"))
        return out

    def activity_ids(self):
        return [r["path"].split("/students/")[1].split("/")[0]
                for r in self.mock.matching("GET", "/students/")]


# ---------------------------------------------------------------------------
# Tests. Each gets a fresh scenario; the installed shortcuts never change.
# ---------------------------------------------------------------------------

def test_skips_children_already_in_the_wanted_state(s):
    """The idempotency guard: no POST at all when nothing needs changing."""
    s.mock.load(Scenario(states={CHILD_A: "in", CHILD_B: "in"}))
    s.run_and_settle(CHECK_IN)

    assert sorted(s.activity_ids()) == sorted([CHILD_A, CHILD_B]), \
        f"expected both children's state to be read, got {s.activity_ids()}"
    assert s.mock.checkins == [], \
        f"expected no check-in to be sent, got {len(s.mock.checkins)}"


def test_checks_both_children_in(s):
    """The happy path, including the exact body Brightwheel is sent."""
    s.mock.load(Scenario(states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)

    posts = s.mock.checkins
    assert len(posts) == 2, f"expected 2 check-ins, got {len(posts)}"
    assert sorted(s.targets_of(posts)) == sorted([CHILD_A, CHILD_B]), \
        f"wrong targets: {s.targets_of(posts)}"
    for r in posts:
        body = r["body"]
        entry = body["checkins"][0]
        assert entry["checked_in"] is True, \
            f"checked_in should be true for a check-in, got {entry['checked_in']!r}"
        assert body["secret"] == "test-school-secret", \
            f"school secret not sent: {body.get('secret')!r}"
        assert body["school_id"] == gen.SCHOOL, \
            f"school_id not sent: {body.get('school_id')!r}"
        assert body["checkin_code"] == "1234", \
            f"checkin_code not sent: {body.get('checkin_code')!r}"
        assert entry["target"]["object_id"], "target object_id was empty"


def test_check_out_sends_checked_in_false(s):
    """Direction is structural: the wrapper decides, not the clock."""
    s.mock.load(Scenario(states={CHILD_A: "in", CHILD_B: "in"}))
    s.run_and_settle(CHECK_OUT)

    posts = s.mock.checkins
    assert len(posts) == 2, f"expected 2 check-outs, got {len(posts)}"
    for r in posts:
        entry = r["body"]["checkins"][0]
        assert entry["checked_in"] is False, \
            f"checked_in should be false for a check-out, got {entry['checked_in']!r}"


def test_stale_school_code_causes_a_second_pass(s):
    """A rejected secret must be retried, not reported as a plain failure.

    On a real device the retry rescans the QR code. A test build seeds the
    school code at the top of every attempt instead — Scan Code does not exist
    on the simulator — so the fresh-code half is simulated by the mock
    accepting the same secret on the later attempt. What this proves is the
    part that lives in the shortcut: the stale reply is recognized, and a
    second pass happens.
    """
    s.mock.load(Scenario(states={CHILD_A: "out", CHILD_B: "out"},
                         checkin_outcomes=["stale_secret", "stale_secret", "ok"]))
    s.run_and_settle(CHECK_IN, timeout=120)

    posts = s.mock.checkins
    assert len(posts) > 2, \
        f"expected a retry beyond the first pass, got {len(posts)} check-ins"
    stored = s.sim.stored_content()
    assert "BrightwheelSchoolCode" in stored, \
        f"school code should be stored again after the retry, saw {list(stored)}"
    assert "test-school-secret" in (stored["BrightwheelSchoolCode"] or ""), \
        f"stored school code looks wrong: {stored['BrightwheelSchoolCode']!r}"


def test_expired_token_signs_in_again(s):
    """E1200 sends the run down the two-step 2FA branch, and it recovers.

    Deliberately not using run_and_settle: this run raises an Ask for Input
    dialog whose Done button is iOS blue, and the generic prompt-clearing would
    submit it empty — which the shortcut treats as "send me another code".
    """
    scenario = Scenario(token_valid=False, states={CHILD_A: "out", CHILD_B: "out"})
    s.mock.load(scenario)

    s.sim.terminate_shortcuts()
    time.sleep(1.2)
    s.sim.run_shortcut(CHECK_IN)

    # Clear consent prompts only while nothing has been sent yet; once traffic
    # starts, the next blue button belongs to the code prompt.
    deadline = time.time() + 75
    while time.time() < deadline:
        if s.mock.matching("POST", "/sessions/start"):
            break
        if not s.mock.requests:
            s.sim.tap_affirmative()
        time.sleep(1.0)
    else:
        raise AssertionError("shortcut never asked Brightwheel to send a code")

    time.sleep(4)
    assert s.sim.answer_prompt(scenario.two_fa_code), \
        "no code prompt appeared to answer"

    s.mock.quiet_for(6, timeout=120)

    exchanges = [r for r in s.mock.requests
                 if r["method"] == "POST" and r["path"].endswith("/sessions")]
    assert exchanges, "the code was never exchanged for a token"
    assert exchanges[0]["body"]["2fa_code"] == scenario.two_fa_code, \
        f"wrong code sent: {exchanges[0]['body'].get('2fa_code')!r}"
    assert s.mock.checkins, "sign-in recovered but nobody was checked in"

    stored = s.sim.stored_content()
    assert stored.get("BrightwheelSessionToken") == scenario.issued_token, \
        f"the new token should have been stored, saw {stored}"


TESTS = [
    test_skips_children_already_in_the_wanted_state,
    test_checks_both_children_in,
    test_check_out_sends_checked_in_false,
    test_stale_school_code_causes_a_second_pass,
    test_expired_token_signs_in_again,
]


def main(argv):
    only = [a for a in argv if not a.startswith("-")]
    suite = Suite(erase="--erase" in argv)
    print("setup:")
    suite.setup()

    chosen = [t for t in TESTS if not only or any(o in t.__name__ for o in only)]
    print(f"\nrunning {len(chosen)} test(s):\n")
    failures = []
    for t in chosen:
        label = t.__name__.replace("_", " ")
        print(f"  … {label}", flush=True)
        try:
            t(suite)
            print(f"  \033[32mPASS\033[0m {label}\n")
        except Exception as exc:
            shot = suite.sim.screenshot(f"FAIL-{t.__name__}.png")
            print(f"  \033[31mFAIL\033[0m {label}\n        {exc}\n"
                  f"        screenshot: {shot}\n")
            failures.append(t.__name__)
    suite.teardown()

    print("-" * 60)
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print(f"all {len(chosen)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Integration tests: real shortcuts, real Shortcuts.app, fake Brightwheel.

These run the shipping shortcut on an iOS 27 simulator against a mock API and
assert on the HTTP traffic it produced. Traffic is the honest signal — "nobody
was checked in" is exactly "no POST reached /checkins/", whereas a notification
only says what the shortcut believes.

Run with ./test.sh. See TESTING.md for what the harness had to work around.
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from certs import ensure_certs                     # noqa: E402
from mock_brightwheel import MockBrightwheel, Scenario  # noqa: E402
from simharness import Simulator                   # noqa: E402
import testbuild                                   # noqa: E402

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
ARTIFACTS = Path(__file__).parent / "artifacts"


class Suite:
    """Shared, expensive setup: one build, one install, many scenarios."""

    def __init__(self, erase=False, runtime="iOS 27"):
        self.erase = erase
        self.runtime = runtime

    def setup(self):
        ca, server = ensure_certs()
        self.sim = Simulator.find(runtime=self.runtime, artifacts=ARTIFACTS)
        print(f"  runtime {self.runtime}")
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

        # Order is irrelevant — Run Shortcut resolves by name at run time,
        # not at import — but all three must exist before anything runs.
        for name in (ATTENDANCE, CHECK_IN, CHECK_OUT):
            fresh = self.sim.install(paths[name], expect_name=name)
            print(f"  {'installed' if fresh else 'already present'}: {name}")

        self._prime()

    def _prime(self):
        """One throwaway run so the consent prompts are answered up front."""
        self.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "in", CHILD_B: "in"}))
        self.run_and_settle(CHECK_IN, timeout=90)
        print(f"  consent primed ({len(self.mock.requests)} requests)")

    def teardown(self):
        self.mock.stop()

    # -- the run loop ----------------------------------------------------
    def run_and_settle(self, shortcut, timeout=180, quiet=10.0, min_wait=18.0):
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
        relaunched = False
        while time.time() - started < timeout:
            time.sleep(0.8)
            # A run URL delivered while Shortcuts is still shutting down is
            # silently dropped; nothing happens and no error is raised.
            if (not relaunched and seen == 0
                    and time.time() - started > 20
                    and not self.sim.blue_buttons()):
                self.sim.run_shortcut(shortcut)
                relaunched = True
                stable = time.time()
                continue
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

    def unique_name(self, base):
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

    assert _roster_requests(s), \
        "the run should have read the roster to learn the current state"
    assert s.mock.checkins == [], \
        f"expected no check-in to be sent, got {len(s.mock.checkins)}"


def test_checks_both_children_in(s):
    """The happy path, including the exact body Brightwheel is sent."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
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
        assert body["school_id"] == SCHOOL_ID, \
            f"school_id not sent: {body.get('school_id')!r}"
        assert body["checkin_code"] == "1234", \
            f"checkin_code not sent: {body.get('checkin_code')!r}"
        assert entry["target"]["object_id"], "target object_id was empty"


def test_check_out_sends_checked_in_false(s):
    """Direction is structural: the wrapper decides, not the clock."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "in", CHILD_B: "in"}))
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
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"},
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
    scenario = Scenario(token_valid=False, roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"})
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



def test_setup_questions_commit_their_answers(s):
    """Canary: does answering an import question actually configure the shortcut?

    This is the mechanism `dist/` relies on — the shipping build ships
    placeholders and expects setup to fill them in. It is deliberately tiny, so
    when it fails it is saying something about iOS and nothing about Brightwheel.

    Known broken on iOS 27 betas from 24A5408d onwards: the wizard collects the
    answers and "Add Shortcut" then does nothing at all, with no error logged.
    Verified working on iOS 26.5 (23F77) and on iOS 27 beta 24A5355p, so the
    question shape is right and this is a regression to wait out. When this
    starts passing, drop the expected_broken marker.
    """
    name = s.unique_name("Setup Canary")
    path = testbuild.build_setup_probe(name)
    marker = "246813"          # digits: immune to the keyboard's autocapitalisation

    s.sim.terminate_shortcuts()
    time.sleep(1.2)
    subprocess.run(["xcrun", "simctl", "openurl", s.sim.udid,
                    "file://" + urllib.parse.quote(str(path))], check=True)
    time.sleep(4)

    assert s.sim.tap_affirmative(), "no Set Up Shortcut button on the import sheet"
    time.sleep(3)

    img = s.sim.image()
    w, h = img.size
    s.sim.tap(int(w * 0.33), int(h * 0.335), device_size=img.size)   # the answer field
    time.sleep(0.8)
    s.sim.type_text(marker)
    time.sleep(0.5)
    assert s.sim.tap_affirmative(), "no Add Shortcut button after answering"
    time.sleep(4)

    assert name in s.sim.library(), \
        "answering the setup question left the shortcut uninstalled"
    actions = s.sim.shortcut_actions(name)
    value = actions[0]["WFWorkflowActionParameters"]["WFTextActionText"]
    assert value == marker, \
        f"setup answer did not reach the action: {value!r} (wanted {marker!r})"


test_setup_questions_commit_their_answers.expected_broken = (
    "iOS 27 beta regression: Add Shortcut is inert once a question is answered "
    "(works on iOS 26.5 and on iOS 27 beta 24A5355p)")


def _roster_requests(s):
    return s.mock.matching(contains="students_for_checkin")


def test_reads_the_roster_at_runtime(s):
    """The shortcut asks the API who the children are, instead of being told.

    This is the whole point of the change: a build with no children in it.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert _roster_requests(s), "the shortcut never asked for a roster"
    posts = s.mock.checkins
    assert sorted(s.targets_of(posts)) == sorted([CHILD_A, CHILD_B]), \
        f"expected both children from the roster, got {s.targets_of(posts)}"


def test_a_failed_roster_stops_loudly(s):
    """An error body must not read as an empty roster.

    Every count the guards derive is zero on a failed call, so without a
    positive test the run does nothing at all and says nothing — which looks
    exactly like the trigger never firing.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="error",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "a failed roster call must not check anybody in"


def test_a_restructured_roster_stops(s):
    """Students present, child shape changed: nobody has a room to send to."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="restructured",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "a roster whose child shape changed must not check anybody in"


def test_a_child_in_two_rooms_stops(s):
    """The extraction takes room_states[0], so multiplicity is refused.

    Proceeding would check the child into the room they are not in, which the
    right teacher sees as an absence.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="two_rooms",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "a child with two room_states must not be checked into a guessed room"


def test_a_dropped_child_stops(s):
    """One child with no room means the run stops, rather than half-runs."""
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="empty_room_states",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "a child dropped from the parse must stop the run"


def test_a_room_with_no_state_stops(s):
    """A room entry that lost its `checked_in` key must stop the run.

    The one malformation the per-child room count cannot see: the child still
    has exactly one room, so both per-child guards pass. Only the whole-roster
    comparison of children against `"checked_in"` keys catches it, and this is
    the only test that exercises that guard.
    """
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="unreadable_state",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "a room with no check-in state must not check anybody in"


def test_two_children_cancelling_out_stops(s):
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
    s.mock.load(Scenario(roster=ROSTER_ROWS, roster_shape="cancelling",
                         states={CHILD_A: "out", CHILD_B: "out"}))
    s.run_and_settle(CHECK_IN)
    assert not s.mock.checkins, \
        "two children failing in opposite directions must stop the run"


def test_a_rotated_secret_on_the_roster_call_recovers(s):
    """The roster call now sees the rotation before any POST does.

    Detection lived only on the check-in response, so without a branch here the
    self-heal dies silently and the run looks like a dead trigger.
    """
    # The school has rotated: the code the shortcut holds is no longer the one
    # the API accepts, so the roster call is the first thing to be rejected.
    sc = Scenario(roster=ROSTER_ROWS, states={CHILD_A: "out", CHILD_B: "out"},
                  required_secret="a-freshly-rotated-secret")
    s.mock.load(sc)
    s.run_and_settle(CHECK_IN, timeout=120)
    assert len(_roster_requests(s)) > 1, \
        "a rotated secret should make the run ask for the roster again"


TESTS = [
    test_skips_children_already_in_the_wanted_state,
    test_checks_both_children_in,
    test_check_out_sends_checked_in_false,
    test_stale_school_code_causes_a_second_pass,
    test_expired_token_signs_in_again,
    test_setup_questions_commit_their_answers,
    test_reads_the_roster_at_runtime,
    test_a_failed_roster_stops_loudly,
    test_a_restructured_roster_stops,
    test_a_child_in_two_rooms_stops,
    test_a_dropped_child_stops,
    test_a_room_with_no_state_stops,
    test_two_children_cancelling_out_stops,
    test_a_rotated_secret_on_the_roster_call_recovers,
]


def _audit_tests():
    """Fail before the simulator boots if a test cannot fail.

    A test whose body was lost to an edit still prints PASS, and a test written
    but never added to TESTS never runs at all. Both read as green. This walks
    this file's own syntax tree and refuses to start on either.
    """
    import ast
    tree = ast.parse(pathlib.Path(__file__).read_text())
    defined = {n.name: n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    registered = {t.__name__ for t in TESTS}

    problems = []
    for name in sorted(defined.keys() - registered):
        problems.append(f"{name} is defined but not in TESTS, so it never runs")
    for name in sorted(registered - defined.keys()):
        problems.append(f"{name} is in TESTS but not defined in this file")
    for name in sorted(registered & defined.keys()):
        if not any(isinstance(s, ast.Assert) for s in ast.walk(defined[name])):
            problems.append(f"{name} has no assert, so it cannot fail")
    if problems:
        raise SystemExit("test suite is not sound:\n  " + "\n  ".join(problems))


def main(argv):
    _audit_tests()
    runtime = "iOS 27"
    if "--runtime" in argv:
        i = argv.index("--runtime")
        runtime = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    only = [a for a in argv if not a.startswith("-")]
    suite = Suite(erase="--erase" in argv, runtime=runtime)
    print("setup:")
    suite.setup()

    chosen = [t for t in TESTS if not only or any(o in t.__name__ for o in only)]
    print(f"\nrunning {len(chosen)} test(s):\n")
    failures, known = [], []
    for t in chosen:
        label = t.__name__.replace("_", " ")
        print(f"  … {label}", flush=True)
        broken = getattr(t, "expected_broken", None)
        try:
            t(suite)
            if broken:
                print(f"  \033[32mFIXED\033[0m {label}\n"
                      f"        this was expected to fail — drop the "
                      f"expected_broken marker\n")
            else:
                print(f"  \033[32mPASS\033[0m {label}\n")
        except Exception as exc:
            shot = suite.sim.screenshot(f"FAIL-{t.__name__}.png")
            if broken:
                print(f"  \033[33mKNOWN\033[0m {label}\n        {broken}\n"
                      f"        (failed as expected: {exc})\n")
                known.append(t.__name__)
            else:
                print(f"  \033[31mFAIL\033[0m {label}\n        {exc}\n"
                      f"        screenshot: {shot}\n")
                failures.append(t.__name__)
    suite.teardown()

    print("-" * 60)
    if known:
        print(f"{len(known)} known-broken (not counted as failures): "
              f"{', '.join(known)}")
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print(f"all {len(chosen) - len(known)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

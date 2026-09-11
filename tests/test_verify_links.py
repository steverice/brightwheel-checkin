"""The verifier's retry, tested against a fake simulator. No device, no network.

The miss it guards against happened once on a freshly erased simulator and
didn't come back on demand, so the branch can't be reached on a real device.

Run with the file named: pytest tests/test_verify_links.py
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import verify_links as V  # noqa: E402

SHEET = [(0, 0, 10, 10)]


class FakeSim:
    """Shows no import sheet until the link has been opened `works_on` times."""

    udid = "FAKE"

    def __init__(self, works_on):
        self.works_on = works_on
        self.opens = 0
        self.tapped = False

    def terminate_shortcuts(self):
        pass

    def blue_buttons(self):
        return SHEET if self.opens >= self.works_on and not self.tapped else []

    def tap_affirmative(self):
        self.tapped = True


@pytest.fixture
def opened(monkeypatch):
    sims = []

    def run(cmd, check):
        assert cmd[:3] == ["xcrun", "simctl", "openurl"]
        sims[0].opens += 1

    monkeypatch.setattr(V.subprocess, "run", run)
    monkeypatch.setattr(V.time, "sleep", lambda s: None)
    return sims


def test_a_link_that_opens_first_time_is_opened_once(opened):
    sim = FakeSim(works_on=1)
    opened.append(sim)
    V.install(sim, "https://www.icloud.com/shortcuts/x", timeout=0.01)
    assert sim.opens == 1 and sim.tapped


def test_a_missed_first_open_is_retried(opened):
    sim = FakeSim(works_on=2)
    opened.append(sim)
    V.install(sim, "https://www.icloud.com/shortcuts/x", timeout=0.01)
    assert sim.opens == 2 and sim.tapped


def test_two_misses_blame_the_link(opened):
    sim = FakeSim(works_on=3)
    opened.append(sim)
    with pytest.raises(RuntimeError, match="still live"):
        V.install(sim, "https://www.icloud.com/shortcuts/x", timeout=0.01)
    assert sim.opens == 2 and not sim.tapped

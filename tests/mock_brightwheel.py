#!/usr/bin/env python3
"""A stand-in for the Brightwheel API, served over HTTPS to the simulator.

The shortcuts cannot see HTTP status codes, so every failure mode they detect
is a shape in the response *body* (see README, "Error signatures"). This mock
reproduces those bodies exactly, which is what makes error paths testable at
all: a stale school code or an expired token can be produced on demand instead
of waited for.

It also records every request, and that recording — not a screenshot — is what
the tests assert on. "Nobody was checked in" is precisely "no POST reached
/checkins/", which is a fact the mock holds.
"""
import json
import ssl
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Bodies keyed by the outcome a test asks for. The exact strings matter: the
# shortcut greps them with fixed patterns, so a paraphrase here would silently
# stop matching and the test would pass for the wrong reason.
CHECKIN_BODIES = {
    "ok": lambda: {"checkins": [{"object_id": "chk_00000000",
                                 "event_date": "2026-08-25T09:00:00.000Z"}]},
    "stale_secret": lambda: {
        "secret": "The given secret does not exist or is expired."},
    "bad_code": lambda: {"checkin_code": "Incorrect checkin code",
                         "code": "E2004"},
    "expired_token": lambda: {"error": "This resource requires authentication",
                              "code": "E1200"},
    "empty_body": lambda: {"checkins": "cannot process empty checkins",
                           "code": "E2001"},
}


@dataclass
class Scenario:
    """What the fake Brightwheel should do this run."""

    # False makes GET /users/me answer E1200, which is what sends the shortcut
    # down the interactive sign-in branch.
    token_valid: bool = True
    two_fa_code: str = "123456"
    issued_token: str = "SIMTOKEN0123456789AB"

    # child object_id -> "in" | "out", the state the activities feed reports.
    # A successful check-in updates this, so a second pass sees the new state
    # and skips — the same idempotency the real API gives us.
    states: dict = field(default_factory=dict)

    # One outcome per POST /checkins/, by name from CHECKIN_BODIES. The last
    # entry repeats once the list runs out, so ["stale_secret", "ok"] means
    # "reject the first send, accept everything after".
    checkin_outcomes: list = field(default_factory=lambda: ["ok"])

    # When set, only this secret is accepted and anything else is answered as
    # a stale code regardless of checkin_outcomes.
    required_secret: str | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Quiet: the harness prints its own summary.
    def log_message(self, *a):
        pass

    # -- plumbing -------------------------------------------------------
    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return raw.decode(), json.loads(raw or b"null")
        except Exception:
            return raw.decode("utf-8", "replace"), None

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return obj

    def _record(self, raw, parsed, response, status):
        self.server.requests.append({
            "method": self.command,
            "path": self.path,
            "token": self.headers.get("X-Parse-Session-Token"),
            "content_type": self.headers.get("Content-Type"),
            "raw_body": raw,
            "body": parsed,
            "response": response,
            "status": status,
        })

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    # -- routes ---------------------------------------------------------
    def _route(self):
        sc = self.server.scenario
        raw, parsed = self._read_body()
        path = self.path.split("?", 1)[0]
        status = 200
        
        if path.endswith("/users/me"):
            if sc.token_valid:
                resp = {"object_id": "usr_guardian", "email": "test@example.invalid"}
            else:
                resp = {"error": "This resource requires authentication",
                        "code": "E1200"}
                status = 401

        elif path.endswith("/sessions/start"):
            resp = {"2fa_required": True,
                    "2fa_code_sent_to": ["t***@example.invalid"]}

        elif path.endswith("/sessions"):
            supplied = (parsed or {}).get("2fa_code")
            if supplied == sc.two_fa_code:
                resp = {"token": sc.issued_token,
                        "user": {"object_id": "usr_guardian"},
                        "csrf": "csrf-token"}
            else:
                resp = {"error": "Please start over", "code": "E2053"}
                status = 401

        elif "/students/" in path and path.endswith("/activities"):
            child = path.split("/students/", 1)[1].split("/", 1)[0]
            state = sc.states.get(child, "out")
            # The shortcut greps the whole body for "state":"1", so nothing
            # else in here may contain that pair.
            resp = {"activities": [{"object_id": "act_1",
                                    "action_type": "ac_checkin",
                                    "state": "1" if state == "in" else "2"}]}

        elif path.endswith("/checkins/") or path.endswith("/checkins"):
            resp, status = self._checkin(sc, parsed)

        else:
            resp = {"error": "unmocked route", "path": path}
            status = 404

        self._send(resp, status)
        self._record(raw, parsed, resp, status)

    def _checkin(self, sc, parsed):
        parsed = parsed or {}
        if sc.required_secret is not None and parsed.get("secret") != sc.required_secret:
            return CHECKIN_BODIES["stale_secret"](), 422

        outcomes = sc.checkin_outcomes or ["ok"]
        idx = min(self.server.checkin_count, len(outcomes) - 1)
        outcome = outcomes[idx]
        self.server.checkin_count += 1

        if outcome == "ok":
            # Mirror the real API: a successful send changes the state the
            # activities feed will report next time.
            for entry in parsed.get("checkins", []) or []:
                target = (entry.get("target") or {}).get("object_id")
                if target:
                    sc.states[target] = "in" if entry.get("checked_in") else "out"
            return CHECKIN_BODIES["ok"](), 200
        return CHECKIN_BODIES[outcome](), 422


class MockBrightwheel:
    """Runs the fake API on localhost and remembers what was asked of it."""

    def __init__(self, port, certfile, scenario=None):
        self.port = port
        self.certfile = certfile
        self.scenario = scenario or Scenario()
        self._srv = None
        self._thread = None

    @property
    def base(self):
        return f"https://localhost:{self.port}/api/v1"

    def start(self):
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile)
        self._srv.socket = ctx.wrap_socket(self._srv.socket, server_side=True)
        self._srv.requests = []
        self._srv.checkin_count = 0
        self._srv.scenario = self.scenario
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    # -- what the tests assert on ---------------------------------------
    def load(self, scenario):
        """Point the mock at a fresh scenario and forget previous traffic."""
        self.scenario = scenario
        self._srv.scenario = scenario
        self._srv.requests = []
        self._srv.checkin_count = 0

    @property
    def requests(self):
        return list(self._srv.requests)

    def matching(self, method=None, contains=None):
        return [r for r in self.requests
                if (method is None or r["method"] == method)
                and (contains is None or contains in r["path"])]

    @property
    def checkins(self):
        """Every POST that actually tried to check somebody in or out."""
        return self.matching("POST", "/checkins")

    def quiet_for(self, seconds, timeout):
        """Block until no new request has arrived for `seconds`."""
        import time
        deadline = time.time() + timeout
        last = len(self.requests)
        stable_since = time.time()
        while time.time() < deadline:
            time.sleep(0.4)
            now = len(self.requests)
            if now != last:
                last, stable_since = now, time.time()
            elif time.time() - stable_since >= seconds:
                return True
        return False

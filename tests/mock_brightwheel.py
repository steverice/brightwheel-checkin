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


# Failure bodies for GET .../students_for_checkin. Measured against the live
# API on 2026-08-27 — note the stale-secret text here has NO trailing period,
# where the /checkins/ version does. The shipped detector matches the prefix,
# so both work; a pattern tightened to the full sentence would break this one.
ROSTER_ERRORS = {
    "missing_params": ({"type": "invalid_request_error",
                        "message": "Request is missing required parameters",
                        "_errors": [{"title": "Could not apply filter",
                                     "code": "E2036"}]}, 400),
    "bad_secret": ({"secret": "The given secret does not exist or is expired",
                    "_errors": [{"title": "Problem scanning QR code",
                                 "code": "E2038"}]}, 403),
    "unknown_guardian": ({"_errors": [{"title": "Not found", "code": "E1204"}]}, 404),
    "expired_token": ({"error": "This resource requires authentication",
                       "code": "E1200"}, 401),
}


@dataclass
class Scenario:
    """What the fake Brightwheel should do this run."""

    # False makes GET /users/me answer E1200, which is what sends the shortcut
    # down the interactive sign-in branch.
    token_valid: bool = True
    two_fa_code: str = "123456"
    issued_token: str = "SIMTOKEN0123456789AB"

    # child object_id -> "in" | "out", served as each child's checked_in.
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

    # The roster students_for_checkin should report: [(id, first_name, room_id)].
    # `checked_in` is derived from `states`, so a successful POST changes what
    # the next roster read says — the same idempotency the real API gives us.
    roster: list = field(default_factory=list)

    # Which constructed shape to return. "normal" builds a well-formed body;
    # the rest exist so a guard can be shown to fire. These payloads do not
    # occur in any capture — that is the point of having them.
    #   error             an E1200 body instead of a roster      -> no children
    #   restructured      students present, room_states renamed  -> no room
    #   empty_room_states first child has []                     -> no room
    #   two_rooms         first child gains a non-default room   -> two rooms
    #   unreadable_state  first child's checked_in is absent     -> no state
    #   cancelling        first child has two rooms, second []   -> per child
    #
    # cancelling is the shape the aggregate counts cannot see. Children and
    # "checked_in" keys both still total two, so every whole-roster comparison
    # agrees; only counting each child's own rooms catches it.
    #
    # The order matters. The two-room child comes first and its extra room says
    # not checked in, so a check-in run reaches a real POST into a guessed room
    # before the empty child is read at all. Put the empty child first and
    # room_states.1 fails on it, ending the run before anything is sent — the
    # right outcome by accident, which no longer tests anything.
    roster_shape: str = "normal"

    # The guardian id the roster path must carry; anything else answers E1204.
    guardian_id: str = "usr_guardian"


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
            if self._authenticated(sc):
                # Several object_ids, as the real body has, so an unanchored
                # guardian-id pattern fails the suite instead of passing it.
                # The wanted one is first, which is what makes ^ load-bearing.
                resp = {"object_id": sc.guardian_id,
                        "email": "test@example.invalid",
                        "profile_photo": {"object_id": "photo_decoy"},
                        "authentication_methods": [{"object_id": "auth_decoy"}],
                        "school_invites": [{"school": {"object_id": "school_decoy"}}]}
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
                self.server.signed_in = True
                resp = {"token": sc.issued_token,
                        "user": {"object_id": "usr_guardian"},
                        "csrf": "csrf-token"}
            else:
                resp = {"error": "Please start over", "code": "E2053"}
                status = 401

        elif path.endswith("/students_for_checkin"):
            resp, status = self._roster(sc, path)

        elif path.endswith("/checkins/") or path.endswith("/checkins"):
            resp, status = self._checkin(sc, parsed)

        else:
            resp = {"error": "unmocked route", "path": path}
            status = 404

        self._send(resp, status)
        self._record(raw, parsed, resp, status)

    def _authenticated(self, sc):
        """Is the token on this request good?

        `token_valid=False` models a stale stored token, and it has to keep
        failing until the run actually signs in — otherwise a token left in the
        store by an earlier test authenticates and the sign-in branch is never
        taken. Once this mock issues a token, that token works.
        """
        if sc.token_valid:
            return True
        if not getattr(self.server, "signed_in", False):
            return False
        return self.headers.get("X-Parse-Session-Token") == sc.issued_token

    def _roster(self, sc, path):
        """GET /guardians/{id}/students_for_checkin.

        Required parameters are enforced the way the live API enforces them,
        because "the roster call failed" is a case the guards exist for and a
        mock that always succeeds cannot exercise them.
        """
        import urllib.parse as _up
        query = dict(_up.parse_qsl(self.path.split("?", 1)[1])) if "?" in self.path else {}

        if not self._authenticated(sc):
            return ROSTER_ERRORS["expired_token"]
        for key in ("school_id", "secret", "time_zone"):
            if not query.get(key):
                return ROSTER_ERRORS["missing_params"]
        if sc.required_secret is not None and query["secret"] != sc.required_secret:
            return ROSTER_ERRORS["bad_secret"]
        who = path.split("/guardians/", 1)[1].split("/", 1)[0]
        if who != sc.guardian_id:
            return ROSTER_ERRORS["unknown_guardian"]

        if sc.roster_shape == "error":
            return ROSTER_ERRORS["expired_token"]

        def entry(cid, name, room):
            state = sc.states.get(cid, "out") == "in"
            student = {"object_id": cid, "billing_status": None,
                       "first_name": name, "last_name": "Test",
                       "profile_photo": {"object_id": f"photo_{cid}"},
                       "raw_passcode": None, "user_type": "student"}
            rs = {"room": {"object_id": room, "name": "Test Room"},
                  "checked_in": state, "is_default_room": True}
            return {"student": student, "room_states": [rs]}

        students = [entry(*c) for c in sc.roster]
        shape = sc.roster_shape

        if students:
            first = students[0]
            if shape == "empty_room_states":
                first["room_states"] = []
            elif shape == "cancelling":
                # The extra room copies the state rather than inverting it, so
                # this child still needs sending and the run has something
                # wrong to do.
                first["room_states"].insert(0, {
                    "room": {"object_id": "room_aftercare", "name": "Aftercare"},
                    "checked_in": first["room_states"][0]["checked_in"],
                    "is_default_room": False})
                if len(students) > 1:
                    students[1]["room_states"] = []
            elif shape == "two_rooms":
                first["room_states"].insert(0, {
                    "room": {"object_id": "room_aftercare", "name": "Aftercare"},
                    "checked_in": not first["room_states"][0]["checked_in"],
                    "is_default_room": False})
            elif shape == "unreadable_state":
                first["room_states"][0].pop("checked_in")

        body = {"school": {"uuid": "sch_uuid", "object_id": "sch_object",
                           "name": "Test School", "signatures_required": False},
                "students": students}
        if shape == "restructured":
            # students present, but the child shape changed: every count the
            # guards derive goes to zero together.
            for s in body["students"]:
                s["room_state"] = s.pop("room_states")[0]
        return body, 200

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
            # next roster read will report.
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
        self._srv.signed_in = False
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
        self._srv.signed_in = False

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

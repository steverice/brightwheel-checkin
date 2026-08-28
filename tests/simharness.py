#!/usr/bin/env python3
"""Drive the iOS Simulator well enough to install and run a Shortcut.

Everything here was established by experiment against a booted iOS 27 sim; the
non-obvious findings are recorded in TESTING.md. The three that shape this file:

  * A shortcut is installed by opening it as a *host* file URL. Simulator
    processes see the Mac's filesystem, so `file:///Users/...` resolves.
    `shortcuts://import-shortcut` is iCloud-only and will not take a local file.
  * A synthesized click needs a MouseMoved event first and ClickState set, or
    the cursor moves and nothing is pressed.
  * Consent prompts ("allow this shortcut to connect to localhost") block the
    run. The affirmative button is always the bottom-most iOS-blue one, which
    is enough to dismiss every prompt shape without reading any text.
"""
import os
import plistlib
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path

import numpy as np
import Quartz
from PIL import Image

# iOS system blue, as rendered on the alert buttons.
BLUE_MIN_B = 200
BLUE_MAX_R = 110
BLUE_MIN_SPREAD = 110          # blue channel must lead red by this much
BUTTON_MIN_W = 360             # device px; a half-width alert button
# US virtual keycodes. The Simulator forwards raw HID codes to the guest, so
# these — not unicode strings — are what actually reach a text field. Enough
# for a 6-digit code and the word "resend".
KEYCODES = {
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
    "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5,
    "h": 4, "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45,
    "o": 31, "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32,
    "v": 9, "w": 13, "x": 7, "y": 16, "z": 6, " ": 49,
}
RETURN_KEY = 36

BUTTON_H_RANGE = (70, 220)     # excludes the tall shortcut tile on
                               # the import sheet, which is also blue


class SimulatorError(RuntimeError):
    pass


def _run(*args, check=True, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw)


def _osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        raise SimulatorError(r.stderr.strip())
    return r.stdout.strip()


class Simulator:
    def __init__(self, udid, artifacts=None):
        self.udid = udid
        self.artifacts = Path(artifacts) if artifacts else None
        if self.artifacts:
            self.artifacts.mkdir(parents=True, exist_ok=True)
        self._shot = 0

    # -- discovery ------------------------------------------------------
    @classmethod
    def find(cls, runtime="iOS 27", model="iPhone", **kw):
        """Prefer an already-booted matching device, else the first available."""
        out = _run("xcrun", "simctl", "list", "devices", "available").stdout
        section, booted, first = None, None, None
        for line in out.splitlines():
            if line.startswith("--"):
                section = line.strip("- ").strip()
                continue
            if not section or not section.startswith(runtime):
                continue
            if model not in line or "(" not in line:
                continue
            udid = line.split("(")[1].split(")")[0]
            first = first or udid
            if "(Booted)" in line:
                booted = booted or udid
        chosen = booted or first
        if not chosen:
            raise SimulatorError(
                f"no {runtime} {model} simulator found. Install the runtime in "
                f"Xcode → Settings → Components.")
        sim = cls(chosen, **kw)
        if not booted:
            sim.boot()
        return sim

    # -- lifecycle ------------------------------------------------------
    def boot(self):
        _run("xcrun", "simctl", "boot", self.udid, check=False)
        _run("open", "-a", "Simulator")
        self.wait_booted()

    def wait_booted(self, timeout=180):
        """Wait for Booted, then for the system to actually be usable.

        "Booted" is reported well before SpringBoard can service an openurl,
        and the gap is much wider on the first boot after an erase — so poll
        for Shortcuts being resolvable rather than sleeping a fixed amount.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = _run("xcrun", "simctl", "list", "devices").stdout
            if any(self.udid in l and "(Booted)" in l for l in out.splitlines()):
                break
            time.sleep(1)
        else:
            raise SimulatorError("simulator did not boot in time")

        while time.time() < deadline:
            r = _run("xcrun", "simctl", "listapps", self.udid, check=False)
            if "com.apple.shortcuts" in r.stdout:
                time.sleep(3)
                return
            time.sleep(2)
        raise SimulatorError("Shortcuts never became available on the device")

    def erase(self):
        """Full clean slate. Also drops the trusted root cert, so re-add it."""
        _run("xcrun", "simctl", "shutdown", self.udid, check=False)
        _run("xcrun", "simctl", "erase", self.udid)
        self.boot()

    def add_root_cert(self, pem):
        _run("xcrun", "simctl", "keychain", self.udid, "add-root-cert", str(pem))

    def terminate_shortcuts(self):
        _run("xcrun", "simctl", "terminate", self.udid, "com.apple.shortcuts",
             check=False)

    # -- window geometry ------------------------------------------------
    WINDOW_MENU = 'menu 1 of menu bar item "Window" of menu bar 1'

    @staticmethod
    def _menu_item(item):
        """(exists, mark_char) for a menu item; (False, None) when it is absent.

        Menu contents depend on the frontmost window, and a menu item that is
        not there raises rather than returning empty — so absence has to be
        caught rather than tested.
        """
        try:
            v = _osa('tell application "System Events" to tell process '
                     f'"Simulator" to return value of attribute '
                     f'"AXMenuItemMarkChar" of {item}')
        except SimulatorError:
            return False, None
        return True, (None if v in ("", "missing value") else v)

    @staticmethod
    def _menu_click(item):
        """Click a menu item. False when this window's menu has no such item."""
        try:
            _osa('tell application "System Events" to tell process "Simulator" '
                 f'to click {item}')
            return True
        except SimulatorError:
            return False

    def focus_window(self, timeout=20):
        """Make this device's window frontmost, and confirm it got there.

        Raising is not the same as arriving. Another booted simulator can stay
        in front, and then every menu below belongs to the wrong device — a
        visionOS window has no "Show Device Bezels" at all, so the harness used
        to die on a missing menu item rather than on anything real.
        """
        name, _version = self.device_label()
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._window_rect()          # matches by title, and AXRaises it
            try:
                front = _osa('tell application "System Events" to tell process '
                             '"Simulator" to return name of window 1')
            except SimulatorError:
                front = ""
            if name in front:
                return front
            time.sleep(0.5)
        raise SimulatorError(
            f"could not bring {name} to the front; another simulator window "
            f"is holding focus (frontmost was {front!r})")

    def prepare_window(self):
        """Point Accurate + no bezels makes device px -> screen points exact.

        Every menu here applies to the frontmost window, so this device's
        window is brought to the front and *verified* first. The settings are
        then applied tolerantly: another device kind may not offer them, and a
        missing item is worth a note rather than a crash.
        """
        _run("open", "-a", "Simulator")
        _osa('tell application "Simulator" to activate')
        # The Window menu only carries these items once a device window exists,
        # and Simulator can still be windowless right after a boot.
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                self._window_rect()      # also raises this device's window
                break
            except SimulatorError:
                time.sleep(2)
        else:
            raise SimulatorError("Simulator never opened a window for this device")
        self.focus_window()
        time.sleep(0.3)
        if not self._menu_click(f'menu item "Point Accurate" of {self.WINDOW_MENU}'):
            print("note: no Point Accurate for this window; "
                  "taps fall back to the window's own scale")
        time.sleep(0.8)
        bezels = f'menu item "Show Device Bezels" of {self.WINDOW_MENU}'
        exists, marked = self._menu_item(bezels)
        if exists and marked:
            self._menu_click(bezels)
            time.sleep(1.0)
        self.ensure_hardware_keyboard()

    def device_label(self):
        """(name, os version) as the Simulator window titles them."""
        out = _run("xcrun", "simctl", "list", "devices", "--json").stdout
        import json
        for runtime, devices in json.loads(out)["devices"].items():
            for d in devices:
                if d["udid"] == self.udid:
                    version = runtime.rsplit(".", 1)[-1].replace("iOS-", "").replace("-", ".")
                    return d["name"], version
        raise SimulatorError(f"device {self.udid} not found")

    def _window_rect(self):
        """Locate *this* device's Simulator window.

        More than one simulator can be booted at once, and "window 1" is then
        whichever happens to be frontmost — which silently sends every tap to
        the wrong device, or off-screen entirely. Match the title instead.
        """
        name, version = self.device_label()
        raw = _osa(
            'tell application "System Events" to tell process "Simulator" to '
            'return name of every window')
        titles = [t.strip() for t in raw.split(",")] if raw else []
        match = next((t for t in titles if name in t and version in t), None)
        if match is None:
            match = next((t for t in titles if name in t), None)
        if match is None:
            raise SimulatorError(
                f"no Simulator window for {name} ({version}); saw {titles}")

        q = match.replace('"', '\\"')
        _osa(f'tell application "System Events" to tell process "Simulator" to '
             f'perform action "AXRaise" of window "{q}"')
        pos = _osa('tell application "System Events" to tell process "Simulator" '
                   f'to return position of window "{q}"')
        size = _osa('tell application "System Events" to tell process "Simulator" '
                    f'to return size of window "{q}"')
        x, y = (int(v) for v in pos.split(", "))
        w, h = (int(v) for v in size.split(", "))
        return x, y, w, h

    def _mapping(self, device_size):
        """(origin_x, origin_y, screen points per device pixel)."""
        dw, dh = device_size
        wx, wy, ww, wh = self._window_rect()
        ppp = ww / dw
        return wx, wy + (wh - dh * ppp), ppp

    # -- screen ---------------------------------------------------------
    def screenshot(self, name=None):
        self._shot += 1
        if self.artifacts:
            path = self.artifacts / (name or f"shot-{self._shot:03d}.png")
        else:
            path = Path("/tmp/_sim_shot.png")
        _run("xcrun", "simctl", "io", self.udid, "screenshot", str(path))
        return path

    def image(self, name=None):
        return Image.open(self.screenshot(name)).convert("RGB")

    # -- input ----------------------------------------------------------
    def tap(self, px, py, device_size=None, settle=0.7):
        """Tap by device-screenshot pixel coordinates."""
        if device_size is None:
            device_size = Image.open(self.screenshot()).size
        _osa('tell application "Simulator" to activate')
        time.sleep(0.35)
        ox, oy, ppp = self._mapping(device_size)
        pt = Quartz.CGPointMake(ox + px * ppp, oy + py * ppp)

        def post(kind, click_state=None):
            ev = Quartz.CGEventCreateMouseEvent(None, kind, pt,
                                                Quartz.kCGMouseButtonLeft)
            if click_state:
                Quartz.CGEventSetIntegerValueField(
                    ev, Quartz.kCGMouseEventClickState, click_state)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

        post(Quartz.kCGEventMouseMoved)
        time.sleep(0.2)
        post(Quartz.kCGEventLeftMouseDown, 1)
        time.sleep(0.1)
        post(Quartz.kCGEventLeftMouseUp, 1)
        time.sleep(settle)

    def type_text(self, text):
        """Type into the focused field, one virtual keycode at a time.

        CGEventKeyboardSetUnicodeString does nothing here: the Simulator passes
        raw keycodes through, so every character arrives as whatever keycode 0
        is and "123456" lands in the field as "Aaaaaa".
        """
        self.ensure_hardware_keyboard()
        _osa('tell application "Simulator" to activate')
        time.sleep(0.3)
        for ch in text:
            code = KEYCODES.get(ch.lower())
            if code is None:
                raise SimulatorError(f"no keycode mapped for {ch!r}")
            for down in (True, False):
                Quartz.CGEventPost(
                    Quartz.kCGHIDEventTap,
                    Quartz.CGEventCreateKeyboardEvent(None, code, down))
                time.sleep(0.03)
            time.sleep(0.06)
        time.sleep(0.5)

    def answer_prompt(self, text):
        """Fill an Ask for Input dialog and commit it.

        Two traps here. The field is *not* focused when the dialog appears, so
        typing without tapping it first goes nowhere and the answer stays
        empty. And the Done button is iOS blue, so the generic
        tap-the-affirmative would submit that empty answer — which this
        shortcut reads as "send me another code", five times over.
        """
        img = self.image()
        boxes = self.blue_buttons(img)
        if not boxes:
            return False
        w, h = img.size
        done = max(boxes, key=lambda b: (b[3], b[2]))
        # The text field sits directly above the button row.
        self.tap(int(w * 0.25), int(done[1] - h * 0.12), device_size=img.size)
        time.sleep(0.8)
        self.type_text(text)
        time.sleep(0.5)
        return self.tap_affirmative()

    def press_return(self):
        """Return key, for committing an Ask for Input prompt."""
        _osa('tell application "Simulator" to activate')
        time.sleep(0.3)
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEY, down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.05)
        time.sleep(0.6)

    def ensure_hardware_keyboard(self):
        """Connect the hardware keyboard, re-applying it even if already ticked.

        Synthesized keystrokes only reach the device through the hardware
        keyboard. An erase resets the device side of this while the Simulator
        menu can still show it ticked, and the giveaway is the software
        keyboard appearing — at which point typing silently goes nowhere. So
        cycle the setting rather than trusting the tick.
        """
        item = ('menu item "Connect Hardware Keyboard" of menu 1 of menu item '
                '"Keyboard" of menu 1 of menu bar item "I/O" of menu bar 1')
        exists, marked = self._menu_item(item)
        if not exists:
            # Unlike the Window-menu settings this one is not optional: without
            # it synthesized keystrokes reach nothing. Say which window owns the
            # menu, because that is the actual problem.
            raise SimulatorError(
                "no Connect Hardware Keyboard item — the frontmost Simulator "
                "window is probably another device; call focus_window() first")
        clicks = 2 if marked else 1
        for _ in range(clicks):
            _osa('tell application "System Events" to tell process "Simulator" '
                 f'to click {item}')
            time.sleep(0.7)

    # -- finding the affirmative button ---------------------------------
    def blue_buttons(self, img=None):
        """Boxes of iOS-blue filled buttons, top-to-bottom then left-to-right.

        Used for both "Add Shortcut" on the import sheet and "Allow" /
        "Always Allow" on consent prompts — in every layout the button we want
        is the bottom-most one, so no text recognition is needed.
        """
        img = img or self.image()
        a = np.asarray(img).astype(int)
        r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        mask = (b > BLUE_MIN_B) & (r < BLUE_MAX_R) & ((b - r) > BLUE_MIN_SPREAD)

        boxes = []
        rows = np.where(mask.sum(axis=1) > 100)[0]
        for band in _contiguous(rows, gap=8):
            y0, y1 = band[0], band[-1]
            if not (BUTTON_H_RANGE[0] <= y1 - y0 <= BUTTON_H_RANGE[1]):
                continue
            cols = np.where(mask[y0:y1 + 1].sum(axis=0) > (y1 - y0) * 0.4)[0]
            for run in _contiguous(cols, gap=20):
                x0, x1 = run[0], run[-1]
                if x1 - x0 < BUTTON_MIN_W:
                    continue
                boxes.append((int(x0), int(y0), int(x1), int(y1)))
        return boxes

    def tap_affirmative(self, img=None):
        """Tap the bottom-most blue button. True if there was one."""
        img = img or self.image()
        boxes = self.blue_buttons(img)
        if not boxes:
            return False
        x0, y0, x1, y1 = max(boxes, key=lambda bx: (bx[3], bx[2]))
        self.tap((x0 + x1) // 2, (y0 + y1) // 2, device_size=img.size)
        return True

    def drain_prompts(self, rounds=6, pause=1.2):
        """Clear consent prompts until none are on screen."""
        cleared = 0
        for _ in range(rounds):
            img = self.image()
            if not self.tap_affirmative(img):
                break
            cleared += 1
            time.sleep(pause)
        return cleared

    # -- shortcuts ------------------------------------------------------
    def install(self, path, expect_name=None, timeout=75):
        """Open a .shortcut as a host file URL and confirm the import sheet.

        The library name comes from the *filename*, not from WFWorkflowName.
        """
        path = Path(path).resolve()
        name = expect_name or path.stem
        if name in self.library():
            return False
        url = "file://" + urllib.parse.quote(str(path))
        # Right after a boot the URL can be refused for a few seconds.
        for attempt in range(4):
            r = _run("xcrun", "simctl", "openurl", self.udid, url, check=False)
            if r.returncode == 0:
                break
            time.sleep(3)
        else:
            raise SimulatorError(f"could not open {path.name}: {r.stderr.strip()}")

        # Wait for the sheet, then keep confirming until the shortcut actually
        # lands. A single tap is not reliable: the first click on an unfocused
        # Simulator window sometimes only raises it, and after an erase the
        # sheet can take several seconds to draw.
        deadline = time.time() + timeout
        tapped = False
        while time.time() < deadline:
            if name in self.library():
                return True
            if self.tap_affirmative():
                tapped = True
            # The import writes through CoreData; re-reading too eagerly can
            # miss a confirm that did land, and the sheet for a large shortcut
            # can still be drawing when the first tap goes out.
            time.sleep(2.5)
        self.screenshot(f"install-failed-{name}.png")
        raise SimulatorError(
            f"{name} did not install"
            f"{'' if tapped else ' (no Add Shortcut button ever appeared)'}")

    def run_shortcut(self, name):
        url = "shortcuts://run-shortcut?name=" + urllib.parse.quote(name)
        _run("xcrun", "simctl", "openurl", self.udid, url)

    # -- reading the device's own state ---------------------------------
    @property
    def _db(self):
        return Path(os.path.expanduser(
            f"~/Library/Developer/CoreSimulator/Devices/{self.udid}"
            "/data/Library/Shortcuts/Shortcuts.sqlite"))

    def _query(self, sql, params=()):
        if not self._db.exists():
            return []
        con = sqlite3.connect(f"file:{self._db}?mode=ro", uri=True)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def shortcut_actions(self, name):
        """The installed shortcut's actions, as the plist list they came from."""
        rows = self._query(
            "SELECT a.ZDATA FROM ZSHORTCUTACTIONS a JOIN ZSHORTCUT s "
            "ON s.Z_PK = a.ZSHORTCUT WHERE s.ZNAME = ? AND s.ZTOMBSTONED = 0",
            (name,))
        if not rows or rows[0][0] is None:
            return None
        return plistlib.loads(bytes(rows[0][0]))

    def library(self):
        return [r[0] for r in
                self._query("SELECT ZNAME FROM ZSHORTCUT WHERE ZTOMBSTONED=0")]

    def stored_content(self):
        """Whatever the shortcuts put in Store Content, keyed by its name.

        Store Content is two hops on disk: ZSTOREDVALUE holds the name and a
        keyed archive naming a file, and the value itself lives in that file
        under Library/Shortcuts/PersistentStorage. Reading only the row gives
        you the key and no value, which is misleading rather than empty.
        """
        out = {}
        for name, blob in self._query(
                "SELECT ZDISPLAYNAME, ZVALUE FROM ZSTOREDVALUE "
                "WHERE ZTOMBSTONED=0"):
            out[name] = self._stored_value(blob)
        return out

    def _stored_value(self, blob):
        archive_name = _keyed_lookup(_plist(blob), "archiveName")
        if not archive_name:
            return None
        path = (self._db.parent / "PersistentStorage" / str(archive_name))
        if not path.exists():
            return None
        # Shortcuts wraps the payload differently depending on how it was
        # produced: a scanned/typed string lands under NS.string, while a
        # value carried out of an action arrives as a WFObjectRepresentation
        # whose "object" is the string.
        return _keyed_lookup(_plist(path.read_bytes()), "NS.string", "object")


def _plist(blob):
    """Parse a Shortcuts value blob; some carry a one-byte version prefix."""
    if blob is None:
        return None
    raw = bytes(blob)
    if raw[:1] == b"\x01":
        raw = raw[1:]
    try:
        return plistlib.loads(raw)
    except Exception:
        return None


def _keyed_lookup(archive, *keys):
    """Find the first of `keys` in an NSKeyedArchiver plist, resolved to text."""
    if not isinstance(archive, dict) or "$objects" not in archive:
        return None
    objects = archive["$objects"]

    def resolve(v):
        if isinstance(v, plistlib.UID):
            return objects[v.data]
        return v

    for key in keys:
        for obj in objects:
            if isinstance(obj, dict) and key in obj:
                value = resolve(obj[key])
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                if isinstance(value, str) and value != "$null":
                    return value
    return None


def _contiguous(indices, gap=1):
    """Split a sorted index array into runs separated by more than `gap`."""
    runs, current = [], []
    for i in indices:
        if current and i - current[-1] > gap:
            runs.append(current)
            current = []
        current.append(int(i))
    if current:
        runs.append(current)
    return runs

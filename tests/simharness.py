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

Xcode 27 deleted Simulator.app and replaced it with Device Hub, which changed
every one of those clicks' addresses but none of their logic. See the _Host
classes below for what differs and TESTING.md for how it was established.
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

BUTTON_H_RANGE = (70, 220)     # excludes the tall shortcut tile on
                               # the import sheet, which is also blue


DARK_SUM = 150                 # r+g+b below this is bezel, not screen content


class SimulatorError(RuntimeError):
    pass


def _run(*args, check=True, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw)


def _osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        raise SimulatorError(r.stderr.strip())
    return r.stdout.strip()


def _mouse_click(x, y, settle=0.7):
    """A click the guest actually feels.

    A synthesized click needs a MouseMoved event first and ClickState set, or
    the cursor moves and nothing is pressed.
    """
    pt = Quartz.CGPointMake(x, y)

    def post(kind, click_state=None):
        ev = Quartz.CGEventCreateMouseEvent(None, kind, pt,
                                            Quartz.kCGMouseButtonLeft)
        # Pin the modifiers off. A posted event otherwise picks up whatever the
        # system currently believes is held, and a stray Command turns a click
        # into a Command-click — which in Device Hub's sidebar adds to the
        # selection instead of replacing it, silently gathering up devices.
        Quartz.CGEventSetFlags(ev, 0)
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


def _type_mac(text):
    """Type into a Mac control — Device Hub's sidebar search, not the guest.

    Unicode strings work here. They do not work on the device, which is why
    Simulator.type_text spells everything out in virtual keycodes instead.
    """
    for ch in text:
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventSetFlags(ev, 0)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.02)
        time.sleep(0.04)


def _press_escape():
    """Clear a focused search field.

    Deliberately not Command-A then Delete. Command-A is Select All, and if the
    click that was meant to focus the field missed, it selects every device in
    the sidebar instead; Delete on a device list is worse still. Escape does
    nothing harmful wherever it lands.
    """
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, 53, down)
        Quartz.CGEventSetFlags(ev, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.03)
    time.sleep(0.15)


def _title_is(title, name, version=""):
    """Does this window title belong to exactly this device?

    A prefix test is not enough on its own: "bw-ios27rc" is a prefix of
    "bw-ios27rc-b", and matching loosely points the whole harness at a
    different device while every check downstream still passes — taps land,
    screenshots come back, and the run reports on a device it never touched.
    The name has to end at a boundary.
    """
    if not title.startswith(name):
        return False
    rest = title[len(name):]
    return (not rest or rest[0] == " ") and version in rest


def _light_edges(light):
    """First and last light column of every row, as arrays.

    Rows that are dark end to end get sentinels that lose to every real edge in
    the min/max that follows.
    """
    any_light = light.any(axis=1)
    first = np.where(any_light, light.argmax(axis=1), light.shape[1])
    last = np.where(any_light,
                    light.shape[1] - 1 - light[:, ::-1].argmax(axis=1), -1)
    return first, last


# -- the Mac app that draws the device --------------------------------------
#
# Xcode 27 deleted Simulator.app. Device Hub replaces it, showing simulators and
# real devices together in one window with a sidebar, and it differs in every
# way this harness cares about: which process AppleScript has to address, which
# menu connects the hardware keyboard, whether a window for a given device
# exists at all, and how device pixels map to screen points. Simulator.app had
# Point Accurate and a bezel toggle, which made that mapping exact arithmetic on
# the window frame. Device Hub has neither, so the screen has to be found in
# pixels inside the bezel it always draws.
#
# Everything that differs lives in one of the two classes below. Only the Device
# Hub path is exercised now — Xcode 27 leaves no Simulator.app to test against.
# The Simulator.app path is the code that produced the support matrix in
# TESTING.md, moved here rather than rewritten.


class _Host:
    """What this harness needs from whichever app is showing the device."""

    proc = None                # the System Events process name
    keyboard_item = None       # menu item that connects the hardware keyboard

    def __init__(self, app):
        self.app = app

    def launch(self):
        _run("open", "-a", str(self.app))

    def activate(self):
        _osa('tell application "System Events" to tell process '
             f'"{self.proc}" to set frontmost to true')

    def select(self, sim):
        """Make a window for this device exist. Runs before focus_window."""

    def configure(self, sim):
        """Per-window display settings, if this host has any."""

    def mapping(self, sim, device_size):
        """(origin_x, origin_y, screen points per device pixel)."""
        raise NotImplementedError


class _SimulatorApp(_Host):
    """Xcode 26 and earlier."""

    proc = "Simulator"
    keyboard_item = ('menu item "Connect Hardware Keyboard" of menu 1 of menu '
                     'item "Keyboard" of menu 1 of menu bar item "I/O" of '
                     'menu bar 1')
    WINDOW_MENU = 'menu 1 of menu bar item "Window" of menu bar 1'

    def activate(self):
        _osa('tell application "Simulator" to activate')

    def select(self, sim):
        # Simulator opens a window per booted device by itself; it can just be
        # windowless for a few seconds after a boot.
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                sim._window_rect()
                return
            except SimulatorError:
                time.sleep(2)
        raise SimulatorError("Simulator never opened a window for this device")

    def configure(self, sim):
        """Point Accurate + no bezels makes device px -> screen points exact.

        Applied tolerantly: another device kind may not offer these, and a
        missing item is worth a note rather than a crash.
        """
        if not sim._menu_click(f'menu item "Point Accurate" of {self.WINDOW_MENU}'):
            print("note: no Point Accurate for this window; "
                  "taps fall back to the window's own scale")
        time.sleep(0.8)
        bezels = f'menu item "Show Device Bezels" of {self.WINDOW_MENU}'
        exists, marked = sim._menu_item(bezels)
        if exists and marked:
            sim._menu_click(bezels)
            time.sleep(1.0)

    def mapping(self, sim, device_size):
        dw, dh = device_size
        wx, wy, ww, wh = sim._window_rect()
        ppp = ww / dw
        return wx, wy + (wh - dh * ppp), ppp


class _DeviceHub(_Host):
    """Xcode 27 and later."""

    proc = "DeviceHub"
    keyboard_item = ('menu item "Simulate Hardware Keyboard" of menu 1 of menu '
                     'item "Keyboard" of menu 1 of menu bar item "Device" of '
                     'menu bar 1')
    # Offsets from the window's top-left corner. Nothing in the sidebar reaches
    # the accessibility tree — the split view reports zero children, so there is
    # no row to name and no field to address — which leaves position as the only
    # handle there is. Every click is checked against the window title
    # afterwards, so a miss is loud rather than a tap into the wrong device.
    SEARCH_FIELD = (128, 74)
    FIRST_ROW = (128, 145)
    ROW_PITCH = 46
    ROWS_TO_TRY = 8

    def __init__(self, app):
        super().__init__(app)
        self._measured = {}

    # -- picking the device --------------------------------------------
    def select(self, sim):
        """Point Device Hub's one window at this device.

        There is a single window and it shows whichever device the sidebar has
        selected, so "no window for this device" is the ordinary state rather
        than a failure. Filtering by name usually leaves one row, but a name is
        not unique — the same model exists on every installed runtime — so the
        rows are tried in turn and the window title decides.
        """
        name, version = sim.device_label()
        if self._shows(name, version):
            return
        self.activate()
        time.sleep(1.0)
        wx, wy, _, _ = self._frame()
        _mouse_click(wx + self.SEARCH_FIELD[0], wy + self.SEARCH_FIELD[1],
                     settle=0.4)
        _press_escape()
        _type_mac(name)
        time.sleep(1.2)
        for row in range(self.ROWS_TO_TRY):
            _mouse_click(wx + self.FIRST_ROW[0],
                         wy + self.FIRST_ROW[1] + row * self.ROW_PITCH,
                         settle=0.8)
            if self._shows(name, version):
                return
        raise SimulatorError(
            f"could not select {name} ({version}) in Device Hub's sidebar; "
            f"the window is showing {self._title()!r}")

    def _title(self):
        try:
            return _osa('tell application "System Events" to tell process '
                        f'"{self.proc}" to return name of window 1')
        except SimulatorError:
            return ""

    def _shows(self, name, version):
        return _title_is(self._title(), name, version)

    def _frame(self):
        pos = _osa('tell application "System Events" to tell process '
                   f'"{self.proc}" to return position of window 1')
        size = _osa('tell application "System Events" to tell process '
                    f'"{self.proc}" to return size of window 1')
        x, y = (int(v) for v in pos.split(", "))
        w, h = (int(v) for v in size.split(", "))
        return x, y, w, h

    def press_home(self, sim):
        _osa('tell application "System Events" to tell process '
             f'"{self.proc}" to click menu item "Home" of menu 1 of '
             'menu bar item "Controls" of menu bar 1')
        time.sleep(1.5)

    # -- where the screen is -------------------------------------------
    def configure(self, sim):
        # The mapping is read off the bezel, and anything dark to the screen's
        # own edge reads as more bezel — so measure on the home screen, once, at
        # the start of a run rather than in the middle of one.
        self.press_home(sim)
        self.mapping(sim, sim.image().size)

    def mapping(self, sim, device_size):
        key = (sim._window_rect(), tuple(device_size))
        if key not in self._measured:
            self._measured[key] = self._measure(sim, key[0], device_size)
        return self._measured[key]

    def _measure(self, sim, rect, device_size, tries=5):
        """Measure, with patience. A device that booted seconds ago is still
        drawing, and a half-drawn screen has no bezel to find yet. The last
        failure keeps its screenshot and names it, because "could not find the
        bezel" tells you nothing on its own.
        """
        for attempt in range(tries):
            self.activate()          # a capture of a window behind iTerm2 is
            time.sleep(0.4)          # a capture of iTerm2
            try:
                return self._measure_once(sim, rect, device_size)
            except SimulatorError as e:
                last = e
                time.sleep(2.0)
        keep = (sim.artifacts or Path("/tmp")) / "measure-failed.png"
        _run("cp", str((sim.artifacts or Path("/tmp")) / "_measure.png"), str(keep),
             check=False)
        raise SimulatorError(f"{last} (window as captured: {keep})")

    def _measure_once(self, sim, rect, device_size):
        """Find the device screen inside the bezel, in screen points.

        Every scan line across the window crosses light background, then bezel,
        then screen, then bezel, then background. Screen content can be dark at
        its own edge, which makes that line's bezel look thicker, so no single
        line is trusted: each edge is the extreme across many lines, which is
        the one least eaten into by content. A screen dark all the way round —
        the dimmed backdrop behind a sheet — defeats that, and the shape check
        at the end is what turns it into an error instead of a bad mapping.
        """
        wx, wy, ww, wh = rect
        # Clamp the capture to the part of the window that is actually on the
        # display, width included. Clamping only the origin leaves the region
        # running off the far edge by however much was trimmed, and whatever
        # window sits behind there gets measured as bezel.
        ox, oy = max(wx, 0), max(wy, 0)
        cw, ch = ww - (ox - wx), wh - (oy - wy)
        shot = (sim.artifacts or Path("/tmp")) / "_measure.png"
        _run("screencapture", "-x", "-o", f"-R{ox},{oy},{cw},{ch}", str(shot))
        px = np.asarray(Image.open(shot).convert("RGB")).astype(int).sum(axis=2)
        dark = px < DARK_SUM
        h, w = dark.shape

        # The bezel runs nearly the full height of the device; sidebar text and
        # toolbar glyphs are dark too, but nowhere near that tall.
        band = dark[int(h * 0.15):int(h * 0.85)]
        cols = np.where(band.sum(axis=0) > band.shape[0] * 0.5)[0]
        if len(cols) < 2:
            raise SimulatorError(
                "no device bezel in the Device Hub window — is it showing a "
                "device at all?")
        bx0, bx1 = int(cols[0]), int(cols[-1])
        if bx1 - bx0 > w * 0.9:
            # Something outside the device — a sidebar icon column, a window
            # edge — was tall and dark enough to pass for bezel, and the span
            # between them is not a phone.
            raise SimulatorError(
                f"the tall dark columns span {bx1 - bx0}px of a {w}px window; "
                f"that is not a device bezel")
        rows = np.where(dark[:, bx0:bx1 + 1].sum(axis=1) > (bx1 - bx0) * 0.5)[0]
        if len(rows) < 2:
            raise SimulatorError("could not find the top and bottom of the bezel")
        by0, by1 = int(rows[0]), int(rows[-1])

        light = ~dark[by0:by1 + 1, bx0:bx1 + 1]
        ih, iw = light.shape
        first, last = _light_edges(light)
        rband = slice(int(ih * 0.25), int(ih * 0.75))
        left, right = int(first[rband].min()), int(last[rband].max())
        tfirst, tlast = _light_edges(light.T)
        cband = slice(int(iw * 0.25), int(iw * 0.75))
        top, bottom = int(tfirst[cband].min()), int(tlast[cband].max())

        sw, sh = right - left + 1, bottom - top + 1
        dw, dh = device_size
        if not 0.97 <= (sw / sh) / (dw / dh) <= 1.03:
            raise SimulatorError(
                f"measured a {sw}x{sh} screen for a {dw}x{dh} device. The "
                f"window is probably showing something dark to its own edges; "
                f"this has to be measured on the home screen.")
        ppp = ((sw / dw) + (sh / dh)) / 2
        # Fit on the centres — the rounded corners cost a pixel at each edge.
        cx = ox + bx0 + (left + right) / 2
        cy = oy + by0 + (top + bottom) / 2
        return cx - dw / 2 * ppp, cy - dh / 2 * ppp, ppp


def _detect_host():
    dev = Path(_run("xcode-select", "--print-path").stdout.strip())
    simulator = dev / "Applications" / "Simulator.app"
    if simulator.exists():
        return _SimulatorApp(simulator)
    device_hub = dev.parent / "Applications" / "DeviceHub.app"
    if device_hub.exists():
        return _DeviceHub(device_hub)
    raise SimulatorError(
        f"neither Simulator.app nor DeviceHub.app under {dev} — is a full "
        f"Xcode selected? xcode-select --print-path says {dev}")


HOST = _detect_host()


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
        HOST.launch()
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
    @staticmethod
    def _menu_item(item):
        """(exists, mark_char) for a menu item; (False, None) when it is absent.

        Menu contents depend on the frontmost window, and a menu item that is
        not there raises rather than returning empty — so absence has to be
        caught rather than tested.
        """
        try:
            v = _osa('tell application "System Events" to tell process '
                     f'"{HOST.proc}" to return value of attribute '
                     f'"AXMenuItemMarkChar" of {item}')
        except SimulatorError:
            return False, None
        return True, (None if v in ("", "missing value") else v)

    @staticmethod
    def _menu_click(item):
        """Click a menu item. False when this window's menu has no such item."""
        try:
            _osa('tell application "System Events" to tell process '
                 f'"{HOST.proc}" to click {item}')
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
                             f'"{HOST.proc}" to return name of window 1')
            except SimulatorError:
                front = ""
            if _title_is(front, name):
                return front
            time.sleep(0.5)
        raise SimulatorError(
            f"could not bring {name} to the front; another simulator window "
            f"is holding focus (frontmost was {front!r})")

    def prepare_window(self):
        """Get this device on screen and make its pixels tappable.

        Every menu the host offers applies to the frontmost window, so this
        device's window is made to exist, brought to the front and *verified*
        before anything is clicked. What "made to exist" and "tappable" mean
        differ per host — see the _Host classes.
        """
        HOST.launch()
        HOST.activate()
        HOST.select(self)
        self.focus_window()
        time.sleep(0.3)
        HOST.configure(self)
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
            'tell application "System Events" to tell process '
            f'"{HOST.proc}" to return name of every window')
        titles = [t.strip() for t in raw.split(",")] if raw else []
        match = next((t for t in titles if _title_is(t, name, version)), None)
        if match is None:
            match = next((t for t in titles if _title_is(t, name)), None)
        if match is None:
            raise SimulatorError(
                f"no {HOST.proc} window for {name} ({version}); saw {titles}")

        q = match.replace('"', '\\"')
        _osa(f'tell application "System Events" to tell process "{HOST.proc}" '
             f'to perform action "AXRaise" of window "{q}"')
        pos = _osa(f'tell application "System Events" to tell process '
                   f'"{HOST.proc}" to return position of window "{q}"')
        size = _osa(f'tell application "System Events" to tell process '
                    f'"{HOST.proc}" to return size of window "{q}"')
        x, y = (int(v) for v in pos.split(", "))
        w, h = (int(v) for v in size.split(", "))
        return x, y, w, h

    def _mapping(self, device_size):
        """(origin_x, origin_y, screen points per device pixel)."""
        return HOST.mapping(self, device_size)

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
        HOST.activate()
        time.sleep(0.35)
        ox, oy, ppp = self._mapping(device_size)
        _mouse_click(ox + px * ppp, oy + py * ppp, settle=settle)

    def type_text(self, text):
        """Type into the focused field, one virtual keycode at a time.

        CGEventKeyboardSetUnicodeString does nothing here: the Simulator passes
        raw keycodes through, so every character arrives as whatever keycode 0
        is and "123456" lands in the field as "Aaaaaa".
        """
        self.ensure_hardware_keyboard()
        HOST.activate()
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

    def ensure_hardware_keyboard(self):
        """Connect the hardware keyboard, re-applying it even if already checked.

        Synthesized keystrokes only reach the device through the hardware
        keyboard. An erase resets the device side of this while the Simulator
        menu can still show it checked, and the giveaway is the software
        keyboard appearing — at which point typing silently goes nowhere. So
        cycle the setting rather than trusting the tick.
        """
        item = HOST.keyboard_item
        exists, marked = self._menu_item(item)
        if not exists:
            # Unlike the display settings this one is not optional: without it
            # synthesized keystrokes reach nothing. Say which window owns the
            # menu, because that is the actual problem.
            raise SimulatorError(
                f"no hardware-keyboard item in {HOST.proc}'s menus — the "
                f"frontmost window is probably another device; call "
                f"focus_window() first")
        clicks = 2 if marked else 1
        for _ in range(clicks):
            self._menu_click(item)
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

"""The controller as mouse and keys in Desktop Mode (what hhd + Steam's
desktop layout did).

Outside Game Mode nothing turns the gamepad into a mouse unless Steam's
desktop layout runs. While this is on, in Desktop Mode and with no Steam game
running:

- right stick: moves the pointer (faster the further it's pushed)
- left stick: scrolls (up/down and sideways)
- right trigger: left click, left trigger: right click
- D-pad: arrow keys, A: Enter, B: Esc, X: Backspace, Y: Space,
  RB: Tab, LB: Shift+Tab
- left stick click (L3): on-screen keyboard. Steam's when Steam runs: it is
  steered with the controller, so HHC stays out of the way (no pointer, no
  keys) until it's closed with B or L3 is pressed again. Without Steam,
  Plasma's keyboard: that one is tapped, so the pointer keeps working; L3
  again hides it.

The gamepad is read read-only (no grab), so apps that use a gamepad still get
it; a virtual mouse + keyboard made through /dev/uinput sends the rest.
Plasma's keyboard is KWin's, asked over the desktop user's session bus.
"""

import fcntl
import logging
import os
import pwd
import select
import struct
import subprocess
import threading
import time

from . import proc
from .padchord import find_gamepads

log = logging.getLogger("hhctl")

EVENT = struct.Struct("llHHi")
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TL2, BTN_TR2, BTN_THUMBL = 0x138, 0x139, 0x13D
BTN_SOUTH, BTN_EAST, BTN_X, BTN_Y, BTN_TL, BTN_TR = 0x130, 0x131, 0x133, 0x134, 0x136, 0x137
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
KEY_ESC, KEY_BACKSPACE, KEY_TAB, KEY_ENTER, KEY_LEFTSHIFT, KEY_SPACE = 1, 14, 15, 28, 42, 57
KEY_UP, KEY_LEFT, KEY_RIGHT, KEY_DOWN = 103, 105, 106, 108
# gamepad button -> keys sent together (the D-pad comes as buttons or a hat)
KEYMAP = {
    BTN_SOUTH: (KEY_ENTER,), BTN_EAST: (KEY_ESC,), BTN_X: (KEY_BACKSPACE,), BTN_Y: (KEY_SPACE,),
    BTN_TR: (KEY_TAB,), BTN_TL: (KEY_LEFTSHIFT, KEY_TAB),
    BTN_DPAD_UP: (KEY_UP,), BTN_DPAD_DOWN: (KEY_DOWN,), BTN_DPAD_LEFT: (KEY_LEFT,), BTN_DPAD_RIGHT: (KEY_RIGHT,),
}
KEYS = sorted({k for ks in KEYMAP.values() for k in ks})
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_GAS, ABS_BRAKE = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x09, 0x0A

# uinput ioctls (linux/uinput.h)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UINPUT = "/dev/uinput"
ABSINFO = struct.Struct("6i")

DEADZONE = 0.15
SPEED = 14.0  # pixels per step at full tilt
SCROLL = 0.25  # wheel notches per step at full tilt
STEP = 0.01  # seconds between pointer moves
TRIGGER = 0.5  # pressed past half way
RESCAN = 5.0


def _absrange(fd: int, code: int) -> tuple[int, int]:
    buf = bytearray(ABSINFO.size)
    try:
        fcntl.ioctl(fd, 0x80184540 + code, buf)  # EVIOCGABS(code)
    except OSError:
        return 0, 0
    _, lo, hi, _, _, _ = ABSINFO.unpack(buf)
    return lo, hi


def stick(v: int, lo: int, hi: int) -> float:
    """-1..1 with the dead zone taken out and a curve for fine control."""
    if hi <= lo:
        return 0.0
    x = (2 * (v - lo) / (hi - lo)) - 1
    m = abs(x)
    if m < DEADZONE:
        return 0.0
    m = (m - DEADZONE) / (1 - DEADZONE)
    return (m * m) * (1 if x > 0 else -1)


class VirtualMouse:
    def __init__(self):
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        try:
            for ev in (EV_KEY, EV_REL, EV_SYN):
                fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
            for key in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, *KEYS):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
            for rel in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
                fcntl.ioctl(self.fd, UI_SET_RELBIT, rel)
            # struct uinput_user_dev: name[80], input_id (4 x u16), ff_effects_max, 4 x abs[64]
            dev = b"Handheld Control mouse".ljust(80, b"\0") + struct.pack("<HHHHi", 0x06, 0x1209, 0x4843, 1, 0)
            dev += b"\0" * (4 * 64 * 4)
            os.write(self.fd, dev)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def emit(self, events: list[tuple[int, int, int]]) -> None:
        data = b"".join(EVENT.pack(0, 0, t, c, v) for t, c, v in events)
        os.write(self.fd, data + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


def _user(home: str) -> tuple[str, int] | None:
    try:
        st = os.stat(home)
        return pwd.getpwuid(st.st_uid).pw_name, st.st_uid
    except (OSError, KeyError):
        return None


def _as_user(user: str, uid: int, home: str) -> list[str]:
    return ["runuser", "-u", user, "--", "env", f"HOME={home}", f"USER={user}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", f"XDG_RUNTIME_DIR=/run/user/{uid}",
            "DISPLAY=:0", "WAYLAND_DISPLAY=wayland-0"]


def steam_running(uid: int) -> bool:
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/comm") as f:
                if f.read().strip() != "steam":
                    continue
            if os.stat(f"/proc/{d}").st_uid == uid:
                return True
        except OSError:
            continue
    return False


def open_steam_keyboard(home: str) -> bool:
    """Steam's on-screen keyboard (steered with the controller). Only when
    Steam already runs: the URL then goes to it instead of starting Steam."""
    u = _user(home)
    if not u or not steam_running(u[1]):
        return False
    try:
        proc.popen(_as_user(u[0], u[1], home) + ["steam", "steam://open/keyboard"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log.info("desktoppad: can't ask Steam for its keyboard: %s", e)
        return False
    log.info("desktoppad: Steam keyboard")
    return True


def kde_keyboard(home: str, show: bool | None = None, only_hide: bool = False) -> bool | None:
    """Plasma's (KWin's) on-screen keyboard: show (True), hide (False) or
    toggle (None; with only_hide, hide it if shown, else leave it). Returns
    whether it's now shown, None when unavailable or left alone."""
    u = _user(home)
    if not u:
        return None
    base = _as_user(u[0], u[1], home) + ["busctl", "--user"]
    obj = ["org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard"]

    def call(*args: str):
        r = proc.run(base + list(args), text=True, timeout=5)
        if r.returncode != 0:
            log.info("desktoppad: busctl %s: %s", args[0], r.stderr.strip())
        return r

    r = call("get-property", *obj, "visible")
    if r.returncode != 0:
        return None
    if show is None:
        show = "true" not in r.stdout
        if show and only_hide:
            return None
    if show:
        call("set-property", *obj, "enabled", "b", "true")
        if call("call", *obj, "forceActivate").returncode != 0:
            call("set-property", *obj, "active", "b", "true")
    else:
        call("set-property", *obj, "active", "b", "false")
    log.info("desktoppad: Plasma keyboard %s", "shown" if show else "hidden")
    return show


class Pad:
    """One gamepad's state: stick positions and trigger levels."""

    def __init__(self, fd: int):
        self.ranges = {c: _absrange(fd, c) for c in (ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE)}
        self.abs = {c: (lo + hi) // 2 for c, (lo, hi) in self.ranges.items()}
        for c in (ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE):
            self.abs[c] = self.ranges[c][0]
        self.hat = [0, 0]  # D-pad as ABS_HAT0X / ABS_HAT0Y
        self.down: set[int] = set()  # buttons held

    def pressed(self) -> set[int]:
        """Buttons held, the hat D-pad counted as D-pad buttons."""
        out = set(self.down)
        if self.hat[0]:
            out.add(BTN_DPAD_LEFT if self.hat[0] < 0 else BTN_DPAD_RIGHT)
        if self.hat[1]:
            out.add(BTN_DPAD_UP if self.hat[1] < 0 else BTN_DPAD_DOWN)
        return out

    def axis(self, code: int) -> float:
        lo, hi = self.ranges[code]
        return stick(self.abs[code], lo, hi)

    def trigger(self, *codes: int) -> bool:
        for c in codes:
            lo, hi = self.ranges[c]
            if hi > lo and (self.abs[c] - lo) / (hi - lo) > TRIGGER:
                return True
        return False


class DesktopPad:
    def __init__(self, home: str, active):
        self.home = home
        self.active = active  # () -> bool: on, Desktop Mode, no game
        self.nodes: list[str] = []
        self.error: str | None = None
        self.keyboard: str | None = None  # on-screen keyboard shown by L3: "steam" or "kde"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hhctl-desktoppad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.active():
                self.nodes = []
                self._stop.wait(2.0)
                continue
            nodes = find_gamepads()
            if not nodes:
                self._stop.wait(2.0)
                continue
            fds = {}
            for n in nodes:
                try:
                    fd = os.open(n, os.O_RDONLY | os.O_NONBLOCK)
                    fds[fd] = Pad(fd)
                except OSError as e:
                    log.warning("desktoppad: cannot open %s: %s", n, e)
            try:
                mouse = VirtualMouse()
                self.error = None
            except OSError as e:
                self.error = f"can't make a virtual mouse ({e})"
                log.warning("desktoppad: %s", self.error)
                for fd in fds:
                    os.close(fd)
                self._stop.wait(10.0)
                continue
            self.nodes = nodes
            log.info("desktoppad: controller as mouse on %s", ", ".join(nodes))
            try:
                self._loop(fds, mouse, nodes)
            except Exception:
                log.exception("desktoppad: failed, starting over")
            finally:
                mouse.close()
                for fd in fds:
                    os.close(fd)
                self.nodes = []

    def _toggle_keyboard(self) -> None:
        """L3, on its own thread (the bus calls take a moment)."""
        if self.keyboard == "steam":
            self.keyboard = None  # closed (or never opened): the controller is HHC's again
        elif kde_keyboard(self.home, None, only_hide=True) is False:
            self.keyboard = None  # Plasma's was up (from L3 or a swipe): now hidden
        elif open_steam_keyboard(self.home):
            self.keyboard = "steam"
        elif kde_keyboard(self.home, True):
            self.keyboard = "kde"
        else:
            log.info("desktoppad: no on-screen keyboard (Steam isn't running, Plasma's isn't available)")

    def _loop(self, fds: dict[int, Pad], mouse: VirtualMouse, nodes: list[str]) -> None:
        held = {BTN_LEFT: False, BTN_RIGHT: False}
        digital = {BTN_LEFT: False, BTN_RIGHT: False}  # pads with trigger buttons instead of axes
        keys: set[int] = set()  # keyboard keys held down on the virtual device
        ignore: set[int] = set()  # held while Steam's keyboard was up: ignored until let go
        last_l3 = 0.0
        wheel = [0.0, 0.0]  # fractional notches (vertical, horizontal)
        rem = [0.0, 0.0]  # fractional pixels
        next_check = time.monotonic() + RESCAN

        def release() -> None:
            out = [(EV_KEY, btn, 0) for btn, down in held.items() if down]
            out += [(EV_KEY, k, 0) for k in keys]
            for btn in held:
                held[btn] = False
            keys.clear()
            if out:
                mouse.emit(out)

        while not self._stop.is_set():
            ready = select.select(list(fds), [], [], STEP)[0]
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                pad = fds[fd]
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if etype == EV_ABS and code in pad.abs:
                        pad.abs[code] = value
                    elif etype == EV_ABS and code in (ABS_HAT0X, ABS_HAT0Y):
                        pad.hat[code - ABS_HAT0X] = value
                    elif etype == EV_KEY and value in (0, 1):
                        if value:
                            pad.down.add(code)
                        else:
                            pad.down.discard(code)
                        if code == BTN_THUMBL and value:
                            if time.monotonic() - last_l3 > 0.5:  # once, even when two pads send it
                                last_l3 = time.monotonic()
                                threading.Thread(target=self._toggle_keyboard, daemon=True).start()
                        elif code == BTN_EAST and value and self.keyboard == "steam":
                            self.keyboard = None  # B closes Steam's keyboard (and isn't Esc: see ignore)
                        elif code in (BTN_TL2, BTN_TR2):
                            digital[BTN_LEFT if code == BTN_TR2 else BTN_RIGHT] = bool(value)
            pressed: set[int] = set()
            for pad in fds.values():
                pressed |= pad.pressed()
            if self.keyboard == "steam":
                # Steam's keyboard is steered with the controller: stay out of it.
                release()
                ignore = pressed | {BTN_EAST}
                rem, wheel = [0.0, 0.0], [0.0, 0.0]
            else:
                out = []
                # Pressed on any gamepad: InputPlumber's virtual one and the real
                # one it grabbed are both listed, and the grabbed one stays at rest.
                for btn, codes in ((BTN_LEFT, (ABS_RZ, ABS_GAS)), (BTN_RIGHT, (ABS_Z, ABS_BRAKE))):
                    down = any(pad.trigger(*codes) for pad in fds.values()) or digital[btn]
                    if down != held[btn]:
                        held[btn] = down
                        out.append((EV_KEY, btn, int(down)))
                ignore &= pressed
                want = {k for b in pressed - ignore if b in KEYMAP for k in KEYMAP[b]}
                for k in sorted(keys - want):
                    out.append((EV_KEY, k, 0))
                for k in sorted(want - keys, key=lambda k: k != KEY_LEFTSHIFT):  # Shift before Tab
                    out.append((EV_KEY, k, 1))
                keys.clear()
                keys.update(want)
                for pad in fds.values():
                    for i, code in enumerate((ABS_RX, ABS_RY)):
                        rem[i] += pad.axis(code) * SPEED
                    wheel[0] -= pad.axis(ABS_Y) * SCROLL
                    wheel[1] += pad.axis(ABS_X) * SCROLL
                for i, rel in enumerate((REL_X, REL_Y)):
                    whole = int(rem[i])
                    if whole:
                        out.append((EV_REL, rel, whole))
                        rem[i] -= whole
                for i, rel in enumerate((REL_WHEEL, REL_HWHEEL)):
                    whole = int(wheel[i])
                    if whole:
                        out.append((EV_REL, rel, whole))
                        wheel[i] -= whole
                if out:
                    mouse.emit(out)
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + RESCAN
                if not self.active() or find_gamepads() != nodes:
                    release()
                    self.keyboard = None
                    return

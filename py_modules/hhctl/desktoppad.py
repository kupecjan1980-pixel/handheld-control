"""The controller as a mouse in Desktop Mode (what hhd's "desktop" mode did).

Outside Game Mode nothing turns the gamepad into a mouse unless Steam's
desktop layout runs. While this is on, in Desktop Mode and with no Steam game
running:

- left stick: moves the pointer (faster the further it's pushed)
- right stick: scrolls (up/down and sideways)
- right trigger: left click, left trigger: right click
- left stick click (L3): KDE's on-screen keyboard, on and off

The gamepad is read read-only (no grab), so apps that use a gamepad still get
it; a virtual mouse made through /dev/uinput sends the pointer and clicks. The
on-screen keyboard is KWin's (Plasma's virtual keyboard, e.g. Maliit), asked
over the desktop user's session bus.
"""

import fcntl
import logging
import os
import pwd
import select
import struct
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
            for key in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
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


def toggle_kde_keyboard(home: str) -> bool:
    """Show or hide KWin's on-screen keyboard as the desktop user."""
    try:
        st = os.stat(home)
        user = pwd.getpwuid(st.st_uid).pw_name
    except (OSError, KeyError):
        return False
    bus = f"unix:path=/run/user/{st.st_uid}/bus"
    base = ["runuser", "-u", user, "--", "env", f"DBUS_SESSION_BUS_ADDRESS={bus}",
            f"XDG_RUNTIME_DIR=/run/user/{st.st_uid}", "busctl", "--user"]
    obj = ["org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard"]
    r = proc.run(base + ["get-property", *obj, "active"], text=True, timeout=5)
    if r.returncode != 0:
        log.info("desktoppad: no KWin virtual keyboard (%s)", r.stderr.strip())
        return False
    show = "true" not in r.stdout
    if show:
        proc.run(base + ["set-property", *obj, "enabled", "b", "true"], text=True, timeout=5)
        r = proc.run(base + ["call", *obj, "forceActivate"], text=True, timeout=5)
        if r.returncode == 0:
            return True
    r = proc.run(base + ["set-property", *obj, "active", "b", "true" if show else "false"], text=True, timeout=5)
    return r.returncode == 0


class Pad:
    """One gamepad's state: stick positions and trigger levels."""

    def __init__(self, fd: int):
        self.ranges = {c: _absrange(fd, c) for c in (ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE)}
        self.abs = {c: (lo + hi) // 2 for c, (lo, hi) in self.ranges.items()}
        for c in (ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE):
            self.abs[c] = self.ranges[c][0]

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

    def _loop(self, fds: dict[int, Pad], mouse: VirtualMouse, nodes: list[str]) -> None:
        held = {BTN_LEFT: False, BTN_RIGHT: False}
        wheel = [0.0, 0.0]  # fractional notches (vertical, horizontal)
        rem = [0.0, 0.0]  # fractional pixels
        next_check = time.monotonic() + RESCAN
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
                    elif etype == EV_KEY and code == BTN_THUMBL and value == 1:
                        threading.Thread(target=toggle_kde_keyboard, args=(self.home,), daemon=True).start()
                    elif etype == EV_KEY and code in (BTN_TL2, BTN_TR2) and value in (0, 1):
                        btn = BTN_LEFT if code == BTN_TR2 else BTN_RIGHT
                        if held[btn] != bool(value):
                            held[btn] = bool(value)
                            mouse.emit([(EV_KEY, btn, value)])
            out = []
            for pad in fds.values():
                for btn, codes in ((BTN_LEFT, (ABS_RZ, ABS_GAS)), (BTN_RIGHT, (ABS_Z, ABS_BRAKE))):
                    down = pad.trigger(*codes)
                    if down != held[btn]:
                        held[btn] = down
                        out.append((EV_KEY, btn, int(down)))
                for i, code in enumerate((ABS_X, ABS_Y)):
                    rem[i] += pad.axis(code) * SPEED
                wheel[0] -= pad.axis(ABS_RY) * SCROLL
                wheel[1] += pad.axis(ABS_RX) * SCROLL
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
                    for btn, down in held.items():
                        if down:
                            mouse.emit([(EV_KEY, btn, 0)])
                    return

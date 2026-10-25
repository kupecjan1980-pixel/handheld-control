"""Suspend: lights off before it sleeps; the controller may wake it.

logind announces a suspend with PrepareForSleep(true) and waits for "delay"
inhibitors (up to InhibitDelayMaxSec, 5 s by default). The plugin holds one
(systemd-inhibit --mode=delay), turns the lights off when the signal comes,
then lets go so the suspend goes on; after resume it takes a new one. The
lights come back with the usual re-apply after resume (controller.tick).
busctl and systemd-inhibit do the D-Bus part, so no Python D-Bus library.

Waking with the controller: USB remote wakeup has to be allowed on the
gamepad's USB device and everything above it (hubs, the xHCI controller):
power/wakeup = enabled. Whether a press or a stick move then wakes it is up
to the controller's firmware. The previous values go back when it's off.
A device that keeps sending reports (the NEXT 2's gamepad: its right trigger
jitters 0-5 all the time) wakes it at once, so each device's power/
wakeup_count is read before and after a sleep to tell what woke it.
"""

import json
import logging
import os
import subprocess
import threading
import time

from . import proc, sysfs

log = logging.getLogger("hhctl")

SYS_INPUT = "/sys/class/input"
MATCH = ("type='signal',sender='org.freedesktop.login1',"
         "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'")


def boottime() -> float:
    """Seconds since boot, suspend included (monotonic leaves it out)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class SleepWatch:
    def __init__(self, before_sleep, after_wake=None):
        self.before_sleep = before_sleep  # called in this thread, quickly
        self.after_wake = after_wake  # called in this thread; just note it, the tick acts
        self._inhibit: subprocess.Popen | None = None
        self._monitor: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.enabled = False

    def _take(self) -> None:
        if self._inhibit and self._inhibit.poll() is None:
            return
        try:
            self._inhibit = proc.popen(
                ["systemd-inhibit", "--what=sleep", "--mode=delay", "--who=Handheld Control",
                 "--why=Turning the lights off first", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.warning("sleep: no inhibitor (%s)", e)

    def _release(self) -> None:
        if self._inhibit and self._inhibit.poll() is None:
            self._inhibit.terminate()
            try:
                self._inhibit.wait(2)
            except subprocess.TimeoutExpired:
                self._inhibit.kill()
        self._inhibit = None

    def start(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        self._take()
        self._thread = threading.Thread(target=self._run, name="hhctl-sleep", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.enabled = False
        self._release()
        if self._monitor and self._monitor.poll() is None:
            self._monitor.terminate()

    def _run(self) -> None:
        while self.enabled:
            try:
                self._monitor = proc.popen(["busctl", "--system", "--json=short", "monitor", "--match", MATCH],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           text=True)
            except OSError as e:
                log.warning("sleep: can't watch for suspend (%s)", e)
                return
            for line in self._monitor.stdout:
                self.handle(line)
            self._monitor.wait()
            if self.enabled:
                threading.Event().wait(5)  # busctl went away: start over

    def handle(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("member") != "PrepareForSleep":
            return
        going = bool((msg.get("payload") or {}).get("data", [False])[0])
        if going:
            log.info("sleep: going to sleep")
            try:
                self.before_sleep()
            except Exception:
                log.exception("sleep: before-sleep failed")
            self._release()
        else:
            log.info("sleep: woke up")
            self._take()
            if self.after_wake:
                try:
                    self.after_wake()
                except Exception:
                    log.exception("sleep: after-wake failed")


def _wakeup_chain(event_dev: str) -> list[str]:
    """power/wakeup files from the input device's USB device up to the PCI controller."""
    out = []
    d = os.path.realpath(f"{SYS_INPUT}/{event_dev}/device")
    while d and d != "/" and not d.endswith("/devices"):
        w = os.path.join(d, "power", "wakeup")
        if os.path.exists(w) and (os.path.exists(os.path.join(d, "idVendor")) or "/pci" in d):
            out.append(w)
        d = os.path.dirname(d)
    return out


def find_gamepads(ids: list[tuple[str, str]]) -> list[str]:
    """eventN of the input devices with these USB vendor:product ids."""
    try:
        names = sorted(n for n in os.listdir(SYS_INPUT) if n.startswith("event"))
    except OSError:
        return []
    found = []
    for n in names:
        d = f"{SYS_INPUT}/{n}/device"
        if (sysfs.read(f"{d}/id/vendor"), sysfs.read(f"{d}/id/product")) in ids:
            found.append(n)
    return found


class PadWake:
    """Allows (or takes back) waking from suspend with the controller."""

    def __init__(self, ids: list[tuple[str, str]]):
        self.ids = ids
        self.orig: dict[str, str] = {}
        self.counts: dict[str, int] = {}  # wakeup_count per device before the sleep

    def devices(self) -> dict[tuple[str, str], str]:
        """(vendor, product) -> its USB device's power/wakeup file."""
        out = {}
        for ev in find_gamepads(self.ids):
            d = f"{SYS_INPUT}/{ev}/device"
            chain = _wakeup_chain(ev)
            if chain:
                out.setdefault((sysfs.read(f"{d}/id/vendor"), sysfs.read(f"{d}/id/product")), chain[0])
        return out

    def files(self) -> list[str]:
        out: list[str] = []
        for ev in find_gamepads(self.ids):
            for w in _wakeup_chain(ev):
                if w not in out:
                    out.append(w)
        return out

    def snapshot(self) -> None:
        self.counts = {}
        for ident, w in self.devices().items():
            n = sysfs.read_int(os.path.join(os.path.dirname(w), "wakeup_count"))
            if n is not None:
                self.counts[f"{ident[0]}:{ident[1]}"] = n

    def woke_by(self) -> list[str]:
        """vendor:product of the devices whose wakeup count went up since snapshot()."""
        out = []
        for ident, w in self.devices().items():
            key = f"{ident[0]}:{ident[1]}"
            n = sysfs.read_int(os.path.join(os.path.dirname(w), "wakeup_count"))
            if n is not None and key in self.counts and n > self.counts[key]:
                out.append(key)
        return out

    def available(self) -> bool:
        return bool(self.ids) and bool(self.files())

    def enable(self) -> None:
        for w in self.files():
            cur = sysfs.read(w)
            if cur is None:
                continue
            self.orig.setdefault(w, cur)
            if cur != "enabled":
                sysfs.write(w, "enabled")

    def restore(self) -> None:
        for w, val in list(self.orig.items()):
            try:
                if sysfs.read(w) != val:
                    sysfs.write(w, val)
            except OSError:
                pass
            self.orig.pop(w, None)


# --- what woke it ------------------------------------------------------------------

WAKEUP_CLASS = "/sys/class/wakeup"


def wake_counts() -> dict[str, tuple[int, int]]:
    """Every wakeup source: name -> (wakeup_count, event_count)."""
    out = {}
    try:
        names = os.listdir(WAKEUP_CLASS)
    except OSError:
        return out
    for n in names:
        d = f"{WAKEUP_CLASS}/{n}"
        name = sysfs.read(f"{d}/name")
        if name:
            out[name] = (sysfs.read_int(f"{d}/wakeup_count") or 0, sysfs.read_int(f"{d}/event_count") or 0)
    return out


def _label(name: str) -> str:
    """'1-4' -> '1-4 (AYANEO COMPOSITE DEVICE 1c4f:0002)' for USB devices."""
    usb = f"/sys/bus/usb/devices/{name}"
    vid, pid = sysfs.read(f"{usb}/idVendor"), sysfs.read(f"{usb}/idProduct")
    if vid and pid:
        prod = sysfs.read(f"{usb}/product") or "USB device"
        return f"{name} ({prod} {vid}:{pid})"
    return name


def wake_irq() -> str | None:
    irq = sysfs.read("/sys/power/pm_wakeup_irq")
    if not irq:
        return None
    for line in (sysfs.read("/proc/interrupts") or "").splitlines():
        head, _, rest = line.partition(":")
        if head.strip() == irq:
            return f"IRQ {irq} ({rest.split()[-1]})" if rest.split() else f"IRQ {irq}"
    return f"IRQ {irq}"


def woken_by(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """Wakeup sources that fired across the sleep: wakeup_count first (they
    woke it or stopped the suspend), else event_count."""
    up = lambda i: [n for n, c in after.items() if n in before and c[i] > before[n][i]]
    return [_label(n) for n in (up(0) or up(1))]

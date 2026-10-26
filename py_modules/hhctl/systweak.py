"""System toggles: memory, storage, PCIe, USB and Wi-Fi power settings.

Each one is set at runtime only, and only when picked; the value it had goes
back when it's set to "system" again, when the plugin unloads and with every
reboot (nothing is written to /etc). Most distros already pick sensible
values, so the panel shows the current one next to each.

- thp: transparent hugepages. "madvise" (SteamOS) gives huge pages only to
  programs that ask; "always" can stall a game while memory is compacted.
- swappiness: how readily memory goes to swap. With zram (compressed RAM,
  Bazzite / CachyOS / SteamOS) a high value is right; with swap on a disk, low.
- nvme_none: the "none" I/O scheduler on NVMe drives (fastest for them).
- aspm: PCIe link power saving. "performance" keeps links awake (less
  latency, more power); "powersave" saves battery.
- usb_awake: no USB autosuspend, so controllers and dongles never doze off.
- wifi_ps_off: Wi-Fi power saving off, for steadier game streaming (more power).
"""

import glob
import logging
import os
import time

from . import proc, sysfs

log = logging.getLogger("hhctl")

THP = "/sys/kernel/mm/transparent_hugepage/enabled"
SWAPPINESS = "/proc/sys/vm/swappiness"
ASPM = "/sys/module/pcie_aspm/parameters/policy"
USB_AUTOSUSPEND = "/sys/module/usbcore/parameters/autosuspend"
USB_DEVICES = "/sys/bus/usb/devices"
NET = "/sys/class/net"
SWAPPINESS_CHOICES = (10, 60, 100, 180)


def nvme_queues() -> list[str]:
    return sorted(glob.glob("/sys/block/nvme*n*/queue/scheduler"))


def wifi_ifaces() -> list[str]:
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f"{NET}/*/wireless"))


def zram() -> bool:
    return bool(glob.glob("/sys/block/zram*")) and "zram" in (sysfs.read("/proc/swaps") or "")


class SysTweaks:
    def __init__(self):
        self.orig: dict[str, object] = {}  # what each touched setting was
        self._ps: tuple[float, bool | None] | None = None  # Wi-Fi power save, looked up every 10 s

    # --- what's there -----------------------------------------------------

    def _wifi_ps(self, iface: str) -> bool | None:
        r = proc.run(["iw", "dev", iface, "get", "power_save"], text=True, timeout=5)
        if r.returncode != 0:
            return None
        return "on" in r.stdout.lower().split(":")[-1]

    def state(self) -> dict:
        out: dict[str, dict] = {}
        if os.path.exists(THP):
            out["thp"] = {"current": sysfs.selected(THP), "choices": sysfs.choices(THP)}
        if os.path.exists(SWAPPINESS):
            out["swappiness"] = {"current": sysfs.read_int(SWAPPINESS), "choices": list(SWAPPINESS_CHOICES),
                                 "zram": zram()}
        queues = nvme_queues()
        if queues and any("none" in sysfs.choices(q) for q in queues):
            out["nvme_none"] = {"current": sorted({sysfs.selected(q) for q in queues})}
        if sysfs.writable(ASPM):
            out["aspm"] = {"current": sysfs.selected(ASPM), "choices": sysfs.choices(ASPM)}
        if os.path.exists(USB_AUTOSUSPEND):
            out["usb_awake"] = {"current": sysfs.read_int(USB_AUTOSUSPEND)}
        ifaces = wifi_ifaces()
        if ifaces:
            if not self._ps or time.monotonic() - self._ps[0] > 10:
                try:
                    self._ps = (time.monotonic(), self._wifi_ps(ifaces[0]))
                except OSError:
                    self._ps = (time.monotonic(), None)  # no iw
            ps = self._ps[1]
            if ps is not None:
                out["wifi_ps_off"] = {"current": ps, "iface": ifaces[0]}
        return out

    # --- setting and putting back ------------------------------------------

    def _keep(self, key: str, value) -> None:
        self.orig.setdefault(key, value)

    def apply(self, s: dict) -> None:
        """s: the "system" settings. None / False leaves (or puts back) a setting."""
        errors = []
        for key in ("thp", "swappiness", "nvme_none", "aspm", "usb_awake", "wifi_ps_off"):
            want = s.get(key)
            try:
                if want in (None, False):
                    self._restore(key)
                else:
                    self._set(key, want)
            except (OSError, ValueError) as e:
                errors.append(f"{key}: {e}")
        if errors:
            raise OSError("; ".join(errors))

    def _set(self, key: str, want) -> None:
        if key == "thp":
            if sysfs.selected(THP) != want:
                self._keep(key, sysfs.selected(THP))
                sysfs.write(THP, want)
        elif key == "swappiness":
            cur = sysfs.read_int(SWAPPINESS)
            if cur != int(want):
                self._keep(key, cur)
                sysfs.write(SWAPPINESS, int(want))
        elif key == "nvme_none":
            for q in nvme_queues():
                cur = sysfs.selected(q)
                if cur != "none" and "none" in sysfs.choices(q):
                    self.orig.setdefault(key, {})[q] = cur  # type: ignore[index]
                    sysfs.write(q, "none")
        elif key == "aspm":
            if sysfs.selected(ASPM) != want:
                self._keep(key, sysfs.selected(ASPM))
                sysfs.write(ASPM, want)
        elif key == "usb_awake":
            cur = sysfs.read_int(USB_AUTOSUSPEND)
            if cur != -1:
                self._keep(key, cur)
                sysfs.write(USB_AUTOSUSPEND, -1)  # devices plugged in from now on
            # ...and the ones already there
            ctl = self.orig.setdefault("usb_awake_dev", {})
            for c in glob.glob(f"{USB_DEVICES}/*/power/control"):
                v = sysfs.read(c)
                if v and v != "on":
                    ctl.setdefault(c, v)  # type: ignore[union-attr]
                    try:
                        sysfs.write(c, "on")
                    except OSError:
                        pass
        elif key == "wifi_ps_off":
            for iface in wifi_ifaces():
                if self._wifi_ps(iface):
                    self.orig.setdefault(key, []).append(iface)  # type: ignore[union-attr]
                    r = proc.run(["iw", "dev", iface, "set", "power_save", "off"], text=True, timeout=5)
                    if r.returncode != 0:
                        raise OSError(r.stderr.strip() or "iw failed")
            self._ps = None
        log.info("systweak: %s set", key)

    def _restore(self, key: str) -> None:
        if key not in self.orig:
            return
        old = self.orig.pop(key)
        if key == "thp":
            sysfs.write(THP, old)
        elif key == "swappiness":
            sysfs.write(SWAPPINESS, old)
        elif key == "nvme_none":
            for q, v in old.items():  # type: ignore[union-attr]
                try:
                    sysfs.write(q, v)
                except OSError:
                    pass  # drive gone
        elif key == "aspm":
            sysfs.write(ASPM, old)
        elif key == "usb_awake":
            sysfs.write(USB_AUTOSUSPEND, old)
            for c, v in self.orig.pop("usb_awake_dev", {}).items():  # type: ignore[union-attr]
                try:
                    sysfs.write(c, v)
                except OSError:
                    pass
        elif key == "wifi_ps_off":
            for iface in old:  # type: ignore[union-attr]
                proc.run(["iw", "dev", iface, "set", "power_save", "on"], text=True, timeout=5)
            self._ps = None
        log.info("systweak: %s put back", key)

    def restore(self) -> None:
        for key in list(self.orig):
            if key != "usb_awake_dev":
                try:
                    self._restore(key)
                except OSError as e:
                    log.warning("systweak: can't put %s back: %s", key, e)

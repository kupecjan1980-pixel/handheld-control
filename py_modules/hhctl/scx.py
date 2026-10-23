"""sched_ext CPU schedulers (scx_lavd, scx_bpfland, ...) through scx_loader.

scx_loader is the D-Bus service CachyOS and Bazzite ship to start and switch
the BPF schedulers; busctl talks to it, so no Python D-Bus library is needed.
The kernel reports the running scheduler in /sys/kernel/sched_ext. If a
scheduler exits (or crashes), the kernel falls back to its own scheduler.
"""

import json
import subprocess
import time

from . import proc, sysfs

SCX = "/sys/kernel/sched_ext"
BUS = ["busctl", "--system", "--json=short"]
DEST = ["org.scx.Loader", "/org/scx/Loader", "org.scx.Loader"]
# scx_loader's SchedMode values
MODES = {"auto": 0, "gaming": 1, "powersave": 2, "lowlatency": 3}
RETRY = 60  # seconds before starting a scheduler again that didn't stay up


def short(name: str) -> str:
    return name.removeprefix("scx_")


class Scx:
    def __init__(self):
        self.tried: dict[str, float] = {}  # name -> when we last started it
        self._supported: list[str] = []
        self._asked = -60.0

    def _get(self, prop: str):
        try:
            r = proc.run(BUS + ["get-property", *DEST, prop], timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout).get("data")
        except ValueError:
            return None

    def _call(self, method: str, *args: str) -> None:
        try:
            r = proc.run(BUS + ["call", *DEST, method, *args], timeout=15)
        except subprocess.SubprocessError as e:
            raise OSError(f"{method}: {e}") from e
        if r.returncode != 0:
            raise OSError((r.stderr or b"").decode(errors="replace").strip() or f"{method} failed")

    def supported(self) -> list[str]:
        # The panel polls state often; don't ask a missing service every time.
        if not self._supported and time.monotonic() - self._asked > 30:
            self._asked = time.monotonic()
            got = self._get("SupportedSchedulers")
            if isinstance(got, list):
                self._supported = [str(x) for x in got]
        return self._supported

    def available(self) -> tuple[bool, str | None]:
        if sysfs.read(f"{SCX}/state") is None:
            return False, "the kernel has no sched_ext"
        if not self.supported():
            return False, "scx_loader isn't installed or isn't running"
        return True, None

    def running(self) -> str | None:
        """The kernel's view: the running scheduler's name, None for the default."""
        if sysfs.read(f"{SCX}/state") != "enabled":
            return None
        ops = sysfs.read(f"{SCX}/root/ops") or "scx"
        return ops.split("_")[0]

    def mode(self) -> str | None:
        val = self._get("SchedulerMode")
        return next((k for k, v in MODES.items() if v == val), None)

    def apply(self, name: str, mode: str) -> None:
        """name: a scx_loader scheduler, or "off" for the kernel's default."""
        if name == "off":
            if self.running():
                self._call("StopScheduler")
            return
        now = self.running()
        if now == short(name):
            if self.mode() == mode:
                return
        elif time.monotonic() - self.tried.get(name, -RETRY) < RETRY:
            return  # failed to start or exited: don't restart it every tick
        self.tried[name] = time.monotonic()
        method = "SwitchScheduler" if now else "StartScheduler"
        self._call(method, "su", name, str(MODES.get(mode, 0)))

"""Gaming memory settings that SteamOS ships by default, for fewer stutters
(not more average fps). Applied at runtime only, and only where the value
differs; the old values go back when it's turned off, on unload and with
every reboot (nothing is written to /etc).

  vm.compaction_proactiveness = 0   no background memory compaction, which can
                                    stall a game for a moment while it runs
  vm.page_lock_unfairness = 1       hands a contended page lock over sooner
                                    (shorter worst-case waits)
  kernel.split_lock_mitigate = 0    no deliberate slow-down of programs that do
                                    split locks (meant for shared servers);
                                    some games do them and stutter badly
"""

import logging

from . import sysfs

log = logging.getLogger("hhctl")

TWEAKS = {
    "vm.compaction_proactiveness": "0",
    "vm.page_lock_unfairness": "1",
    "kernel.split_lock_mitigate": "0",
}
PROC = "/proc/sys"


def _path(key: str) -> str:
    return f"{PROC}/{key.replace('.', '/')}"


class MemTweak:
    def __init__(self):
        self.orig: dict[str, str] = {}

    def available(self) -> dict[str, str]:
        """The tweaks this kernel has, with their current values."""
        out = {}
        for k in TWEAKS:
            v = sysfs.read(_path(k))
            if v is not None:
                out[k] = v
        return out

    def pending(self) -> list[str]:
        """Settings not at the SteamOS value yet."""
        return [k for k, v in self.available().items() if v != TWEAKS[k]]

    def apply(self) -> None:
        for k, cur in self.available().items():
            if cur != TWEAKS[k]:
                self.orig.setdefault(k, cur)
                sysfs.write(_path(k), TWEAKS[k])
                log.info("memtweak: %s %s -> %s", k, cur, TWEAKS[k])

    def restore(self) -> None:
        for k, v in list(self.orig.items()):
            try:
                sysfs.write(_path(k), v)
            except OSError as e:
                log.warning("memtweak: can't put %s back: %s", k, e)
            self.orig.pop(k, None)

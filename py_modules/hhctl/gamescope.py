"""Game Mode (gamescope) HDR output control.

On the ONEXPLAYER 3, gamescope drives the internal OLED in HDR10
(connector colorspace BT2020_RGB) even though Steam shows no HDR toggle, and
in that mode the panel ignores the backlight. Gamescope decides this with two
convars, changeable at runtime through `gamescopectl`:

- hdr_enabled: HDR output when the panel supports it (Steam sets this).
- hdr_content_driven (newer gamescope): only switch the panel to HDR while
  an HDR app is on screen; Steam's UI and SDR games stay SDR.

Convars live only as long as the gamescope process, so a reboot or session
restart always returns to Steam's defaults. gamescopectl's replies go to
gamescope's own log, so success is judged from the DRM connector colorspace.
"""

import glob
import os
import pwd
import shutil

from . import proc, sysfs

MODES = {
    # mode: convar values to set (only those the running gamescope has)
    "steam": {"hdr_content_driven": "0", "hdr_enabled": "1"},
    "content": {"hdr_enabled": "1", "hdr_content_driven": "1"},
    "off": {"hdr_enabled": "0"},
}


def _gamescope_pid() -> int | None:
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm) as f:
                if f.read().strip() in ("gamescope", "gamescope-wl"):
                    return int(comm.split("/")[2])
        except (OSError, ValueError):
            continue
    return None


def _environ(pid: int) -> dict[str, str]:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read().split(b"\0")
    except OSError:
        return {}
    out = {}
    for item in raw:
        k, sep, v = item.partition(b"=")
        if sep:
            out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


def hdr_output_active() -> bool | None:
    """Is the internal panel's connector in the BT2020 (HDR) colorspace?"""
    for state in sorted(glob.glob("/sys/kernel/debug/dri/*/state")):
        text = sysfs.read(state) or ""
        block = text.split("connector[")
        for part in block[1:]:
            if "eDP" in part.split("\n", 1)[0]:
                for line in part.splitlines():
                    line = line.strip()
                    if line.startswith("colorspace="):
                        return "BT2020" in line
        return None
    return None


class Gamescope:
    def __init__(self, pid: int):
        self.pid = pid
        st = os.stat(f"/proc/{pid}")
        self.uid, self.gid = st.st_uid, st.st_gid
        env = _environ(pid)
        self.runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{self.uid}"
        self.display = env.get("GAMESCOPE_WAYLAND_DISPLAY") or self._find_socket()
        self.exe = os.path.realpath(f"/proc/{pid}/exe")
        self.ctl = shutil.which("gamescopectl", path=proc.env()["PATH"])
        self._convars = self._scan_convars()

    @classmethod
    def find(cls):
        pid = _gamescope_pid()
        if pid is None:
            return None
        try:
            return cls(pid)
        except OSError:
            return None

    def _find_socket(self) -> str:
        socks = sorted(glob.glob(f"{self.runtime_dir}/gamescope-*"))
        socks = [s for s in socks if not s.endswith(".lock")]
        return os.path.basename(socks[0]) if socks else "gamescope-0"

    def _scan_convars(self) -> set[str]:
        try:
            with open(self.exe, "rb") as f:
                blob = f.read()
        except OSError:
            return set()
        return {c for c in ("hdr_enabled", "hdr_content_driven") if c.encode() in blob}

    def alive(self) -> bool:
        return os.path.exists(f"/proc/{self.pid}")

    def modes(self) -> list[str]:
        if not self.ctl or "hdr_enabled" not in self._convars:
            return []
        out = ["steam"]
        if "hdr_content_driven" in self._convars:
            out.append("content")
        out.append("off")
        return out

    def _set(self, convar: str, value: str) -> None:
        env = proc.env()
        env.update({"XDG_RUNTIME_DIR": self.runtime_dir, "GAMESCOPE_WAYLAND_DISPLAY": self.display})
        try:
            env["HOME"] = pwd.getpwuid(self.uid).pw_dir
        except KeyError:
            pass
        res = proc.run([self.ctl, convar, value], text=True, timeout=10, env=env,
                       user=self.uid, group=self.gid)
        if res.returncode != 0:
            raise OSError(f"gamescopectl {convar} {value}: {(res.stderr or res.stdout).strip() or res.returncode}")

    def apply(self, mode: str) -> None:
        for convar, value in MODES[mode].items():
            if convar in self._convars:
                self._set(convar, value)

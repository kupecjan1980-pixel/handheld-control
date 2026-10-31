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

SDR-in-HDR brightness: while the output is HDR, gamescope draws SDR content
(Steam's UI, normal games) at GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS nits,
an X root-window property holding a float's bit pattern. Changing it dims or
brightens the picture without any display mode change. Removing it returns
gamescope to its built-in default.
"""

import glob
import os
import pwd
import re
import shutil
import stat
import struct
import time
from collections import Counter

from . import proc, sysfs

SDR_ATOM = "GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS"


def nits_to_cardinal(nits: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(nits)))[0]


def cardinal_to_nits(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value & 0xFFFFFFFF))[0]


MODES = {
    # mode: convar values to set (only those the running gamescope has)
    "steam": {"hdr_content_driven": "0", "hdr_enabled": "1"},
    "content": {"hdr_enabled": "1", "hdr_content_driven": "1"},
    "off": {"hdr_enabled": "0"},
}


_pid_seen: list = [None, 0.0]  # the gamescope found last, and when the processes were last read for it


def _is_gamescope(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip() in ("gamescope", "gamescope-wl")
    except OSError:
        return False


def _gamescope_pid() -> int | None:
    """Gamescope's process: the one found before while it still runs (one file read); otherwise every process
    read, at most every 5 s (asked every 2 s and more, a full read was much of HHC's own CPU use)."""
    pid, at = _pid_seen
    if pid and _is_gamescope(pid):
        return pid
    if not pid and time.monotonic() - at < 5:
        return None
    _pid_seen[:] = [None, time.monotonic()]
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm) as f:
                if f.read().strip() in ("gamescope", "gamescope-wl"):
                    _pid_seen[0] = int(comm.split("/")[2])
                    return _pid_seen[0]
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
        self.exe = os.path.realpath(f"/proc/{pid}/exe")
        self.ctl = shutil.which("gamescopectl", path=proc.env()["PATH"])
        self._convars = self._scan_convars()
        self.display: str | None = None  # found lazily: gamescope may still be starting

    @classmethod
    def find(cls):
        pid = _gamescope_pid()
        if pid is None:
            return None
        try:
            return cls(pid)
        except OSError:
            return None

    def _candidates(self) -> list[str]:
        """Possible GAMESCOPE_WAYLAND_DISPLAY names, most likely first.

        Gamescope sets the variable for the programs it starts (Steam and
        games), not reliably for itself, so the session's own processes are
        the best source; the sockets in the runtime dir are the fallback.
        """
        seen = Counter()
        for environ in glob.glob("/proc/[0-9]*/environ"):
            try:
                if os.stat(environ).st_uid != self.uid:
                    continue
                with open(environ, "rb") as f:
                    m = re.search(rb"(?:^|\0)GAMESCOPE_WAYLAND_DISPLAY=([^\0]+)", f.read())
            except OSError:
                continue
            if m:
                seen[m.group(1).decode(errors="replace")] += 1
        names = [n for n, _ in seen.most_common()]
        for path in sorted(glob.glob(f"{self.runtime_dir}/gamescope-*")):
            name = os.path.basename(path)
            try:
                is_sock = stat.S_ISSOCK(os.stat(path).st_mode)
            except OSError:
                continue
            if is_sock and re.fullmatch(r"gamescope-\d+", name) and name not in names:
                names.append(name)
        return names

    def _env(self, display: str) -> dict[str, str]:
        env = proc.env()
        env.update({"XDG_RUNTIME_DIR": self.runtime_dir, "GAMESCOPE_WAYLAND_DISPLAY": display})
        try:
            env["HOME"] = pwd.getpwuid(self.uid).pw_dir
        except KeyError:
            pass
        return env

    def _ctl(self, args: list[str], display: str):
        return proc.run([self.ctl, *args], text=True, timeout=10, env=self._env(display),
                        user=self.uid, group=self.gid)

    def _connect(self) -> str:
        """Find the display name gamescopectl can actually talk to."""
        if self.display:
            return self.display
        tried = []
        for name in self._candidates():
            res = self._ctl([], name)  # no command: just prints info if it connects
            if res.returncode == 0:
                self.display = name
                return name
            tried.append(name)
        raise OSError(
            "couldn't reach gamescope's control socket"
            + (f" (tried {', '.join(tried)})" if tried else f" (no gamescope sockets in {self.runtime_dir})")
        )

    def _scan_convars(self) -> set[str]:
        try:
            with open(self.exe, "rb") as f:
                blob = f.read()
        except OSError:
            return set()
        return {c for c in ("hdr_enabled", "hdr_content_driven", SDR_ATOM) if c.encode() in blob}

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
        res = self._ctl([convar, value], self._connect())
        if res.returncode != 0:
            self.display = None  # rediscover next time
            raise OSError(f"gamescopectl {convar} {value}: {(res.stderr or res.stdout).strip() or res.returncode}")

    # --- SDR-in-HDR brightness (X root property on Steam's Xwayland) -------

    def _x_env(self) -> dict[str, str] | None:
        """DISPLAY / XAUTHORITY of the Xwayland Steam runs on."""
        fallback = None
        for comm in glob.glob("/proc/[0-9]*/comm"):
            try:
                if os.stat(comm).st_uid != self.uid:
                    continue
                with open(comm) as f:
                    name = f.read().strip()
            except OSError:
                continue
            env = _environ(int(comm.split("/")[2]))
            if not env.get("DISPLAY") or "GAMESCOPE_WAYLAND_DISPLAY" not in env:
                continue
            x = {"DISPLAY": env["DISPLAY"]}
            if env.get("XAUTHORITY"):
                x["XAUTHORITY"] = env["XAUTHORITY"]
            if name == "steam":
                return x
            fallback = fallback or x
        return fallback

    def sdr_brightness_supported(self) -> bool:
        return SDR_ATOM in self._convars and bool(shutil.which("xprop", path=proc.env()["PATH"]))

    def _xprop(self, args: list[str]):
        x = self._x_env()
        if not x:
            raise OSError("couldn't find Steam's X display in the Game Mode session")
        env = self._env(self.display or "gamescope-0")
        env.update(x)
        return proc.run(["xprop", "-root", *args], text=True, timeout=10, env=env,
                        user=self.uid, group=self.gid)

    def sdr_brightness(self) -> float | None:
        """Current SDR-in-HDR brightness in nits, or None if unset (default)."""
        res = self._xprop([SDR_ATOM])
        m = re.search(r"=\s*(\d+)", res.stdout or "")
        return cardinal_to_nits(int(m.group(1))) if res.returncode == 0 and m else None

    def set_sdr_brightness(self, nits: float | None) -> None:
        """Set it in nits, or None to remove it (gamescope's default)."""
        if nits is None:
            res = self._xprop(["-remove", SDR_ATOM])
        else:
            res = self._xprop(["-f", SDR_ATOM, "32c", "-set", SDR_ATOM, str(nits_to_cardinal(nits))])
        if res.returncode != 0:
            raise OSError(f"xprop {SDR_ATOM}: {(res.stderr or res.stdout).strip() or res.returncode}")

    def apply(self, mode: str) -> None:
        for convar, value in MODES[mode].items():
            if convar in self._convars:
                self._set(convar, value)

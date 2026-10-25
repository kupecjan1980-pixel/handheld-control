"""Lossless Scaling frame generation in Proton games (lsfg-vk).

lsfg-vk is a Vulkan layer that runs Lossless Scaling's frame generation (its
DLL, from the user's own Lossless Scaling on Steam, "lsfg-vk" branch). The
plugin installs the layer into the user's ~/.local (where Proton games see
it) from the project's build server, pinned to a checksum, unless a system
package already has it. It's switched on per game with environment
variables in the launch options (LSFGVK_ENV=1: settings from variables, no
config file), which the panel writes.
"""

import hashlib
import io
import logging
import os
import re
import shutil
import tarfile
import time

from . import updater

log = logging.getLogger("hhctl")

URL = "https://builds.lsfg-vk.dev/lsfg-vk-2.0.0.tar.xz"
SHA256 = "08bdbdf373a111022df87dac7aa87e3b564bb841f961552e3ca85fea12b5aa74"
# Only the layer: both libraries (64- and 32-bit games) and their manifests.
FILES = (
    "lib/liblsfg-vk-layer.so",
    "lib/liblsfg-vk-layer.x86.so",
    "share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json",
    "share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.x86.json",
)
MANIFEST = "VkLayer_LSFGVK_frame_generation.json"
SYSTEM_DIRS = ("/usr/share/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d")
# Where the layer looks for the DLL by itself (relative to home).
DEFAULT_DIRS = (".steam/steam/steamapps/common", ".local/share/Steam/steamapps/common",
                ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common")


def steam_libraries(home: str) -> list[str]:
    """steamapps folders of every Steam library (libraryfolders.vdf)."""
    roots = [f"{home}/.local/share/Steam", f"{home}/.steam/steam",
             f"{home}/.var/app/com.valvesoftware.Steam/.local/share/Steam"]
    out: list[str] = []
    for r in roots:
        cands = [f"{r}/steamapps"]
        try:
            with open(f"{r}/steamapps/libraryfolders.vdf", encoding="utf-8", errors="replace") as f:
                cands += [f"{p}/steamapps" for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
        for c in cands:
            c = os.path.realpath(c.replace("\\\\", "\\"))
            if os.path.isdir(c) and c not in out:
                out.append(c)
    return out


def find_dll(home: str) -> tuple[str | None, bool]:
    """(path, is the lsfg-vk branch's DLL). lsfg-vk.dll comes with the
    'lsfg-vk' branch of Lossless Scaling; Lossless.dll is the regular one."""
    found = None
    for lib in steam_libraries(home):
        for name in ("lsfg-vk.dll", "Lossless.dll"):
            p = f"{lib}/common/Lossless Scaling/{name}"
            if os.path.exists(p):
                if name == "lsfg-vk.dll":
                    return p, True
                found = found or p
    return found, False


class Lsfg:
    def __init__(self, home: str):
        self.home = home
        self.local = os.path.join(home, ".local")
        self._dll: tuple[float, tuple[str | None, bool]] | None = None

    def dll(self) -> tuple[str | None, bool]:
        """find_dll, looked up again at most every 30 s (state is read every 2 s)."""
        now = time.monotonic()
        if not self._dll or now - self._dll[0] > 30:
            self._dll = (now, find_dll(self.home))
        return self._dll[1]

    def system(self) -> bool:
        return any(os.path.exists(f"{d}/{MANIFEST}") for d in SYSTEM_DIRS)

    def ours(self) -> bool:
        return all(os.path.exists(os.path.join(self.local, f)) for f in FILES)

    def installed(self) -> bool:
        return self.system() or self.ours() or os.path.exists(
            f"{self.local}/share/vulkan/implicit_layer.d/{MANIFEST}")

    def install(self) -> None:
        data = updater._get(URL, 16 * 1024 * 1024)
        digest = hashlib.sha256(data).hexdigest()
        if digest != SHA256:
            raise OSError(f"lsfg-vk download doesn't match its checksum ({digest[:12]}…); nothing installed")
        st = os.stat(self.home)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
            members = {m.name.lstrip("./"): m for m in tar.getmembers()}
            missing = [f for f in FILES if f not in members]
            if missing:
                raise OSError(f"lsfg-vk download lacks {', '.join(missing)}; nothing installed")
            for f in FILES:
                dest = os.path.join(self.local, f)
                d = os.path.dirname(dest)
                made = []
                while not os.path.isdir(d):
                    made.append(d)
                    d = os.path.dirname(d)
                for p in reversed(made):
                    os.mkdir(p)
                    os.chown(p, st.st_uid, st.st_gid)
                src = tar.extractfile(members[f])
                tmp = dest + ".hhc.tmp"
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(tmp, 0o755 if f.endswith(".so") else 0o644)
                os.chown(tmp, st.st_uid, st.st_gid)
                os.replace(tmp, dest)
        log.info("lsfg: installed lsfg-vk 2.0.0 in %s", self.local)

    def remove(self) -> None:
        for f in FILES:
            try:
                os.remove(os.path.join(self.local, f))
            except FileNotFoundError:
                pass

    def vars(self, multiplier: int, dll: str | None) -> str:
        """Launch-option prefix for one game. Performance mode and a lower flow
        scale: handheld GPUs have little to spare for the generated frames."""
        out = f"LSFGVK_ENV=1 LSFGVK_MULTIPLIER={multiplier} LSFGVK_PERFORMANCE_MODE=1 LSFGVK_FLOW_SCALE=0.75"
        if dll and not any(dll.startswith(os.path.realpath(f"{self.home}/{d}") + "/") for d in DEFAULT_DIRS):
            out += f' LSFGVK_DLL_PATH="{dll}"'  # e.g. on an SD card: not where it looks by itself
        return out

    def state(self) -> dict:
        dll, branch = self.dll()
        return {
            "installed": self.installed(),
            "ours": self.ours() and not self.system(),
            "dll": dll,
            "branch": branch,
            "vars": {m: self.vars(m, dll) for m in (2, 3)} if dll else {},
        }

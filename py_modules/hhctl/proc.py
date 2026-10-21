"""Run system tools with the host's environment, not Decky's.

Decky's plugin loader is a PyInstaller bundle: it points LD_LIBRARY_PATH at
its own bundled libraries (saving the original as LD_LIBRARY_PATH_ORIG) and
may set PYTHONHOME/PYTHONPATH. Child processes such as limine-update,
modprobe, rpm-ostree or the system python3 can then load the wrong libraries
and fail, sometimes silently. Every external command goes through here.
"""

import os
import subprocess

SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def env() -> dict[str, str]:
    e = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        orig = e.pop(f"{var}_ORIG", None)
        if orig:
            e[var] = orig
        else:
            e.pop(var, None)
    for var in ("PYTHONHOME", "PYTHONPATH"):
        e.pop(var, None)
    path = e.get("PATH", "")
    e["PATH"] = ":".join(p for p in (path + ":" + SYSTEM_PATH).split(":") if p)
    return e


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("env", env())
    return subprocess.run(cmd, **kw)


def popen(cmd: list[str], **kw) -> subprocess.Popen:
    kw.setdefault("env", env())
    return subprocess.Popen(cmd, **kw)

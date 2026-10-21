"""Brightness diagnostics, runnable from the panel (no SSH needed).

Steps the backlight 25% -> 75% -> original, sampling `brightness` and
`actual_brightness` after each write to see whether the value sticks, gets
reverted by something else, or is never applied by the driver. Collects the
context needed to explain it and writes a report to the user's home.
"""

import glob
import os
import platform
import subprocess
import time

from . import sysfs

STEPS = (25, 75)
SAMPLES = (0.1, 0.5, 1.0, 2.0)  # seconds after each write

# Never toggle PSR at runtime (i915_edp_psr_debug): on the ONEXPLAYER 3's xe
# driver it blanked the panel until a second reboot. PSR status below is
# read-only.


def _proc_args(names: tuple[str, ...]) -> list[str]:
    out = []
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm) as f:
                if f.read().strip() not in names:
                    continue
            with open(comm.replace("comm", "cmdline"), "rb") as f:
                out.append(f.read().replace(b"\0", b" ").decode(errors="replace").strip())
        except OSError:
            continue
    return out


def context() -> list[str]:
    lines = [f"kernel: {platform.release()}", f"cmdline: {sysfs.read('/proc/cmdline')}"]
    gs = _proc_args(("gamescope", "gamescope-wl"))
    lines.append(f"game mode (gamescope): {'yes' if gs else 'no'}")
    lines += [f"  gamescope: {a}" for a in gs]
    for b in sorted(glob.glob("/sys/class/backlight/*")):
        vals = {k: sysfs.read(f"{b}/{k}") for k in ("type", "brightness", "actual_brightness", "max_brightness", "bl_power")}
        lines.append(f"backlight {os.path.basename(b)}: " + " ".join(f"{k}={v}" for k, v in vals.items()))
    for c in sorted(glob.glob("/sys/class/drm/card*-eDP-*")):
        vals = {k: sysfs.read(f"{c}/{k}") for k in ("status", "enabled", "dpms")}
        lines.append(f"connector {os.path.basename(c)}: " + " ".join(f"{k}={v}" for k, v in vals.items()))
    for drv in ("xe", "i915", "amdgpu"):
        params = sorted(
            glob.glob(f"/sys/module/{drv}/parameters/*backlight*")
            + glob.glob(f"/sys/module/{drv}/parameters/enable_psr*")
            + glob.glob(f"/sys/module/{drv}/parameters/enable_panel_replay")
            + glob.glob(f"/sys/module/{drv}/parameters/enable_dc")
        )
        for p in params:
            lines.append(f"{drv}.{os.path.basename(p)} = {sysfs.read(p)}")
    for status in sorted(glob.glob("/sys/kernel/debug/dri/*/i915_edp_psr_status")):
        text = (sysfs.read(status) or "").splitlines()[:12]
        lines.append(f"psr status ({status}):")
        lines += [f"  {line}" for line in text]
    try:
        log = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=10).stdout
        hits = [l for l in log.splitlines() if any(k in l.lower() for k in ("backlight", "dpcd", "psr", "panel replay"))]
        lines.append(f"dmesg backlight/psr lines: {len(hits)}")
        lines += [f"  {l}" for l in hits[-20:]]
    except (OSError, subprocess.SubprocessError):
        pass
    return lines


def brightness_test(bl) -> tuple[str, list[str]]:
    """Return (verdict, report lines). `bl` is a backlight.Backlight."""
    path = bl.path
    orig = sysfs.read_int(f"{path}/brightness")
    lines = [f"device: {bl.name} max={bl.max} start={orig}"]
    reverted, unapplied, errors = [], [], []
    targets = [round(p * bl.max / 100) for p in STEPS] + [orig]
    for target in targets:
        start = time.monotonic()
        try:
            sysfs.write(f"{path}/brightness", target)
        except OSError as e:
            errors.append(str(e))
            lines.append(f"write {target}: FAILED {e}")
            continue
        for t in SAMPLES:
            time.sleep(max(0, start + t - time.monotonic()))
            b = sysfs.read_int(f"{path}/brightness")
            a = sysfs.read_int(f"{path}/actual_brightness")
            lines.append(f"write {target}: +{t:.1f}s brightness={b} actual={a}")
            if b != target:
                reverted.append((target, b))
            elif a is not None and a != target:
                unapplied.append((target, a))
    if errors:
        verdict = f"Writing the backlight failed: {errors[0]}"
    elif reverted:
        t, b = reverted[0]
        verdict = (f"Something changed it back: wrote {t}, it became {b}. "
                   "Gamescope or another service is overriding the backlight.")
    elif unapplied:
        t, a = unapplied[0]
        verdict = (f"The driver didn't apply it: wrote {t}, the hardware reports {a}.")
    else:
        verdict = ("The values stuck. If the screen didn't change, the panel ignores "
                   "the backlight in this mode and brightness has to go through gamescope.")
    return verdict, lines


def write_report(home: str | None, sections: list[tuple[str, list[str]]]) -> str | None:
    if not home or not os.path.isdir(home):
        return None
    path = os.path.join(home, "handheld-control-brightness.txt")
    with open(path, "w") as f:
        f.write(f"Handheld Control brightness report, {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for title, lines in sections:
            f.write(f"\n[{title}]\n")
            f.writelines(f"{line}\n" for line in lines)
    try:
        st = os.stat(home)
        os.chown(path, st.st_uid, st.st_gid)  # owned by the user, not root
    except OSError:
        pass
    return path

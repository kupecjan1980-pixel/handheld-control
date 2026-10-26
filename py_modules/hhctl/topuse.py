"""What's using the CPU and GPU: the busiest processes over a 2 s look.

Run on demand from the panel (a button), not every tick: reading GPU fdinfo
makes the driver collect its counters, and on Intel (xe) that wakes the GPU.

CPU: utime + stime from /proc/<pid>/stat, as a share of all cores.
GPU: per-client engine time from the DRM fdinfo of each open GPU file:
- amdgpu / i915: drm-engine-<name> in ns, as a share of the elapsed time;
- xe: drm-cycles-<class> against drm-total-cycles-<class> (GPU clock cycles).
The busiest engine of each process counts.
"""

import glob
import os
import time

WAIT = 2.0


def _comm(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return pid


def _cpu_ticks() -> dict[str, int]:
    out = {}
    for path in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(path) as f:
                data = f.read()
        except OSError:
            continue
        fields = data[data.rfind(")") + 2:].split()
        try:
            out[path.split("/")[2]] = int(fields[11]) + int(fields[12])  # utime, stime
        except (IndexError, ValueError):
            continue
    return out


def _gpu_counters() -> dict[tuple[str, str], dict[str, int]]:
    """(pid, client id) -> {counter: value} for every open GPU file."""
    out: dict[tuple[str, str], dict[str, int]] = {}
    for fd in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            if not os.readlink(fd).startswith("/dev/dri/"):
                continue
            with open(fd.replace("/fd/", "/fdinfo/")) as f:
                text = f.read()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
        client = fields.get("drm-client-id", "").strip()
        if not client:
            continue
        counters = {}
        for k, v in fields.items():
            if k.startswith(("drm-engine-", "drm-cycles-", "drm-total-cycles-")) and not k.startswith("drm-engine-capacity"):
                try:
                    counters[k] = int(v.split()[0])
                except (IndexError, ValueError):
                    pass
        if counters:
            out[(fd.split("/")[2], client)] = counters
    return out


def _gpu_share(a: dict[str, int], b: dict[str, int], dt: float) -> float:
    best = 0.0
    for k, v in b.items():
        if k.startswith("drm-engine-") and k in a:
            best = max(best, (v - a[k]) / (dt * 1e9))
        elif k.startswith("drm-cycles-"):
            cls = k[len("drm-cycles-"):]
            tot = f"drm-total-cycles-{cls}"
            if k in a and tot in a and tot in b and b[tot] > a[tot]:
                best = max(best, (v - a[k]) / (b[tot] - a[tot]))
    return 100 * min(1.0, max(0.0, best))


def sample() -> dict:
    hz = os.sysconf("SC_CLK_TCK")
    ncpu = os.cpu_count() or 1
    c0, g0, t0 = _cpu_ticks(), _gpu_counters(), time.monotonic()
    time.sleep(WAIT)
    c1, g1, t1 = _cpu_ticks(), _gpu_counters(), time.monotonic()
    dt = t1 - t0
    cpu: dict[str, float] = {}
    for pid, v in c1.items():
        if pid in c0 and v > c0[pid]:
            name = _comm(pid)
            cpu[name] = cpu.get(name, 0.0) + 100 * (v - c0[pid]) / hz / dt / ncpu
    gpu: dict[str, float] = {}
    for key, v in g1.items():
        if key in g0:
            share = _gpu_share(g0[key], v, dt)
            if share >= 0.5:
                name = _comm(key[0])
                gpu[name] = max(gpu.get(name, 0.0), share)

    def top(d: dict[str, float]) -> list[list]:
        return [[n, round(p, 1)] for n, p in sorted(d.items(), key=lambda x: -x[1])[:6] if p >= 0.5]

    return {"cpu": top(cpu), "gpu": top(gpu), "at": time.strftime("%H:%M:%S")}

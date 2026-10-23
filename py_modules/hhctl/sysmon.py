"""System monitor for the desktop app's dashboard (in the style of Mission Center).

Everything comes from /proc and /sys; rates (CPU %, disk and network
speeds, per-process CPU) are the change since the previous sample, so the
first sample after start reports zero rates.
"""

import glob
import os
import platform
import pwd
import time

from . import proc, sysfs

FS_TYPES = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "zfs", "bcachefs"}
SKIP_BLOCK = ("loop", "ram", "zram", "sr", "fd", "dm-", "md")
IP_EVERY = 10.0  # seconds between `ip addr` calls
PAGE = os.sysconf("SC_PAGE_SIZE")
CLK_TCK = os.sysconf("SC_CLK_TCK")


def _read_lines(path: str) -> list[str]:
    try:
        with open(path) as f:
            return f.read().splitlines()
    except OSError:
        return []


def cpu_times() -> dict[str, tuple[int, int]]:
    """cpu name -> (busy jiffies, total jiffies) from /proc/stat."""
    out = {}
    for line in _read_lines("/proc/stat"):
        if not line.startswith("cpu"):
            continue
        name, *vals = line.split()
        v = [int(x) for x in vals[:8]]
        idle = v[3] + v[4]  # idle + iowait
        out[name] = (sum(v) - idle, sum(v))
    return out


def meminfo() -> dict[str, int]:
    out = {}
    for line in _read_lines("/proc/meminfo"):
        k, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[k] = int(parts[0]) * 1024
    return out


def diskstats() -> dict[str, tuple[int, int, int]]:
    """block device -> (bytes read, bytes written, io ms)."""
    out = {}
    for line in _read_lines("/proc/diskstats"):
        f = line.split()
        if len(f) < 13:
            continue
        name = f[2]
        out[name] = (int(f[5]) * 512, int(f[9]) * 512, int(f[12]))
    return out


def netdev() -> dict[str, tuple[int, int]]:
    """interface -> (bytes received, bytes sent)."""
    out = {}
    for line in _read_lines("/proc/net/dev")[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            out[name.strip()] = (int(f[0]), int(f[8]))
    return out


def _user(uid: int, cache: dict[int, str] = {}) -> str:
    if uid not in cache:
        try:
            cache[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            cache[uid] = str(uid)
    return cache[uid]


def process_times() -> dict[int, tuple[str, int, int, int, str]]:
    """pid -> (name, cpu jiffies, rss bytes, threads, user)."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                stat = f.read()
            uid = os.stat(f"/proc/{d}").st_uid
        except OSError:
            continue
        # The name is in parentheses and may itself contain spaces or ')'.
        lp, rp = stat.find("("), stat.rfind(")")
        name = stat[lp + 1:rp]
        f = stat[rp + 2:].split()
        try:
            out[int(d)] = (name, int(f[11]) + int(f[12]), int(f[21]) * PAGE, int(f[17]), _user(uid))
        except (IndexError, ValueError):
            continue
    return out


def _block_devices() -> list[str]:
    devs = []
    for p in sorted(glob.glob("/sys/block/*")):
        name = os.path.basename(p)
        if name.startswith(SKIP_BLOCK) or (sysfs.read_int(f"{p}/size") or 0) == 0:
            continue
        devs.append(name)
    return devs


def filesystems() -> list[dict]:
    seen, out = set(), []
    for line in _read_lines("/proc/mounts"):
        f = line.split()
        if len(f) < 3 or f[2] not in FS_TYPES or f[0] in seen:
            continue
        seen.add(f[0])
        try:
            st = os.statvfs(f[1])
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        out.append({"mount": f[1], "device": f[0], "type": f[2], "total": total, "used": total - free})
    return out


def sensors() -> list[dict]:
    """Every hwmon temperature and fan, with its chip and label."""
    out = []
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip = sysfs.read(f"{hw}/name") or os.path.basename(hw)
        for t in sorted(glob.glob(f"{hw}/temp*_input")):
            v = sysfs.read_int(t)
            if v is None:
                continue
            label = sysfs.read(t.replace("_input", "_label")) or os.path.basename(t).split("_")[0]
            out.append({"chip": chip, "label": label, "kind": "temp", "value": round(v / 1000, 1)})
        for fan in sorted(glob.glob(f"{hw}/fan*_input")):
            v = sysfs.read_int(fan)
            if v is None:
                continue
            label = sysfs.read(fan.replace("_input", "_label")) or os.path.basename(fan).split("_")[0]
            out.append({"chip": chip, "label": label, "kind": "fan", "value": v})
    return out


def cpu_info() -> dict:
    model, cores = None, set()
    for line in _read_lines("/proc/cpuinfo"):
        k, _, v = line.partition(":")
        k = k.strip()
        if k == "model name" and not model:
            model = v.strip()
        elif k == "core id":
            cores.add(v.strip())
    return {"model": model, "cores": len(cores) or None, "threads": os.cpu_count()}


def gpu_stats(card: str | None) -> dict | None:
    """amdgpu / i915 / xe: usage, VRAM, clock, temperature, power where the driver has them."""
    if not card:
        return None
    dev = f"{card}/device"
    out: dict = {"busy": sysfs.read_int(f"{dev}/gpu_busy_percent")}
    out["vram_used"] = sysfs.read_int(f"{dev}/mem_info_vram_used")
    out["vram_total"] = sysfs.read_int(f"{dev}/mem_info_vram_total")
    out["gtt_used"] = sysfs.read_int(f"{dev}/mem_info_gtt_used")
    sclk = sysfs.read(f"{dev}/pp_dpm_sclk") or ""
    cur = next((l for l in sclk.splitlines() if l.rstrip().endswith("*")), None)
    out["clock_mhz"] = int("".join(c for c in cur.split(":")[1] if c.isdigit())) if cur else None
    hw = next(iter(glob.glob(f"{dev}/hwmon/hwmon*")), None)
    if hw:
        t = sysfs.read_int(f"{hw}/temp1_input")
        out["temp"] = round(t / 1000, 1) if t is not None else None
        p = sysfs.read_int(f"{hw}/power1_average") or sysfs.read_int(f"{hw}/power1_input")
        out["power_w"] = round(p / 1e6, 1) if p else None
        f = sysfs.read_int(f"{hw}/freq1_input")
        if out["clock_mhz"] is None and f:
            out["clock_mhz"] = f // 1_000_000
    out["driver"] = os.path.basename(os.path.realpath(f"{dev}/driver")) if os.path.exists(f"{dev}/driver") else None
    out["name"] = sysfs.read(f"{dev}/product_name")
    return out


class Monitor:
    def __init__(self):
        self.prev: dict | None = None
        self.ips: dict[str, list[str]] = {}
        self.ips_at = 0.0
        self.cpu = cpu_info()
        self.card = next(
            (c for c in sorted(glob.glob("/sys/class/drm/card[0-9]")) if os.path.exists(f"{c}/device/vendor")), None
        )

    def _ip_addrs(self) -> dict[str, list[str]]:
        if time.monotonic() - self.ips_at < IP_EVERY:
            return self.ips
        self.ips_at = time.monotonic()
        try:
            import json

            res = proc.run(["ip", "-j", "addr"], text=True, timeout=3)
            data = json.loads(res.stdout or "[]")
            self.ips = {
                i["ifname"]: [a["local"] for a in i.get("addr_info", []) if a.get("scope") == "global"] for i in data
            }
        except (OSError, ValueError, KeyError):
            pass
        return self.ips

    def sample(self) -> dict:
        now = time.monotonic()
        cur = {
            "t": now,
            "cpu": cpu_times(),
            "disk": diskstats(),
            "net": netdev(),
            "procs": process_times(),
        }
        prev, self.prev = self.prev, cur
        dt = (now - prev["t"]) if prev else 0.0

        def pct(name: str) -> float:
            if not prev or name not in prev["cpu"]:
                return 0.0
            b0, t0 = prev["cpu"][name]
            b1, t1 = cur["cpu"][name]
            return round(100 * (b1 - b0) / (t1 - t0), 1) if t1 > t0 else 0.0

        cores = sorted((n for n in cur["cpu"] if n != "cpu"), key=lambda n: int(n[3:]))
        freqs = [sysfs.read_int(f"/sys/devices/system/cpu/{n}/cpufreq/scaling_cur_freq") for n in cores]
        load = (sysfs.read("/proc/loadavg") or "0 0 0").split()[:3]
        uptime = float((sysfs.read("/proc/uptime") or "0").split()[0])

        disks = []
        for name in _block_devices():
            d1 = cur["disk"].get(name)
            d0 = prev["disk"].get(name) if prev else None
            rate = lambda i: round((d1[i] - d0[i]) / dt) if (d0 and d1 and dt) else 0  # noqa: E731
            disks.append({
                "name": name,
                "model": sysfs.read(f"/sys/block/{name}/device/model"),
                "size": (sysfs.read_int(f"/sys/block/{name}/size") or 0) * 512,
                "rotational": sysfs.read(f"/sys/block/{name}/queue/rotational") == "1",
                "read_bps": rate(0),
                "write_bps": rate(1),
                "busy": min(100.0, round((d1[2] - d0[2]) / (dt * 10), 1)) if (d0 and d1 and dt) else 0.0,
            })

        ips = self._ip_addrs()
        nets = []
        for name, (rx, tx) in cur["net"].items():
            if name == "lo":
                continue
            p = prev["net"].get(name) if prev else None
            nets.append({
                "name": name,
                "rx_bps": round((rx - p[0]) / dt) if (p and dt) else 0,
                "tx_bps": round((tx - p[1]) / dt) if (p and dt) else 0,
                "rx_total": rx,
                "tx_total": tx,
                "up": sysfs.read(f"/sys/class/net/{name}/operstate") == "up",
                "ips": ips.get(name, []),
            })

        total_jiffies = (cur["cpu"]["cpu"][1] - prev["cpu"]["cpu"][1]) if prev else 0
        procs = []
        threads = 0
        for pid, (name, jif, rss, nthreads, user) in cur["procs"].items():
            threads += nthreads
            p = prev["procs"].get(pid) if prev else None
            # Share of the whole machine (all cores), like Mission Center's default.
            cpu = 100 * (jif - p[1]) / total_jiffies if (p and total_jiffies) else 0.0
            procs.append({"pid": pid, "name": name, "user": user, "cpu": round(cpu, 1), "rss": rss, "threads": nthreads})
        procs.sort(key=lambda p: (p["cpu"], p["rss"]), reverse=True)

        mem = meminfo()
        return {
            "cpu": {
                **self.cpu,
                "usage": pct("cpu"),
                "per_core": [pct(n) for n in cores],
                "freq_mhz": [f // 1000 if f else None for f in freqs],
                "load": [float(x) for x in load],
                "uptime_s": round(uptime),
                "processes": len(cur["procs"]),
                "threads_total": threads,
            },
            "memory": {
                "total": mem.get("MemTotal", 0),
                "available": mem.get("MemAvailable", 0),
                "used": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0),
                "cached": mem.get("Cached", 0) + mem.get("Buffers", 0),
                "swap_total": mem.get("SwapTotal", 0),
                "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
            },
            "gpu": gpu_stats(self.card),
            "disks": disks,
            "filesystems": filesystems(),
            "network": nets,
            "sensors": sensors(),
            "processes": procs,
            "system": {"kernel": platform.release(), "hostname": platform.node()},
        }

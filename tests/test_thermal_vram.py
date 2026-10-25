"""Temperature limit (AMD Tctl through the SMU / ryzenadj, Intel TCC offset
through sysfs or MSR 0x1A2) and dedicated VRAM (amdgpu uma/carveout)."""
import os, sys, tempfile, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import gpu, proc, thermal

tmp = tempfile.mkdtemp()

# --- AMD: SMU message 0x19 (0x1F on Raven), re-sent once a minute, stock back on restore
sent = []
smu = types.SimpleNamespace(send=lambda msg, args: sent.append((msg, args[0])))
t = thermal.find("amd", "Phoenix / Hawk Point", smu)
assert t.range == (60, 100) and t.msg == 0x19
t.apply(85); t.apply(85)
assert sent == [(0x19, 85)], "not re-sent within a minute"
t.apply(85, force=True); t.apply(80)
assert sent[1:] == [(0x19, 85), (0x19, 80)]
t.last = (80, t.last[1] - 61); t.apply(80)
assert sent[-1] == (0x19, 80) and len(sent) == 4, "re-asserted after a minute"
t.restore(); t.restore()
assert sent[-1] == (0x19, 100) and len(sent) == 5, "stock once"
r = thermal.find("amd", "Raven / Picasso / Dali", smu)
assert r.msg == 0x1F and r.range == (60, 95)
c = thermal.find("amd", "Cezanne / Barcelo", smu)
assert c.range == (60, 95)
assert thermal.find("amd", "", None, None) is None
# ryzenadj when there's no direct SMU access
calls = []
proc.run = lambda cmd, **kw: (calls.append(cmd), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
a = thermal.find("amd", "", None, "/usr/bin/ryzenadj")
a.apply(90)
assert calls == [["/usr/bin/ryzenadj", "--tctl-temp=90"]] and a.range == (60, 100)

# --- Intel: sysfs offset file
p = f"{tmp}/tcc_offset_degree_celsius"; open(p, "w").write("5\n")
i = thermal.IntelTcc(105, p, [])
assert i.range == (42, 105)
i.apply(90)
assert open(p).read().strip() == "15" and i.orig == 5
i.apply(200); assert open(p).read().strip() == "0"
i.apply(10); assert open(p).read().strip() == "63"
i.restore(); assert open(p).read().strip() == "5" and i.orig is None

# --- Intel: MSR 0x1A2 on every CPU (a file stands in for /dev/cpu/N/msr)
msrs = []
for n in range(2):
    m = f"{tmp}/msr{n}"
    with open(m, "wb") as f:
        f.truncate(0x1000)
        f.seek(thermal.TEMPERATURE_TARGET)
        f.write((100 << 16 | 3 << 24 | 0x80000000).to_bytes(8, "little"))
    msrs.append(m)
j = thermal.IntelTcc(100, None, msrs)
assert j.offset() == 3
j.apply(80)
for m in msrs:
    v = thermal.IntelTcc._rd(m, thermal.TEMPERATURE_TARGET)
    assert v >> 24 & 0x3F == 20 and v >> 16 & 0xFF == 100 and v & 0x80000000, hex(v)
j.restore()
assert all(thermal.IntelTcc._rd(m, thermal.TEMPERATURE_TARGET) >> 24 & 0x3F == 3 for m in msrs)

# --- VRAM
dev = f"{tmp}/card"; os.makedirs(f"{dev}/uma")
open(f"{dev}/uma/carveout_options", "w").write(
    "0: Minimum (512 MB)\n1:  (1 GB)\n2:  (2 GB)\n3:  (4 GB)\n7: Medium (16 GB)\n9: High (32 GB)\n")
open(f"{dev}/uma/carveout", "w").write("3\n")
u = gpu.UmaCarveout(dev, "amdgpu")
assert u.available() == (True, None) and u.boot == 3
opts = u.options()
assert opts[0] == {"index": 0, "label": "512 MB (Minimum)"} and opts[1] == {"index": 1, "label": "1 GB"}
assert [o["index"] for o in opts] == [0, 1, 2, 3, 7, 9]
u.set(7)
assert u.current() == 7 and u.boot == 3
try:
    u.set(5); raise SystemExit("unknown option accepted")
except OSError:
    pass
assert not gpu.UmaCarveout(f"{tmp}/none", "amdgpu").available()[0]
assert gpu.UmaCarveout(dev, "xe").available() == (False, "AMD graphics only")
# --- suggestion, BIOS path, GPU memory limit (ttm.pages_limit)
assert [gpu.suggested_vram_gb(g) for g in (8, 16, 32, 64, 128)] == [1, 4, 8, 16, 32]
assert "Share Memory" in gpu.bios_path("ASRock") and "Share Memory" not in gpu.bios_path("Default string")
from hhctl import sysfs
real_read = sysfs.read
sysfs.read = lambda path, default=None: "MemTotal:       31457280 kB\n" if path == "/proc/meminfo" else real_read(path, default)
assert gpu.installed_ram_gb(2048) == 32 and gpu.installed_ram_gb() == 32
assert gpu.GpuMemLimit.ram_pages() == 31457280 // 4
sets = []
boot = types.SimpleNamespace(name="Limine", set=lambda add, rm: sets.append((add, rm)))
lim = gpu.GpuMemLimit(boot)
lim.PARAM = f"{tmp}/pages_limit"; open(lim.PARAM, "w").write(f"{31457280 // 8}\n")
assert lim.available() and lim.current_pct() == 50
lim.set(75)
n = 31457280 // 4 * 75 // 100
assert sets[-1] == ([f"ttm.pages_limit={n}", f"ttm.page_pool_size={n}"], set())
assert lim.configured_pct(["quiet", f"ttm.pages_limit={n}"]) == 75 and lim.configured_pct(["quiet"]) == 50
lim.set(50)
assert sets[-1] == ([], {"ttm.pages_limit", "ttm.page_pool_size"})
try:
    lim.set(99); raise SystemExit("odd percentage accepted")
except OSError:
    pass
assert not gpu.GpuMemLimit(None).available()
sysfs.read = real_read
print("all thermal and vram tests passed")

"""NPU upscaling row: the NPU found by PCI id or by a loaded accel driver; never usable (no Linux software)."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import npu

def pci(root, slot, vendor, device, driver=None):
    d = f"{root}/bus/pci/devices/{slot}"; os.makedirs(d)
    open(f"{d}/vendor", "w").write(vendor + "\n"); open(f"{d}/device", "w").write(device + "\n")
    if driver:
        os.makedirs(f"{root}/drv/{driver}", exist_ok=True); os.symlink(f"{root}/drv/{driver}", f"{d}/driver")
    return d

none = tempfile.mkdtemp(); pci(none, "0000:04:00.0", "0x1002", "0x15e7", "amdgpu")  # the Ren7000: Vega, no NPU
s = npu.state(none)
assert s["present"] is False and s["usable"] is False and s["enabled"] is False and "no NPU" in s["reason"], s

phx = tempfile.mkdtemp(); pci(phx, "0000:c5:00.1", "0x1022", "0x1502")  # 8700G / 7840U, no driver loaded
s = npu.state(phx)
assert s["present"] and s["name"].startswith("AMD XDNA 1") and s["driver"] is None and not s["usable"], s
assert "nothing on Linux" in s["reason"]

stx = tempfile.mkdtemp(); pci(stx, "0000:c6:00.1", "0x1022", "0x17F0", "amdxdna")
s = npu.state(stx)
assert s["name"].startswith("AMD XDNA 2") and s["driver"] == "amdxdna" and not s["usable"], s

new = tempfile.mkdtemp(); d = pci(new, "0000:00:0b.0", "0x8086", "0xffff", "intel_vpu")  # an id not listed
os.makedirs(f"{new}/class/accel/accel0"); os.symlink(d, f"{new}/class/accel/accel0/device")
s = npu.state(new)
assert s["present"] and s["name"] == "Intel AI Boost" and s["driver"] == "intel_vpu", s
print("ok")

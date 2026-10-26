import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import systweak, rebar
tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
systweak.THP, systweak.SWAPPINESS = f"{tmp}/thp", f"{tmp}/swap"
systweak.USB_AUTOSUSPEND, systweak.USB_DEVICES, systweak.NET = f"{tmp}/usbas", f"{tmp}/usb", f"{tmp}/net"
w(systweak.THP, "[always] madvise never"); w(systweak.SWAPPINESS, 60)
w(systweak.USB_AUTOSUSPEND, 2); w(f"{tmp}/usb/1-1/power/control", "auto")
w(f"{tmp}/usb/1-2/power/control", "auto"); w(f"{tmp}/usb/1-2/1-2:1.0/bInterfaceClass", "08")  # a card reader
t = systweak.SysTweaks()
st = t.state()
assert st["thp"]["current"] == "always" and st["swappiness"]["current"] == 60 and "wifi_ps_off" not in st
t.apply({"thp": "madvise", "swappiness": 180, "usb_awake": True})
r = lambda p: open(p).read().strip()
assert r(systweak.THP) == "madvise" and r(systweak.SWAPPINESS) == "180"
assert r(systweak.USB_AUTOSUSPEND) == "-1" and r(f"{tmp}/usb/1-1/power/control") == "on"
assert r(f"{tmp}/usb/1-2/power/control") == "auto", "storage is left alone"
t.apply({"thp": None, "swappiness": 180, "usb_awake": True})
assert r(systweak.THP) == "always", "set back to the system's: the old value returns"
t.restore()
assert r(systweak.SWAPPINESS) == "60"
assert r(systweak.USB_AUTOSUSPEND) == "2" and r(f"{tmp}/usb/1-1/power/control") == "auto"
# Resizable BAR
dev = f"{tmp}/gpu"
w(f"{dev}/resource", "0x00000000d0000000 0x00000000dfffffff 0x000000000014220c\n0x000000000000e000 0x000000000000e0ff 0x0000000000040101")
w(f"{dev}/mem_info_vram_vendor", "samsung"); w(f"{dev}/mem_info_vram_total", 8 << 30)
assert rebar.status(dev, "amdgpu")["state"] == "off", "256 MB window"
w(f"{dev}/mem_info_vis_vram_total", 8 << 30)
assert rebar.status(dev, "amdgpu")["state"] == "on"
os.remove(f"{dev}/mem_info_vram_vendor")
assert rebar.status(dev, "amdgpu")["state"] == "n/a", "integrated"
shutil.rmtree(tmp)
print("all systweak tests passed")

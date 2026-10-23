import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import gpu
tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
dev = f"{tmp}/0000:c4:00.0"
gpu.DEBUG_DRI = f"{tmp}/dri"
gpu.os.path.ismount = lambda p: True
for k, v in zip(("major", "minor", "revision"), (9, 3, 0)):
    w(f"{dev}/ip_discovery/die/0/GC/0/{k}", v)
assert gpu.gfx_pipes(dev) == {"gc": "9.3.0", "state": "unsupported"}, "Vega"
for k, v in zip(("major", "minor", "revision"), (11, 5, 0)):
    w(f"{dev}/ip_discovery/die/0/GC/0/{k}", v)
assert gpu.gfx_pipes(dev)["state"] == "unknown", "no debugfs"
w(f"{gpu.DEBUG_DRI}/1/name", "amdgpu dev=0000:03:00.0 unique=0000:03:00.0")  # another GPU
w(f"{gpu.DEBUG_DRI}/1/amdgpu_ring_gfx_0.1.0", "")
w(f"{gpu.DEBUG_DRI}/0/name", "amdgpu dev=0000:c4:00.0 unique=0000:c4:00.0")
w(f"{gpu.DEBUG_DRI}/0/amdgpu_ring_gfx_0.0.0", "")
assert gpu.gfx_pipes(dev) == {"gc": "11.5.0", "state": "off"}
w(f"{gpu.DEBUG_DRI}/0/amdgpu_ring_gfx_0.1.0", "")
assert gpu.gfx_pipes(dev)["state"] == "on"
shutil.rmtree(tmp)
print("all pipe tests passed")

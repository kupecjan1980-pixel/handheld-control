"""lsfg-vk: layer installed into ~/.local from a checksum-pinned download;
the Lossless Scaling DLL found in any Steam library; launch-option variables."""
import os, sys, tempfile, tarfile, io, hashlib
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import lsfg

tmp = tempfile.mkdtemp()
home = f"{tmp}/home"; os.makedirs(home)
# a stand-in tarball with the release's layout
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:xz") as t:
    for f in lsfg.FILES + ("bin/lsfg-vk-ui",):
        data = f.encode(); ti = tarfile.TarInfo("./" + f); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
blob = buf.getvalue()
lsfg.updater._get = lambda url, limit: blob
lsfg.SYSTEM_DIRS = (f"{tmp}/none",)
l = lsfg.Lsfg(home)
assert not l.installed()
try:
    l.install(); raise SystemExit("checksum must be checked")
except OSError as e:
    assert "checksum" in str(e)
assert not l.installed()
lsfg.SHA256 = hashlib.sha256(blob).hexdigest()
l.install()
assert l.installed() and l.ours()
assert not os.path.exists(f"{home}/.local/bin/lsfg-vk-ui"), "only the layer"
assert open(f"{home}/.local/lib/liblsfg-vk-layer.so").read() == "lib/liblsfg-vk-layer.so"
assert oct(os.stat(f"{home}/.local/lib/liblsfg-vk-layer.so").st_mode & 0o777) == "0o755"
l.remove(); assert not l.installed()

# the DLL: Lossless.dll (regular branch) in the home library, lsfg-vk.dll on an SD card library
steam = f"{home}/.local/share/Steam/steamapps"
os.makedirs(f"{steam}/common/Lossless Scaling")
open(f"{steam}/common/Lossless Scaling/Lossless.dll", "w").write("x")
assert lsfg.find_dll(home) == (f"{steam}/common/Lossless Scaling/Lossless.dll", False)
assert "LSFGVK_DLL_PATH" not in l.vars(2, lsfg.find_dll(home)[0]), "default place: found by itself"
sd = f"{tmp}/run/media/deck/SD"
os.makedirs(f"{sd}/steamapps/common/Lossless Scaling")
open(f"{sd}/steamapps/common/Lossless Scaling/lsfg-vk.dll", "w").write("x")
open(f"{steam}/libraryfolders.vdf", "w").write(f'"libraryfolders"\n{{\n "0" {{ "path" "{home}/.local/share/Steam" }}\n "1" {{ "path" "{sd}" }}\n}}\n')
dll, branch = lsfg.find_dll(home)
assert dll == f"{sd}/steamapps/common/Lossless Scaling/lsfg-vk.dll" and branch
v = l.vars(3, dll)
assert v.startswith("LSFGVK_ENV=1 LSFGVK_MULTIPLIER=3 LSFGVK_PERFORMANCE_MODE=1 LSFGVK_FLOW_SCALE=0.75")
assert f'LSFGVK_DLL_PATH="{dll}"' in v, v
st = l.state()
assert st["branch"] and set(st["vars"]) == {2, 3}
print("all lsfg tests passed")

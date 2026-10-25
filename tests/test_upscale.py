"""Upscaling: FSR 4 only on RDNA 3+, XeSS everywhere; variables in environment.d."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import upscale

tmp = tempfile.mkdtemp()
gpu = f"{tmp}/gpu"; os.makedirs(f"{gpu}/ip_discovery/die/0/GC/0")
open(f"{gpu}/ip_discovery/die/0/GC/0/major", "w").write("11\n")
home = f"{tmp}/home"; os.makedirs(f"{home}/.local/share/Steam/compatibilitytools.d/proton-cachyos-11.0-x86_64")
os.makedirs(f"{home}/.local/share/Steam/compatibilitytools.d/Proton-Experimental-ish")
u = upscale.Upscale(home, "amdgpu", gpu)
assert u.options == ["fsr4", "xess"] and u.rdna3
assert upscale.Upscale(home, "xe", None).options == ["xess"]
open(f"{gpu}/ip_discovery/die/0/GC/0/major", "w").write("9\n")
assert upscale.Upscale(home, "amdgpu", gpu).options == ["xess"], "Vega: no FSR 4"
assert u.state()["tools"] == ["proton-cachyos-11.0-x86_64"]
u.set("fsr4", True)
txt = open(u.path).read()
assert "PROTON_FSR4_UPGRADE=1\n" in txt and "FSR4_UPGRADE=1\n" in txt and u.current() == {"fsr4": True, "xess": False}
u.set("xess", True); assert u.current() == {"fsr4": True, "xess": True}
u.set("fsr4", False); assert u.current() == {"fsr4": False, "xess": True} and "FSR4" not in open(u.path).read()
u.set("xess", False); assert not os.path.exists(u.path)
try:
    upscale.Upscale(home, "xe", None).set("fsr4", True); raise SystemExit("fsr4 must be refused on Intel")
except ValueError:
    pass
assert upscale.optiscaler_vars("xe", None) == 'PROTON_USE_OPTISCALER=1 PROTON_OPTISCALER_CONFIG="Upscalers.Dx11Upscaler=xess;Upscalers.Dx12Upscaler=xess"'
assert upscale.optiscaler_vars("amdgpu", 11).endswith("Dx12Upscaler=fsr31\" PROTON_FSR4_UPGRADE=1")
assert "FSR4" not in upscale.optiscaler_vars("amdgpu", 9)
assert u.state()["opti_upscaler"] == "FSR 4"
print("all upscale tests passed")

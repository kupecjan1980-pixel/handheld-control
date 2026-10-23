import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import gpu, sysfs
tmp = tempfile.mkdtemp(); f = f"{tmp}/pp_power_profile_mode"
SMU = """PROFILE_INDEX(NAME) CLOCK_TYPE(NAME) FPS MinActiveFreqType MinActiveFreq BoosterFreqType BoosterFreq PD_Data_limit_c PD_Data_error_coeff PD_Data_error_rate_coeff
 0 BOOTUP_DEFAULT*:
  0(       GFXCLK)       0       5       0       0       0       0
 1 3D_FULL_SCREEN :
 2   POWER_SAVING :
 3          VIDEO :
 4             VR :
 5        COMPUTE :
 6         CUSTOM :"""
RAVEN = """NUM        MODE_NAME BUSY_SET_POINT FPS USE_RLC_BUSY MIN_ACTIVE_LEVEL
  0 BOOTUP_DEFAULT :             70  60          0              0
  1 3D_FULL_SCREEN*:             70  60          1              3
  2   POWER_SAVING :             90  60          0              0
  3          VIDEO :             70  60          0              0
  4             VR :             70  90          0              0
  5        COMPUTE :             30  60          0              6
  6         CUSTOM :              0   0          0              0"""
# the kernel rewrites the file on a write; emulate by moving the star
def fake_write(path, v):
    lines = open(path).read().splitlines()
    out = []
    for l in lines:
        import re
        m = re.match(r"(\s*)(\d+)(\s+)([A-Z0-9_]+)(\*?)(\s*:.*)?$", l)
        if m:
            star = "*" if int(m.group(2)) == int(v) else ""
            l = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{star}{m.group(6) or ''}"
        out.append(l)
    open(path, "w").write("\n".join(out))
gpu.sysfs.write = fake_write
for text, start in ((SMU, "BOOTUP_DEFAULT"), (RAVEN, "3D_FULL_SCREEN")):
    open(f, "w").write(text)
    p = gpu.PowerProfile(tmp, "amdgpu")
    assert p.available() == (True, None) and p.current() == start, p.current()
    assert p.options() == ["BOOTUP_DEFAULT", "3D_FULL_SCREEN", "POWER_SAVING", "VIDEO", "VR", "COMPUTE"]
    p.apply("POWER_SAVING"); assert p.current() == "POWER_SAVING"
    p.restore(); assert p.current() == start
print("profile parse / apply / restore ok (SMU and powerplay formats)")
assert not gpu.PowerProfile(tmp, "xe").available()[0]
shutil.rmtree(tmp)
print("all gpu profile tests passed")

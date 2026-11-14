"""AI apps toggle: oneDNN/OpenMP variables in ~/.config/environment.d."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import ai

v = ai.variables("amdgpu", {"avx512f", "avx512_bf16"})
assert v == {"ONEDNN_DEFAULT_FPMATH_MODE": "BF16"}, v  # (no OMP_PLACES / OMP_PROC_BIND: they pinned the desktop)
assert ai.variables("amdgpu", {"avx2"}) == {}
v = ai.variables("xe", {"avx2"})
assert "ONEDNN_DEFAULT_FPMATH_MODE" not in v and v["SYCL_CACHE_PERSISTENT"] == "1" and v["ZES_ENABLE_SYSMAN"] == "1"
home = tempfile.mkdtemp()
e = ai.AiEnv(home, "xe")
assert not e.enabled()
e.set(True)
txt = open(e.path).read()
assert e.enabled() and "OMP_" not in txt and "ZES_ENABLE_SYSMAN=1\n" in txt and e.available() == (True, None)
# a file from an older version: rewritten without the OpenMP binding; removed when nothing is left to set
open(e.path, "a").write("OMP_PLACES=cores\nOMP_PROC_BIND=close\n")
assert e.migrate() and "OMP_" not in open(e.path).read() and "ZES_ENABLE_SYSMAN=1\n" in open(e.path).read()
assert not e.migrate()
amd = ai.AiEnv(home, "amdgpu")
amd.vars = {}
open(amd.path, "w").write("OMP_PLACES=cores\nOMP_PROC_BIND=close\n")
assert amd.migrate() and not amd.enabled() and not amd.available()[0]
e.set(False)
assert not e.enabled() and os.path.isdir(os.path.dirname(e.path))
print("all ai tests passed")

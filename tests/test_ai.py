"""AI apps toggle: oneDNN/OpenMP variables in ~/.config/environment.d."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import ai

v = ai.variables("amdgpu", {"avx512f", "avx512_bf16"})
assert v == {"OMP_PLACES": "cores", "OMP_PROC_BIND": "close", "ONEDNN_DEFAULT_FPMATH_MODE": "BF16"}, v
v = ai.variables("xe", {"avx2"})
assert "ONEDNN_DEFAULT_FPMATH_MODE" not in v and v["SYCL_CACHE_PERSISTENT"] == "1" and v["ZES_ENABLE_SYSMAN"] == "1"
home = tempfile.mkdtemp()
e = ai.AiEnv(home, "xe")
assert not e.enabled()
e.set(True)
txt = open(e.path).read()
assert e.enabled() and "OMP_PLACES=cores\n" in txt and "ZES_ENABLE_SYSMAN=1\n" in txt
e.set(False)
assert not e.enabled() and os.path.isdir(os.path.dirname(e.path))
print("all ai tests passed")

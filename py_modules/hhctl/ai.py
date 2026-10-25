"""Settings for AI apps that use oneDNN / OpenMP (PyTorch, OpenVINO, ONNX
Runtime, llama.cpp builds): environment variables in the user's
~/.config/environment.d, which systemd hands to every app of the next
session. Games don't use oneDNN, so they're unaffected.

  OMP_PLACES=cores, OMP_PROC_BIND=close   one OpenMP thread per physical core, kept there
  ONEDNN_DEFAULT_FPMATH_MODE=BF16         only with bf16 in the CPU (Zen 4/5 AVX-512 BF16,
                                          Intel AMX): fp32 math may run as bf16, faster,
                                          slightly less precise
  SYCL_CACHE_PERSISTENT=1                 Intel graphics: compiled GPU kernels are kept
                                          between runs (oneDNN/SYCL start faster)
  ZES_ENABLE_SYSMAN=1                     Intel graphics: apps can see free GPU memory
"""

import os

FILE = ".config/environment.d/90-handheld-control-ai.conf"


def _cpu_flags() -> set[str]:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("flags"):
                    return set(line.split(":", 1)[1].split())
    except OSError:
        pass
    return set()


def variables(gpu_driver: str | None, flags: set[str] | None = None) -> dict[str, str]:
    flags = _cpu_flags() if flags is None else flags
    out = {"OMP_PLACES": "cores", "OMP_PROC_BIND": "close"}
    if flags & {"avx512_bf16", "amx_bf16"}:
        out["ONEDNN_DEFAULT_FPMATH_MODE"] = "BF16"
    if gpu_driver in ("xe", "i915"):
        out["SYCL_CACHE_PERSISTENT"] = "1"
        out["ZES_ENABLE_SYSMAN"] = "1"
    return out


class AiEnv:
    def __init__(self, home: str, gpu_driver: str | None):
        self.path = os.path.join(home, FILE)
        self.home = home
        self.vars = variables(gpu_driver)

    def enabled(self) -> bool:
        return os.path.exists(self.path)

    def set(self, on: bool) -> None:
        if not on:
            if os.path.exists(self.path):
                os.remove(self.path)
            return
        st = os.stat(self.home)
        d = os.path.dirname(self.path)
        for p in (os.path.dirname(d), d):
            if not os.path.isdir(p):
                os.mkdir(p)
                os.chown(p, st.st_uid, st.st_gid)
        with open(self.path, "w") as f:
            f.write("# Written by Handheld Control (AI apps). Turn it off there to remove.\n")
            f.writelines(f"{k}={v}\n" for k, v in self.vars.items())
        os.chown(self.path, st.st_uid, st.st_gid)

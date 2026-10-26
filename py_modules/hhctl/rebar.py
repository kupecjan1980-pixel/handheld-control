"""Resizable BAR status (AMD: Smart Access Memory).

The BAR is the window through which the CPU sees the GPU's memory. Without
Resizable BAR it's 256 MB; with it the CPU sees all of the VRAM at once.
Only the firmware can turn it on or off (BIOS: "Above 4G Decoding" plus
"Re-Size BAR Support" / "Smart Access Memory"); the GPU driver then takes
the largest window it offers at boot. The display GPU can't be resized while
it runs, so this only reports what's in effect.
"""

import os

from . import sysfs

IORESOURCE_MEM, IORESOURCE_PREFETCH = 0x200, 0x2000
SMALL = 256 << 20


def bars(dev: str) -> list[int]:
    """Sizes (bytes) of the GPU's prefetchable memory BARs."""
    out = []
    for line in (sysfs.read(f"{dev}/resource") or "").splitlines():
        try:
            start, end, flags = (int(x, 16) for x in line.split()[:3])
        except ValueError:
            continue
        if end > start and flags & IORESOURCE_MEM and flags & IORESOURCE_PREFETCH:
            out.append(end - start + 1)
    return out


def _gb(n: int) -> str:
    return f"{n / (1 << 30):g} GB" if n >= 1 << 30 else f"{n >> 20} MB"


def integrated(dev: str, driver: str | None) -> bool:
    """amdgpu reports the memory chips' vendor only for real VRAM; Intel's
    integrated graphics are always 00:02.0."""
    if driver == "amdgpu":
        return not os.path.exists(f"{dev}/mem_info_vram_vendor")
    return os.path.basename(os.path.realpath(dev)) == "0000:00:02.0"


def status(dev: str | None, driver: str | None) -> dict | None:
    if not dev:
        return None
    sizes = bars(dev)
    if not sizes:
        return None
    # amdgpu says how much VRAM the CPU sees; elsewhere the largest window.
    bar = sysfs.read_int(f"{dev}/mem_info_vis_vram_total") or max(sizes)
    vram = sysfs.read_int(f"{dev}/mem_info_vram_total")
    if integrated(dev, driver):
        state, text = "n/a", (f"BAR {_gb(bar)}. Integrated graphics use system RAM, which the CPU "
                              "reaches directly, so Resizable BAR makes little difference here.")
    elif bar > SMALL and (not vram or bar >= vram):
        state, text = "on", f"On: the CPU sees all {_gb(bar)} of VRAM."
    elif bar > SMALL:
        state, text = "partial", f"Partly: the CPU sees {_gb(bar)} of {_gb(vram)} VRAM."
    else:
        state, text = "off", ("Off (256 MB window). Turn on \"Above 4G Decoding\" and \"Re-Size BAR "
                              "Support\" (or \"Smart Access Memory\") in the BIOS.")
    return {"state": state, "bar_mb": bar >> 20, "vram_mb": vram >> 20 if vram else None, "text": text}

"""Advanced BIOS menu: Smokeless UMAF on AMD PCs.

UMAF (Universal AMD Form Browser, github.com/DavidS95/Smokeless_UMAF) is a small UEFI program that shows the
firmware's hidden AMD menus (AMD CBS / PBS / Overclocking: UMA frame buffer, memory speed and timings, power
limits, fan table…) that the vendor's setup hides. It changes the same settings the firmware's own setup does,
saved in the firmware; nothing is flashed.

Installed here on the EFI system partition, with its own UEFI boot entry (not added to the boot order, so the PC
boots as before): "Restart into it" sets BootNext for one boot and restarts. Its files are downloaded from the
author's repository (it has no license to ship it with HHC) and checked against the exact archive known to work;
its display engine is replaced by HHC's (built from EDK2: the same menus with HHC's look, a plain-language
explanation under every setting and a list of what was changed before saving). Unsigned: with Secure Boot on the
firmware refuses to start it.
"""

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import zipfile

from . import updater

log = logging.getLogger(__name__)

ZIP_URL = "https://raw.githubusercontent.com/DavidS95/Smokeless_UMAF/main/UniversalAMDFormBrowser.zip"
ZIP_SHA256 = "93b15e88cdc0f1d6f90ed41ffbab93f8b28d41f3bbefed9de2072fe01ba5b018"
LABEL = "UMAF (Advanced BIOS - HHC)"
LOADER = "EFI/UMAF/UMAF.efi"  # on the ESP; the loader looks for the three apps below at the root of FAT volumes
APPS = ("SetupBrowser.efi", "DisplayEngine.efi", "UiApp.efi")
MARK = "EFI/UMAF/installed-by-hhc"  # only what HHC put there is changed or removed by it
HERE = os.path.dirname(os.path.abspath(__file__))
OUR_ENGINE = os.path.join(HERE, "data", "umaf-DisplayEngine.efi")
SB_VAR = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"


def _run(*cmd, timeout=20) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)


def esp_path() -> str | None:
    """Where the EFI system partition is mounted (/boot/efi on Fedora/Bazzite, /boot or /efi elsewhere)."""
    for p in ("/boot/efi", "/efi", "/boot"):
        try:
            if os.path.isdir(os.path.join(p, "EFI")) and _fstype(p) == "vfat":
                return p
        except OSError:
            continue
    return None


def _fstype(path: str) -> str:
    r = _run("findmnt", "-no", "FSTYPE", "--target", path)
    return r.stdout.strip()


def secure_boot() -> bool:
    try:
        with open(SB_VAR, "rb") as f:
            data = f.read()
        return len(data) >= 5 and data[4] == 1
    except OSError:
        return False


def _entries() -> dict[str, str]:
    """efibootmgr's entries: number -> label."""
    r = _run("efibootmgr")
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^Boot([0-9A-Fa-f]{4})\*?\s+(.+?)(?:\t|$)", r.stdout,
                                                                re.M)}


def entry() -> str | None:
    return next((n for n, label in _entries().items() if label.startswith("UMAF (Advanced BIOS")), None)


def status(is_amd: bool) -> dict:
    """What the panel shows: whether it can be installed (why not), whether it is, its boot entry."""
    st = {"available": False, "why": None, "installed": False, "entry": None, "secure_boot": False, "esp": None}
    if not is_amd:
        st["why"] = "Only for AMD processors: UMAF shows AMD's own firmware menus (AMD CBS / PBS / Overclocking)."
        return st
    if not os.path.isdir("/sys/firmware/efi"):
        st["why"] = "This PC started in legacy BIOS mode, not UEFI: UMAF is a UEFI program."
        return st
    if not shutil.which("efibootmgr"):
        st["why"] = "efibootmgr isn't installed (it adds UMAF's boot entry)."
        return st
    esp = esp_path()
    st["esp"] = esp
    if not esp:
        st["why"] = "The EFI system partition isn't mounted (looked at /boot/efi, /efi and /boot)."
        return st
    st["secure_boot"] = secure_boot()
    present = os.path.isfile(os.path.join(esp, LOADER)) or any(os.path.isfile(os.path.join(esp, a)) for a in APPS)
    ours = os.path.isfile(os.path.join(esp, MARK))
    st["installed"] = ours and os.path.isfile(os.path.join(esp, LOADER))
    st["entry"] = entry()
    if present and not ours:
        st["why"] = ("UMAF is already set up on this PC by something else (its files are on the EFI partition, e.g. "
                     "from a boot menu entry): HHC leaves it alone. Start it the way it was set up.")
        return st
    if st["secure_boot"]:
        st["why"] = ("Secure Boot is on: UMAF isn't signed, so the firmware won't start it. Turn Secure Boot off in "
                     "the firmware setup to use it (your Linux keeps working if it boots without Secure Boot).")
    st["available"] = True
    return st


def _disk_part(esp: str) -> tuple[str, str] | None:
    """(disk, partition number) of the ESP, for efibootmgr."""
    src = _run("findmnt", "-no", "SOURCE", "--target", esp).stdout.strip()
    if not src.startswith("/dev/"):
        return None
    disk = _run("lsblk", "-no", "PKNAME", src).stdout.strip().splitlines()
    part = _run("lsblk", "-no", "PARTN", src).stdout.strip().splitlines()
    if not disk or not part:
        m = re.match(r"(/dev/(?:nvme\d+n\d+|mmcblk\d+))p(\d+)$|(/dev/[a-z]+)(\d+)$", src)
        if not m:
            return None
        return (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
    return "/dev/" + disk[0].strip(), part[0].strip()


def install() -> str:
    """Downloads UMAF, checks it, puts it on the ESP with HHC's display engine and adds its boot entry (not in the
    boot order). Returns what was done; raises with the reason when it can't."""
    esp = esp_path()
    if not esp:
        raise RuntimeError("the EFI system partition isn't mounted")
    present = os.path.isfile(os.path.join(esp, LOADER)) or any(os.path.isfile(os.path.join(esp, a)) for a in APPS)
    if present and not os.path.isfile(os.path.join(esp, MARK)):
        raise RuntimeError("UMAF is already set up here by something else; HHC leaves it alone")
    data = updater._get(ZIP_URL, 4 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != ZIP_SHA256:
        raise RuntimeError("the downloaded UMAF isn't the archive HHC knows (it changed upstream); nothing installed")
    z = zipfile.ZipFile(io.BytesIO(data))
    files = {"EFI/Boot/Bootx64.efi": LOADER, "SetupBrowser.efi": "SetupBrowser.efi", "UiApp.efi": "UiApp.efi",
             "DisplayEngine.efi": "DisplayEngine.stock.efi"}
    for name, dest in files.items():
        out = os.path.join(esp, dest)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".part", "wb") as f:
            f.write(z.read(name))
        os.replace(out + ".part", out)
    engine = os.path.join(esp, "DisplayEngine.efi")
    src = OUR_ENGINE if os.path.isfile(OUR_ENGINE) else os.path.join(esp, "DisplayEngine.stock.efi")
    shutil.copyfile(src, engine + ".part")
    os.replace(engine + ".part", engine)
    with open(os.path.join(esp, MARK), "w") as f:
        f.write("Smokeless UMAF installed by Handheld Control (HHC); removed with it.\n")
    os.sync()
    done = "UMAF files put on the EFI partition"
    if not entry():
        dp = _disk_part(esp)
        if not dp:
            raise RuntimeError("couldn't tell which disk the EFI partition is on, so no boot entry was added")
        before = _run("efibootmgr").stdout
        order = re.search(r"^BootOrder:\s*(\S+)", before, re.M)
        # --create-only: the entry exists, but the boot order stays as it is (the PC boots as before)
        r = _run("efibootmgr", "--create-only", "--disk", dp[0], "--part", dp[1], "--label", LABEL,
                 "--loader", "\\" + LOADER.replace("/", "\\"))
        if r.returncode:
            raise RuntimeError(f"efibootmgr couldn't add the boot entry: {(r.stderr or r.stdout).strip()}")
        after = re.search(r"^BootOrder:\s*(\S+)", _run("efibootmgr").stdout, re.M)
        if order and after and after.group(1) != order.group(1):  # (an old efibootmgr put it first anyway)
            _run("efibootmgr", "--bootorder", order.group(1))
        done += ", boot entry added"
    log.info("UMAF installed on %s", esp)
    return done


def restart_into() -> None:
    """Starts UMAF on the next boot only (BootNext), then restarts."""
    n = entry()
    if not n:
        raise RuntimeError("UMAF's boot entry is missing: install it again")
    r = _run("efibootmgr", "--bootnext", n)
    if r.returncode:
        raise RuntimeError(f"couldn't set the next boot: {(r.stderr or r.stdout).strip()}")
    subprocess.Popen(["systemctl", "reboot"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def remove() -> str:
    """Its boot entry and files gone (the ESP as it was)."""
    esp = esp_path()
    if esp and not os.path.isfile(os.path.join(esp, MARK)):
        raise RuntimeError("this UMAF wasn't installed by HHC; it's left alone")
    n = entry()
    if n:
        _run("efibootmgr", "--bootnum", n, "--delete-bootnum")
    if esp:
        for rel in (LOADER, "DisplayEngine.stock.efi", MARK, *APPS):
            try:
                os.remove(os.path.join(esp, rel))
            except OSError:
                pass
        try:
            os.rmdir(os.path.join(esp, os.path.dirname(LOADER)))
        except OSError:
            pass
        os.sync()
    return "UMAF removed"

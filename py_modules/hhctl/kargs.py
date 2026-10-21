"""Add/remove kernel command-line parameters through the system's bootloader.

Supported: Limine (/etc/default/limine, CachyOS default), GRUB,
systemd-boot via sdboot-manage, and rpm-ostree (Bazzite/Fedora Atomic).

Only parameters whose key we manage are touched; the rest of the line is
left exactly as it was. Config files are backed up before every change
(`.hhctl-orig` once, `.hhctl.bak` every time) and restored if regenerating
the boot entries fails.
"""

import os
import re
import shutil
import subprocess

TIMEOUT = 600


def _key(token: str) -> str:
    return token.split("=", 1)[0]


def edit_tokens(tokens: list[str], add: list[str], remove_keys: set[str]) -> list[str]:
    drop = remove_keys | {_key(t) for t in add}
    out = [t for t in tokens if _key(t) not in drop]
    return out + add


class FileBootloader:
    def __init__(self, name: str, path: str, var: str, regen: list[list[str]], create: bool = False):
        self.name = name
        self.path = path
        self.var = var
        self.regen = regen
        self.create = create  # append the variable if no uncommented line exists
        self.line_re = re.compile(rf'^(\s*{re.escape(var)}\+?=)(["\'])(.*)\2\s*$')

    def _lines(self) -> list[str]:
        with open(self.path) as f:
            return f.read().splitlines(keepends=True)

    def configured(self) -> list[str]:
        tokens = []
        for line in self._lines():
            m = self.line_re.match(line.rstrip("\n"))
            if m:
                tokens += m.group(3).split()
        return tokens

    def _regen_cmd(self) -> list[str]:
        for cmd in self.regen:
            if shutil.which(cmd[0]):
                return cmd
        raise OSError(f"none of {', '.join(c[0] for c in self.regen)} is installed")

    def set(self, add: list[str], remove_keys: set[str]) -> None:
        cmd = self._regen_cmd()
        lines = self._lines()
        out, placed = [], False
        for line in lines:
            m = self.line_re.match(line.rstrip("\n"))
            if not m:
                out.append(line)
                continue
            # Remove our keys from every matching line; add to the first one.
            tokens = edit_tokens(m.group(3).split(), [] if placed else add, remove_keys)
            placed = True
            nl = "\n" if line.endswith("\n") else ""
            out.append(f"{m.group(1)}{m.group(2)}{' '.join(tokens)}{m.group(2)}{nl}")
        if not placed:
            if not (self.create and add):
                if add:
                    raise OSError(f"no {self.var}= line found in {self.path}")
                return
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f'{self.var}="{" ".join(add)}"\n')
        if out == lines:
            return

        if not os.path.exists(self.path + ".hhctl-orig"):
            shutil.copy2(self.path, self.path + ".hhctl-orig")
        shutil.copy2(self.path, self.path + ".hhctl.bak")
        tmp = self.path + ".hhctl.tmp"
        with open(tmp, "w") as f:
            f.writelines(out)
        shutil.copymode(self.path, tmp)
        os.replace(tmp, self.path)

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if res.returncode != 0:
            shutil.copy2(self.path + ".hhctl.bak", self.path)
            err = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["failed"]
            raise OSError(f"{' '.join(cmd)}: {err[0]} (config restored)")


class RpmOstree:
    name = "rpm-ostree"

    def configured(self) -> list[str]:
        res = subprocess.run(["rpm-ostree", "kargs"], capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise OSError(res.stderr.strip() or "rpm-ostree kargs failed")
        return res.stdout.split()

    def set(self, add: list[str], remove_keys: set[str]) -> None:
        current = self.configured()
        drop = remove_keys | {_key(t) for t in add}
        args = [f"--delete-if-present={t}" for t in current if _key(t) in drop and t not in add]
        args += [f"--append-if-missing={t}" for t in add]
        if not args:
            return
        res = subprocess.run(["rpm-ostree", "kargs", *args], capture_output=True, text=True, timeout=TIMEOUT)
        if res.returncode != 0:
            raise OSError(res.stderr.strip() or "rpm-ostree kargs failed")


def detect():
    if os.path.exists("/run/ostree-booted") and shutil.which("rpm-ostree"):
        return RpmOstree()
    if os.path.exists("/etc/default/limine"):
        return FileBootloader(
            "Limine", "/etc/default/limine", "KERNEL_CMDLINE[default]",
            [["limine-update"], ["limine-mkinitcpio"]],
        )
    if os.path.exists("/etc/sdboot-manage.conf"):
        return FileBootloader(
            "systemd-boot", "/etc/sdboot-manage.conf", "LINUX_OPTIONS",
            [["sdboot-manage", "gen"]], create=True,
        )
    if os.path.exists("/etc/default/grub"):
        return FileBootloader(
            "GRUB", "/etc/default/grub", "GRUB_CMDLINE_LINUX_DEFAULT",
            [["update-grub"], ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"]],
        )
    return None

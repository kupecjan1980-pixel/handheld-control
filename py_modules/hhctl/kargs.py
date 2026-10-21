"""Add/remove kernel command-line parameters through the system's bootloader.

Supported: Limine (/etc/default/limine, CachyOS default), GRUB,
systemd-boot via sdboot-manage, and rpm-ostree (Bazzite/Fedora Atomic).

Only parameters whose key we manage are touched; the rest of the line is
left exactly as it was. Config files are backed up before every change
(`.hhctl-orig` once, `.hhctl.bak` every time) and restored if regenerating
the boot entries fails.

For Limine, success is verified by reading the generated limine.conf: an
edit to /etc/default/limine does nothing until the boot entries are rebuilt.
"""

import os
import re
import shutil

from . import proc, sysfs

TIMEOUT = 600


def _key(token: str) -> str:
    return token.split("=", 1)[0]


def _which(cmd: str) -> str | None:
    return shutil.which(cmd, path=proc.env()["PATH"])


def edit_tokens(tokens: list[str], add: list[str], remove_keys: set[str]) -> list[str]:
    drop = remove_keys | {_key(t) for t in add}
    out = [t for t in tokens if _key(t) not in drop]
    return out + add


def _tail(res) -> str:
    lines = ((res.stderr or "") + "\n" + (res.stdout or "")).strip().splitlines()
    return lines[-1] if lines else f"exit code {res.returncode}"


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

    def entry_cmdlines(self) -> list[list[str]] | None:
        """Kernel command lines of the generated boot entries, if readable."""
        return None

    def in_entries(self, add: list[str], remove_keys: set[str]) -> bool | None:
        """Does some boot entry carry `add` and none of `remove_keys`?"""
        cmdlines = self.entry_cmdlines()
        if cmdlines is None:
            return None
        return any(
            all(t in cl for t in add) and not any(_key(t) in remove_keys for t in cl)
            for cl in cmdlines
        )

    def _regen_cmds(self) -> list[list[str]]:
        cmds = [c for c in self.regen if _which(c[0])]
        if not cmds:
            raise OSError(f"none of {', '.join(c[0] for c in self.regen)} is installed")
        return cmds

    def _edited(self, add: list[str], remove_keys: set[str]) -> tuple[list[str], list[str]]:
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
                return lines, lines
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f'{self.var}="{" ".join(add)}"\n')
        return lines, out

    def set(self, add: list[str], remove_keys: set[str]) -> None:
        cmds = self._regen_cmds()
        lines, out = self._edited(add, remove_keys)
        if out != lines:
            if not os.path.exists(self.path + ".hhctl-orig"):
                shutil.copy2(self.path, self.path + ".hhctl-orig")
            shutil.copy2(self.path, self.path + ".hhctl.bak")
            tmp = self.path + ".hhctl.tmp"
            with open(tmp, "w") as f:
                f.writelines(out)
            shutil.copymode(self.path, tmp)
            os.replace(tmp, self.path)
        elif self.in_entries(add, remove_keys) is not False:
            return  # file already right and entries match (or can't be checked)

        # Try each regeneration command until the boot entries reflect the edit.
        problems = []
        for cmd in cmds:
            res = proc.run(cmd, text=True, timeout=TIMEOUT)
            if res.returncode != 0:
                problems.append(f"{' '.join(cmd)}: {_tail(res)}")
                continue
            ok = self.in_entries(add, remove_keys)
            if ok is None or ok:
                return
            problems.append(f"{' '.join(cmd)} ran but the boot entries don't have the change")
        if out != lines:
            shutil.copy2(self.path + ".hhctl.bak", self.path)
            problems.append("config restored")
        raise OSError("; ".join(problems))


class Limine(FileBootloader):
    def __init__(self):
        super().__init__(
            "Limine", "/etc/default/limine", "KERNEL_CMDLINE[default]",
            # limine-update rebuilds entries (and updates the EFI binary);
            # limine-mkinitcpio is what the CachyOS wiki documents.
            [["limine-update"], ["limine-mkinitcpio"]],
        )

    def conf_path(self) -> str | None:
        esp = None
        for line in self._lines():
            m = re.match(r'^\s*ESP_PATH=["\']?([^"\'\s]+)', line)
            if m:
                esp = m.group(1)
        roots = [r for r in (esp, "/boot", "/efi", "/boot/efi") if r]
        for root in roots:
            for rel in ("limine.conf", "limine/limine.conf", "EFI/limine/limine.conf", "EFI/BOOT/limine.conf"):
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    return path
        return None

    def entry_cmdlines(self) -> list[list[str]] | None:
        path = self.conf_path()
        if not path:
            return None
        text = sysfs.read(path) or ""
        out = []
        for line in text.splitlines():
            m = re.match(r'^\s*(?:kernel_)?cmdline\s*[:=]\s*(.*)$', line, re.IGNORECASE)
            if m:
                out.append(m.group(1).split())
        return out


class RpmOstree:
    name = "rpm-ostree"

    def configured(self) -> list[str]:
        res = proc.run(["rpm-ostree", "kargs"], text=True, timeout=60)
        if res.returncode != 0:
            raise OSError(_tail(res))
        return res.stdout.split()

    def in_entries(self, add: list[str], remove_keys: set[str]) -> bool | None:
        return None  # rpm-ostree kargs already reports the deployment's cmdline

    def set(self, add: list[str], remove_keys: set[str]) -> None:
        current = self.configured()
        drop = remove_keys | {_key(t) for t in add}
        args = [f"--delete-if-present={t}" for t in current if _key(t) in drop and t not in add]
        args += [f"--append-if-missing={t}" for t in add]
        if not args:
            return
        res = proc.run(["rpm-ostree", "kargs", *args], text=True, timeout=TIMEOUT)
        if res.returncode != 0:
            raise OSError(_tail(res))


def detect():
    if os.path.exists("/run/ostree-booted") and _which("rpm-ostree"):
        return RpmOstree()
    if os.path.exists("/etc/default/limine"):
        return Limine()
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

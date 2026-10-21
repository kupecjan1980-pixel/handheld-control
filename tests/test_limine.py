import sys, os, tempfile, shutil, stat
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import kargs, proc
tmp = tempfile.mkdtemp(); bindir = f"{tmp}/bin"; os.makedirs(bindir)
default = f"{tmp}/limine"; conf = f"{tmp}/boot/limine.conf"; os.makedirs(f"{tmp}/boot")
def script(name, body):
    p = f"{bindir}/{name}"; open(p, "w").write("#!/bin/sh\n" + body + "\n"); os.chmod(p, 0o755)
# good regen: copy KERNEL_CMDLINE[default] tokens into limine.conf entries
script("regen-good", f'''tok=$(sed -n 's/^KERNEL_CMDLINE\\[default\\]+\\{{0,1\\}}="\\(.*\\)"/\\1/p' {default})
printf '/CachyOS\\n    protocol: linux\\n    cmdline: %s\\n/Snapshots\\n    cmdline: quiet old\\n' "$tok" > {conf}''')
script("regen-noop", "exit 0")
script("regen-fail", "echo 'mkinitcpio: error' >&2; exit 1")
os.environ["PATH"] = bindir + ":" + os.environ["PATH"]
BASE = 'ESP_PATH="' + tmp + '/boot"\nKERNEL_CMDLINE[default]+="quiet rw root=UUID=c988"\n'
def fresh(cmds, text=BASE, entries="/CachyOS\n    cmdline: quiet rw root=UUID=c988\n"):
    open(default, "w").write(text); open(conf, "w").write(entries)
    for f in (".hhctl-orig", ".hhctl.bak"):
        if os.path.exists(default + f): os.remove(default + f)
    l = kargs.Limine(); l.path = default; l.regen = cmds
    return l
P = ["xe.enable_psr=0", "xe.enable_panel_replay=0"]
# 1. good regen verifies
l = fresh([["regen-good"]]); l.set(P, set())
assert l.in_entries(P, set()) is True and l.conf_path() == conf
# 2. first command does nothing -> falls back to second
l = fresh([["regen-noop"], ["regen-good"]]); l.set(P, set()); assert l.in_entries(P, set())
# 3. nothing works -> error, file restored
l = fresh([["regen-noop"], ["regen-fail"]])
try: l.set(P, set()); raise SystemExit("should fail")
except OSError as e: msg = str(e)
assert "ran but the boot entries don't have the change" in msg and "mkinitcpio: error" in msg and "config restored" in msg, msg
assert open(default).read() == BASE
print("limine: verify / fallback / restore ok ->", msg[:70])
# 4. user's state: file already has params, entries don't -> set() rebuilds instead of returning
withp = BASE.replace('c988"', 'c988 xe.enable_psr=0 xe.enable_panel_replay=0"')
l = fresh([["regen-good"]], text=withp)
assert l.in_entries(P, set()) is False
l.set(P, set()); assert l.in_entries(P, set()) is True and open(default).read() == withp
print("already-saved-but-not-in-entries -> rebuilt ok")
# 5. removal verified
l.set([], {"xe.enable_psr", "xe.enable_panel_replay"})
assert l.in_entries([], {"xe.enable_psr", "xe.enable_panel_replay"}) is True and "xe.enable_psr" not in open(default).read()
print("removal verified ok")
# 6. clean env for children
os.environ["LD_LIBRARY_PATH"] = "/decky/_MEI"; os.environ["LD_LIBRARY_PATH_ORIG"] = "/usr/lib/orig"; os.environ["PYTHONHOME"] = "/decky"
e = proc.env(); assert e["LD_LIBRARY_PATH"] == "/usr/lib/orig" and "PYTHONHOME" not in e and "/usr/sbin" in e["PATH"]
del os.environ["LD_LIBRARY_PATH_ORIG"]; assert "LD_LIBRARY_PATH" not in proc.env()
print("proc.env restores host LD_LIBRARY_PATH, drops PYTHONHOME ok")
shutil.rmtree(tmp); print("all limine tests passed")

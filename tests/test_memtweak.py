"""Gaming memory settings: only differing values are changed, and put back."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import memtweak as m

tmp = tempfile.mkdtemp()
m.PROC = tmp
vals = {"vm/compaction_proactiveness": "20", "vm/page_lock_unfairness": "1", "kernel/split_lock_mitigate": "1"}
for k, v in vals.items():
    os.makedirs(os.path.dirname(f"{tmp}/{k}"), exist_ok=True); open(f"{tmp}/{k}", "w").write(v + "\n")
t = m.MemTweak()
assert t.pending() == ["vm.compaction_proactiveness", "kernel.split_lock_mitigate"]
t.apply()
rd = lambda k: open(f"{tmp}/{k}").read().strip()
assert rd("vm/compaction_proactiveness") == "0" and rd("kernel/split_lock_mitigate") == "0" and t.pending() == []
assert set(t.orig) == {"vm.compaction_proactiveness", "kernel.split_lock_mitigate"}, "page_lock_unfairness was right already"
t.restore()
assert rd("vm/compaction_proactiveness") == "20" and rd("kernel/split_lock_mitigate") == "1" and rd("vm/page_lock_unfairness") == "1"
os.remove(f"{tmp}/kernel/split_lock_mitigate")  # an older kernel / an AMD without it
assert "kernel.split_lock_mitigate" not in m.MemTweak().available()
print("all memtweak tests passed")

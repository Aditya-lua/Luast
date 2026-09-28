#!/usr/bin/env python3
"""Build ground-truth trace v2: parser-based dispatcher location."""
import re, subprocess, sys

SCRIPT = sys.argv[1]
TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)
from luau_recover.parser import parse
from luau_recover.model import Node
from luau_recover.luast_l3 import find_dispatcher

src = open(SCRIPT, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = find_dispatcher(root)
assert d, "no dispatcher"
stvar = d["var"]
wnode = d["node"]

# Z var: first `+=` target inside the dispatcher body
zvar = None
def find_z(node):
    global zvar
    if isinstance(node, Node):
        if node.kind == "assign" and node.get("op") == "+=":
            ts = node.get("targets", [])
            if ts and ts[0].kind == "name":
                zvar = ts[0].get("name")
                return
        for v in node.fields.values():
            find_z(v)
            if zvar:
                return
    elif isinstance(node, list):
        for v in node:
            find_z(v)
            if zvar:
                return

# search inside the dispatcher body only
for st in wnode.get("body", []):
    find_z(st)
    if zvar:
        break
assert zvar, "Z var not found in dispatcher"
print(f"[trace] state={stvar} Z={zvar}")

# textual insertion anchored on the exact dispatcher prologue
pat = re.compile(
    r"(while true do %s = [0-9.]+ - %s;)" % (re.escape(stvar), re.escape(stvar)))
m2 = pat.search(src)
assert m2, "dispatcher prologue not found in text"
traced = src[:m2.end()] + f' print("TRACE", {stvar}, {zvar});' + src[m2.end():]

prelude = open("/home/z/my-project/scratch/roblox_prelude.lua").read()
_pl = prelude.count("\n") + 1
prelude = prelude.replace("@@OFFSET@@", str(_pl))
combo = "/home/z/my-project/scratch/_combo_trace.lua"
open(combo, "w", encoding="utf-8").write(prelude + "\n" + traced)

LU = TOOL + "/../ROBLOX_ENV/luau"
r = subprocess.run([LU, combo], capture_output=True, text=True, timeout=120)
real = []
for line in r.stdout.splitlines():
    if line.startswith("TRACE"):
        parts = line.split()
        try:
            z = float(parts[2])
        except (ValueError, IndexError):
            continue  # nil/inf/nan Z — skip until numeric
        try:
            s = int(float(parts[1]))
        except (ValueError, IndexError):
            continue
        real.append((s, z))
print(f"[real luau] {len(real)} trace points, rc={r.returncode}")
if r.stderr.strip():
    print("STDERR:", r.stderr[:300])
if not real:
    # show first MISSING-GLOBAL lines
    for line in r.stdout.splitlines():
        if line.startswith("MISSING-GLOBAL"):
            print("  ", line)
    sys.exit(1)
for st, z in real[:8]:
    print("   real:", st, z)
open("/home/z/my-project/scratch/real_trace.txt", "w").write(
    "\n".join(f"{s} {z}" for s, z in real))
print("saved -> /home/z/my-project/scratch/real_trace.txt")

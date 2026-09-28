#!/usr/bin/env python3
"""Build ground-truth trace: concat prelude + traced script, run real Luau,
then run emulator with same trace, diff and report first divergence."""
import re, subprocess, sys

SCRIPT = sys.argv[1]
TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)

# --- 1. build traced file
src = open(SCRIPT, encoding="utf-8", errors="replace").read()
m = re.search(r"while true do ([A-Za-z_][A-Za-z0-9_]*) = ([0-9.]+) - \1;", src)
assert m, "dispatcher not found"
stvar = m.group(1)
tail = src[m.end():m.end() + 300000]
zm = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*) \+= ", tail)
assert zm, "Z var not found"
zvar = zm.group(1)
print(f"[trace] state={stvar} Z={zvar}")

traced = src[:m.end()] + f'print("TRACE", {stvar}, {zvar}); ' + src[m.end():]
prelude = open("/home/z/my-project/scratch/roblox_prelude.lua").read()
combo = "/home/z/my-project/scratch/_combo_trace.lua"
open(combo, "w", encoding="utf-8").write(prelude + "\n" + traced)

# --- 2. run real luau
LU = TOOL + "/../ROBLOX_ENV/luau"
r = subprocess.run([LU, combo], capture_output=True, text=True, timeout=120)
real = []
for line in r.stdout.splitlines():
    if line.startswith("TRACE"):
        _, st, z = line.split()
        real.append((int(float(st)), float(z)))
print(f"[real luau] {len(real)} trace points")
for st, z in real[:12]:
    print("   real:", st, z)
if r.returncode != 0 and not real:
    print("STDERR:", r.stderr[:600])

# --- 3. run emulator with hook
from luau_recover.parser import parse
from luau_recover import luast_l3

root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)
interp = luast_l3.Interp()
interp.set_source(src)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

emu = []
def hook(state):
    # capture Z AFTER the state update: hook fires on dispatch_node entry
    holder = interp.chunk_env.find(zvar) if hasattr(interp, "chunk_env") else None
    z = 0.0
    e = interp.chunk_env
    while e is not None:
        if zvar in e.vars:
            z = e.vars[zvar]
            break
        e = e.parent
    emu.append((int(float(state)), float(z) if z == z and abs(z) != float("inf") else z))
interp.state_hook = hook

try:
    interp.exec_chunk(root)
except Exception as e:
    print("[emu] error:", e)
print(f"[emulator] {len(emu)} trace points")

# --- 4. diff
n = min(len(real), len(emu))
for i in range(n):
    rs, rz = real[i]
    es, ez = emu[i]
    if rs != es or abs(rz - ez) > 0.001:
        print(f"FIRST DIVERGENCE at trace#{i}: real=({rs},{rz}) emu=({es},{ez})")
        print("   context real:", real[max(0, i - 3):i + 3])
        print("   context emu :", emu[max(0, i - 3):i + 3])
        break
else:
    print("no divergence in common prefix" if n else "no comparable points")

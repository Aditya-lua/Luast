#!/usr/bin/env python3
"""Diff emulator trace vs real-Luau ground truth trace."""
import sys
TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)
from luau_recover.parser import parse
from luau_recover import luast_l3

SCRIPT = sys.argv[1]
ZVAR = sys.argv[2]
src = open(SCRIPT, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)
interp = luast_l3.Interp()
interp.set_source(src)
luast_l3.register_string_table(interp.globals_env.vars)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

emu = []
def hook(state):
    e = interp.chunk_env
    z = 0.0
    while e is not None:
        if ZVAR in e.vars:
            z = e.vars[ZVAR]
            break
        e = e.parent
    if z is None or z is 0 and False:
        return
    import luau_recover.lua_rt as _lrt
    if z is _lrt.NIL:
        return
    emu.append((int(float(state)), float(z)))
interp.state_hook = hook
try:
    interp.exec_chunk(root)
    print("[emu] completed")
except Exception as e:
    print("[emu] error:", e)

real = []
for line in open("/home/z/my-project/scratch/real_trace.txt"):
    s, z = line.split()
    real.append((int(s), float(z)))

n = min(len(real), len(emu))
print(f"real={len(real)} emu={len(emu)} common={n}")
div = None
for i in range(n):
    rs, rz = real[i]
    es, ez = emu[i]
    if rs != es or abs(rz - ez) > 0.5:
        div = i
        break
if div is None:
    print("NO DIVERGENCE in common prefix!")
else:
    i = div
    print(f"FIRST DIVERGENCE at #{i}: real={real[i]} emu={emu[i]}")
    lo = max(0, i - 4)
    for j in range(lo, min(n, i + 3)):
        mark = ">>" if j == i else "  "
        print(f"{mark} #{j} real={real[j]}  emu={emu[j]}")
    # save divergence context for analysis
    print("divergence state (the state about to execute):", real[i][0])

#!/usr/bin/env python3
"""Per-contribution Z tracing: real Luau vs emulator, statement-level diff."""
import re, subprocess, sys

SCRIPT = sys.argv[1]
TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)
from luau_recover.parser import parse
from luau_recover import luast_l3
from luau_recover.model import Node
from luau_recover.lua_rt import NIL

src = open(SCRIPT, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)
stvar = d["var"]

# find Z var (first += target inside dispatcher)
zvar = None
def find_z(node):
    global zvar
    if isinstance(node, Node) and node is not d["node"]:
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

for st in d["node"].get("body", []):
    find_z(st)
    if zvar:
        break
assert zvar
print(f"[trace] state={stvar} Z={zvar}")

# --- real: instrument every mutation of zvar (+= and plain =) inside script
zesc = re.escape(zvar)
src2 = re.sub(r"(?<![\w\+\-\*/])" + zesc + r" \+= ([^;]*);",
              zvar + r" += \1; print('T2', " + zvar + ");", src)
src2 = re.sub(r"(?<![\w\+\-\*/\+=])" + zesc + r" = ([^;]*);",
              zvar + r" = \1; print('T2', " + zvar + ");", src2)
traced = re.sub(r"(while true do %s = [0-9.]+ - %s;)" % (zesc, zesc),
                r"\1 print('TS', " + stvar + ");", src2, count=1)
prelude = open("/home/z/my-project/scratch/roblox_prelude.lua").read()
_pl = prelude.count("\n") + 1
prelude = prelude.replace("@@OFFSET@@", str(_pl))
combo = "/home/z/my-project/scratch/_combo_t2.lua"
open(combo, "w", encoding="utf-8").write(prelude + "\n" + traced)

LU = TOOL + "/../ROBLOX_ENV/luau"
r = subprocess.run([LU, combo], capture_output=True, text=True, timeout=120)
real = []
for line in r.stdout.splitlines():
    if line.startswith("T2"):
        try:
            real.append(float(line.split()[1]))
        except (ValueError, IndexError):
            pass
print(f"[real] {len(real)} Z-mutation points, rc={r.returncode}")
if r.stderr.strip():
    print("STDERR:", r.stderr[:250])

# --- emu: record Z after each statement that mutates zvar
interp = luast_l3.Interp()
interp.set_source(src)
luast_l3.register_string_table(interp.globals_env.vars)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

emu = []
orig_exec = luast_l3.Interp.exec_stmt
def exec_stmt2(self, st, env):
    res = orig_exec(self, st, env)
    if st.kind == "assign":
        ts = st.get("targets", [])
        if ts and ts[0].kind == "name" and ts[0].get("name") == zvar:
            e2 = env
            while e2 is not None:
                if zvar in e2.vars:
                    v = e2.vars[zvar]
                    if v is not NIL and isinstance(v, (int, float)) \
                            and not isinstance(v, bool):
                        emu.append(float(v))
                    break
                e2 = e2.parent
    return res
luast_l3.Interp.exec_stmt = exec_stmt2

try:
    interp.exec_chunk(root)
except Exception as e:
    print("[emu] error:", e)

print(f"[emu] {len(emu)} Z-mutation points")
n = min(len(real), len(emu))
for i in range(n):
    if abs(real[i] - emu[i]) > 0.5:
        print(f"FIRST DIVERGENCE at mutation #{i}: real={real[i]} emu={emu[i]}")
        print("   prev real:", real[max(0, i-3):i])
        print("   prev emu :", emu[max(0, i-3):i])
        break
else:
    print("no divergence in", n, "common mutations")

#!/usr/bin/env python3
"""Pinpoint the exact failing statement + expression for emulator errors."""
import sys, traceback
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3, lua_rt
from luau_recover.lua_rt import LuaError

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)

interp = luast_l3.Interp()
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

# track last statements
last_stmts = []
orig_exec = luast_l3.Interp.exec_stmt
def exec_stmt(self, st, env):
    last_stmts.append(st)
    if len(last_stmts) > 12:
        last_stmts.pop(0)
    return orig_exec(self, st, env)
luast_l3.Interp.exec_stmt = exec_stmt

orig_eval = luast_l3.Interp.eval
last_evals = []
def eval_(self, e, env):
    last_evals.append(e)
    if len(last_evals) > 20:
        last_evals.pop(0)
    return orig_eval(self, e, env)
luast_l3.Interp.eval = eval_

def dump_node(st, depth=0, maxd=6):
    pad = "  " * depth
    print(f"{pad}kind={st.kind} start={st.start} end={st.end}")
    if depth >= maxd:
        return
    for k, v in st.fields.items():
        if isinstance(v, luast_l3.Node):
            print(f"{pad} .{k}:")
            dump_node(v, depth + 1, maxd)
        elif isinstance(v, list) and v and isinstance(v[0], luast_l3.Node):
            print(f"{pad} .{k}: [{len(v)} nodes]")
            if depth + 1 <= maxd:
                dump_node(v[0], depth + 1, maxd)

try:
    interp.exec_chunk(root)
    print("[ok] no error")
except LuaError as e:
    print("LUA ERROR:", e)
    print("\n--- last statements ---")
    for st in last_stmts[-4:]:
        print("STMT:", st.kind, src[st.start:st.end][:400].replace("\n", " "))
        print()
    print("--- last evals ---")
    for e in last_evals[-10:]:
        print("EVAL:", e.kind, src[e.start:e.end][:160].replace("\n", " "))
except Exception:
    traceback.print_exc()

print("\nsteps:", interp.steps)

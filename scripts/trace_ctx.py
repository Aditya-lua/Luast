#!/usr/bin/env python3
"""Dump the last N statements with source text to understand context."""
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
interp.set_source(src)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

def fmt(s):
    if s is None:
        return "?"
    return str(int(s)) if s == int(s) else str(s)

N = 60
last_stmts = []
orig_exec = luast_l3.Interp.exec_stmt
def exec_stmt(self, st, env):
    last_stmts.append((st, interp.current_state))
    if len(last_stmts) > N:
        last_stmts.pop(0)
    return orig_exec(self, st, env)
luast_l3.Interp.exec_stmt = exec_stmt

try:
    interp.exec_chunk(root)
    print("[ok]")
except LuaError as e:
    print("LUA ERROR:", e)
    print("\n--- last %d statements ---" % N)
    for st, state in last_stmts:
        txt = src[st.start:st.end].replace("\n", " ")
        if len(txt) > 200:
            txt = txt[:200] + "..."
        print("[st=%s] %s: %s" % (fmt(state), st.kind, txt))

print("\nsteps:", interp.steps)

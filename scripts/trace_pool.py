#!/usr/bin/env python3
"""Dump the pool entry used by the failing call."""
import sys, traceback
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3, lua_rt
from luau_recover.lua_rt import LuaError, LuaTable, NIL

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)

interp = luast_l3.Interp()
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

orig_call = luast_l3.Interp.call_function
def call_function(self, f, args):
    if f is NIL:
        # find pool-ish table: dump recent LuaTable stores with function values
        print("[NIL CALL] args:", [luast_l3.brief(a) for a in args][:3])
        # locate candidate tables: scan MUTLOG for table storing key 161.0
        for tid, k, v in reversed(lua_rt.MUTLOG):
            if isinstance(k, float) and k == 161.0:
                print("   MUTLOG table 0x%x key 161 ->" % tid, luast_l3.brief(v))
                break
        raise LuaError("attempt to call a nil value (instrumented)")
    return orig_call(self, f, args)
luast_l3.Interp.call_function = call_function

try:
    interp.exec_chunk(root)
    print("[ok] no error")
except LuaError as e:
    print("LUA ERROR:", e)
except Exception:
    traceback.print_exc()

print("\nAll MUTLOG stores for numeric keys 155-170:")
for tid, k, v in lua_rt.MUTLOG:
    if isinstance(k, float) and 150 <= k <= 175:
        print("   tbl 0x%x [%s] = %s" % (tid, luast_l3.fmt_num(k), luast_l3.brief(v)))

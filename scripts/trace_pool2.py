#!/usr/bin/env python3
"""Dump actual pool table contents at the failing NIL call."""
import sys, gc, traceback
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
        print("[NIL CALL] args:", [luast_l3.brief(a) for a in args][:3])
        # find the biggest LuaTable alive -> the pool
        best = None
        for o in gc.get_objects():
            if isinstance(o, LuaTable) and len(o.hash) > 300:
                if best is None or len(o.hash) > len(best.hash):
                    best = o
        if best is not None:
            print("pool table 0x%x size=%d" % (id(best), len(best.hash)))
            for k in range(150, 176):
                v = best.hash.get(float(k), "<MISSING>")
                print("   [%d] = %s" % (k, luast_l3.brief(v) if v != "<MISSING>" else v))
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

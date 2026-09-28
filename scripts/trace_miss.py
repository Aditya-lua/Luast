#!/usr/bin/env python3
"""Log every lu_index miss (NIL result) with obj type + key."""
import sys, traceback
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3, lua_rt
from luau_recover.lua_rt import LuaError, LuaTable, NIL, Instance, type_desc

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)

interp = luast_l3.Interp()
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

orig_index = luast_l3.lu_index
misses = []
def lu_index(obj, key):
    r = orig_index(obj, key)
    if r is NIL:
        kt = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        misses.append((type_desc(obj), kt))
        if len(misses) <= 40:
            print("[INDEX-MISS] %s . %s" % (type_desc(obj), kt))
    return r
luast_l3.lu_index = lu_index

orig_call = luast_l3.Interp.call_function
def call_function(self, f, args):
    if f is NIL:
        print("[NIL CALL] last misses:", misses[-6:])
        raise LuaError("attempt to call a nil value (instrumented)")
    return orig_call(self, f, args)
luast_l3.Interp.call_function = call_function

try:
    interp.exec_chunk(root)
    print("[ok]")
except LuaError as e:
    print("LUA ERROR:", e)
except Exception:
    traceback.print_exc()

#!/usr/bin/env python3
"""Log datatype member lookups to find the function-valued leak."""
import sys
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

orig_index = luast_l3.lu_index
def dbg_index(obj, key):
    r = orig_index(obj, key)
    if luast_l3._rbs_known_type(obj):
        kt = key.decode("latin-1", "replace") if isinstance(key, bytes) else str(key)
        print("[DTYPE-INDEX] %s.%s -> %r" % (type(obj).__name__, kt, r))
    return r
luast_l3.lu_index = dbg_index

try:
    interp.exec_chunk(root)
    print("[ok]")
except LuaError as e:
    print("LUA ERROR:", e)

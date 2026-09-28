#!/usr/bin/env python3
"""At the decode crash: dump seed inputs, then brute-force the Z delta that
yields a printable decoded key (string-method name)."""
import sys
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3
from luau_recover.model import Node
from luau_recover.lua_rt import LuaError, NIL, LuaTable

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)
interp = luast_l3.Interp()
interp.set_source(src)
luast_l3.register_string_table(interp.globals_env.vars)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

# capture the decode context at the failing call
captured = {}
orig_call = luast_l3.Interp.call_function
def call2(self, f, args):
    if f is NIL and not captured:
        e = interp.chunk_env
        vals = {}
        while e is not None:
            for k, v in e.vars.items():
                if k not in vals:
                    vals[k] = v
            e = e.parent
        captured["locals"] = vals
        raise LuaError("captured")
    return orig_call(self, f, args)
luast_l3.Interp.call_function = call2

try:
    interp.exec_chunk(root)
except LuaError as e:
    if str(e) != "captured":
        print("[emu] other error:", e)
        sys.exit(1)
except Exception as e:
    print("[emu] error:", e)
    sys.exit(1)

vals = captured["locals"]
# find cipher (bytes), state, Z, modulus: locate the decode prologue vars
state = vals.get(d["var"])
print("state var value:", state)
# Z var name: same heuristic as before (first += target) -> cO
import re
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
for st in d["node"].get("body", []):
    find_z(st)
    if zvar:
        break
Z = vals.get(zvar)
print("Z:", Z)

# find cipher candidates: bytes locals with non-ascii content
ciphers = [(k, v) for k, v in vals.items() if isinstance(v, bytes) and len(v) >= 4
           and any(b > 127 or b < 9 for b in v)]
print("cipher candidates:")
for k, v in ciphers[:10]:
    print("   ", k, len(v), v[:24])

# find the round closure: search locals for LuaClosure with pure-number body
from luau_recover.lua_rt import LuaClosure
closures = [(k, v) for k, v in vals.items() if isinstance(v, LuaClosure)]
print("closures:", [k for k, _ in closures])

# modulus pool entry: B2[511] — find pool (biggest LuaTable)
import gc
pool = None
for o in gc.get_objects():
    if isinstance(o, LuaTable) and len(o.hash) > 300:
        if pool is None or len(o.hash) > len(pool.hash):
            pool = o
if pool is not None:
    print("pool size:", len(pool.hash))
    for idx in (511, 27, 135, 129):
        print("   pool[%d] = %r" % (idx, pool.get(float(idx))))

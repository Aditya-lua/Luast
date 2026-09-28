#!/usr/bin/env python3
"""Find the exact failing statement for emulator errors."""
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

orig_call = interp.call_function
def call_function(f, args):
    try:
        return orig_call(f, args)
    except LuaError as e:
        if "nil value" in str(e) or "userdata" in str(e):
            print(f"[CALL-FAIL] callee={f!r} args={[luast_l3.brief(a) for a in args][:4]}: {e}")
            raise
        raise
interp.call_function = call_function

# wrap lu_index to catch dummy arith source
orig_lu_index = luast_l3.lu_index

try:
    interp.exec_chunk(root)
    print("[ok] no error")
except LuaError as e:
    print("LUA ERROR:", e)
except Exception as e:
    traceback.print_exc()

print("steps:", interp.steps)
print("last calllog entries:")
for name, args in interp.calllog[-25:]:
    print("   ", name, args[:5])
print("warnings:", interp.warnings[-10:])

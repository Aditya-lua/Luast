#!/usr/bin/env python3
"""Brute-force the Z drift delta: try seed offsets until the decoded key is
a printable identifier-like string."""
import sys
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3
from luau_recover.lua_rt import LuaError, LuaTable, LuaClosure, NIL

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
root, errs, _ = parse(src)
d = luast_l3.find_dispatcher(root)
interp = luast_l3.Interp()
interp.set_source(src)
luast_l3.register_string_table(interp.globals_env.vars)
interp.dispatch_node = d["node"]
interp.dispatch_var = d["var"]

captured = {}
orig_call = luast_l3.Interp.call_function
def call2(self, f, args):
    if f is NIL and not captured:
        vals = {}
        e = interp.chunk_env
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
        print("[emu] error:", e); sys.exit(1)
except Exception as e:
    print("[emu] error:", e); sys.exit(1)

vals = captured["locals"]
state = float(vals[d["var"]])
Z = float(vals["cO"])

pool = None
import gc
for o in gc.get_objects():
    if isinstance(o, LuaTable) and len(o.hash) > 300:
        if pool is None or len(o.hash) > len(pool.hash):
            pool = o

# round function
rnd_closure = vals.get("cN")
steps = luast_l3.extract_round_steps(rnd_closure.node)
if steps is None:
    print("round fn not parseable"); sys.exit(1)
rnd = luast_l3.make_round(steps)
rnd = rnd[0] if isinstance(rnd, tuple) else rnd
print("round steps:", steps[:6], "... total", len(steps))

M31 = 2147483647
M31M1 = 2147483646
M32 = 4294967296.0

def decode_key(cipher, seed, n):
    aq = rnd(seed % M32) % M31M1 + 1.0
    if n == 1:
        aq = aq * 16807 % M31
        return bytes([cipher[0] ^ (int(aq) & 255)])
    # n >= 2: first advance then per-byte advance
    out = bytearray()
    aq = aq * 16807 % M31
    for i in range(min(n, 4)):
        out.append(cipher[i] ^ (int(aq) >> ((i % 4) * 8)) & 255)
        # NOTE: LCG advances BETWEEN bytes: after byte i, aq advances
        if i < min(n, 4) - 1:
            aq = aq * 16807 % M31
    return bytes(out)

# candidate ciphers: short byte strings
cands = [(k, v) for k, v in vals.items()
         if isinstance(v, bytes) and 1 <= len(v) <= 8]
seed0 = (state + Z) % M32
print("state=%d Z=%d seed0=%d" % (state, Z, seed0))

D = 4_000_000
found = []
for delta in range(-D, D + 1):
    seed = seed0 + delta
    for k, c in cands:
        n = len(c)
        key = decode_key(c, seed, n)
        if all(97 <= b <= 122 or 65 <= b <= 90 or 48 <= b <= 57 or b == 95
               for b in key):
            found.append((delta, k, key))
            print("HIT delta=%+d var=%s key=%r" % (delta, k, key))
    if len(found) >= 8:
        break
if not found:
    print("no printable key found in ±%d" % D)

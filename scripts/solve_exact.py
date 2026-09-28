#!/usr/bin/env python3
"""Exact Z-drift recovery: invert the LCG + round function from a known
plaintext decoded key.  Tells us the true Z (c8 + cO) vs emulated."""
import sys
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover import luast_l3
from luau_recover.lua_rt import LuaError, LuaTable, NIL, LuaClosure

M31 = 2147483647
M32C = 0xFFFFFFFF
M32 = 4294967296
INV16807 = pow(16807, -1, M31)

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
except LuaError:
    pass

vals = captured["locals"]
state = int(float(vals[d["var"]]))
Z = int(float(vals["cO"]))
rnd_closure = vals.get("cN")
steps = luast_l3.extract_round_steps(rnd_closure.node)
rnd_fwd = luast_l3.make_round(steps)
rnd_fwd = rnd_fwd[0] if isinstance(rnd_fwd, tuple) else rnd_fwd

import gc
pool = None
for o in gc.get_objects():
    if isinstance(o, LuaTable) and len(o.hash) > 300:
        if pool is None or len(o.hash) > len(pool.hash):
            pool = o

cipher = bytes(pool.get(442.0))
offset = int(pool.get(355.0))
mod = int(pool.get(511.0))
print("cipher=%r offset=%d decoded_len=%d" % (cipher, offset, len(cipher) - offset))
print("emulated: state=%d Z=%d seed0=%d" % (state, Z, (state + Z) % M32))

# ---- round function inverse
def _inv_rs(y, p):
    # inverse of x ^= x >> p  (fill bits from high to low)
    x = 0
    for i in range(31, -1, -1):
        hi = ((x >> (i + p)) & 1) if i + p < 32 else 0
        x |= (((y >> i) & 1) ^ hi) << i
    return x


def _inv_ls(y, p):
    # inverse of x ^= (x << p) & 0xffffffff  (fill bits from low to high)
    x = 0
    for i in range(32):
        lo = ((x >> (i - p)) & 1) if i >= p else 0
        x |= (((y >> i) & 1) ^ lo) << i
    return x


def inv_round(x):
    x = int(x) & M32C
    for kind, p in reversed(steps):
        if kind == "add":
            x = (x - p) & M32C
        elif kind == "rs":
            x = _inv_rs(x, p)
        elif kind == "ls":
            x = _inv_ls(x, p)
        elif kind == "rot":
            # inverse of lrotate(x, p) is rrotate(x, p)
            x = ((x >> p) | (x << (32 - p))) & M32C
    return x

# verify inverse
import random
for _ in range(200):
    v = random.randrange(0, 1 << 32)
    assert inv_round(rnd_fwd(v)) == v, "inverse broken"
print("round inverse verified")

# ---- solve
targets = [b"rep", b"sub", b"len", b"byte", b"char"]
seed0 = (state + Z) % M32
results = []
for target in targets:
    n = len(target)
    ct = cipher[offset:offset + n]
    if len(ct) < n:
        continue
    # keystream bytes from single 31-bit LCG state (Bc == 3 branch)
    ks = [ct[i] ^ target[i] for i in range(n)]
    AX24 = ks[0] | (ks[1] << 8) | (ks[2] << 16)
    for h in range(128):
        AX = AX24 | (h << 24)
        if AX >= M31:
            continue
        aq = AX * INV16807 % M31
        if not (1 <= aq <= 2147483646):
            continue
        for k in (0, 1):
            rv = aq - 1 + k * 2147483646
            if rv >= M32:
                continue
            seed = inv_round(rv)
            zt = (seed - state) % M32
            drift = zt - Z
            if abs(drift) < 100_000_000:
                results.append((abs(drift), target, drift, zt))

results.sort()
for _, target, drift, zt in results[:6]:
    print("target=%r drift=%+d  Z_true=%d" % (target, drift, zt))

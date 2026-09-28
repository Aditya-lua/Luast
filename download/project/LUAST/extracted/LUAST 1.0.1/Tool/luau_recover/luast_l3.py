#!/usr/bin/env python3
"""LUAST v1.0.x level-3 deobfuscator.

Statically executes the obfuscated chunk with the mini-Luau interpreter
(lua_rt.py), resolving the dispatcher state machine, constant-pool
permutations, opaque predicates, Z-accumulator transitions and the
keystream payload decoder.  Produces:
  * a recovered .luau file (the program's real observable behaviour)
  * a full emulation report (state trace, Z trace, pool mutations)

Usage:
  python3 luast3_deobf.py <input.luau> [-o out.luau] [--report out.txt]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

from . import lua_rt as _luart
from .lua_rt import (  # noqa: E402
    NIL, LuaTable, LuaError, ContinueSignal, BreakSignal, ReturnSignal,
    Instance, Buffer, V2Value, Vec3, ColorVal, VecLib, EnumItem, EnumType,
    EnumRoot, PyFunc, LuaClosure, Userdata, RobloxDummy, NilChain,
    FuncLikeDummy, truthy, first, fmt_num, lua_tostr, typeof, type_desc,
    need_num, MUTLOG, build_globals,
)
from .parser import parse  # noqa: E402
from .model import Node  # noqa: E402
from . import roblox_shims as _rbs  # f32-exact Roblox datatypes  # noqa: E402

_RBS_TYPES = None


def _rbs_known_type(v):
    """True if v is one of the f32-exact roblox_shims datatypes."""
    global _RBS_TYPES
    if _RBS_TYPES is None:
        _RBS_TYPES = (_rbs.Vector3, _rbs.Vector2, _rbs.UDim, _rbs.UDim2,
                      _rbs.Color3, _rbs.CFrame, _rbs.NumberRange,
                      _rbs.NumberSequenceKeypoint, _rbs.ColorSequenceKeypoint,
                      _rbs.NumberSequence, _rbs.ColorSequence, _rbs.Rect,
                      _rbs.Ray, _rbs.Faces, _rbs.Axes, _rbs.TweenInfo,
                      _rbs.Random)
    return isinstance(v, _RBS_TYPES)

MAX_LOOP_ITERS = 200000
MAX_TOTAL_STEPS = 2000000
DEADLINE_SECONDS = float(os.environ.get("LUAST_L3_SECONDS", "90"))
_DEADLINE = None


class Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def find(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return e
            e = e.parent
        return None


class Interp:
    def __init__(self):
        self.services = {}
        self.globals_env = Env(None)
        self.globals_env.vars = build_globals(self)
        self._src_lines = None   # newline offsets for debug.info 'l' lookups
        self.outputs = []          # captured print payloads (bytes)
        self.calllog = []          # (name, brief-args) for every builtin/closure call
        self.steps = 0
        self.dispatch_node = None  # the dispatcher `while` AST node
        self.state_hook = None     # fn(state_value)
        self.current_state = None
        self.unknown_globals = set()
        self.warnings = []
        # L3 decode observations (for the seed-recovery fallback)
        self.obs_fromstring = []   # buffer.fromstring args (bytes)
        self.obs_readstring = []   # (offset, n) args of buffer.readstring
        self.obs_round_calls = []  # (closure_node, arg, result) single-number closure calls
        # self-healing: Z-drift correction applied to (state + Z) seeds
        self.z_correction = 0.0
        self.z_var_name = None
        self.last_seed_obs = None  # (state_val, z_val, raw_result) pre-correction
        self.byte_obs = []         # raw bytes seen by string.byte (cipher capture)

    def set_source(self, src: str):
        """Record source newline offsets so debug.info(f,'l') can report
        1-based LINE numbers exactly like the real Luau VM."""
        import bisect
        self._src_lines = [i for i, ch in enumerate(src) if ch == "\n"]

    def line_of(self, pos: int) -> float:
        if self._src_lines is None:
            return -1.0
        import bisect
        return float(bisect.bisect_right(self._src_lines, pos) + 1)

    # ------------------------------------------------------------- calling

    def call_function(self, f, args):
        self.steps += 1
        if self.steps > MAX_TOTAL_STEPS:
            raise LuaError("emulation step limit exceeded")
        if isinstance(f, VecLib):
            # vector(x, y, z) direct call -> f32-exact vector
            self.calllog.append(("vector", [brief(a) for a in args]))
            xs = []
            for i in range(3):
                xs.append(need_num(args[i]) if i < len(args) else 0.0)
            return [_rbs.Vector3(xs[0], xs[1], xs[2])]
        if isinstance(f, RobloxDummy):
            # calling a decoy Roblox global yields another dummy value
            self.calllog.append((f.name, [brief(a) for a in args]))
            return [f]
        if isinstance(f, PyFunc):
            self.calllog.append((f.name, [brief(a) for a in args]))
            if f.name == "string.byte" and args and isinstance(args[0], bytes) \
                    and 4 <= len(args[0]) <= 64:
                self.byte_obs.append(args[0])
            if f.name == "buffer.fromstring" and args and isinstance(args[0], bytes):
                self.obs_fromstring.append(args[0])
            if f.name == "buffer.readstring" and len(args) >= 3:
                self.obs_readstring.append((args[1], args[2]))
            r = f.fn(*args)
            if r is None:
                return []
            if isinstance(r, list):
                return list(r)
            return [r]
        if isinstance(f, LuaClosure):
            self.calllog.append(("<closure:%d>" % id(f.node),
                                 [brief(a) for a in args]))
            if len(args) == 1 and isinstance(args[0], float):
                env_probe = Env(f.env)
                params = f.params or []
                for i, p in enumerate(params):
                    env_probe.vars[p] = args[i] if i < len(args) else NIL
                try:
                    self.exec_stmts(f.node.get("body", []), env_probe)
                except ReturnSignal as r:
                    if len(r.values) == 1 and isinstance(r.values[0], float):
                        self.obs_round_calls.append(
                            (f.node, args[0], r.values[0], self.current_state))
                    return r.values
                except (BreakSignal, ContinueSignal):
                    return []
                return []
            env = Env(f.env)
            params = f.params or []
            for i, p in enumerate(params):
                env.vars[p] = args[i] if i < len(args) else NIL
            if f.vararg:
                env.vars["..."] = args[len(params):]
            try:
                self.exec_stmts(f.node.get("body", []), env)
            except ReturnSignal as r:
                return r.values
            except BreakSignal:
                return []
            except ContinueSignal:
                return []
            return []
        raise LuaError("attempt to call a %s value" % type_desc(f))

    def eval_list(self, values, env, want=None):
        out = []
        n = len(values)
        for i, e in enumerate(values):
            v = self.eval(e, env)
            if i == n - 1 and isinstance(v, list):
                out.extend(v)
            else:
                out.append(first(v))
        if want is not None:
            while len(out) < want:
                out.append(NIL)
            if want < len(out):
                out = out[:want]
        return out

    # ---------------------------------------------------------- statements

    def exec_stmts(self, stmts, env):
        for st in stmts:
            self.exec_stmt(st, env)

    def exec_stmt(self, st, env):
        self.steps += 1
        if self.steps > MAX_TOTAL_STEPS:
            raise LuaError("emulation step limit exceeded")
        k = st.kind
        if k == "local":
            names = st.get("names", [])
            vals = self.eval_list(st.get("values", []), env, want=len(names))
            for i, n in enumerate(names):
                env.vars[n] = vals[i]
        elif k == "assign":
            op = st.get("op", "=")
            targets = st.get("targets", [])
            values = st.get("values", [])
            if op == "=":
                addrs = [self.target_addr(t, env) for t in targets]
                vals = self.eval_list(values, env, want=len(addrs))
                for addr, v in zip(addrs, vals):
                    self.assign(addr, v)
            else:
                addr = self.target_addr(targets[0], env)
                cur = self.read_addr(addr)
                rhs = first(self.eval(values[0], env))
                self.assign(addr, arith(op[:-1], cur, rhs))
        elif k == "if":
            if truthy(first(self.eval(st.get("cond"), env))):
                self.exec_stmts(st.get("then", []), Env(env))
            else:
                done = False
                for c, body in st.get("elifs", []):
                    if truthy(first(self.eval(c, env))):
                        self.exec_stmts(body, Env(env))
                        done = True
                        break
                if not done and st.get("else_"):
                    self.exec_stmts(st.get("else_", []), Env(env))
        elif k == "while":
            cond = st.get("cond")
            body = st.get("body", [])
            if st is self.dispatch_node and body:
                # dispatcher loop: body[0] is the state transform
                it = 0
                while truthy(first(self.eval(cond, env))):
                    it += 1
                    if it > MAX_LOOP_ITERS:
                        raise LuaError("dispatcher iteration limit exceeded")
                    self.exec_stmt(body[0], env)
                    self.current_state = first(env.find(
                        self.dispatch_var).vars[self.dispatch_var])
                    if self.state_hook:
                        self.state_hook(self.current_state)
                    try:
                        self.exec_stmts(body[1:], Env(env))
                    except ContinueSignal:
                        continue
                    except BreakSignal:
                        break
            else:
                it = 0
                while truthy(first(self.eval(cond, env))):
                    it += 1
                    if it > MAX_LOOP_ITERS:
                        raise LuaError("while loop iteration limit exceeded")
                    try:
                        self.exec_stmts(body, Env(env))
                    except ContinueSignal:
                        continue
                    except BreakSignal:
                        break
        elif k == "repeat":
            body = st.get("body", [])
            env_r = Env(env)
            while True:
                try:
                    self.exec_stmts(body, Env(env_r))
                except ContinueSignal:
                    pass
                except BreakSignal:
                    break
                if truthy(first(self.eval(st.get("cond"), env_r))):
                    break
        elif k == "fornum":
            var = st.get("var")
            start = need_num(first(self.eval(st.get("start_expr"), env)))
            limit = need_num(first(self.eval(st.get("limit"), env)))
            step = need_num(first(self.eval(st["step"], env))) if st.get("step") else 1.0
            env_f = Env(env)
            i = start
            while (step > 0 and i <= limit) or (step < 0 and i >= limit):
                env_f.vars[var] = i
                try:
                    self.exec_stmts(st.get("body", []), Env(env_f))
                except ContinueSignal:
                    pass
                except BreakSignal:
                    break
                i += step
        elif k == "forin":
            names = st.get("names") or st.get("vars") or []
            vals = self.eval_list(st.get("iterators", []), env)
            env_f = Env(env)
            if len(vals) >= 4 and vals[0] == "__pyiter__":
                # Python-side iterator protocol: [marker, fn, state, ctrl]
                _mk, ffn, fstate, fctrl = vals[0], vals[1], vals[2], vals[3]
                ctrl = fctrl
                iters = 0
                while True:
                    iters += 1
                    if iters > MAX_LOOP_ITERS:
                        raise LuaError("forin iteration limit exceeded")
                    row = ffn(fstate, ctrl)
                    if not row:
                        break
                    ctrl = row[0]
                    for vi, nm in enumerate(names):
                        env_f.vars[nm] = row[vi] if vi < len(row) else NIL
                    try:
                        self.exec_stmts(st.get("body", []), Env(env_f))
                    except ContinueSignal:
                        pass
                    except BreakSignal:
                        break
            else:
                f = vals[0] if vals else NIL
                state = vals[1] if len(vals) > 1 else NIL
                ctrl = vals[2] if len(vals) > 2 else NIL
                iters = 0
                while True:
                    iters += 1
                    if iters > MAX_LOOP_ITERS:
                        raise LuaError("forin iteration limit exceeded")
                    row = self.call_function(f, [state, ctrl])
                    first_v = row[0] if row else NIL
                    if first_v is NIL or first_v is None:
                        break
                    ctrl = first_v
                    for vi, nm in enumerate(names):
                        env_f.vars[nm] = row[vi] if vi < len(row) else NIL
                    try:
                        self.exec_stmts(st.get("body", []), Env(env_f))
                    except ContinueSignal:
                        pass
                    except BreakSignal:
                        break
        elif k == "do":
            self.exec_stmts(st.get("body", []), Env(env))
        elif k == "return":
            raise ReturnSignal(self.eval_list(st.get("values", []), env))
        elif k == "break":
            raise BreakSignal()
        elif k == "continue":
            raise ContinueSignal()
        elif k == "localfunc":
            env.vars[st.get("name")] = NIL
            env.vars[st.get("name")] = LuaClosure(st, env)
        elif k == "funcdef":
            target = st.get("target")
            closure = LuaClosure(st, env)
            if isinstance(target, list):
                raise LuaError("dotted funcdef not supported")
            env.vars[target] = closure
        elif k == "call" or k == "methodcall":
            self.eval(st, env)
        else:
            raise LuaError("unsupported statement kind '%s'" % k)

    # --------------------------------------------------------- expressions

    def eval(self, e, env):
        self.steps += 1
        if self.steps > MAX_TOTAL_STEPS:
            raise LuaError("emulation step limit exceeded")
        if _DEADLINE is not None and (self.steps & 8191) == 0 \
                and time.monotonic() > _DEADLINE:
            raise LuaError("emulation time budget exceeded")
        k = e.kind
        if k == "paren":
            return self.eval(e.get("expr"), env)
        if k == "number":
            v = e.get("value")
            return float(v)
        if k == "string":
            return e.get("value")
        if k == "bool":
            return e.get("value")
        if k == "nil":
            return NIL
        if k == "vararg":
            va = None
            scope = env
            while scope is not None:
                if "..." in scope.vars:
                    va = scope.vars["..."]
                    break
                scope = scope.parent
            return list(va) if va else []
        if k == "name":
            name = e.get("name")
            holder = env.find(name)
            if holder is not None:
                return holder.vars[name]
            if name in self.globals_env.vars:
                return self.globals_env.vars[name]
            if name not in self.unknown_globals:
                self.unknown_globals.add(name)
                self.warnings.append(
                    "unknown global read: %s (-> falsy chainable)" % name)
            # Unknown globals are nil on Roblox (falsy) — but decoy code may
            # still index/call them with pool keys, so hand out a NilChain
            # instead of NIL to keep emulation alive.
            return NilChain(name)
        if k == "index":
            obj = first(self.eval(e.get("obj"), env))
            key = first(self.eval(e.get("key"), env))
            return lu_index(obj, key)
        if k == "indexname":
            obj = first(self.eval(e.get("obj"), env))
            return lu_index(obj, e.get("name").encode())
        if k == "call":
            f = first(self.eval(e.get("func"), env))
            args = self.eval_list(e.get("args", []), env)
            return self.call_function(f, args)
        if k == "methodcall":
            obj = first(self.eval(e.get("obj"), env))
            meth = lu_index(obj, e.get("method").encode())
            args = self.eval_list(e.get("args", []), env)
            return self.call_function(meth, [obj] + args)
        if k == "binop":
            op = e.get("op")
            # seed-pattern detection: (state + Z) — the keystream seed.  When
            # self-healing discovered a Z correction, apply it exactly here.
            if op == "+" and self.dispatch_var and self.z_var_name:
                le, re_ = e.get("left"), e.get("right")
                if le is not None and le.kind == "name" \
                        and re_ is not None and re_.kind == "name":
                    ln, rn = le.get("name"), re_.get("name")
                    if {ln, rn} == {self.dispatch_var, self.z_var_name}:
                        a = first(self.eval(le, env))
                        b = first(self.eval(re_, env))
                        try:
                            raw = arith("+", a, b)
                            self.last_seed_obs = (a, b, raw)
                        except LuaError:
                            raw = None
                        if self.z_correction:
                            try:
                                return arith("+", a, b) + self.z_correction
                            except LuaError:
                                pass
                        return raw if raw is not None else binop("+", a, b)
            left = first(self.eval(e.get("left"), env))
            if op == "and":
                return left if not truthy(left) else first(self.eval(e.get("right"), env))
            if op == "or":
                return left if truthy(left) else first(self.eval(e.get("right"), env))
            right = first(self.eval(e.get("right"), env))
            return binop(op, left, right)
        if k == "unop":
            op = e.get("op")
            v = first(self.eval(e.get("expr"), env))
            if op == "-":
                return -need_num(v, "unary minus")
            if op == "not":
                return not truthy(v)
            if op == "#":
                if isinstance(v, bytes):
                    return float(len(v))
                if isinstance(v, LuaTable):
                    return v.length()
                raise LuaError("attempt to get length of a %s value" % type_desc(v))
            raise LuaError("unsupported unop '%s'" % op)
        if k == "ifexpr":
            if truthy(first(self.eval(e.get("cond"), env))):
                return first(self.eval(e.get("then"), env))
            for c, body in e.get("elifs", []):
                if truthy(first(self.eval(c, env))):
                    return first(self.eval(body, env))
            return first(self.eval(e.get("else_"), env))
        if k == "function":
            return LuaClosure(e, env)
        if k == "table":
            t = LuaTable()
            idx = 1
            for key, value in e.get("items", []):
                v = first(self.eval(value, env))
                if key is None:
                    t.set(float(idx), v)
                    idx += 1
                elif key.kind == "namekey":
                    t.set(key.get("name").encode(), v)
                elif key.kind == "indexkey":
                    t.set(first(self.eval(key.get("key"), env)), v)
                else:
                    raise LuaError("bad table key kind %s" % key.kind)
            return t
        raise LuaError("unsupported expression kind '%s'" % k)

    # ------------------------------------------------------ assign support

    def target_addr(self, t, env):
        if t.kind == "name":
            return ("name", t.get("name"), env)
        if t.kind == "indexname":
            obj = first(self.eval(t.get("obj"), env))
            return ("idx", obj, t.get("name").encode())
        if t.kind == "index":
            obj = first(self.eval(t.get("obj"), env))
            key = first(self.eval(t.get("key"), env))
            return ("idx", obj, key)
        raise LuaError("unsupported assignment target '%s'" % t.kind)

    def read_addr(self, addr):
        if addr[0] == "name":
            _, name, env = addr
            holder = env.find(name)
            if holder is not None:
                return holder.vars[name]
            if name in self.globals_env.vars:
                return self.globals_env.vars[name]
            return NIL
        _, obj, key = addr
        return lu_index(obj, key)

    def assign(self, addr, v):
        if addr[0] == "name":
            _, name, env = addr
            holder = env.find(name)
            if holder is not None:
                holder.vars[name] = v
            else:
                self.globals_env.vars[name] = v
        else:
            _, obj, key = addr
            lu_setindex(obj, key, v)

    # ------------------------------------------------------------- chunk

    def exec_chunk(self, root):
        env = Env(self.globals_env)
        self.chunk_env = env
        self.exec_stmts(root.get("body", []), env)
        return env


# ------------------------------------------------------------- operations

def lu_index(obj, key):
    if isinstance(key, (int, float)) and not isinstance(key, bool):
        key = float(key)
    if isinstance(obj, LuaTable):
        v = obj.get(key)
        if v is NIL and getattr(obj, "lib", False) and isinstance(key, bytes):
            # standard-library decoy member (e.g. coroutine.flags):
            # function-like dummy keeps emulation alive
            return FuncLikeDummy("%s.%s" % (obj.__repr__().split(':')[0], key.decode("latin-1")))
        return v
    if isinstance(obj, bytes):
        if isinstance(key, bytes):
            return string_lib_lookup(key)
        return NIL
    if isinstance(obj, Instance):
        v = obj.props.get(key, NIL)
        if v is NIL and isinstance(key, bytes):
            # missing instance method (e.g. decoy GetChildren call):
            # function-like dummy keeps emulation alive
            return FuncLikeDummy("%s.%s" % (obj.cls, key.decode("latin-1")))
        return v
    if isinstance(obj, V2Value):
        if key == b"X":
            return obj.x
        if key == b"Y":
            return obj.y
        return NIL
    if isinstance(obj, Vec3):
        if key in (b"X", b"x"):
            return obj.x
        if key in (b"Y", b"y"):
            return obj.y
        if key in (b"Z", b"z"):
            return obj.z
        if key == b"Magnitude":
            return math.sqrt(obj.x * obj.x + obj.y * obj.y + obj.z * obj.z)
        if key == b"Unit":
            m = math.sqrt(obj.x * obj.x + obj.y * obj.y + obj.z * obj.z) or 1.0
            return Vec3(obj.x / m, obj.y / m, obj.z / m)
        return NIL
    if isinstance(obj, ColorVal):
        if key in (b"R", b"r"):
            return obj.r
        if key in (b"G", b"g"):
            return obj.g
        if key in (b"B", b"b"):
            return obj.b
        return NIL
    if isinstance(obj, VecLib):
        # vector.create / .floor / .ceil / ... -> f32-exact member table
        tbl = getattr(obj, "members", None)
        if tbl is not None:
            v = tbl.get(key)
            if v is not NIL:
                return v
        return PyFunc(lambda x=0.0, y=0.0, z=0.0: _rbs.Vector3(x, y, z),
                      "vector.create")
    # f32-exact datatype property access (Vector3/Vector2/CFrame/Color3/...)
    found, dval = _rbs.index_datatype(obj, key)
    if found:
        return dval
    if _rbs_known_type(obj):
        # method access on a datatype (obj:Dot(...), obj:Lerp(...), ...)
        if isinstance(key, bytes):
            mname = key
            return PyFunc(
                lambda *a, _o=obj, _n=mname: _rbs.call_datatype_method(_o, _n, list(a))[1],
                "method.%s" % key.decode("latin-1", "ignore"))
        return NIL
    if isinstance(obj, EnumRoot):
        return obj.types.get(key.decode() if isinstance(key, bytes) else key, NIL)
    if isinstance(obj, EnumType):
        if isinstance(key, bytes):
            name = key.decode()
            return obj.items.get(name, NIL)
        return NIL
    if isinstance(obj, EnumItem):
        if key == b"Name":
            return obj.name.encode()
        if key == b"Value":
            return obj.value
        if key == b"EnumType":
            return obj.etype.encode()
        return NIL
    if isinstance(obj, RobloxDummy):
        return obj  # chainable dummy: .new / .fromName / ... -> dummy
    if obj is NIL:
        raise LuaError("attempt to index nil with '%s'"
                       % (key.decode("latin-1") if isinstance(key, bytes) else str(key)))
    raise LuaError("attempt to index a %s value" % type_desc(obj))


def lu_setindex(obj, key, v):
    if isinstance(key, (int, float)) and not isinstance(key, bool):
        key = float(key)
    if isinstance(obj, LuaTable):
        obj.set(key, v)
        return
    if obj is NIL:
        raise LuaError("attempt to assign field of nil")
    raise LuaError("attempt to assign field of a %s value" % type_desc(obj))


def arith(op, a, b):
    # f32-exact datatype arithmetic (Vector3/Vector2/int16 wrap) first
    rv = _rbs.arith_vector(op, a, b)
    if rv is not None:
        return rv
    if isinstance(a, _rbs.CFrame) or isinstance(b, _rbs.CFrame):
        if op == "*":
            return _rbs.cframe_mul_components(a, b)
        raise LuaError("unsupported CFrame op '%s'" % op)
    if isinstance(a, V2Value) or isinstance(b, V2Value):
        # Vector2int16 arithmetic (componentwise); scalar operand allowed
        ax, ay = (a.x, a.y) if isinstance(a, V2Value) else (need_num(a), need_num(a))
        bx, by = (b.x, b.y) if isinstance(b, V2Value) else (need_num(b), need_num(b))
        if op == "+":
            return V2Value(ax + bx, ay + by)
        if op == "-":
            return V2Value(ax - bx, ay - by)
        if op == "*":
            return V2Value(ax * bx, ay * by)
        if op == "/":
            def _d(p, q):
                try:
                    return p / q
                except ZeroDivisionError:
                    if p == 0 or p != p:
                        return float("nan")
                    return math.copysign(float("inf"), p) * math.copysign(1.0, q)
            return V2Value(_d(ax, bx), _d(ay, by))
        raise LuaError("unsupported vector op '%s'" % op)
    if isinstance(a, Vec3) or isinstance(b, Vec3):
        # vector arithmetic (componentwise); scalar operand allowed
        ax, ay, az = (a.x, a.y, a.z) if isinstance(a, Vec3) else (
            need_num(a), need_num(a), need_num(a))
        bx, by, bz = (b.x, b.y, b.z) if isinstance(b, Vec3) else (
            need_num(b), need_num(b), need_num(b))
        if op == "+":
            return Vec3(ax + bx, ay + by, az + bz)
        if op == "-":
            return Vec3(ax - bx, ay - by, az - bz)
        if op == "*":
            return Vec3(ax * bx, ay * by, az * bz)
        if op == "/":
            def _d(p, q):
                try:
                    return p / q
                except ZeroDivisionError:
                    if p == 0 or p != p:
                        return float("nan")
                    return math.copysign(float("inf"), p) * math.copysign(1.0, q)
            return Vec3(_d(ax, bx), _d(ay, by), _d(az, bz))
        raise LuaError("unsupported vector op '%s'" % op)
    if op == "..":
        return lua_tostr(a) + lua_tostr(b)
    x = need_num(a)
    y = need_num(b)
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if op == "/":
        # Luau: 1/0 = inf, -1/0 = -inf, 0/0 = nan (IEEE754)
        try:
            return x / y
        except ZeroDivisionError:
            if x == 0 or x != x:
                return float("nan")
            return math.copysign(float("inf"), x) * math.copysign(1.0, y)
    if op == "%":
        # Luau: x % 0 = nan; python % == lua % for our domain (sign of divisor)
        if y == 0:
            return float("nan")
        return x % y
    if op == "^":
        try:
            return float(math.pow(x, y))
        except (ValueError, OverflowError):
            # 0^negative -> inf; overflow -> inf (Luau/IEEE754)
            if x == 0:
                return float("inf")
            return float("inf")
    raise LuaError("unsupported arith op '%s'" % op)


def binop(op, a, b):
    if op in ("+", "-", "*", "/", "%", "^", ".."):
        return arith(op, a, b)
    if op == "==":
        return lu_eq(a, b)
    if op == "~=":
        return not lu_eq(a, b)
    if op in ("<", "<=", ">", ">="):
        return lu_cmp(op, a, b)
    raise LuaError("unsupported binop '%s'" % op)


def lu_eq(a, b):
    if a is NIL or b is NIL:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (float, int)) and isinstance(b, (float, int)):
        return float(a) == float(b)
    if isinstance(a, bytes) and isinstance(b, bytes):
        return a == b
    return a is b


def lu_cmp(op, a, b):
    if isinstance(a, bytes) and isinstance(b, bytes):
        pass
    elif isinstance(a, (float, int)) and isinstance(b, (float, int)):
        a = float(a)
        b = float(b)
    else:
        raise LuaError("attempt to compare %s with %s" % (type_desc(a), type_desc(b)))
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def brief(v):
    if v is NIL:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return fmt_num(v)
    if isinstance(v, bytes):
        if len(v) > 40:
            return repr(v[:40].decode("latin-1")) + "..(%d)" % len(v)
        return repr(v.decode("latin-1"))
    if isinstance(v, (LuaTable, Instance, Buffer, PyFunc, LuaClosure,
                      EnumItem, EnumType, EnumRoot, V2Value, Userdata)):
        return "%s<%s>" % (typeof(v), repr(v))
    return str(v)


# patch: string lib lookup for indexing on strings
_string_tbl = {}


def string_lib_lookup(key: bytes):
    if not _string_tbl:
        return NIL
    return _string_tbl.get(key, NIL)


def register_string_table(g):
    _string_tbl.clear()
    _string_tbl.update(g["string"].hash)


# ---------------------------------------------------------------- driver

def find_dispatcher(root):
    """Locate `while true do <var> = K - <var>; do <tree> end end`."""
    body = root.get("body", [])
    if not body or body[-1].kind != "while":
        return None
    wh = body[-1]
    bstmts = wh.get("body", [])
    if not bstmts:
        return None
    t0 = bstmts[0]
    if t0.kind != "assign" or t0.get("op", "=") != "=":
        return None
    targets = t0.get("targets", [])
    values = t0.get("values", [])
    if len(targets) != 1 or targets[0].kind != "name" or len(values) != 1:
        return None
    v = values[0]
    if v.kind != "binop" or v.get("op") != "-":
        return None
    left, right = v.get("left"), v.get("right")
    if left.kind != "number" or right.kind != "name":
        return None
    var = targets[0].get("name")
    if right.get("name") != var:
        return None
    k = float(left.get("value"))
    # initial value: last top-level assignment `<var> = <number>`
    init = None
    for st in reversed(body[:-1]):
        if st.kind == "assign":
            ts = st.get("targets", [])
            vs = st.get("values", [])
            if len(ts) == 1 and ts[0].kind == "name" and ts[0].get("name") == var \
                    and len(vs) == 1 and vs[0].kind == "number":
                init = float(vs[0].get("value"))
                break
    return {"node": wh, "var": var, "K": k, "init": init}


# ------------------------------------------------- L3 seed-recovery fallback

def extract_round_steps(fn_node):
    """Pattern-match a LUAST L3 round function (pure mix of add/bxor-shift/
    rotate on one variable) into invertible steps. Returns list or None."""
    M32C = 0xFFFFFFFF
    steps = []
    body = fn_node.get("body", [])
    for st in body:
        if st.kind == "return":
            break
        if st.kind != "assign" or st.get("op", "=") != "=":
            return None
        tgts = st.get("targets", [])
        vals = st.get("values", [])
        if len(tgts) != 1 or tgts[0].kind != "name" or len(vals) != 1:
            return None
        v = vals[0]
        if v.kind == "paren":
            v = v.get("expr")
        if v.kind == "binop" and v.get("op") == "%":
            left, right = v.get("left"), v.get("right")
            if left.kind == "paren":
                left = left.get("expr")
            if right.kind != "number" or int(right.get("value")) != 4294967296:
                return None
            if left.kind == "binop" and left.get("op") == "+":
                a, b = left.get("left"), left.get("right")
                if a.kind == "name" and b.kind == "number":
                    steps.append(("add", int(b.get("value")) & M32C))
                    continue
            return None
        if v.kind == "call":
            func = v.get("func")
            args = v.get("args", [])
            if func.kind != "indexname" or func.get("obj").get("name") != "bit32":
                return None
            op = func.get("name")
            if op == "lrotate":
                if len(args) == 2 and args[0].kind == "name" and args[1].kind == "number":
                    steps.append(("rot", int(args[1].get("value"))))
                    continue
                return None
            if op == "bxor" and len(args) == 2 and args[0].kind == "name":
                inner = args[1]
                if inner.kind == "call":
                    inf = inner.get("func")
                    if inf.kind == "indexname" and inf.get("obj").get("name") == "bit32" \
                            and inf.get("name") in ("rshift", "lshift") and len(inner.get("args", [])) == 2 \
                            and inner.get("args", [])[0].kind == "name" and inner.get("args", [])[1].kind == "number":
                        kind = "rs" if inf.get("name") == "rshift" else "ls"
                        steps.append((kind, int(inner.get("args", [])[1].get("value"))))
                        continue
                return None
            return None
        return None
    return steps or None


def make_round(steps):
    M32C = 0xFFFFFFFF

    def aa(x):
        x = int(x) & M32C
        for kind, p in steps:
            if kind == "add":
                x = (x + p) & M32C
            elif kind == "rs":
                x = (x ^ (x >> p)) & M32C
            elif kind == "ls":
                x = (x ^ ((x << p) & M32C)) & M32C
            else:
                p %= 32
                x = ((x << p) | (x >> (32 - p))) & M32C if p else x
        return x

    def _bx_rs_inv(y, n):
        x = 0
        for i in range(31, -1, -1):
            bit = (y >> i) & 1
            if i + n <= 31:
                bit ^= (x >> (i + n)) & 1
            x |= bit << i
        return x

    def _bx_ls_inv(y, n):
        x = 0
        for i in range(32):
            bit = (y >> i) & 1
            if i - n >= 0:
                bit ^= (x >> (i - n)) & 1
            x |= bit << i
        return x

    def aa_inv(y):
        y = int(y) & M32C
        for kind, p in reversed(steps):
            if kind == "add":
                y = (y - p) & M32C
            elif kind == "rs":
                y = _bx_rs_inv(y, p)
            elif kind == "ls":
                y = _bx_ls_inv(y, p)
            else:
                p %= 32
                y = ((y >> p) | (y << (32 - p))) & M32C if p else y
        return y

    return aa, aa_inv


COMMON_WORDS = set('''
the be to of and a in that have i it for not on with he as you do at this but his by from they we say her she or an will my one all would there their what so up out if about who get which go me when make can like time no just him know take people into year your good some could them see other than then now look only come its over think also back after use two how our work first well way even new want because any these give day most us is are was were been has had did having may should could shall
hello world welcome thanks thank you congrats congrats agent game games roblox script scripts hub key system free download discord invite join server link get loadstring require module source code obfuscated deobfuscated luast clv cloud level demo test message protected crack cracked bypass bypassed please subscribe like share comment channel video tutorial part update version premium paid unlock found bug fix fixed press run play enjoy funny meme lol owner admin mod moderator player players account cookie token session telegram twitter youtube github website http https www com net org
money cash dollar rich wealthy success winner winning win prize reward gift freebies promo code coupon discount sale offer limited offer exclusive early access beta alpha release stable final build
start stop begin end finish complete done ready set go wait hold pause resume continue break exit quit close open read write send receive call function return local global variable constant number string table array index key value pair loop while for repeat until then do else elseif end true false nil and or not
agent spy mission target objective operation secret classified confidential top hq headquarters base camp team squad unit member member crew guild clan group party lobby match round round stage level score points health damage weapon gun knife kill death respawn spawn map location position vector coordinate
'''.split())


def _word_coverage(payload):
    try:
        s = payload.decode("ascii")
    except Exception:
        return 0.0
    import re as _re
    words = _re.findall(r"[A-Za-z]{2,}", s)
    if not words:
        return 0.0
    hits = sum(1 for w in words if w.lower() in COMMON_WORDS)
    return hits / len(words)


def recover_payload(cipher, ap, round_steps, ae_state, verbose=True, z_shim=None):
    """Constrained seed recovery: find aq0 (and ak) such that the standard
    LUAST-L3 keystream decrypts `cipher` to printable text. Returns list of
    (payload, ak, aq0) candidates sorted by English-likeness."""
    M32C = 0xFFFFFFFF
    M31 = 2147483647
    aa, aa_inv = make_round(round_steps)
    if ap <= 0 or ap > len(cipher):
        ap = len(cipher)
    c = cipher[:ap]
    c_le1 = int.from_bytes(c[0:4], "little") if ap >= 4 else int.from_bytes(c.ljust(4, b"\\0")[:4], "little")
    inv = pow(16807, -1, M31)
    alpha = [b for b in range(32, 127)]
    out = []

    def full_decode(aq0):
        aq = aq0
        buf = bytearray(c)
        at = 0
        while at <= ap - 4:
            aq = aq * 16807 % M31
            chunk = int.from_bytes(buf[at:at + 4], "little")
            buf[at:at + 4] = (chunk ^ (aq & M32C)).to_bytes(4, "little")
            at += 4
        aq = aq * 16807 % M31
        au = 0
        while at < ap:
            buf[at] ^= (aq >> (au * 8)) & 255
            at += 1
            au += 1
        return bytes(buf)

    # iterate printable first-4-byte plaintexts
    try:
        import numpy as np
        bases = np.uint64([95 ** i for i in range(4)])
        PM = np.zeros(256, dtype=bool)
        for b in range(32, 127):
            PM[b] = True
        for b in (9, 10, 13):
            PM[b] = True
        n = 95 ** 4
        CH = 4_000_000
        for start in range(0, n, CH):
            idx = np.arange(start, min(start + CH, n), dtype=np.uint64)
            p = np.zeros(len(idx), dtype=np.uint64)
            for i in range(4):
                p |= ((idx // bases[i]) % np.uint64(95)) << np.uint64(8 * i)
            k1 = np.uint64(c_le1) ^ p
            aq0 = (k1 * np.uint64(inv)) % np.uint64(M31)
            k2 = (k1 * np.uint64(16807)) % np.uint64(M31)
            if ap < 8:
                for a in aq0:
                    out.append((full_decode(int(a)), None, int(a)))
                continue
            p2 = np.uint64(int.from_bytes(c[4:8], "little")) ^ k2
            m = (PM[(p2 & np.uint64(0xFF)).astype(np.int64)]
                 & PM[((p2 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)]
                 & PM[((p2 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)]
                 & PM[((p2 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)])
            if not m.any():
                continue
            k3 = (k2[m] * np.uint64(16807)) % np.uint64(M31)
            if ap < 12:
                for a in aq0[m]:
                    out.append((full_decode(int(a)), None, int(a)))
                continue
            p3 = np.uint64(int.from_bytes(c[8:12], "little")) ^ k3
            m2 = (PM[(p3 & np.uint64(0xFF)).astype(np.int64)]
                  & PM[((p3 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)]
                  & PM[((p3 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)]
                  & PM[((p3 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)])
            if not m2.any():
                continue
            aq0m = aq0[m][m2]
            k4 = (k3[m2] * np.uint64(16807)) % np.uint64(M31)
            if ap < 13:
                for a in aq0m:
                    out.append((full_decode(int(a)), None, int(a)))
                continue
            b12 = (np.uint64(c[12]) ^ (k4 & np.uint64(0xFF))).astype(np.int64)
            m3 = PM[b12]
            if ap < 14:
                for a in aq0m[m3]:
                    out.append((full_decode(int(a)), None, int(a)))
                continue
            b13 = (np.uint64(c[13]) ^ ((k4 >> np.uint64(8)) & np.uint64(0xFF))).astype(np.int64)
            if ap < 15:
                for a in aq0m[m3]:
                    out.append((full_decode(int(a)), None, int(a)))
                continue
            b14 = (np.uint64(c[14]) ^ ((k4 >> np.uint64(16)) & np.uint64(0xFF))).astype(np.int64)
            m4 = m3 & PM[b13] & PM[b14]
            for a in aq0m[m4]:
                out.append((full_decode(int(a)), None, int(a)))
    except ImportError:
        # pure-python fallback: much slower, restricted scan
        import itertools as it
        for pbytes in it.product(alpha, repeat=4):
            p = int.from_bytes(bytes(pbytes), "little")
            k1 = c_le1 ^ p
            aq0 = k1 * inv % M31
            payload = full_decode(aq0)
            if all(32 <= b < 127 or b in (9, 10, 13) for b in payload):
                out.append((payload, None, aq0))

    def eng(p):
        try:
            s = p.decode("ascii")
        except Exception:
            return 0.0
        return sum(1 for ch in s if ch.isalnum() or ch in " .,!?:;'\"-_()[]+/") / len(s)

    scored = sorted(out, key=lambda t: -eng(t[0]))
    results = []
    for payload, _, aq0 in scored:
        if eng(payload) < 0.8:
            break
        aks = []
        for t in range(3):
            v = aq0 - 1 + t * 2147483646
            if v > M32C:
                continue
            aks.append(aa_inv(v))
        # plausibility filter on Z drift: a handful of flipped Roblox branches
        # can only move Z by a few million, never by billions
        if z_shim is not None and aks:
            ok = False
            for ak in aks:
                z_true = (ak - ae_state) % M32C
                if z_true >= 2 ** 31:
                    z_true -= 2 ** 32
                if abs(z_true - z_shim) <= 50_000_000:
                    ok = True
                    break
            if not ok:
                continue
        results.append((payload, aks, aq0))
        if len(results) >= 8:
            break
    # rank by dictionary coverage
    results.sort(key=lambda r: -_word_coverage(r[0]))
    return results


def find_z_var_name(root, d):
    """The Z accumulator: first `+=` target inside the dispatcher body."""
    zvar = None

    def _find(node):
        nonlocal zvar
        if isinstance(node, Node):
            if node.kind == "assign" and node.get("op") == "+=":
                ts = node.get("targets", [])
                if ts and ts[0].kind == "name":
                    zvar = ts[0].get("name")
                    return True
            for v in node.fields.values():
                if _find(v):
                    return True
        elif isinstance(node, list):
            for v in node:
                if _find(v):
                    return True
        return False

    if d is not None:
        for st in d["node"].get("body", []):
            if _find(st):
                break
    return zvar


# known-plaintext dictionary: decoded keys are library member names
_KEY_PLAINTEXTS = (
    b"rep", b"sub", b"len", b"new", b"abs", b"byte", b"char", b"find",
    b"band", b"bxor", b"bor", b"info", b"floor", b"sign", b"concat",
    b"match", b"gsub", b"ceil", b"max", b"min", b"readu32", b"readu8",
    b"writeu32", b"writeu8", b"fromstring", b"readstring", b"lrotate",
    b"rrotate", b"lshift", b"rshift", b"resume", b"create", b"yield",
    b"format", b"gmatch", b"upper", b"lower", b"reverse", b"insert",
    b"remove", b"isfrozen", b"GetService", b"graphemes", b"offset",
)

_M31 = 2147483647
_M31M1 = 2147483646
_M32 = 4294967296
_M32C = 0xFFFFFFFF
_INV16807 = pow(16807, -1, _M31)


def _inv_rs(y, p):
    x = 0
    for i in range(31, -1, -1):
        hi = ((x >> (i + p)) & 1) if i + p < 32 else 0
        x |= (((y >> i) & 1) ^ hi) << i
    return x


def _inv_ls(y, p):
    x = 0
    for i in range(32):
        lo = ((x >> (i - p)) & 1) if i >= p else 0
        x |= (((y >> i) & 1) ^ lo) << i
    return x


def _make_round_inv(steps):
    def inv(x):
        x = int(x) & _M32C
        for kind, p in reversed(steps):
            if kind == "add":
                x = (x - p) & _M32C
            elif kind == "rs":
                x = _inv_rs(x, p)
            elif kind == "ls":
                x = _inv_ls(x, p)
            elif kind == "rot":
                x = ((x >> p) | (x << (32 - p))) & _M32C
        return x
    return inv


def solve_z_correction(interp, state_val=None, z_val=None):
    """Recover candidate true (state + Z) seeds from known plaintexts.

    Returns a list of candidate Z corrections, most plausible first."""
    seed_raw = interp.last_seed_obs
    if seed_raw is None:
        return []
    state_val = float(seed_raw[0])
    z_val = float(seed_raw[1])

    # candidate ciphers: raw bytes seen by string.byte (most recent first)
    ciphers = []
    if interp.byte_obs:
        ciphers.append(interp.byte_obs[-1])  # the failing decode's cipher
    for b in reversed(interp.byte_obs[-64:]):
        if b not in ciphers:
            ciphers.append(b)
        if len(ciphers) >= 4:
            break
    if not ciphers:
        return []

    return _vote_correction(interp, ciphers, state_val, z_val)


def _vote_correction(interp, ciphers, state_val, z_val):
    # locate the round steps used by obs (re-derive from obs_round_calls)
    steps = None
    for node, arg, result, _st in interp.obs_round_calls:
        steps = extract_round_steps(node)
        if steps:
            break
    if not steps:
        return None
    rnd = make_round(steps)
    if isinstance(rnd, tuple):
        rnd = rnd[0]
    aa_inv = _make_round_inv(steps)

    from collections import Counter
    votes = Counter()
    for cipher in ciphers:
        n_total = len(cipher)
        for offset in range(1, min(n_total - 2, 20)):
            for target in _KEY_PLAINTEXTS:
                n = len(target)
                if offset + n > n_total:
                    continue
                if n not in (3, 4):
                    continue
                ct = cipher[offset:offset + n]
                ks = [ct[i] ^ target[i] for i in range(n)]
                if n == 3:
                    AX24 = ks[0] | (ks[1] << 8) | (ks[2] << 16)
                    for h in range(128):
                        AX = AX24 | (h << 24)
                        if AX >= _M31:
                            continue
                        aq = AX * _INV16807 % _M31
                        if not (1 <= aq <= _M31M1):
                            continue
                        for k in (0, 1):
                            rv = aq - 1 + k * _M31M1
                            if rv > _M32C:
                                continue
                            seed = aa_inv(rv)
                            zt = (seed - state_val) % _M32
                            corr = zt - z_val
                            if abs(corr) < 100_000_000:
                                votes[corr] += 1
                else:  # n == 4 — fully determined
                    AX = ks[0] | (ks[1] << 8) | (ks[2] << 16) | (ks[3] << 24)
                    if AX >= _M31:
                        continue
                    aq = AX * _INV16807 % _M31
                    if not (1 <= aq <= _M31M1):
                        continue
                    for k in (0, 1):
                        rv = aq - 1 + k * _M31M1
                        if rv > _M32C:
                            continue
                        seed = aa_inv(rv)
                        zt = (seed - state_val) % _M32
                        corr = zt - z_val
                        if abs(corr) < 100_000_000:
                            votes[corr] += 1
    if not votes:
        return []
    # rank by plausibility: small drifts first (a handful of flipped branches)
    corr = sorted(votes.keys(), key=lambda c: abs(c))
    return [float(c) for c in corr[:24]]


def _trial_run(interp, root, states, ztrace):
    """Bounded trial emulation with the current z_correction applied."""
    states.clear()
    ztrace.clear()
    interp.outputs.clear()
    interp.calllog.clear()
    interp.obs_round_calls.clear()
    interp.byte_obs.clear()
    err = None
    chunk_env = None
    try:
        chunk_env = interp.exec_chunk(root)
    except (LuaError, RecursionError) as e:
        err = e
    except Exception as e:
        err = LuaError(str(e))
    return len(states), err, chunk_env


def main(argv=None):
    global _DEADLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--report")
    ap.add_argument("--max-states", type=int, default=500)
    args = ap.parse_args(argv)

    raw = open(args.input, "rb").read()
    src = raw.decode("utf-8", "surrogateescape")
    root, errors, _ = parse(src)
    if errors:
        print("PARSE ERRORS:", errors[:5])
        return 2

    d = find_dispatcher(root)
    if d is None:
        print("no LUAST dispatcher pattern found (need `while true do s = K - s; ... end end`)")
        return 2

    interp = Interp()
    _DEADLINE = time.monotonic() + DEADLINE_SECONDS
    interp.set_source(src)
    register_string_table(interp.globals_env.vars)
    interp.z_var_name = find_z_var_name(root, d)

    states = []
    ztrace = []

    def hook(state):
        states.append(state)
        interp.current_state = state
        z = NIL
        holder = interp.chunk_env.find("Z") if hasattr(interp, "chunk_env") else None
        if holder is not None:
            z = holder.vars.get("Z", NIL)
        ztrace.append((state, brief(z)))

    interp.dispatch_node = d["node"]
    interp.dispatch_var = d["var"]
    interp.state_hook = hook

    header = []
    header.append("LUAST L3 emulation")
    header.append("  state var : %s   K = %s   init = %s"
                  % (d["var"], fmt_num(d["K"]),
                     fmt_num(d["init"]) if d["init"] is not None else "?"))
    print("\n".join(header))

    # ---- self-healing emulation loop: on a nil-call crash (garbage decoded
    # key), solve the true (state+Z) seed from known plaintexts, apply the
    # recovered Z correction, and restart.  The drift accumulates from
    # Roblox-vs-shim semantic gaps, so this iterates until clean.
    err = None
    chunk_env = None
    MAX_HEAL = 8
    tried = set()
    for attempt in range(MAX_HEAL + 1):
        states.clear()
        ztrace.clear()
        interp.outputs.clear()
        interp.calllog.clear()
        interp.obs_round_calls.clear()
        interp.byte_obs.clear()
        err = None
        chunk_env = None
        try:
            chunk_env = interp.exec_chunk(root)
        except (LuaError, RecursionError) as e:
            err = e
        except Exception as e:  # shims raise plain exceptions in pcall contexts
            err = LuaError(str(e))
        if err is None:
            break
        msg = str(err)
        if "attempt to call a nil value" not in msg \
                and "attempt to index" not in msg:
            break
        if attempt == MAX_HEAL:
            break
        cands = solve_z_correction(interp)
        if not cands:
            break
        # validate candidates by emulation progress: the true correction
        # executes the most steps before hitting the next decode problem
        base_steps = interp.steps
        best = None  # (steps, corr)
        for c in cands:
            interp.z_correction = float(c)
            s2, e2, _ce2 = _trial_run(interp, root, states, ztrace)
            steps2 = interp.steps
            if e2 is None:
                best = (steps2, float(c))
                err = None
                chunk_env = _ce2
                break
            if best is None or steps2 > best[0]:
                best = (steps2, float(c))
        if best is None:
            break
        interp.z_correction = best[1]
        print("  [self-heal] applying Z correction %+d "
              "(progress %d -> %d steps, attempt %d)"
              % (int(best[1]), base_steps, best[0], attempt + 1))
        if best[0] <= base_steps:
            break  # no candidate made progress

    if len(states) > args.max_states + 2:
        note = " (stopped early: exceeded --max-states)"
    else:
        note = ""

    print("  states executed : %d%s" % (len(states), note))
    uniq = []
    for s in states:
        if s not in uniq:
            uniq.append(s)
    print("  unique states   : %d -> %s" % (len(uniq),
                                           ", ".join(fmt_num(s) for s in uniq[:40])))
    if err is not None:
        print("  EMULATION ERROR : %s (at state %s)"
              % (err, fmt_num(interp.current_state) if interp.current_state is not None else "?"))

    print("\n--- captured print output (the payload) ---")
    for line in interp.outputs:
        sys.stdout.write(line.decode("utf-8", "surrogateescape") + "\n")

    # ---------------- report ----------------
    rep = []
    rep.append("LUAST L3 emulation report")
    rep.append("input: %s" % os.path.abspath(args.input))
    rep.append("state var=%s K=%s init=%s" % (d["var"], fmt_num(d["K"]),
                                              fmt_num(d["init"])))
    rep.append("")
    rep.append("== state trace (state, Z at dispatch) ==")
    for i, (s, z) in enumerate(ztrace):
        rep.append("%3d  state=%-10s Z=%s" % (i, fmt_num(s), z))
    rep.append("")
    rep.append("== print outputs ==")
    for line in interp.outputs:
        rep.append(repr(line))
    rep.append("")

    # ---------------- seed-recovery fallback ----------------
    final_payloads = list(interp.outputs)
    if interp.obs_fromstring and interp.obs_readstring:
        cipher = interp.obs_fromstring[-1]
        _, apn = interp.obs_readstring[-1]
        payload_ok = all(
            all(32 <= b < 127 or b in (9, 10, 13) for b in line)
            for line in interp.outputs) and interp.outputs
        if not payload_ok:
            print("\n[seed-recovery] shim payload not printable -> running "
                  "constrained seed solver ...")
            fn_node = None
            if interp.obs_round_calls:
                fn_node = interp.obs_round_calls[-1][0]
            round_steps = extract_round_steps(fn_node) if fn_node is not None else None
            if round_steps is None:
                print("[seed-recovery] round function does not match the "
                      "invertible add/xorshift/rotate pattern; cannot solve")
            else:
                ae_state = interp.obs_round_calls[-1][3]
                # self-check: native round fn must reproduce the observed call
                aa_native, _ = make_round(round_steps)
                obs_arg, obs_ret = interp.obs_round_calls[-1][1], interp.obs_round_calls[-1][2]
                ak_shim = (ae_state + _zfinal(interp)) % 4294967296
                if interp.obs_round_calls and abs(aa_native(obs_arg) - obs_ret) < 0.5 \
                        and abs(obs_arg - ak_shim) < 0.5:
                    pass  # pattern + seed derivation consistent
                else:
                    print("[seed-recovery] warning: native round fn / seed "
                          "mismatch (obs arg=%s, derived ak=%s)"
                          % (fmt_num(obs_arg), fmt_num(ak_shim)))
                cands = recover_payload(cipher, int(apn), round_steps, ae_state,
                                        z_shim=_zfinal(interp))
                if cands:
                    payload, aks, aq0 = cands[0]
                    print("[seed-recovery] RECOVERED PAYLOAD: %r" % payload)
                    print("[seed-recovery] aq0 = %d, ak candidates = %s"
                          % (aq0, [str(a) for a in aks]))
                    if aks:
                        z_true = (aks[0] - ae_state) % 4294967296
                        if z_true >= 2 ** 31:
                            z_true -= 2 ** 32
                        z_shim = _zfinal(interp)
                        print("[seed-recovery] Z_true = %d  (emulated Z = %d, "
                              "drift = %d — Roblox shim semantics differ)"
                              % (z_true, z_shim, z_true - z_shim))
                    final_payloads = [c[0] for c in cands]
                    rep.append("== seed recovery ==")
                    rep.append("shim payload was: %r" % (interp.outputs or []))
                    rep.append("aq0 = %d" % aq0)
                    rep.append("candidates:")
                    for p2, a2, q2 in cands:
                        rep.append("  payload=%r aks=%s" % (p2, [str(a) for a in a2]))
                    rep.append("")
                else:
                    print("[seed-recovery] no printable candidate found")
    rep.append("")

    if args.report:
        open(args.report, "w").write("\n".join(rep) + "\n")
        print("\nreport -> %s" % args.report)

    # ---------------- recovered program ----------------
    out_path = args.output
    if out_path:
        lines = []
        lines.append("-- Recovered by luau_recover LUAST-L3 static emulator")
        lines.append("-- Source: %s" % os.path.basename(args.input))
        lines.append("-- States executed: %d, unique: %d"
                     % (len(states), len(uniq)))
        if final_payloads != interp.outputs:
            lines.append("-- NOTE: payload recovered via constrained seed "
                         "solver (Roblox shim drift compensated)")
        if final_payloads:
            lines.append("print(%s)" % lua_quote(final_payloads[0]))
            for alt in final_payloads[1:4]:
                lines.append("-- alternate candidate: %s" % lua_quote(alt))
        open(out_path, "w").write("\n".join(lines) + "\n")
        print("recovered -> %s" % out_path)
    return 0


def _zfinal(interp):
    z = interp.chunk_env.vars.get("Z") if hasattr(interp, "chunk_env") else None
    return float(z) if isinstance(z, float) else 0.0


def lua_quote(b: bytes) -> str:
    out = ['"']
    for ch in b:
        c = ch if isinstance(ch, int) else ord(ch)
        if c == 0x22:
            out.append('\\"')
        elif c == 0x5C:
            out.append("\\\\")
        elif c == 0x0A:
            out.append("\\n")
        elif c == 0x0D:
            out.append("\\r")
        elif c == 0x09:
            out.append("\\t")
        elif 0x20 <= c < 0x7F:
            out.append(chr(c))
        else:
            out.append("\\x%02X" % c)
    out.append('"')
    return "".join(out)


if __name__ == "__main__":
    sys.exit(main())

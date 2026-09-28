#!/usr/bin/env python3
"""Minimal Luau interpreter + Roblox runtime shims.

Purpose: statically execute LUAST-obfuscated level-3 scripts inside the
luau_recover AST so the dispatcher state machine, pool permutations,
opaque predicates and keystream decoders are resolved exactly as the
real Luau VM would resolve them.  Only the subset of Luau used by
LUAST output is implemented.
"""
from __future__ import annotations

import math as _math
import json as _json

from . import lua_patterns as _lp

M32 = 0xFFFFFFFF


# ---------------------------------------------------------------- values

class LuaNil:
    __slots__ = ()

    def __repr__(self):
        return "nil"


NIL = LuaNil()


class LuaError(Exception):
    def __init__(self, value):
        super().__init__(str(value))
        self.value = value


class ContinueSignal(Exception):
    pass


class BreakSignal(Exception):
    pass


class ReturnSignal(Exception):
    def __init__(self, values):
        self.values = values


class LuaTable:
    __slots__ = ("hash", "lib")

    def __init__(self, lib=False):
        self.hash = {}
        self.lib = lib  # True for standard libraries: missing members
                        # resolve to function-like dummies (decoy safety)

    def get(self, k):
        try:
            k = norm_key(k)
        except LuaError:
            return NIL
        return self.hash.get(k, NIL)

    def set(self, k, v):
        self.hash[norm_key(k)] = v
        MUTLOG.append((id(self), k, v))

    def length(self):
        n = 1
        while float(n) in self.hash:
            n += 1
        return float(n - 1)

    def __repr__(self):
        return "table: 0x%x" % id(self)


MUTLOG = []  # (table_id, key, new_value) for every table store


class Instance:
    __slots__ = ("cls", "ttypeof", "props")

    def __init__(self, cls, ttypeof=None):
        self.cls = cls
        self.ttypeof = ttypeof or cls
        self.props = {}

    def __repr__(self):
        return self.cls


class Userdata:
    __slots__ = ("ttypeof",)

    def __init__(self, t="userdata"):
        self.ttypeof = t

    def __repr__(self):
        return "userdata"


class RobloxDummy:
    """Chainable stand-in for any un-modelled Roblox global.

    Obfuscated pools embed decoy *values* such as ``Instance.new``,
    ``CFrame`` or ``Color3``; merely evaluating the pool constructor
    indexes them.  A dummy is truthy, indexable (``.new`` -> dummy),
    callable (-> dummy) and compares by identity, mirroring Roblox
    semantics closely enough for pool construction and opaque
    predicates while never silently corrupting arithmetic (need_num
    still raises on it, exactly like Luau does for userdata).
    Singleton per name so identity comparisons behave sanely.
    """
    __slots__ = ("name",)
    _cache = {}

    def __new__(cls, name="RobloxGlobal"):
        inst = cls._cache.get(name)
        if inst is None:
            inst = object.__new__(cls)
            inst.name = name
            cls._cache[name] = inst
        return inst

    def __repr__(self):
        return self.name


class NilChain(RobloxDummy):
    """Falsy chainable stand-in for *unknown* globals.
    Real Roblox globals that the runtime lacks get RobloxDummy (truthy,
    matching live-server semantics).  A global that does not exist at
    all is nil on Roblox: falsy.  But obfuscated code sometimes *indexes*
    such a name with a pool string key (decoy chains); nil would abort
    emulation, so unknown globals resolve to a NilChain: falsy for
    truthiness, chainable for indexing, and callable.
    """
    _cache = {}

    def __new__(cls, name="RobloxGlobal"):
        inst = cls._cache.get(name)
        if inst is None:
            inst = object.__new__(cls)
            inst.name = name
            cls._cache[name] = inst
        return inst

    def __repr__(self):
        return self.name


class FuncLikeDummy(RobloxDummy):
    """Function-like decoy: truthy, callable, accepted where the real
    code expects a function (debug.info targets, callback stores)."""
    _cache = {}


# Roblox globals that may appear as pool decoys / opaque-predicate fodder.
ROBLOX_GLOBAL_NAMES = (
    "Instance", "CFrame", "Vector3", "Vector2", "Vector3int16", "Color3",
    "ColorSequence", "ColorSequenceKeypoint", "NumberSequence",
    "NumberSequenceKeypoint", "NumberRange", "BrickColor", "UDim", "UDim2",
    "Rect", "Region3", "Region3int16", "Ray", "TweenInfo", "Random",
    "DateTime", "FontFace", "Content", "PhysicalProperties", "Axes",
    "Faces", "CatalogSearchParams", "FloatCurveKey", "RotationCurveKey",
    "Secret", "SharedTable", "Path2DControlPoint", "OverlappedParams",
    "task", "script", "shared", "_G", "settings", "UserSettings",
    "PluginManager", "stats", "ProfilerSession", "elapsedTime",
    "DockWidgetPluginGuiInfo", "TweenService", "Players", "Lighting",
    "ReplicatedStorage", "ServerStorage", "RunService", "TweenPort",
    "utf8",  # utf8 library exists on Roblox (graphemes/offset/char/byte)
)


def make_roblox_dummies(g: dict) -> None:
    """Register chainable dummies for any Roblox global not already modelled."""
    for name in ROBLOX_GLOBAL_NAMES:
        if name not in g:
            g[name] = RobloxDummy(name)


class Buffer:
    __slots__ = ("data",)

    def __init__(self, data: bytearray):
        self.data = data

    def __repr__(self):
        return "buffer: 0x%x" % id(self)


class V2Value:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return "Vector2int16(%s, %s)" % (self.x, self.y)


class Vec3:
    """Luau native vector / Vector3-like value (componentwise semantics)."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __repr__(self):
        return "vector(%s, %s, %s)" % (fmt_num(self.x), fmt_num(self.y),
                                       fmt_num(self.z))


class ColorVal:
    """Color3-like value."""
    __slots__ = ("r", "g", "b")

    def __init__(self, r, g, b):
        self.r = float(r)
        self.g = float(g)
        self.b = float(b)

    def __repr__(self):
        return "Color3(%s, %s, %s)" % (fmt_num(self.r), fmt_num(self.g),
                                       fmt_num(self.b))


class VecLib:
    """The Luau `vector` library object: callable AND indexable.
    `members` holds the f32-exact implementation table (roblox_shims)."""
    __slots__ = ("members",)

    def __init__(self):
        self.members = None

    def __repr__(self):
        return "vector"


class EnumItem:
    __slots__ = ("etype", "name", "value")

    def __init__(self, etype, name, value):
        self.etype = etype
        self.name = name  # str
        self.value = value

    def __repr__(self):
        return "Enum.%s.%s" % (self.etype, self.name)


class EnumType:
    __slots__ = ("name", "items")

    def __init__(self, name, items):
        self.name = name  # str
        self.items = items  # str -> EnumItem

    def __repr__(self):
        return "Enum.%s" % self.name


class EnumRoot:
    __slots__ = ("types",)

    def __init__(self):
        self.types = {}

    def __repr__(self):
        return "Enum"


class PyFunc:
    __slots__ = ("fn", "name")

    def __init__(self, fn, name="pyfn"):
        self.fn = fn
        self.name = name

    def __repr__(self):
        return "function: %s" % self.name


class LuaClosure:
    __slots__ = ("node", "env", "params", "vararg")

    def __init__(self, node, env):
        self.node = node
        self.env = env
        self.params = node.get("params", [])
        self.vararg = node.get("vararg", False)

    def __repr__(self):
        return "function: 0x%x" % id(self)


# ---------------------------------------------------------------- helpers

def norm_key(k):
    if isinstance(k, bool):
        return k
    if isinstance(k, (int, float)):
        return float(k)
    if isinstance(k, bytes):
        return k
    if k is NIL or k is None:
        raise LuaError("table index is nil")
    return k


def lu_eq_value(a, b):
    """Raw equality used by rawequal: identity/numeric/string semantics."""
    if a is NIL or a is None:
        return b is NIL or b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, bytes) and isinstance(b, bytes):
        return a == b
    return a is b


def truthy(v):
    if isinstance(v, NilChain):
        return False
    return v is not NIL and v is not False


def first(v):
    if isinstance(v, list):
        return v[0] if v else NIL
    return v


def fmt_num(v):
    if isinstance(v, bool):
        v = 1.0 if v else 0.0
    if v != v:
        return "nan"
    if v == _math.inf:
        return "inf"
    if v == -_math.inf:
        return "-inf"
    if v == int(v) and abs(v) < 2 ** 53:
        return str(int(v))
    return "%.14g" % v


def lua_tostr(v) -> bytes:
    if v is NIL:
        return b"nil"
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if isinstance(v, (float, int)):
        return fmt_num(float(v)).encode()
    if isinstance(v, bytes):
        return v
    if isinstance(v, LuaTable):
        return ("table: 0x%x" % id(v)).encode()
    if isinstance(v, (PyFunc, LuaClosure)):
        return ("function: 0x%x" % id(v)).encode()
    if isinstance(v, Buffer):
        return ("buffer: 0x%x" % id(v)).encode()
    return str(v).encode()


def typeof(v) -> str:
    if v is NIL:
        return "nil"
    if isinstance(v, RobloxDummy):
        return "userdata"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (float, int)):
        return "number"
    if isinstance(v, bytes):
        return "string"
    if isinstance(v, LuaTable):
        return "table"
    if isinstance(v, (PyFunc, LuaClosure)):
        return "function"
    if isinstance(v, Buffer):
        return "buffer"
    if isinstance(v, V2Value):
        return "Vector2int16"
    if isinstance(v, EnumItem):
        return "EnumItem"
    if isinstance(v, (EnumType, EnumRoot)):
        return "Enum"
    if isinstance(v, Instance):
        return v.ttypeof
    if isinstance(v, Userdata):
        return v.ttypeof
    return "userdata"


def type_desc(v) -> str:
    if v is NIL:
        return "nil"
    return typeof(v)


def need_num(v, ctx="arithmetic"):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        return float(v)
    raise LuaError("attempt to perform %s on a %s value" % (ctx, type_desc(v)))


# ---------------------------------------------------------------- bit32

def tou32(x):
    if isinstance(x, bool):
        x = 1.0 if x else 0.0
    if isinstance(x, int):
        x = float(x)
    if not isinstance(x, float):
        raise LuaError("bit32: number expected, got %s" % type_desc(x))
    xi = int(x)  # truncate; integral doubles only in our corpus
    return xi & M32


def b_xor(*a):
    r = 0
    for x in a:
        r ^= tou32(x)
    return float(r)


def b_and(*a):
    r = M32
    for x in a:
        r &= tou32(x)
    return float(r)


def b_or(*a):
    r = 0
    for x in a:
        r |= tou32(x)
    return float(r)


def b_not(x):
    return float((~tou32(x)) & M32)


def b_rshift(x, n):
    n = tou32(n)
    if n >= 32:
        return 0.0
    return float(tou32(x) >> n)


def b_lshift(x, n):
    n = tou32(n)
    if n >= 32:
        return 0.0
    return float((tou32(x) << n) & M32)


def b_lrotate(x, n):
    n = tou32(n) % 32
    x = tou32(x)
    if n == 0:
        return float(x)
    return float(((x << n) | (x >> (32 - n))) & M32)


def b_rrotate(x, n):
    n = tou32(n) % 32
    x = tou32(x)
    if n == 0:
        return float(x)
    return float(((x >> n) | (x << (32 - n))) & M32)


# ---------------------------------------------------------------- string lib

def _need_str(s):
    if not isinstance(s, bytes):
        raise LuaError("string expected, got %s" % type_desc(s))
    return s


def s_byte(s, i=1.0, j=None):
    s = _need_str(s)
    i = int(need_num(i))
    j = int(need_num(j)) if j is not None else i
    if i < 0:
        i += len(s) + 1
    if j < 0:
        j += len(s) + 1
    i = max(i, 1)
    j = min(j, len(s))
    if i > j:
        return []
    return [float(b) for b in s[i - 1:j]]


def s_char(*a):
    return bytes(int(tou32(x)) & 0xFF for x in a)


def s_rep(s, n):
    s = _need_str(s)
    n = int(need_num(n))
    if n <= 0:
        return b""
    return s * n


def s_sub(s, i, j=None):
    s = _need_str(s)
    n = len(s)
    i = int(need_num(i))
    j = int(need_num(j)) if j is not None else n
    if i < 0:
        i += n + 1
    if j < 0:
        j += n + 1
    i = max(i, 1)
    j = min(j, n)
    if i > j:
        return b""
    return s[i - 1:j]


def s_len(s):
    return float(len(_need_str(s)))


def s_upper(s):
    return _need_str(s).upper()


def s_lower(s):
    return _need_str(s).lower()


def s_format(s, *a):
    _need_str(s)
    fmt = s.decode("utf-8", "surrogateescape")
    out = []
    ai = 0

    def next_arg():
        nonlocal ai
        v = a[ai] if ai < len(a) else NIL
        ai += 1
        return v

    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            raise LuaError("invalid format string")
        if fmt[i] == "%":
            out.append("%")
            i += 1
            continue
        # flags
        minus = False
        plus = False
        zero = False
        space = False
        while i < n and fmt[i] in "-+0 ":
            c = fmt[i]
            if c == "-":
                minus = True
            elif c == "+":
                plus = True
            elif c == "0":
                zero = True
            elif c == " ":
                space = True
            i += 1
        # width
        width = 0
        while i < n and fmt[i].isdigit():
            width = width * 10 + int(fmt[i])
            i += 1
        # precision
        prec = None
        if i < n and fmt[i] == ".":
            i += 1
            prec = 0
            while i < n and fmt[i].isdigit():
                prec = prec * 10 + int(fmt[i])
                i += 1
        # length modifiers (l, ll, h) — accepted and ignored
        while i < n and fmt[i] in "lh":
            i += 1
        if i >= n:
            raise LuaError("invalid format string")
        conv = fmt[i]
        i += 1

        def _num_str(v):
            # Lua integer-ish rendering for d/i/u
            iv = int(v)
            return str(iv)

        if conv in "diu":
            v = need_num(next_arg())
            body = _num_str(v)
            if v >= 0 and plus:
                body = "+" + body
            elif v >= 0 and space:
                body = " " + body
            if width > len(body) and not minus:
                body = body.rjust(width, "0" if zero else " ")
            elif width > len(body):
                body = body.ljust(width)
            out.append(body)
        elif conv in "xX":
            v = need_num(next_arg())
            iv = int(v) & 0xFFFFFFFFFFFFFFFF  # Lua wraps negatives for %x
            body = format(iv, "x" if conv == "x" else "X")
            if width > len(body) and not minus:
                body = body.rjust(width, "0" if zero else " ")
            elif width > len(body):
                body = body.ljust(width)
            out.append(body)
        elif conv == "o":
            v = need_num(next_arg())
            iv = int(v) & 0xFFFFFFFFFFFFFFFF
            body = format(iv, "o")
            if width > len(body) and not minus:
                body = body.rjust(width, "0" if zero else " ")
            elif width > len(body):
                body = body.ljust(width)
            out.append(body)
        elif conv in "eEfgG":
            v = need_num(next_arg())
            p = 6 if prec is None else prec
            spec = "%" + ("+" if plus else "") + ("-" if minus else "") + \
                   ("0" if zero else "") + \
                   (str(width) if width else "") + "." + str(p) + conv
            out.append(spec % v)
        elif conv == "q":
            v = next_arg()
            if not isinstance(v, bytes):
                v = lua_tostr(v)
            inner = v.decode("utf-8", "surrogateescape")
            inner = inner.replace("\\", "\\\\").replace('"', '\\"') \
                         .replace("\n", "\\n").replace("\r", "\\r") \
                         .replace("\0", "\\0")
            out.append('"' + inner + '"')
        elif conv == "s":
            v = next_arg()
            if not isinstance(v, bytes):
                v = lua_tostr(v)
            body = v.decode("utf-8", "surrogateescape")
            if prec is not None:
                body = body[:prec]
            if width > len(body):
                body = body.ljust(width) if minus else body.rjust(width)
            out.append(body)
        elif conv == "c":
            v = need_num(next_arg())
            out.append(chr(int(v) & 0xFF))
        elif conv == "a" or conv == "A":
            v = need_num(next_arg())
            out.append(float.hex(v) if conv == "a" else float.hex(v).upper())
        else:
            raise LuaError("invalid conversion '%%%s' to 'format'" % conv)
    return "".join(out).encode("utf-8", "surrogateescape")


def s_reverse(s):
    _need_str(s)
    return s[::-1]


# ---------------------------------------------------------------- math lib

def m_sign(x):
    x = need_num(x)
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


def m_floor(x):
    return float(_math.floor(need_num(x)))


def m_abs(x):
    return abs(need_num(x))


def m_max(*a):
    if not a:
        raise LuaError("wrong number of arguments")
    return max(need_num(x) for x in a)


def m_min(*a):
    if not a:
        raise LuaError("wrong number of arguments")
    return min(need_num(x) for x in a)


def m_sqrt(x):
    return _math.sqrt(need_num(x))


# ---------------------------------------------------------------- table lib

def t_isfrozen(t):
    # Real Luau freezes every builtin library table (string, bit32, buffer,
    # table, math, debug, coroutine, os, utf8, vector, ...).  The obfuscator
    # uses table.isfrozen(<lib>) as an opaque predicate feeding the Z
    # accumulator, so this MUST report True for our lib-marked tables.
    if isinstance(t, LuaTable):
        return bool(getattr(t, "lib", False))
    return False


def t_insert(t, *a):
    if not isinstance(t, LuaTable):
        raise LuaError("table expected")
    if len(a) == 1:
        n = t.length()
        t.set(float(n + 1), a[0])
    else:
        pos, v = a
        t.set(pos, v)
    return []


def t_remove(t, pos=None):
    if not isinstance(t, LuaTable):
        raise LuaError("table expected")
    n = t.length()
    p = int(need_num(pos)) if pos is not None else int(n)
    v = t.get(float(p))
    for i in range(p, int(n)):
        t.set(float(i), t.get(float(i + 1)))
    t.set(float(n), NIL)
    return [v]


def t_concat(t, sep=b"", i=1.0, j=None):
    if not isinstance(t, LuaTable):
        raise LuaError("table expected")
    n = int(t.length()) if j is None else int(need_num(j))
    start = int(need_num(i))
    parts = []
    for k in range(start, n + 1):
        v = t.get(float(k))
        if isinstance(v, bytes):
            parts.append(v)
        elif isinstance(v, (float, int)):
            parts.append(fmt_num(float(v)).encode())
        else:
            raise LuaError("invalid value in table.concat")
    return sep.join(parts)


# ---------------------------------------------------------------- buffer lib

def _need_buf(b):
    if not isinstance(b, Buffer):
        raise LuaError("buffer expected, got %s" % type_desc(b))
    return b


def _chk(b, off, size):
    b = _need_buf(b)
    off = int(off)
    if off < 0 or off + size > len(b.data):
        raise LuaError("buffer access out of bounds")
    return b, off


def buf_fromstring(s):
    s = _need_str(s)
    return Buffer(bytearray(s))


def buf_tostring(b):
    return bytes(_need_buf(b).data)


def buf_len(b):
    return float(len(_need_buf(b).data))


def buf_readu32(b, off):
    b, off = _chk(b, need_num(off), 4)
    return float(int.from_bytes(b.data[off:off + 4], "little"))


def buf_writeu32(b, off, v):
    b, off = _chk(b, need_num(off), 4)
    b.data[off:off + 4] = (tou32(v)).to_bytes(4, "little")
    return []


def buf_readu8(b, off):
    b, off = _chk(b, need_num(off), 1)
    return float(b.data[off])


def buf_writeu8(b, off, v):
    b, off = _chk(b, need_num(off), 1)
    b.data[off] = tou32(v) & 0xFF
    return []


def buf_readstring(b, off, n):
    b, off = _chk(b, need_num(off), int(need_num(n)))
    cnt = int(need_num(n))
    return bytes(b.data[off:off + cnt])


def buf_readi16(b, off):
    b, off = _chk(b, need_num(off), 2)
    return float(int.from_bytes(b.data[off:off + 2], "little", signed=True))


# ---------------------------------------------------------------- json → lua

def to_lua(data):
    if data is None:
        return NIL
    if isinstance(data, bool):
        return data
    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, str):
        return data.encode("utf-8", "surrogateescape")
    if isinstance(data, list):
        t = LuaTable()
        for i, item in enumerate(data):
            t.set(float(i + 1), to_lua(item))
        return t
    if isinstance(data, dict):
        t = LuaTable()
        for k, v in data.items():
            t.set(k.encode("utf-8", "surrogateescape"), to_lua(v))
        return t
    return NIL


# ---------------------------------------------------------------- globals

def make_enum_root():
    root = EnumRoot()
    fw = {
        "Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400,
        "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800,
        "Heavy": 900,
    }
    fs = {"Normal": 0, "Italic": 1}
    mat = {
        "Plastic": 256, "SmoothPlastic": 272, "Neon": 288, "Wood": 512,
        "Grass": 1280, "Brick": 848, "Metal": 1088, "DiamondPlate": 1056,
        "Slate": 800, "Concrete": 816, "Ice": 1536, "Glass": 1568,
        "ForceField": 1584,
    }
    key = {
        "KeyCode": 0, "ItemType": 1, "LayoutOrder": 2, "AnimationPriority": 3,
    }
    for name, table in (("FontWeight", fw), ("FontStyle", fs),
                        ("Material", mat), ("Key", key)):
        items = {n: EnumItem(name, n, float(v)) for n, v in table.items()}
        root.types[name] = EnumType(name, items)
    return root


def build_globals(interp):
    g = {}

    bit32 = LuaTable(lib=True)
    for n, f in (("bxor", b_xor), ("band", b_and), ("bor", b_or),
                 ("bnot", b_not), ("rshift", b_rshift), ("lshift", b_lshift),
                 ("lrotate", b_lrotate), ("rrotate", b_rrotate)):
        bit32.set(n.encode(), PyFunc(f, "bit32." + n))
    g["bit32"] = bit32

    string = LuaTable(lib=True)
    for n, f in (("byte", s_byte), ("char", s_char), ("rep", s_rep),
                 ("sub", s_sub), ("len", s_len), ("upper", s_upper),
                 ("lower", s_lower), ("format", s_format),
                 ("reverse", s_reverse)):
        string.set(n.encode(), PyFunc(f, "string." + n))

    # ---- pattern-based string functions (lua_patterns engine)

    def _bs(s):
        if not isinstance(s, bytes):
            s = lua_tostr(s)
        return s.decode("utf-8", "surrogateescape")

    def _pos_args(s, init, plain=None):
        init = need_num(init) if init is not None and init is not NIL else 1.0
        if init < 0:
            init = max(len(s) + 1 + init, 1)
        elif init == 0:
            init = 1
        return int(init)

    def _str_find(s, pat, init=None, plain=None):
        ss, pp = _bs(s), _bs(pat)
        i = _pos_args(ss, init)
        if truthy(plain):
            j = ss.find(pp, i - 1)
            if j < 0:
                return []
            return [float(j + 1), float(j + len(pp))]
        a, b = _lp.str_find(ss, pp, i)
        if a is None:
            return []
        return [float(a), float(b)]

    def _str_match(s, pat, init=None):
        ss, pp = _bs(s), _bs(pat)
        i = _pos_args(ss, init)
        caps = _lp.str_match(ss, pp, i)
        return [c if isinstance(c, float) else
                (c.encode("utf-8", "surrogateescape") if isinstance(c, str) else c)
                for c in caps]

    def _str_gmatch(s, pat):
        ss, pp = _bs(s), _bs(pat)
        results = []
        for caps in _lp.str_gmatch(ss, pp):
            row = [c if isinstance(c, float) else
                   (c.encode("utf-8", "surrogateescape") if isinstance(c, str) else c)
                   for c in caps]
            results.append(row)
        state = {"i": 0}

        def _iter(*_a):
            if state["i"] >= len(results):
                return None
            row = results[state["i"]]
            state["i"] += 1
            return row
        return ["__pyiter__", _iter, NIL, NIL]

    def _str_gsub(s, pat, repl, init=None, max_n=None):
        ss, pp = _bs(s), _bs(pat)
        i = _pos_args(ss, init)
        n = int(need_num(max_n)) if max_n is not None and max_n is not NIL \
            else None
        if isinstance(repl, PyFunc):
            call = repl.fn
        elif isinstance(repl, RobloxDummy):
            call = lambda *a: repl
        else:
            call = None
        out, count = _lp.str_gsub(
            ss, pp, repl if call is None else
            (lambda caps: _repl_call(call, caps)), i, n)
        return [out.encode("utf-8", "surrogateescape"), float(count)]

    def _repl_call(call, caps):
        args = [c if isinstance(c, float) else
                (c.encode("utf-8", "surrogateescape") if isinstance(c, str) else c)
                for c in caps]
        r = call(*args)
        if isinstance(r, (bytes, bytearray)):
            return bytes(r).decode("utf-8", "surrogateescape")
        if isinstance(r, (float, int)) and not isinstance(r, bool):
            return fmt_num(float(r))
        if r is NIL or r is None:
            return caps[0] if caps else ""
        return str(r)

    for _n, _f in (("find", _str_find), ("match", _str_match),
                   ("gsub", _str_gsub), ("gmatch", _str_gmatch)):
        string.set(_n.encode(), PyFunc(_f, "string." + _n))
    g["string"] = string

    mathlib = LuaTable(lib=True)
    for n, f in (("sign", m_sign), ("floor", m_floor), ("abs", m_abs),
                 ("max", m_max), ("min", m_min), ("sqrt", m_sqrt)):
        mathlib.set(n.encode(), PyFunc(f, "math." + n))
    mathlib.set(b"huge", _math.inf)
    mathlib.set(b"pi", _math.pi)
    g["math"] = mathlib

    table = LuaTable(lib=True)
    for n, f in (("isfrozen", t_isfrozen), ("insert", t_insert),
                 ("remove", t_remove), ("concat", t_concat)):
        table.set(n.encode(), PyFunc(f, "table." + n))
    g["table"] = table

    buffer = LuaTable(lib=True)
    for n, f in (("fromstring", buf_fromstring), ("tostring", buf_tostring),
                 ("len", buf_len), ("readu32", buf_readu32),
                 ("writeu32", buf_writeu32), ("readu8", buf_readu8),
                 ("writeu8", buf_writeu8), ("readstring", buf_readstring),
                 ("readi16", buf_readi16)):
        buffer.set(n.encode(), PyFunc(f, "buffer." + n))
    g["buffer"] = buffer

    coroutine = LuaTable(lib=True)
    coroutine.set(b"resume", PyFunc(lambda *a: [True], "coroutine.resume"))
    coroutine.set(b"create", PyFunc(lambda f, *a: LuaClosure(f.node, f.env)
                                   if isinstance(f, LuaClosure) else NIL,
                                   "coroutine.create"))
    g["coroutine"] = coroutine

    # ---- Roblox instances
    game = Instance("DataModel", "Instance")
    workspace = Instance("Workspace", "Workspace")
    camera = Instance("Camera")
    workspace.props[b"CurrentCamera"] = camera
    workspace.props[b"Raycast"] = PyFunc(lambda *a: NIL, "workspace.Raycast")
    workspace.props[b"FindFirstChildOfClass"] = PyFunc(
        lambda self, n: NIL, "workspace.FindFirstChildOfClass")

    def GetService(self, name):
        if not isinstance(name, bytes):
            raise LuaError("GetService: string expected")
        key = name.decode("latin-1")
        svc = interp.services.get(key)
        if svc is None:
            svc = Instance(key)
            if key == "HttpService":
                def JSONDecode(self, s):
                    if not isinstance(s, bytes):
                        s = lua_tostr(s)
                    data = _json.loads(s.decode("utf-8", "surrogateescape"))
                    return to_lua(data)
                svc.props[b"JSONDecode"] = PyFunc(JSONDecode,
                                                  "HttpService.JSONDecode")
                svc.props[b"GetAsync"] = PyFunc(lambda self, u: NIL,
                                                "HttpService.GetAsync")
                svc.props[b"GenerateGUID"] = PyFunc(
                    lambda self, w=False: b"00000000-0000-0000-0000-000000000000",
                    "HttpService.GenerateGUID")
            interp.services[key] = svc
        return svc

    def FindService(self, name):
        if not isinstance(name, bytes):
            raise LuaError("FindService: string expected")
        return interp.services.get(name.decode("latin-1"), NIL)

    game.props[b"GetService"] = PyFunc(GetService, "game.GetService")
    game.props[b"FindService"] = PyFunc(FindService, "game.FindService")
    game.props[b"FindFirstChildOfClass"] = PyFunc(
        lambda self, n: NIL, "game.FindFirstChildOfClass")
    game.props[b"GetDescendants"] = PyFunc(lambda self: [], "game.GetDescendants")
    g["game"] = game
    g["workspace"] = workspace

    g["Enum"] = make_enum_root()

    def v2_new(x=0.0, y=0.0):
        return V2Value(need_num(x), need_num(y))

    Vector2int16 = LuaTable()
    Vector2int16.set(b"new", PyFunc(v2_new, "Vector2int16.new"))
    g["Vector2int16"] = Vector2int16

    def font_make(family=None, weight=None, style=None):
        f = Instance("Font", "Font")
        f.props[b"Family"] = family if isinstance(family, bytes) else b"SourceSans"
        fw_t = g["Enum"].types["FontWeight"]
        fs_t = g["Enum"].types["FontStyle"]
        w = weight if isinstance(weight, EnumItem) else fw_t.items["Regular"]
        st = style if isinstance(style, EnumItem) else fs_t.items["Normal"]
        f.props[b"Weight"] = w
        f.props[b"Style"] = st
        # Roblox Font.Bold is derived: true when weight >= Bold (700)
        f.props[b"Bold"] = (w.value >= 700.0)
        return f

    Font = Instance("Font", "Font")
    Font.props[b"new"] = PyFunc(
        lambda family=None, weight=None, style=None: font_make(family, weight, style),
        "Font.new")
    Font.props[b"fromName"] = PyFunc(
        lambda name=None, weight=None, style=None: font_make(name, weight, style),
        "Font.fromName")
    g["Font"] = Font

    debug = LuaTable(lib=True)

    def _debug_info(f, opts, *extra):
        # Luau semantics; results in fixed order s, l, n, a
        if isinstance(f, RobloxDummy):
            # decoy function value (e.g. utf8.graphemes resolved to a dummy):
            # behave as if it were a C function
            s, l, n = b"[C]", -1.0, f.name.encode()
            varg, nparams = True, 0
        elif isinstance(f, PyFunc):
            short = f.name.rsplit(".", 1)[-1].encode()
            s, l, n = b"[C]", -1.0, short
            varg, nparams = True, 0
        elif isinstance(f, LuaClosure):
            node = f.node
            nm = node.get("name") if "name" in node.fields else None
            s = b"=[script]"
            # real Luau reports the 1-based LINE of the function definition;
            # use the interpreter's source-line map when available
            line_fn = getattr(interp, "line_of", None)
            if line_fn is not None:
                l = line_fn(node.start)
            else:
                l = float(node.start) if node.start else -1.0
            n = nm.encode() if isinstance(nm, str) else NIL
            varg = bool(node.get("vararg", False))
            nparams = float(len(node.get("params", [])))
        else:
            raise LuaError("debug.info: function expected, got %s" % type_desc(f))
        out = []
        for ch in (opts if isinstance(opts, bytes) else lua_tostr(opts)).decode():
            if ch == "s":
                out.append(s)
            elif ch == "l":
                out.append(l)
            elif ch == "n":
                out.append(n)
            elif ch == "a":
                out.append(varg)
                out.append(nparams)
        return out

    debug.set(b"info", PyFunc(_debug_info, "debug.info"))
    debug.set(b"traceback", PyFunc(lambda *a: b"stack traceback:", "debug.traceback"))
    g["debug"] = debug

    g["newproxy"] = PyFunc(lambda free=False: Userdata(), "newproxy")

    def _tostring(v):
        return lua_tostr(v)

    def _tonumber(v):
        if isinstance(v, (float, int)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, bytes):
            try:
                return float(v.strip())
            except ValueError:
                return NIL
        return NIL

    def _select(k, *rest):
        if k == b"#":
            return float(len(rest))
        return list(rest[int(need_num(k)) - 1:])

    def _rawget(t, k):
        if not isinstance(t, LuaTable):
            raise LuaError("rawget: table expected, got %s" % type_desc(t))
        return t.get(k)

    def _error(msg, level=None):
        raise LuaError(msg if isinstance(msg, bytes) else lua_tostr(msg))

    def _assert(v, msg=None):
        if not truthy(v):
            raise LuaError(msg if msg is not None else b"assertion failed!")
        return list(_toret([v]))

    def _toret(vs):
        return vs

    def _typeof(v):
        return typeof(v).encode()

    def _type(v):
        # Luau type(): nil/boolean/number/string/table/function/userdata/vector
        return typeof(v).encode()

    def _pcall(f, *args):
        try:
            rets = interp.call_function(f, list(args))
            return [True] + rets
        except LuaError as e:
            v = e.value
            if not isinstance(v, bytes):
                v = lua_tostr(v)
            return [False, v]
        except Exception as e:  # shim-level failures also surface as pcall(false)
            return [False, str(e).encode("utf-8", "surrogateescape")]

    def _print(*args):
        line = b" ".join(lua_tostr(a) for a in args)
        interp.outputs.append(line)

    g["tostring"] = PyFunc(_tostring, "tostring")
    g["tonumber"] = PyFunc(_tonumber, "tonumber")
    g["type"] = PyFunc(_type, "type")
    g["select"] = PyFunc(_select, "select")
    g["rawget"] = PyFunc(_rawget, "rawget")
    g["rawset"] = PyFunc(_rawget, "rawset")
    g["error"] = PyFunc(_error, "error")
    g["assert"] = PyFunc(_assert, "assert")
    g["typeof"] = PyFunc(_typeof, "typeof")
    g["pcall"] = PyFunc(_pcall, "pcall")
    g["print"] = PyFunc(_print, "print")
    g["tick"] = PyFunc(lambda: 0.0, "tick")
    g["time"] = PyFunc(lambda: 0.0, "time")
    g["wait"] = PyFunc(lambda *a: [0.0], "wait")
    g["warn"] = PyFunc(lambda *a: [], "warn")
    g["os"] = LuaTable(lib=True)
    g["os"].set(b"time", PyFunc(lambda *a: 0.0, "os.time"))
    g["os"].set(b"clock", PyFunc(lambda *a: 0.0, "os.clock"))
    g["os"].set(b"date", PyFunc(lambda *a: b"Thu Jan  1 00:00:00 1970", "os.date"))
    g["unpack"] = PyFunc(lambda t: [t.get(float(i))
                                    for i in range(1, int(t.length()) + 1)]
                         if isinstance(t, LuaTable) else [],
                         "unpack")
    make_roblox_dummies(g)

    # ---- f32-exact Roblox datatype shims (roblox_shims) — these OVERRIDE the
    # simple placeholders above and are value-exact vs the real engine, which
    # is what keeps the string-decryption keystream from drifting.
    from . import roblox_shims as _rbs  # lazy import (module imports lua_rt)

    libs = _rbs.make_datatype_libs()
    for _k, _v in libs.items():
        g[_k] = _v
    veclib = VecLib()
    veclib.members = _rbs.make_vector_lib()
    g["vector"] = veclib
    g["task"] = _rbs.make_task_lib(interp)
    g["_rbs_mod"] = _rbs  # stash for interpreter dispatch (arith/index)

    # ---- iteration builtins (generic-for support)

    def _ipairs_iter(t, i):
        if not isinstance(t, LuaTable):
            raise LuaError("ipairs: table expected, got %s" % type_desc(t))
        i = need_num(i)
        j = i + 1.0
        v = t.get(j)
        if v is NIL:
            return None
        return [j, v]

    def _ipairs(t):
        return ["__pyiter__", _ipairs_iter, t, 0.0]

    def _next(t, k=None):
        if not isinstance(t, LuaTable):
            raise LuaError("next: table expected, got %s" % type_desc(t))
        if k is None or k is NIL:
            k = None
        keys = list(t.hash.keys())
        if k is None:
            if not keys:
                return None
            nk = keys[0]
        else:
            nk = norm_key(k)
            if nk not in t.hash:
                raise LuaError("next: invalid key")
            idx = keys.index(nk)
            if idx + 1 >= len(keys):
                return None
            nk = keys[idx + 1]
        v = t.hash[nk]
        # denormalize key back to lua value
        if isinstance(nk, float):
            outk = float(nk)
        elif isinstance(nk, bytes):
            outk = nk
        elif isinstance(nk, bool):
            outk = nk
        else:
            outk = nk
        return [outk, v]

    def _pairs(t):
        return ["__pyiter__", _next, t, NIL]

    def _xpcall(f, handler, *args):
        try:
            rets = interp.call_function(f, list(args))
            return [True] + list(rets)
        except LuaError as e:
            v = e.value
            if not isinstance(v, bytes):
                v = lua_tostr(v)
            try:
                hret = interp.call_function(handler, [v])
                return [False] + list(hret)
            except Exception:
                return [False, v]
        except Exception as e:
            return [False, str(e).encode("utf-8", "surrogateescape")]

    def _rawequal(a, b):
        return lu_eq_value(a, b)
    def _rawlen(t):
        if isinstance(t, LuaTable):
            return float(t.length())
        if isinstance(t, bytes):
            return float(len(t))
        raise LuaError("rawlen: table or string expected")

    g["ipairs"] = PyFunc(_ipairs, "ipairs")
    g["pairs"] = PyFunc(_pairs, "pairs")
    g["next"] = PyFunc(_next, "next")
    g["xpcall"] = PyFunc(_xpcall, "xpcall")
    g["rawequal"] = PyFunc(_rawequal, "rawequal")
    g["rawlen"] = PyFunc(_rawlen, "rawlen")

    # ---- native vector library + typed Roblox value constructors

    def _vector_new(x=0.0, y=0.0, z=0.0):
        return Vec3(need_num(x), need_num(y), need_num(z))

    veclib = VecLib()
    g["vector"] = veclib

    def _v3_new(x=0.0, y=0.0, z=0.0):
        return Vec3(need_num(x), need_num(y), need_num(z))

    Vector3 = LuaTable(lib=True)
    Vector3.set(b"new", PyFunc(_v3_new, "Vector3.new"))
    g["Vector3"] = Vector3

    def _c3_new(r=0.0, g_=0.0, b=0.0):
        return ColorVal(need_num(r), need_num(g_), need_num(b))

    def _c3_fromrgb(r, g_, b):
        return ColorVal(need_num(r) / 255.0, need_num(g_) / 255.0,
                        need_num(b) / 255.0)

    Color3 = LuaTable(lib=True)
    Color3.set(b"new", PyFunc(_c3_new, "Color3.new"))
    Color3.set(b"fromRGB", PyFunc(_c3_fromrgb, "Color3.fromRGB"))
    g["Color3"] = Color3

    return g

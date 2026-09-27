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
    __slots__ = ("hash",)

    def __init__(self):
        self.hash = {}

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


def truthy(v):
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
    raise LuaError("string.format: not implemented in emulator")


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
        "Black": 900, "Heavy": 1000,
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

    bit32 = LuaTable()
    for n, f in (("bxor", b_xor), ("band", b_and), ("bor", b_or),
                 ("bnot", b_not), ("rshift", b_rshift), ("lshift", b_lshift),
                 ("lrotate", b_lrotate), ("rrotate", b_rrotate)):
        bit32.set(n.encode(), PyFunc(f, "bit32." + n))
    g["bit32"] = bit32

    string = LuaTable()
    for n, f in (("byte", s_byte), ("char", s_char), ("rep", s_rep),
                 ("sub", s_sub), ("len", s_len), ("upper", s_upper),
                 ("lower", s_lower), ("format", s_format)):
        string.set(n.encode(), PyFunc(f, "string." + n))
    g["string"] = string

    mathlib = LuaTable()
    for n, f in (("sign", m_sign), ("floor", m_floor), ("abs", m_abs),
                 ("max", m_max), ("min", m_min), ("sqrt", m_sqrt)):
        mathlib.set(n.encode(), PyFunc(f, "math." + n))
    mathlib.set(b"huge", _math.inf)
    mathlib.set(b"pi", _math.pi)
    g["math"] = mathlib

    table = LuaTable()
    for n, f in (("isfrozen", t_isfrozen), ("insert", t_insert),
                 ("remove", t_remove), ("concat", t_concat)):
        table.set(n.encode(), PyFunc(f, "table." + n))
    g["table"] = table

    buffer = LuaTable()
    for n, f in (("fromstring", buf_fromstring), ("tostring", buf_tostring),
                 ("len", buf_len), ("readu32", buf_readu32),
                 ("writeu32", buf_writeu32), ("readu8", buf_readu8),
                 ("writeu8", buf_writeu8), ("readstring", buf_readstring),
                 ("readi16", buf_readi16)):
        buffer.set(n.encode(), PyFunc(f, "buffer." + n))
    g["buffer"] = buffer

    coroutine = LuaTable()
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
        f.props[b"Bold"] = (w.name == "Bold")
        return f

    Font = Instance("Font", "Font")
    Font.props[b"new"] = PyFunc(
        lambda family=None, weight=None, style=None: font_make(family, weight, style),
        "Font.new")
    Font.props[b"fromName"] = PyFunc(
        lambda name=None, weight=None, style=None: font_make(name, weight, style),
        "Font.fromName")
    g["Font"] = Font

    debug = LuaTable()

    def _debug_info(f, opts, *extra):
        # Luau semantics; results in fixed order s, l, n, a
        if isinstance(f, PyFunc):
            short = f.name.rsplit(".", 1)[-1].encode()
            s, l, n = b"[C]", -1.0, short
            varg, nparams = True, 0
        elif isinstance(f, LuaClosure):
            node = f.node
            nm = node.get("name") if "name" in node.fields else None
            s = b"=[script]"
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
    g["os"] = LuaTable()
    g["os"].set(b"time", PyFunc(lambda *a: 0.0, "os.time"))
    g["os"].set(b"clock", PyFunc(lambda *a: 0.0, "os.clock"))
    g["os"].set(b"date", PyFunc(lambda *a: b"Thu Jan  1 00:00:00 1970", "os.date"))
    g["unpack"] = PyFunc(lambda t: [t.get(float(i))
                                    for i in range(1, int(t.length()) + 1)]
                         if isinstance(t, LuaTable) else [],
                         "unpack")
    return g

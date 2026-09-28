"""Value-exact Roblox datatypes for the LUAST-L3 static emulator.

Every member the obfuscator's Z-accumulator can probe must return the SAME
number the real engine returns (float32 where the engine stores f32), or the
string-decryption keystream drifts and the payload renders as garbage.
Semantics mirrored from Roblox Studio / the Luau source (lveclib, lmathlib).
"""

from __future__ import annotations

import math as _math
import struct as _struct

from .lua_rt import NIL, LuaTable, PyFunc, Instance, LuaError

# ---------------------------------------------------------------- f32 helpers

def f32(x: float) -> float:
    """Round to float32 precision like the engine's vector/datatype math."""
    try:
        return _struct.unpack("f", _struct.pack("f", float(x)))[0]
    except (OverflowError, ValueError):
        return x


def int16(x: float) -> float:
    x = math_ceil_int(x)
    return (int(x) + 32768) % 65536 - 32768


def math_ceil_int(x: float) -> float:
    return _math.floor(x + 0.5) if x >= 0 else _math.ceil(x - 0.5)


# ---------------------------------------------------------------- vector math

def _v3_str(v) -> bytes:
    return ("%g, %g, %g" % (v.x, v.y, v.z)).encode()


class Vector3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = f32(x)
        self.y = f32(y)
        self.z = f32(z)

    def __repr__(self):
        return "Vector3(%s, %s, %s)" % (self.x, self.y, self.z)

    def _add(self, o):
        return Vector3(self.x + o.x, self.y + o.y, self.z + o.z)

    def _sub(self, o):
        return Vector3(self.x - o.x, self.y - o.y, self.z - o.z)

    def _mul(self, k):
        return Vector3(self.x * k, self.y * k, self.z * k)

    def _div(self, k):
        return Vector3(self.x / k, self.y / k, self.z / k)

    def _neg(self):
        return Vector3(-self.x, -self.y, -self.z)

    def magnitude(self):
        return f32(_math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z))

    def unit(self):
        m = self.magnitude()
        if m == 0:
            return Vector3(0, 0, 0)
        return Vector3(self.x / m, self.y / m, self.z / m)

    def dot(self, o):
        return f32(self.x * o.x + self.y * o.y + self.z * o.z)

    def cross(self, o):
        return Vector3(self.y * o.z - self.z * o.y,
                       self.z * o.x - self.x * o.z,
                       self.x * o.y - self.y * o.x)


class Vector2:
    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=0.0):
        self.x = f32(x)
        self.y = f32(y)

    def __repr__(self):
        return "Vector2(%s, %s)" % (self.x, self.y)


class UDim:
    __slots__ = ("scale", "offset")

    def __init__(self, scale=0.0, offset=0):
        self.scale = f32(scale)
        self.offset = int(offset)

    def __repr__(self):
        return "UDim(%s, %d)" % (self.scale, self.offset)


class UDim2:
    __slots__ = ("xd", "yd")

    def __init__(self, xs=0.0, xo=0, ys=0.0, yo=0):
        self.xd = UDim(xs, xo)
        self.yd = UDim(ys, yo)

    def __repr__(self):
        return "UDim2(%s, %d, %s, %d)" % (self.xd.scale, self.xd.offset,
                                          self.yd.scale, self.yd.offset)


class Color3:
    __slots__ = ("r", "g", "b")

    def __init__(self, r=0.0, g=0.0, b=0.0):
        self.r = f32(r)
        self.g = f32(g)
        self.b = f32(b)

    def __repr__(self):
        return "%s, %s, %s" % (self.r, self.g, self.b)


class CFrame:
    """Position + 3x3 rotation (row vectors R = [right, up, -look] columns)."""
    __slots__ = ("pos", "r00", "r01", "r02", "r10", "r11", "r12", "r20", "r21", "r22")

    def __init__(self, x=0.0, y=0.0, z=0.0, r00=1.0, r01=0.0, r02=0.0,
                 r10=0.0, r11=1.0, r12=0.0, r20=0.0, r21=0.0, r22=1.0):
        self.pos = Vector3(x, y, z)
        self.r00, self.r01, self.r02 = f32(r00), f32(r01), f32(r02)
        self.r10, self.r11, self.r12 = f32(r10), f32(r11), f32(r12)
        self.r20, self.r21, self.r22 = f32(r20), f32(r21), f32(r22)

    def __repr__(self):
        p = self.pos
        return "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s" % (
            p.x, p.y, p.z, self.r00, self.r01, self.r02, self.r10, self.r11,
            self.r12, self.r20, self.r21, self.r22)

    # members accessed by probes
    def Position(self):
        return self.pos

    def LookVector(self):
        return Vector3(-self.r02, -self.r12, -self.r22)

    def RightVector(self):
        return Vector3(self.r00, self.r10, self.r20)

    def UpVector(self):
        return Vector3(self.r01, self.r11, self.r21)


def cframe_mul_components(a: CFrame, b: CFrame) -> CFrame:
    return CFrame(
        a.pos.x * b.r00 + a.pos.y * b.r10 + a.pos.z * b.r20 + b.pos.x,
        a.pos.x * b.r01 + a.pos.y * b.r11 + a.pos.z * b.r21 + b.pos.y,
        a.pos.x * b.r02 + a.pos.y * b.r12 + a.pos.z * b.r22 + b.pos.z,
        a.r00 * b.r00 + a.r01 * b.r10 + a.r02 * b.r20,
        a.r00 * b.r01 + a.r01 * b.r11 + a.r02 * b.r21,
        a.r00 * b.r02 + a.r01 * b.r12 + a.r02 * b.r22,
        a.r10 * b.r00 + a.r11 * b.r10 + a.r12 * b.r20,
        a.r10 * b.r01 + a.r11 * b.r11 + a.r12 * b.r21,
        a.r10 * b.r02 + a.r11 * b.r12 + a.r12 * b.r22,
        a.r20 * b.r00 + a.r21 * b.r10 + a.r22 * b.r20,
        a.r20 * b.r01 + a.r21 * b.r11 + a.r22 * b.r21,
        a.r20 * b.r02 + a.r21 * b.r12 + a.r22 * b.r22,
    )


class NumberRange:
    __slots__ = ("min", "max")

    def __init__(self, mn, mx=None):
        self.min = f32(mn)
        self.max = f32(mx if mx is not None else mn)

    def __repr__(self):
        return "%s, %s" % (self.min, self.max)


class NumberSequenceKeypoint:
    __slots__ = ("time", "value", "envelope")

    def __init__(self, t, v, env=0.0):
        self.time = f32(t)
        self.value = f32(v)
        self.envelope = f32(env)


class ColorSequenceKeypoint:
    __slots__ = ("time", "color")

    def __init__(self, t, c):
        self.time = f32(t)
        self.color = c


class NumberSequence:
    __slots__ = ("points",)

    def __init__(self, points):
        self.points = list(points)


class ColorSequence:
    __slots__ = ("points",)

    def __init__(self, points):
        self.points = list(points)


class Rect:
    __slots__ = ("minx", "miny", "maxx", "maxy")

    def __init__(self, a=0.0, b=0.0, c=0.0, d=0.0):
        self.minx, self.miny, self.maxx, self.maxy = f32(a), f32(b), f32(c), f32(d)


class Ray:
    __slots__ = ("origin", "direction")

    def __init__(self, origin=None, direction=None):
        self.origin = origin or Vector3()
        self.direction = direction or Vector3()


class Faces:
    __slots__ = ("items",)

    AXIS = {"Right": "X", "Left": "X", "Top": "Y", "Bottom": "Y",
            "Back": "Z", "Front": "Z"}

    def __init__(self, *normals):
        self.items = []
        for n in normals:
            name = n.name if isinstance(n, EnumItem) else str(n)
            self.items.append(name)

    def has(self, name):
        return name in self.items


class Axes:
    __slots__ = ("items",)

    def __init__(self, *axes):
        self.items = []
        for a in axes:
            name = a.name if isinstance(a, EnumItem) else str(a)
            self.items.append(name)

    def has(self, name):
        return name in self.items


class TweenInfo:
    __slots__ = ("time", "easingStyle", "easingDirection", "repeatCount",
                 "reverses", "delayTime")

    def __init__(self, t=1.0, style=None, direction=None, rep=0, rev=False, delay=0.0):
        self.time = f32(t)
        self.easingStyle = style
        self.easingDirection = direction
        self.repeatCount = int(rep)
        self.reverses = bool(rev)
        self.delayTime = f32(delay)


class Random:
    """Roblox Random object (deterministic Mersenne-style; probed for exist)."""
    __slots__ = ("seed", "state")

    def __init__(self, seed=None):
        self.seed = seed
        self.state = int(seed if seed is not None else 0) & 0xFFFFFFFF or 2463534242
        self._next_state = self._xorshift(self.state)

    @staticmethod
    def _xorshift(x):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        return x & 0xFFFFFFFF

    def NextNumber(self, a=None, b=None):
        self._next_state = self._xorshift(self._next_state)
        v = self._next_state / 4294967296.0
        if a is None:
            return f32(v)
        return f32(a + v * (b - a))

    def NextInteger(self, a, b):
        self._next_state = self._xorshift(self._next_state)
        v = self._next_state / 4294967296.0
        return float(int(a + v * (b - a + 1)))


from .lua_rt import EnumItem  # noqa: E402  (after class defs to avoid cycles)


# ---------------------------------------------------------------- library tables

def make_vector_lib():
    lib = LuaTable()

    def v3_create(x=0.0, y=0.0, z=0.0):
        return Vector3(x, y, z)

    def v3_mag(v):
        return v.magnitude()

    def v3_unit(v):
        return v.unit()

    def v3_dot(a, b):
        return a.dot(b)

    def v3_cross(a, b):
        return a.cross(b)

    def v3_min(a, b):
        return Vector3(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z))

    def v3_max(a, b):
        return Vector3(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z))

    def v3_floor(v):
        return Vector3(_math.floor(v.x), _math.floor(v.y), _math.floor(v.z))

    def v3_ceil(v):
        return Vector3(_math.ceil(v.x), _math.ceil(v.y), _math.ceil(v.z))

    def v3_abs(v):
        return Vector3(abs(v.x), abs(v.y), abs(v.z))

    def v3_angle(a, b, relative_axis=None):
        d = max(-1.0, min(1.0, a.dot(b) / (a.magnitude() * b.magnitude())))
        if relative_axis is not None:
            # signed angle around axis (Luau behaviour)
            cr = a.cross(b)
            sign = 1.0 if cr.dot(relative_axis) < 0 else -1.0
            return f32(sign * _math.acos(d))
        return f32(_math.acos(d))

    lib.set(b"create", PyFunc(v3_create, "vector.create"))
    lib.set(b"magnitude", PyFunc(v3_mag, "vector.magnitude"))
    lib.set(b"normalize", PyFunc(v3_unit, "vector.normalize"))
    lib.set(b"dot", PyFunc(v3_dot, "vector.dot"))
    lib.set(b"cross", PyFunc(v3_cross, "vector.cross"))
    lib.set(b"min", PyFunc(v3_min, "vector.min"))
    lib.set(b"max", PyFunc(v3_max, "vector.max"))
    lib.set(b"floor", PyFunc(v3_floor, "vector.floor"))
    lib.set(b"ceil", PyFunc(v3_ceil, "vector.ceil"))
    lib.set(b"abs", PyFunc(v3_abs, "vector.abs"))
    lib.set(b"zero", Vector3(0, 0, 0))
    lib.set(b"one", Vector3(1, 1, 1))
    lib.set(b"angle", PyFunc(v3_angle, "vector.angle"))
    return lib


def make_task_lib(interp):
    lib = LuaTable()

    def spawn(f, *args):
        try:
            interp.call_function(f, list(args))
        except LuaError:
            pass
        return []

    lib.set(b"spawn", PyFunc(spawn, "task.spawn"))
    lib.set(b"defer", PyFunc(spawn, "task.defer"))
    lib.set(b"delay", PyFunc(spawn, "task.delay"))
    lib.set(b"cancel", PyFunc(lambda th: [], "task.cancel"))
    lib.set(b"wait", PyFunc(lambda *a: [0.0], "task.wait"))
    lib.set(b"isyieldable", PyFunc(lambda: False, "task.isyieldable"))
    return lib


def make_datatype_libs():
    """Build the Roblox datatype constructor tables the emulator exposes."""
    out = {}

    # ---- CFrame
    cf = LuaTable()

    def cf_new(*args):
        if len(args) == 0:
            return CFrame()
        if len(args) == 1 and isinstance(args[0], Vector3):
            v = args[0]
            return CFrame(v.x, v.y, v.z)
        if len(args) == 12:
            return CFrame(*[float(a) for a in args])
        if len(args) == 3:
            return CFrame(float(args[0]), float(args[1]), float(args[2]))
        # (pos, lookAt) form
        if len(args) == 2 and isinstance(args[0], Vector3) and isinstance(args[1], Vector3):
            a, b = args
            fwd = b._sub(a).unit()
            up0 = Vector3(0, 1, 0)
            right = up0.cross(fwd).unit()
            up = fwd.cross(right)
            return CFrame(a.pos.x, a.pos.y, a.pos.z,
                          right.x, up.x, fwd.x, right.y, up.y, fwd.y,
                          right.z, up.z, fwd.z)
        raise LuaError("CFrame.new: bad arguments")

    def cf_angles(rx=0.0, ry=0.0, rz=0.0):
        cx, sx = _math.cos(rx), _math.sin(rx)
        cy, sy = _math.cos(ry), _math.sin(ry)
        cz, sz = _math.cos(rz), _math.sin(rz)
        # fromEulerAnglesXYZ: R = Rz*Ry*Rx (column-major layout in CFrame)
        r00, r01, r02 = cz * sy, -cz * sy * sx + sz * cy, cz * sy * cx + sz * sy
        r10, r11, r12 = sx, cx * cy, -cx * sx
        r20, r21, r22 = -sz * cy, sz * sy * sx + cz * cy, -sz * sy * cx + cz * cy
        return CFrame(0, 0, 0, r00, r01, r02, r10, r11, r12, r20, r21, r22)

    def cf_lookat(a, b, up=None):
        return cf_new(a, b)

    def cf_identity():
        return CFrame()

    cf.set(b"new", PyFunc(cf_new, "CFrame.new"))
    cf.set(b"identity", PyFunc(cf_identity, "CFrame.identity"))
    cf.set(b"fromEulerAnglesXYZ", PyFunc(cf_angles, "CFrame.fromEulerAnglesXYZ"))
    cf.set(b"Angles", PyFunc(cf_angles, "CFrame.Angles"))
    cf.set(b"lookAt", PyFunc(cf_lookat, "CFrame.lookAt"))
    cf.set(b"fromMatrix", PyFunc(cf_new, "CFrame.fromMatrix"))
    out["CFrame"] = cf

    # ---- Vector3 / Vector2
    v3 = LuaTable()
    v3.set(b"new", PyFunc(lambda x=0.0, y=0.0, z=0.0: Vector3(x, y, z), "Vector3.new"))
    out["Vector3"] = v3

    v2 = LuaTable()
    v2.set(b"new", PyFunc(lambda x=0.0, y=0.0: Vector2(x, y), "Vector2.new"))
    out["Vector2"] = v2

    # ---- Vector3int16 / Vector2int16 (int16-wrap arithmetic)
    v3i = LuaTable()

    def v3i_new(x=0, y=0, z=0):
        o = Vector3(int16(float(x)), int16(float(y)), int16(float(z)))
        o.__class__ = _V3Int16
        return o

    v3i.set(b"new", PyFunc(v3i_new, "Vector3int16.new"))
    out["Vector3int16"] = v3i

    v2i = LuaTable()

    def v2i_new(x=0, y=0):
        o = _V2Int16(0, 0)
        o.x = int16(float(x))
        o.y = int16(float(y))
        return o

    v2i.set(b"new", PyFunc(v2i_new, "Vector2int16.new"))
    out["Vector2int16"] = v2i

    # ---- Color3
    c3 = LuaTable()
    c3.set(b"new", PyFunc(lambda r=0.0, g=0.0, b=0.0: Color3(r, g, b), "Color3.new"))
    c3.set(b"fromRGB", PyFunc(lambda r=0, g=0, b=0: Color3(r / 255.0, g / 255.0, b / 255.0),
                              "Color3.fromRGB"))

    def hsv(h, s, v):
        h = (h % 360.0) / 60.0
        i = _math.floor(h)
        f = h - i
        p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
        i = int(i % 6)
        tab = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
        r, g, b = tab[i]
        return Color3(r, g, b)

    c3.set(b"fromHSV", PyFunc(hsv, "Color3.fromHSV"))
    out["Color3"] = c3

    # ---- UDim / UDim2
    ud = LuaTable()
    ud.set(b"new", PyFunc(lambda s=0.0, o=0: UDim(s, o), "UDim.new"))
    out["UDim"] = ud
    ud2 = LuaTable()

    def ud2_new(*a):
        if len(a) == 4:
            return UDim2(float(a[0]), float(a[1]), float(a[2]), float(a[3]))
        if len(a) == 2 and isinstance(a[0], UDim) and isinstance(a[1], UDim):
            return UDim2(a[0].scale, a[0].offset, a[1].scale, a[1].offset)
        return UDim2()

    ud2.set(b"new", PyFunc(ud2_new, "UDim2.new"))
    out["UDim2"] = ud2

    # ---- misc constructors
    nr = LuaTable()
    nr.set(b"new", PyFunc(lambda a, b=None: NumberRange(float(a), float(b) if b is not None else None),
                          "NumberRange.new"))
    out["NumberRange"] = nr

    nsk = LuaTable()
    nsk.set(b"new", PyFunc(lambda t, v, e=0.0: NumberSequenceKeypoint(float(t), float(v), float(e)),
                           "NumberSequenceKeypoint.new"))
    out["NumberSequenceKeypoint"] = nsk

    def ns_new(*a):
        if len(a) == 2:
            return NumberSequence([NumberSequenceKeypoint(0.0, float(a[0])),
                                   NumberSequenceKeypoint(1.0, float(a[1]))])
        return NumberSequence(list(a))

    nsl = LuaTable()
    nsl.set(b"new", PyFunc(ns_new, "NumberSequence.new"))
    out["NumberSequence"] = nsl

    csk = LuaTable()
    csk.set(b"new", PyFunc(lambda t, c: ColorSequenceKeypoint(float(t), c),
                           "ColorSequenceKeypoint.new"))
    out["ColorSequenceKeypoint"] = csk

    def cs_new(*a):
        if len(a) == 2 and isinstance(a[0], Color3):
            return ColorSequence([ColorSequenceKeypoint(0.0, a[0]),
                                  ColorSequenceKeypoint(1.0, a[1])])
        return ColorSequence(list(a))

    csl = LuaTable()
    csl.set(b"new", PyFunc(cs_new, "ColorSequence.new"))
    out["ColorSequence"] = csl

    rect = LuaTable()
    rect.set(b"new", PyFunc(lambda a=0.0, b=0.0, c=0.0, d=0.0: Rect(float(a), float(b), float(c), float(d)),
                            "Rect.new"))
    out["Rect"] = rect

    ray = LuaTable()
    ray.set(b"new", PyFunc(lambda o=None, d=None: Ray(o, d), "Ray.new"))
    out["Ray"] = ray

    ti = LuaTable()

    def ti_new(t=1.0, style=None, direction=None, rep=0, rev=False, delay=0.0):
        return TweenInfo(float(t), style, direction, rep, rev, float(delay))

    ti.set(b"new", PyFunc(ti_new, "TweenInfo.new"))
    out["TweenInfo"] = ti

    faces = LuaTable()
    faces.set(b"new", PyFunc(lambda *a: Faces(*a), "Faces.new"))
    out["Faces"] = faces

    axes = LuaTable()
    axes.set(b"new", PyFunc(lambda *a: Axes(*a), "Axes.new"))
    out["Axes"] = axes

    rnd = LuaTable()

    def rnd_new(seed=None):
        s = float(seed) if seed is not None else None
        return Random(s)

    rnd.set(b"new", PyFunc(rnd_new, "Random.new"))
    out["Random"] = rnd

    return out


class _V2Int16(Vector2):
    pass


class _V3Int16(Vector3):
    pass


def arith_vector(op, a, b):
    """Vector arithmetic incl. int16 wrap and scalar broadcast."""
    pairs = (
        (Vector3, lambda x, y, z: Vector3(x, y, z)),
        (Vector2, lambda x, y, z=None: Vector2(x, y)),
    )
    for cls, mk in pairs:
        if isinstance(a, cls) or isinstance(b, cls):
            wrap = None
            if isinstance(a, (_V2Int16, _V3Int16)) or isinstance(b, (_V2Int16, _V3Int16)):
                wrap = int16
            if cls is Vector2:
                ax, ay = (a.x, a.y) if isinstance(a, Vector2) else (float(a), float(a))
                bx, by = (b.x, b.y) if isinstance(b, Vector2) else (float(b), float(b))
                comps = [(ax, bx), (ay, by)]
                n = 2
            else:
                az = a.z if isinstance(a, Vector3) else 0.0
                bz = b.z if isinstance(b, Vector3) else 0.0
                ax = a.x if isinstance(a, Vector3) else float(a)
                ay = a.y if isinstance(a, Vector3) else float(a)
                bx = b.x if isinstance(b, Vector3) else float(b)
                by = b.y if isinstance(b, Vector3) else float(b)
                comps = [(ax, bx), (ay, by), (az, bz)]
                n = 3
            if isinstance(a, Vector3) and isinstance(b, Vector3):
                res = [f32(comps[0][0] + comps[0][1]) for _ in (0,)]
                res = None
            outs = []
            for i in range(n):
                x, y = comps[i][0], comps[i][1]
                if op == "+":
                    v = x + y
                elif op == "-":
                    v = x - y
                elif op == "*":
                    v = x * y
                elif op == "/":
                    v = x / y if y != 0 else (_math.inf if x > 0 else (-_math.inf if x < 0 else _math.nan))
                else:
                    raise LuaError("unsupported vector op '%s'" % op)
                outs.append(f32(v))
            if wrap:
                outs = [wrap(v) for v in outs]
            if isinstance(a, _V2Int16) or isinstance(b, _V2Int16):
                o = _V2Int16(0, 0)
                o.x, o.y = outs[0], outs[1]
                return o
            if isinstance(a, _V3Int16) or isinstance(b, _V3Int16):
                o = _V3Int16(0, 0, 0)
                o.x, o.y, o.z = outs[0], outs[1], outs[2]
                return o
            return mk(*outs)
    return None


# ---------------------------------------------------------------- member index

DATATYPE_MEMBERS = {
    Vector3: {"X": "x", "Y": "y", "Z": "z", "Magnitude": "_mag", "Unit": "_unit",
              "XVector": None, "YVector": None, "ZVector": None},
    Vector2: {"X": "x", "Y": "y", "Magnitude": "_mag", "Unit": "_unit"},
    UDim: {"Scale": "scale", "Offset": "offset"},
    UDim2: {"X": "xd", "Y": "yd", "Width": "xd", "Height": "yd"},
    Color3: {"R": "r", "G": "g", "B": "b"},
    NumberRange: {"Min": "min", "Max": "max"},
    NumberSequenceKeypoint: {"Time": "time", "Value": "value", "Envelope": "envelope"},
    ColorSequenceKeypoint: {"Time": "time", "Color": "color"},
    Rect: {"MinX": "minx", "MinY": "miny", "MaxX": "maxx", "MaxY": "maxy"},
    Ray: {"Origin": "origin", "Direction": "direction", "Unit": None, "ClosestPoint": None},
    TweenInfo: {"Time": "time", "RepeatCount": "repeatCount", "Reverses": "reverses",
                "DelayTime": "delayTime", "EasingStyle": "easingStyle",
                "EasingDirection": "easingDirection"},
}


def index_datatype(obj, key: bytes):
    """Member lookup for the shim datatypes; returns (found, value)."""
    if isinstance(key, str):
        key = key.encode("latin-1", "ignore")
    ks = key.decode("latin-1", "ignore")

    for cls, members in DATATYPE_MEMBERS.items():
        if isinstance(obj, cls):
            if ks not in members:
                return False, None
            attr = members[ks]
            if attr is None:
                return True, NIL
            v = getattr(obj, attr)
            if callable(v):
                return True, v()
            return True, v

    if isinstance(obj, CFrame):
        names = {b"Position": lambda: obj.pos,
                 b"LookVector": lambda: obj.LookVector(),
                 b"RightVector": lambda: obj.RightVector(),
                 b"UpVector": lambda: obj.UpVector(),
                 b"X": lambda: obj.pos.x, b"Y": lambda: obj.pos.y, b"Z": lambda: obj.pos.z,
                 b"Identity": lambda: CFrame()}
        if key in names:
            return True, names[key]()
        return False, None

    if isinstance(obj, Faces):
        dec = {"Right": b"Right", "Left": b"Left", "Top": b"Top", "Bottom": b"Bottom",
               "Front": b"Front", "Back": b"Back"}
        if key in dec:
            return True, obj.has(dec[key].decode())
        return False, None

    if isinstance(obj, Axes):
        dec = {"X": b"X", "Y": b"Y", "Z": b"Z"}
        if key in dec:
            return True, obj.has(dec[key].decode())
        return False, None

    if isinstance(obj, Random):
        if key in (b"NextNumber", b"NextInteger", b"NextVector3", b"Clone"):
            def _call(*args):
                if key == b"NextNumber":
                    return obj.NextNumber(*args) if len(args) > 1 else obj.NextNumber(*args)
                if key == b"NextInteger":
                    return obj.NextInteger(*args)
                if key == b"Clone":
                    return Random(obj.seed)
                return Vector3(0, 0, 0)
            return True, PyFunc(_call, "Random." + key.decode())
        return False, None

    if isinstance(obj, EnumItem):
        if key == b"Name":
            return True, obj.name.encode()
        if key == b"Value":
            return True, obj.value
        if key == b"EnumType":
            return True, obj.etype
        return False, None

    return False, None


def call_datatype_method(obj, name: bytes, args):
    """Method calls on shim datatypes (obj:Method(args)); returns (found, value)."""
    if isinstance(obj, Vector3):
        if name == b"Dot":
            return True, obj.dot(args[0])
        if name == b"Cross":
            return True, obj.cross(args[0])
        if name == b"Lerp":
            t = f32(float(args[1]))
            return True, Vector3(obj.x + (args[0].x - obj.x) * t,
                                 obj.y + (args[0].y - obj.y) * t,
                                 obj.z + (args[0].z - obj.z) * t)
        if name == b"FuzzyEq":
            return True, False
    if isinstance(obj, CFrame):
        if name == b"PointToWorldSpace":
            return True, obj.pos
        if name == b"GetComponents":
            return True, [obj.pos.x, obj.pos.y, obj.pos.z, obj.r00, obj.r01, obj.r02,
                          obj.r10, obj.r11, obj.r12, obj.r20, obj.r21, obj.r22]
        if name == b"Inverse":
            return True, CFrame(-obj.pos.x, -obj.pos.y, -obj.pos.z)
        if name == b"ToWorldSpace":
            return True, obj
    return False, None

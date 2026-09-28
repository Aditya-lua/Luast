"""LUAST opaque-predicate evaluation.

The obfuscator pads dispatch trees with junk branches guarded by predicates
that are mathematically decided regardless of runtime values:

  1. self comparisons            x == x / x <= x  (same expression both sides)
  2. vector identities           dot(cross(A,B),C) == dot(cross(B,C),A)
                                 dot(cross(A,cross(B,C)),D) == dot(B*dot(A,C)-C*dot(A,B),D) + K
  3. angle gap                   abs(angle(A,B,C)) - abs(angle(B,A,C)) == K, |K| > pi
  4. string doubling             s:len() >= s:gsub(p, "%1%1", n):len()

Vector operands are seeded from runtime values, so identities are verified by
probing with random concrete vectors: a predicate that evaluates the same way
for several random inputs is folded; anything else is left untouched.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

from .model import Node
from .passes import UNKNOWN, is_number, truthy

_PROBE_SEEDS = 4
_MAX_PROBE_NODES = 20000
_PROBE_TIME_BUDGET = 0.05


class Vector3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, other):
        if isinstance(other, Vector3):
            return self.x * other.x + self.y * other.y + self.z * other.z
        return Vector3(self.x * other, self.y * other, self.z * other)

    __rmul__ = __mul__


def _random_vector(rng: random.Random) -> Vector3:
    return Vector3(rng.randrange(1, 1000), rng.randrange(1, 1000), rng.randrange(1, 1000))


def _random_scalar(rng: random.Random) -> Any:
    return rng.randrange(1, 1000)


def vector_call(name: str | None, args: list[Any]) -> Any:
    """Evaluate vector.* calls when all operands are concrete Vector3/numbers."""
    if name == "vector.create":
        if len(args) >= 3 and all(is_number(value) for value in args[:3]):
            return Vector3(args[0], args[1], args[2])
        return UNKNOWN
    if len(args) >= 2 and isinstance(args[0], Vector3) and isinstance(args[1], Vector3):
        a, b = args[0], args[1]
        if name == "vector.dot":
            return a * b
        if name == "vector.cross":
            return Vector3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
    if len(args) >= 1 and isinstance(args[0], Vector3):
        a = args[0]
        if name == "vector.floor":
            return Vector3(math.floor(a.x), math.floor(a.y), math.floor(a.z))
        if name == "vector.ceil":
            return Vector3(math.ceil(a.x), math.ceil(a.y), math.ceil(a.z))
    if len(args) >= 3 and all(isinstance(value, Vector3) for value in args[:3]):
        a, b, c = args[0], args[1], args[2]
        if name == "vector.angle":
            u = Vector3(a.x - b.x, a.y - b.y, a.z - b.z)
            v = Vector3(c.x - b.x, c.y - b.y, c.z - b.z)
            uu = u * u
            vv = v * v
            if uu == 0 or vv == 0:
                return 0.0
            cosine = max(-1.0, min(1.0, (u * v) / math.sqrt(uu * vv)))
            return math.acos(cosine)
    return UNKNOWN


def _vector_arith(op: str, left: Any, right: Any) -> Any:
    if isinstance(left, Vector3) and isinstance(right, Vector3):
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        return UNKNOWN
    if isinstance(left, Vector3) and is_number(right):
        if op in {"*", "/"}:
            return left * right if op == "*" else Vector3(left.x / right, left.y / right, left.z / right)
        return UNKNOWN
    if is_number(left) and isinstance(right, Vector3):
        if op == "*":
            return right * left
        return UNKNOWN
    return UNKNOWN


def _collect_vector_atoms(node: Node | None, atoms: dict[int, Node], counter: list[int], vector_args: set[int] | None = None) -> None:
    """Collect probe targets: vector operands, unknown names, and string
    constants that appear inside arithmetic (junk states compute with
    pooled strings; they are never really strings there)."""
    if vector_args is None:
        vector_args = set()
    if node is None or counter[0] > _MAX_PROBE_NODES:
        return
    counter[0] += 1
    kind = node.kind
    if kind == "call":
        func = node.get("func")
        name = _dotted(func)
        if name and name.startswith("vector."):
            for arg in node.get("args", []):
                if isinstance(arg, Node) and arg.kind not in {"number", "bool", "nil"}:
                    atoms.setdefault(id(arg), arg)
                    vector_args.add(id(arg))
                    _collect_vector_atoms(arg, atoms, counter, vector_args)
            return
        if name and (name.startswith("math.") or name in {"tonumber", "tostring"}):
            for arg in node.get("args", []):
                _collect_vector_atoms(arg, atoms, counter, vector_args)
            return
        return
    if kind == "methodcall":
        for arg in node.get("args", []):
            _collect_vector_atoms(arg, atoms, counter, vector_args)
        _collect_vector_atoms(node.get("obj"), atoms, counter, vector_args)
        return
    if kind == "name":
        atoms.setdefault(id(node), node)
        return
    if kind == "string":
        return
    if kind in {"function", "localfunc"}:
        return
    if kind == "binop" and node.get("op") in {"+", "-", "*", "/", "%", "^"}:
        for side in (node.get("left"), node.get("right")):
            if isinstance(side, Node) and side.kind == "string":
                atoms.setdefault(id(side), side)
        _collect_vector_atoms(node.get("left"), atoms, counter, vector_args)
        _collect_vector_atoms(node.get("right"), atoms, counter, vector_args)
        return
    for field in node.fields.values():
        if isinstance(field, Node):
            _collect_vector_atoms(field, atoms, counter, vector_args)
        elif isinstance(field, (list, tuple)):
            for item in field:
                if isinstance(item, Node):
                    _collect_vector_atoms(item, atoms, counter, vector_args)


def _dotted(node: Node | None) -> str | None:
    if not isinstance(node, Node):
        return None
    if node.kind == "name":
        return node.get("name")
    if node.kind == "indexname":
        base = _dotted(node.get("obj"))
        return base + "." + node.get("name", "") if base else None
    return None


class _ProbeAbort(Exception):
    pass


def _probe_eval(node: Node | None, assignment: dict[int, Any], base_eval: Callable[[Node], Any], budget: list, depth: int = 0) -> Any:
    """Evaluate an expression treating atoms in `assignment` as concrete vectors."""
    if node is None or depth > 48:
        return UNKNOWN
    budget[0] -= 1
    if budget[0] <= 0 or time.monotonic() > budget[1]:
        raise _ProbeAbort()
    if id(node) in assignment:
        return assignment[id(node)]
    kind = node.kind
    if kind == "paren":
        return _probe_eval(node.get("expr"), assignment, base_eval, budget, depth + 1)
    if kind == "number" or kind == "string" or kind == "bool":
        return node.get("value")
    if kind == "nil":
        return None
    if kind == "unop":
        value = _probe_eval(node.get("expr"), assignment, base_eval, budget, depth + 1)
        if value is UNKNOWN:
            return UNKNOWN
        op = node.get("op")
        if op == "not":
            return not truthy(value)
        if op == "-" and is_number(value):
            return -value
        if op == "-" and isinstance(value, Vector3):
            return Vector3(-value.x, -value.y, -value.z)
        return UNKNOWN
    if kind == "binop":
        op = node.get("op")
        if op in {"and", "or"}:
            left = _probe_eval(node.get("left"), assignment, base_eval, budget, depth + 1)
            if left is UNKNOWN:
                return UNKNOWN
            if op == "and":
                return _probe_eval(node.get("right"), assignment, base_eval, budget, depth + 1) if truthy(left) else left
            return left if truthy(left) else _probe_eval(node.get("right"), assignment, base_eval, budget, depth + 1)
        left = _probe_eval(node.get("left"), assignment, base_eval, budget, depth + 1)
        right = _probe_eval(node.get("right"), assignment, base_eval, budget, depth + 1)
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        if op in {"==", "~=", "<", "<=", ">", ">="}:
            try:
                if op == "==":
                    return left == right
                if op == "~=":
                    return left != right
                if op == "<":
                    return left < right
                if op == "<=":
                    return left <= right
                if op == ">":
                    return left > right
                return left >= right
            except (TypeError, ValueError):
                return UNKNOWN
        vector_result = _vector_arith(op, left, right)
        if vector_result is not UNKNOWN:
            return vector_result
        if is_number(left) and is_number(right):
            try:
                if abs(left) > 2 ** 53 or abs(right) > 2 ** 53 or (op == "^" and abs(right) > 64):
                    return UNKNOWN
                if op == "+":
                    return left + right
                if op == "-":
                    return left - right
                if op == "*":
                    return left * right
                if op == "/":
                    return left / right
                if op == "%":
                    return math.fmod(left, right)
                if op == "^":
                    return left ** right
                return UNKNOWN
            except (ArithmeticError, ValueError, OverflowError, ZeroDivisionError):
                return UNKNOWN
        return UNKNOWN
    if kind == "call":
        func = node.get("func")
        name = _dotted(func)
        if name == "math.abs":
            args = node.get("args") or [None]
            value = _probe_eval(args[0], assignment, base_eval, budget, depth + 1)
            return abs(value) if is_number(value) else UNKNOWN
        if name == "math.floor":
            args = node.get("args") or [None]
            value = _probe_eval(args[0], assignment, base_eval, budget, depth + 1)
            return math.floor(value) if is_number(value) else UNKNOWN
        if name == "math.ceil":
            args = node.get("args") or [None]
            value = _probe_eval(args[0], assignment, base_eval, budget, depth + 1)
            return math.ceil(value) if is_number(value) else UNKNOWN
        if name and name.startswith("vector."):
            args = [_probe_eval(arg, assignment, base_eval, budget, depth + 1) for arg in node.get("args", [])]
            if any(value is UNKNOWN for value in args):
                return UNKNOWN
            return vector_call(name, args)
        return UNKNOWN
    if kind == "ifexpr":
        condition = _probe_eval(node.get("cond"), assignment, base_eval, budget, depth + 1)
        if condition is UNKNOWN:
            return UNKNOWN
        if truthy(condition):
            return _probe_eval(node.get("then"), assignment, base_eval, budget, depth + 1)
        for branch_condition, branch_value in node.get("elifs", []):
            branch = _probe_eval(branch_condition, assignment, base_eval, budget, depth + 1)
            if branch is UNKNOWN:
                return UNKNOWN
            if truthy(branch):
                return _probe_eval(branch_value, assignment, base_eval, budget, depth + 1)
        return _probe_eval(node.get("else_"), assignment, base_eval, budget, depth + 1)
    if kind == "index":
        return base_eval(node)
    return base_eval(node)


def _dotted_call_shape(node: Node) -> tuple[str, Node, list[Node]] | None:
    """Match `obj:name(args)` / `obj.name(args)` shapes for method probes."""
    if isinstance(node, Node) and node.kind == "methodcall":
        return (node.get("method", ""), node.get("obj"), list(node.get("args", [])))
    if node.kind != "call":
        return None
    func = node.get("func")
    if isinstance(func, Node) and func.kind == "methodname":
        args = node.get("args", [])
        return (func.get("name", ""), func.get("obj"), list(args))
    if isinstance(func, Node) and func.kind == "indexname":
        args = node.get("args", [])
        return (func.get("name", ""), func.get("obj"), list(args))
    return None


def _string_doubling_truth(node: Node) -> bool | None:
    """`s:len() >= s:gsub(p, r, n):len()` where r duplicates each match."""
    if node.kind != "binop" or node.get("op") not in {">=", "<", "<=", ">"}:
        return None
    left = _strip(node.get("left"))
    right = _strip(node.get("right"))
    left_call = _dotted_call_shape(left) if isinstance(left, Node) else None
    right_call = _dotted_call_shape(right) if isinstance(right, Node) else None
    if not left_call or not right_call:
        return None
    if left_call[0] != "len" or right_call[0] != "len":
        return None
    gsub = _dotted_call_shape(right_call[1]) if isinstance(right_call[1], Node) else None
    if not gsub or gsub[0] != "gsub":
        return None
    # both len() calls must measure the SAME subject string
    if _render(left_call[1]) != _render(gsub[1]):
        return None
    args = gsub[2]
    if len(args) < 3:
        return None
    repl = args[1]
    if not isinstance(repl, Node) or repl.kind != "string":
        return None
    pattern = repl.get("value")
    if not isinstance(pattern, (bytes, str)):
        return None
    text = pattern.decode("utf-8", "replace") if isinstance(pattern, bytes) else pattern
    # "%1%1"-style: every match is rewritten strictly longer.
    stripped = text.replace("%1", "").replace("%2", "").replace("%0", "")
    if "%" in stripped or len(text) <= 1:
        return None
    op = node.get("op")
    if op == ">=":
        return True
    if op == "<":
        return False
    return None


def _strip(node: Node | None) -> Node | None:
    while isinstance(node, Node) and node.kind == "paren":
        node = node.get("expr")
    return node


def _render(node: Node | None) -> str:
    if not isinstance(node, Node):
        return ""
    try:
        from .emitter import emit_expr_cached
        return emit_expr_cached(node)
    except Exception:
        return f"<{node.kind}@{node.start}:{node.end}>"


def self_compare_truth(node: Node) -> bool | None:
    """Fold `X op X` for pure deterministic comparisons of one expression."""
    if node.kind != "binop":
        return None
    op = node.get("op")
    if op not in {"==", "~=", "<", "<=", ">", ">="}:
        return None
    left = _strip(node.get("left"))
    right = _strip(node.get("right"))
    if left is None or right is None:
        return None
    if _render(left) != _render(right):
        return None
    if op in {"==", "<=", ">="}:
        return True
    return False


def angle_gap_truth(node: Node, base_eval: Callable[[Node], Any]) -> bool | None:
    """`math.abs(angle(A,B,C)) - math.abs(angle(B,A,C)) == K` with |K| > pi.

    Both angle results lie in [0, pi] (radians), so their absolute
    difference can never exceed pi; when the pooled constant K is beyond
    that range the comparison is always false. When K == 0 the predicate
    reduces to angle symmetry, decided by probing."""
    if node.kind != "binop" or node.get("op") not in {"==", "~="}:
        return None
    left = _strip(node.get("left"))
    right = _strip(node.get("right"))
    if not isinstance(left, Node) or not isinstance(right, Node):
        return None
    constant = None
    difference = None
    for candidate, other in ((left, right), (right, left)):
        value = base_eval(candidate)
        if is_number(value):
            constant = value
            difference = other
            break
    if constant is None or not isinstance(difference, Node) or difference.kind != "binop" or difference.get("op") != "-":
        return None
    if _angle_abs(difference.get("left")) is None or _angle_abs(difference.get("right")) is None:
        return None
    op = node.get("op")
    if abs(constant) > math.pi + 1e-9:
        return op == "~="
    if constant == 0:
        return _probe_truth(difference, base_eval)
    return None


def _angle_abs(node: Node | None) -> Node | None:
    """Return the angle call node inside math.abs(...), else None."""
    current = _strip(node)
    if not isinstance(current, Node) or current.kind != "call":
        return None
    func = current.get("func")
    name = _dotted(func)
    if name != "math.abs":
        return None
    args = current.get("args", [])
    if len(args) != 1:
        return None
    inner = _strip(args[0])
    if isinstance(inner, Node) and inner.kind == "paren":
        inner = _strip(inner)
    if not isinstance(inner, Node) or inner.kind != "call":
        return None
    inner_name = _dotted(inner.get("func"))
    if inner_name != "vector.angle":
        return None
    return inner


_verdict_cache: dict[int, bool | None] = {}


def clear_verdict_cache() -> None:
    """Reset probe verdict cache (node ids are only unique within one AST)."""
    _verdict_cache.clear()


def _is_probeable(node: Node | None) -> bool:
    """True for arithmetic/vector expressions; bare names and constants are
    excluded so equality checks against runtime values are never probed."""
    node = _strip(node)
    if not isinstance(node, Node):
        return False
    kind = node.kind
    if kind == "paren":
        return _is_probeable(node.get("expr"))
    if kind == "binop" and node.get("op") in {"+", "-", "*", "/", "%", "^"}:
        return True
    if kind == "unop" and node.get("op") == "-":
        return True
    if kind == "call":
        name = _dotted(node.get("func"))
        return bool(name) and (name.startswith("vector.") or name.startswith("math."))
    return False


def _probe_truth(node: Node, base_eval: Callable[[Node], Any]) -> bool | None:
    """Random-probe a predicate: fold only when truth is input-invariant."""
    cached = _verdict_cache.get(id(node))
    if cached is not None or id(node) in _verdict_cache:
        return cached
    result: bool | None = None
    left = _strip(node.get("left"))
    right = _strip(node.get("right"))
    if _is_probeable(left) and _is_probeable(right):
        atoms: dict[int, Node] = {}
        vector_args: set[int] = set()
        counter = [0]
        _collect_vector_atoms(node, atoms, counter, vector_args)
        if atoms:
            rng = random.Random(0x5EED)
            verdicts: list[bool] = []
            try:
                for _ in range(_PROBE_SEEDS):
                    assignment = {
                        identity: (_random_vector(rng) if identity in vector_args else _random_scalar(rng))
                        for identity in atoms
                    }
                    budget = [_MAX_PROBE_NODES, time.monotonic() + _PROBE_TIME_BUDGET]
                    probe = _probe_eval(node, assignment, base_eval, budget)
                    if probe is UNKNOWN or not isinstance(probe, bool):
                        verdicts = []
                        break
                    verdicts.append(probe)
                if verdicts and all(value == verdicts[0] for value in verdicts):
                    result = verdicts[0]
            except _ProbeAbort:
                result = None
            except (RecursionError, ArithmeticError, ValueError, TypeError, OverflowError):
                result = None
    _verdict_cache[id(node)] = result
    return result


_JUNK_NODE_CAP = 512


def _fast_size(node: Node) -> int:
    """Iterative subtree size; bails out once above _JUNK_NODE_CAP."""
    if not isinstance(node, Node):
        return 0
    count = 0
    stack = [node]
    while stack:
        current = stack.pop()
        count += 1
        if count > _JUNK_NODE_CAP:
            return count
        for value in current.fields.values():
            if isinstance(value, Node):
                stack.append(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        stack.append(item)
    return count


def junk_truth(node: Node | None, base_eval: Callable[[Node], Any]) -> bool | None:
    """Try every opaque-predicate strategy; None when undecided.

    Only small predicate expressions are considered: LUAST junk
    identities are tiny, and large subtrees here are real payload
    expressions that must neither be probed nor rendered."""
    if not isinstance(node, Node):
        return None
    node = _strip(node) or node
    if node.kind != "binop":
        return None
    if _fast_size(node) > _JUNK_NODE_CAP:
        return None
    verdict = self_compare_truth(node)
    if verdict is not None:
        return verdict
    verdict = _string_doubling_truth(node)
    if verdict is not None:
        return verdict
    verdict = angle_gap_truth(node, base_eval)
    if verdict is not None:
        return verdict
    return _probe_truth(node, base_eval)

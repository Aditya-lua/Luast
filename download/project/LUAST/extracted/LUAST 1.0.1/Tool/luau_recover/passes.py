from __future__ import annotations

import copy
import math
import struct
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .analysis import Analyzer, Binding, analyze, binding_for, is_pure
from .model import Node, children, clone_value, iter_nodes, walk


UNKNOWN = object()


class Phi:
    """Symbolic both-branch value: the result of an if-expression whose
    condition is unknown but whose leaves are all statically known.

    LUAST junk predicates are built exactly like that: an auxiliary local
    receives `if <unknown> then k1 else k2` and later arithmetic over it is
    designed to yield the same answer for either branch. Propagating a Phi
    through that arithmetic collapses the predicate to a constant instead
    of forking the dispatch into two exponentially-growing paths."""

    __slots__ = ("values",)

    def __init__(self, *values: Any):
        self.values = tuple(values)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Phi{self.values}"


def is_phi(value: Any) -> bool:
    return isinstance(value, Phi)


def unphi(value: Any) -> Any:
    """Phi values must not leak into consumers that cannot handle them."""
    return UNKNOWN if is_phi(value) else value


def phi_apply(operator: Callable[..., Any], *operands: Any) -> Any:
    """Apply a scalar operator component-wise over Phi/scalar operands.

    Collapses to a plain scalar when every component produces the same
    value of the same type; produces a fresh Phi when the components
    differ but all stay numeric; returns UNKNOWN otherwise."""
    size = 0
    for value in operands:
        if is_phi(value):
            size = len(value.values)
            break
    if size == 0:
        return operator(*operands)
    if size > 4:
        return UNKNOWN
    results = []
    for index in range(size):
        column = [value.values[index] if is_phi(value) else value for value in operands]
        try:
            result = operator(*column)
        except (TypeError, ValueError, OverflowError, ArithmeticError):
            return UNKNOWN
        if result is UNKNOWN:
            return UNKNOWN
        results.append(result)
    first = results[0]
    if all(type(result) is type(first) and result == first for result in results):
        return first
    if all(is_number(result) for result in results):
        return Phi(*results)
    return UNKNOWN


def phi_from_leaves(leaves: list, eval_leaf: Callable[[Any], Any]) -> Any:
    """Evaluate every if-expression leaf; Phi when they are known but differ."""
    values = []
    for leaf in leaves:
        value = eval_leaf(leaf)
        if value is UNKNOWN or is_phi(value):
            return UNKNOWN
        values.append(value)
    if not values:
        return UNKNOWN
    first = values[0]
    if all(type(value) is type(first) and value == first for value in values):
        return first
    if all(is_number(value) for value in values) or all(isinstance(value, bytes) for value in values):
        return Phi(*values)
    return UNKNOWN


def phi_binop(operator: str, left: Any, right: Any) -> Any:
    """Binary operator over operands that may contain Phi values."""
    if operator == "..":
        return phi_apply(lambda a, b: a + b if isinstance(a, bytes) and isinstance(b, bytes) else UNKNOWN, left, right)
    if operator in {"==", "~=", "<", "<=", ">", ">="}:
        def compare(a: Any, b: Any) -> Any:
            if is_nan(a) or is_nan(b):
                return operator == "~="
            if numeric_kind(a) != numeric_kind(b) and not (is_number(a) and is_number(b)):
                return UNKNOWN
            if not is_number(a) and type(a) is not type(b):
                return UNKNOWN
            try:
                if operator == "==":
                    return a == b
                if operator == "~=":
                    return a != b
                if operator == "<":
                    return a < b
                if operator == "<=":
                    return a <= b
                if operator == ">":
                    return a > b
                return a >= b
            except (TypeError, ValueError):
                return UNKNOWN
        return phi_apply(compare, left, right)
    return phi_apply(lambda a, b: numeric_result(operator, a, b), left, right)


@dataclass
class PassStats:
    folded: int = 0
    propagated: int = 0
    pool_reads: int = 0
    pool_values: int = 0
    decoder_calls: int = 0
    branches_removed: int = 0
    dead_locals: int = 0
    dispatchers_found: int = 0
    dispatchers_removed: int = 0
    states_recovered: int = 0
    names_renamed: int = 0
    aliases_restored: int = 0
    errors: list[str] = field(default_factory=list)


def literal(node: Node | None) -> Any:
    if not isinstance(node, Node):
        return UNKNOWN
    if node.kind == "number":
        return node.get("value")
    if node.kind == "string":
        return node.get("value")
    if node.kind == "bool":
        return node.get("value")
    if node.kind == "nil":
        return None
    return UNKNOWN


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def numeric_kind(value: Any) -> str | None:
    if is_int(value):
        return "int"
    if isinstance(value, float):
        return "float"
    return None


def value_node(value: Any) -> Node:
    if value is None:
        return Node("nil")
    if value is True:
        return Node("bool", value=True)
    if value is False:
        return Node("bool", value=False)
    if isinstance(value, bytes):
        return Node("string", value=value)
    if isinstance(value, str):
        return Node("string", value=value.encode("utf-8"))
    if isinstance(value, (int, float)):
        return Node("number", value=value)
    raise TypeError(type(value).__name__)


def truthy(value: Any) -> bool:
    return value is not UNKNOWN and value is not None and value is not False


def numeric_result(op: str, left: Any, right: Any) -> Any:
    if not is_number(left) or not is_number(right):
        return UNKNOWN
    if op in {"&", "|", "~", "<<", ">>"}:
        if not is_int(left) or not is_int(right):
            return UNKNOWN
        if op == "~":
            return left
        if op == "<<":
            return left << right if 0 <= right < 512 else UNKNOWN
        if op == ">>":
            return left >> right if right >= 0 else UNKNOWN
        return (left & right) if op == "&" else (left | right)
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            if left == 0:
                return float("nan")
            return math.copysign(float("inf"), float(left)) * math.copysign(1.0, float(right))
        return float(left) / float(right)
    if op == "//":
        if right == 0:
            return UNKNOWN
        return left // right
    if op == "%":
        if right == 0:
            return UNKNOWN
        if isinstance(left, int) and isinstance(right, int):
            return left % right
        # Lua/Luau float modulo is floor-based (a - floor(a/b)*b), not the
        # C fmod sign convention; match Luau exactly so junk predicates that
        # mix signs still evaluate bit-identically.
        result = math.fmod(left, right)
        if result != 0 and (result < 0) != (right < 0):
            result += right
        return result
    if op == "^":
        try:
            if abs(left) > 2 ** 53 or abs(right) > 64:
                return UNKNOWN
            result = left ** right
        except (OverflowError, ValueError, ZeroDivisionError):
            return UNKNOWN
        try:
            finite = math.isfinite(float(result))
        except (OverflowError, ValueError):
            finite = False
        return result if isinstance(result, (int, float)) and finite else UNKNOWN
    return UNKNOWN


def is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def render_expr(node: Node) -> str:
    """Deterministic rendering used for structural AST equality checks."""
    try:
        from .emitter import emit_expr_cached
        return emit_expr_cached(node)
    except Exception:
        return f"<{node.kind}@{node.start}:{node.end}>"


def _strip_paren(node: Node | None) -> Node | None:
    while isinstance(node, Node) and node.kind == "paren":
        node = node.get("expr")
    return node


def neg_equal(left: Node | None, right: Node | None) -> bool:
    """True when left and right are structurally `A` vs `not A`."""
    left = _strip_paren(left)
    right = _strip_paren(right)
    if not isinstance(left, Node) or not isinstance(right, Node):
        return False
    if left.kind == "unop" and left.get("op") == "not":
        return render_expr(_strip_paren(left.get("expr"))) == render_expr(right)
    if right.kind == "unop" and right.get("op") == "not":
        return render_expr(left) == render_expr(_strip_paren(right.get("expr")))
    return False


def bool_const(node: Node | None) -> bool | None:
    """Structurally decide a boolean formula that mixes known/unknown atoms.

    Recognizes tautologies the obfuscator injects, e.g.:
      x or not x        -> true
      x and not x       -> false
      (A or A) with A known, De-Morgan-style monsters, etc.
    Returns True, False, or None when not decidable.
    """
    node = _strip_paren(node)
    if not isinstance(node, Node):
        return None
    kind = node.kind
    if kind == "bool":
        return bool(node.get("value"))
    if kind == "unop" and node.get("op") == "not":
        inner = bool_const(node.get("expr"))
        if inner is not None:
            return not inner
        return None
    if kind == "binop" and node.get("op") in {"and", "or"}:
        operator = node.get("op")
        left = bool_const(node.get("left"))
        right = bool_const(node.get("right"))
        if operator == "and":
            if left is False or right is False:
                return False
            if left is True and right is True:
                return True
            if neg_equal(node.get("left"), node.get("right")):
                return False
            return None
        if operator == "or":
            if left is True or right is True:
                return True
            if left is False and right is False:
                return False
            if neg_equal(node.get("left"), node.get("right")):
                return True
            return None
    return None


def _junk_truth(node: Node | None, base_eval: Callable[[Node], Any]) -> bool | None:
    """Lazy proxy to luau_recover.junk.junk_truth (avoids import cycle)."""
    if node is None:
        return None
    try:
        from .junk import junk_truth
    except ImportError:
        return None
    try:
        return junk_truth(node, base_eval)
    except Exception:
        return None


def evaluate(node: Node | None, env: dict[int, Any] | None = None, analyzer: Analyzer | None = None) -> Any:
    if node is None:
        return UNKNOWN
    env = env or {}
    if node.kind == "paren":
        return evaluate(node.get("expr"), env, analyzer)
    if node.kind == "number" or node.kind == "string" or node.kind == "bool":
        return node.get("value")
    if node.kind == "nil":
        return None
    if node.kind == "name" and analyzer is not None:
        binding = binding_for(analyzer, node)
        if binding is not None and binding.ident in env:
            return env[binding.ident]
        return UNKNOWN
    if node.kind == "unop":
        value = evaluate(node.get("expr"), env, analyzer)
        if is_phi(value):
            if node.get("op") == "not":
                return phi_apply(lambda item: not truthy(item), value)
            if node.get("op") == "-":
                return phi_apply(lambda item: -item if is_number(item) else UNKNOWN, value)
            if node.get("op") == "~":
                return phi_apply(lambda item: ~item if is_int(item) else UNKNOWN, value)
            return UNKNOWN
        if value is UNKNOWN:
            # Tautology rescue: `not (x or not x)` and friends are constant
            # even when the atom is unknown.
            resolved = bool_const(node)
            if resolved is not None:
                return resolved
            return UNKNOWN
        if node.get("op") == "not":
            return not truthy(value)
        if node.get("op") == "-" and is_number(value):
            return -value
        if node.get("op") == "#" and isinstance(value, bytes):
            return len(value)
        if node.get("op") == "~" and is_int(value):
            return ~value
        return UNKNOWN
    if node.kind == "binop":
        operator = node.get("op")
        if operator == "and":
            left = evaluate(node.get("left"), env, analyzer)
            if left is UNKNOWN or is_phi(left):
                resolved = bool_const(node)
                return resolved if resolved is not None else UNKNOWN
            return evaluate(node.get("right"), env, analyzer) if truthy(left) else left
        if operator == "or":
            left = evaluate(node.get("left"), env, analyzer)
            if left is UNKNOWN or is_phi(left):
                resolved = bool_const(node)
                return resolved if resolved is not None else UNKNOWN
            return left if truthy(left) else evaluate(node.get("right"), env, analyzer)
        left = evaluate(node.get("left"), env, analyzer)
        right = evaluate(node.get("right"), env, analyzer)
        if is_phi(left) or is_phi(right):
            return phi_binop(operator, left, right)
        if operator in {"==", "~=", "<", "<=", ">", ">="} and (is_nan(left) or is_nan(right)):
            return operator == "~="
        if left is UNKNOWN or right is UNKNOWN:
            if operator in {"==", "~=", "<", "<=", ">", ">="}:
                folded = _junk_truth(node, lambda item: unphi(evaluate(item, env, analyzer)))
                if folded is not None:
                    return folded
            return UNKNOWN
        if operator == "..":
            if isinstance(left, bytes) and isinstance(right, bytes):
                return left + right
            return UNKNOWN
        if operator in {"==", "~=", "<", "<=", ">", ">="}:
            if numeric_kind(left) != numeric_kind(right) and not (is_number(left) and is_number(right)):
                return UNKNOWN
            if not is_number(left) and type(left) is not type(right):
                return UNKNOWN
            try:
                if operator == "==":
                    return left == right
                if operator == "~=":
                    return left != right
                if operator == "<":
                    return left < right
                if operator == "<=":
                    return left <= right
                if operator == ">":
                    return left > right
                return left >= right
            except (TypeError, ValueError):
                return UNKNOWN
        return numeric_result(operator, left, right)
    if node.kind == "ifexpr":
        all_leaves = [node.get("then")] + [branch_value for _, branch_value in node.get("elifs", [])] + [node.get("else_")]
        condition = evaluate(node.get("cond"), env, analyzer)
        if is_phi(condition):
            if all(type(item) is bool for item in condition.values) and all(item == condition.values[0] for item in condition.values):
                condition = condition.values[0]
            else:
                return phi_from_leaves(all_leaves, lambda leaf: evaluate(leaf, env, analyzer))
        if condition is UNKNOWN:
            return phi_from_leaves(all_leaves, lambda leaf: evaluate(leaf, env, analyzer))
        if truthy(condition):
            return evaluate(node.get("then"), env, analyzer)
        pending = list(node.get("elifs", []))
        index = 0
        while index < len(pending):
            branch_condition, branch_value = pending[index]
            branch_result = evaluate(branch_condition, env, analyzer)
            if branch_result is UNKNOWN or is_phi(branch_result):
                remaining = [branch_value] + [item for _, item in pending[index + 1:]] + [node.get("else_")]
                return phi_from_leaves(remaining, lambda leaf: evaluate(leaf, env, analyzer))
            if truthy(branch_result):
                return evaluate(branch_value, env, analyzer)
            index += 1
        return evaluate(node.get("else_"), env, analyzer)
    return UNKNOWN


def transform_expr(node: Node, callback: Callable[[Node, bool], Node], target: bool = False) -> Node:
    kind = node.kind
    if kind == "binop":
        node.fields["left"] = transform_expr(node.get("left"), callback)
        node.fields["right"] = transform_expr(node.get("right"), callback)
    elif kind == "unop":
        node.fields["expr"] = transform_expr(node.get("expr"), callback)
    elif kind in ("paren", "typecast"):
        node.fields["expr"] = transform_expr(node.get("expr"), callback)
    elif kind == "call":
        node.fields["func"] = transform_expr(node.get("func"), callback)
        node.fields["args"] = [transform_expr(item, callback) for item in node.get("args", [])]
    elif kind == "methodcall":
        node.fields["obj"] = transform_expr(node.get("obj"), callback)
        node.fields["args"] = [transform_expr(item, callback) for item in node.get("args", [])]
    elif kind == "index":
        node.fields["obj"] = transform_expr(node.get("obj"), callback, target)
        # The KEY of an index is a value position even when the index
        # itself is an assignment target (`t[pool[k]] = v` reads pool[k]),
        # so it must never inherit the target flag.
        node.fields["key"] = transform_expr(node.get("key"), callback, False)
    elif kind in ("indexname", "methodname"):
        node.fields["obj"] = transform_expr(node.get("obj"), callback)
    elif kind == "function":
        node.fields["body"] = transform_block(node.fields.get("body", []), callback)
    elif kind == "ifexpr":
        node.fields["cond"] = transform_expr(node.get("cond"), callback)
        node.fields["then"] = transform_expr(node.get("then"), callback)
        node.fields["elifs"] = [(transform_expr(condition, callback), transform_expr(value, callback)) for condition, value in node.get("elifs", [])]
        node.fields["else_"] = transform_expr(node.get("else_"), callback)
    elif kind == "table":
        items = []
        for key, value in node.get("items", []):
            new_key = key
            if key is not None and key.kind == "indexkey":
                new_key = key.clone()
                new_key.fields["key"] = transform_expr(key.get("key"), callback)
            items.append((new_key, transform_expr(value, callback)))
        node.fields["items"] = items
    else:
        for key, value in list(node.fields.items()):
            if isinstance(value, Node) and key not in {"raw", "type_text"}:
                if key in {"left", "right", "expr", "obj", "key", "func", "cond", "then", "else_"}:
                    node.fields[key] = transform_expr(value, callback)
        if kind == "ifexpr":
            node.fields["elifs"] = [(transform_expr(condition, callback), transform_expr(value, callback)) for condition, value in node.get("elifs", [])]
    return callback(node, target)


def transform_block(statements: list[Node], callback: Callable[[Node, bool], Node]) -> list[Node]:
    output: list[Node] = []
    for statement in statements:
        output.extend(transform_statement(statement, callback))
    return output


def transform_statement(node: Node, callback: Callable[[Node, bool], Node]) -> list[Node]:
    kind = node.kind
    if kind == "local":
        node.fields["values"] = [transform_expr(value, callback) for value in node.get("values", [])]
    elif kind == "localfunc":
        node.fields["body"] = transform_block(node.fields.get("body", []), callback)
    elif kind == "funcdef":
        node.fields["target"] = transform_expr(node.get("target"), callback, True)
        node.fields["body"] = transform_block(node.fields.get("body", []), callback)
    elif kind == "assign":
        node.fields["targets"] = [transform_expr(value, callback, True) for value in node.get("targets", [])]
        node.fields["values"] = [transform_expr(value, callback) for value in node.get("values", [])]
    elif kind in ("call", "methodcall"):
        transform_expr(node, callback)
    elif kind == "if":
        node.fields["cond"] = transform_expr(node.get("cond"), callback)
        node.fields["then"] = transform_block(node.get("then", []), callback)
        node.fields["elifs"] = [(transform_expr(condition, callback), transform_block(body, callback)) for condition, body in node.get("elifs", [])]
        node.fields["else_"] = transform_block(node.get("else_", []), callback)
    elif kind == "while":
        node.fields["cond"] = transform_expr(node.get("cond"), callback)
        node.fields["body"] = transform_block(node.get("body", []), callback)
    elif kind == "repeat":
        node.fields["body"] = transform_block(node.get("body", []), callback)
        node.fields["cond"] = transform_expr(node.get("cond"), callback)
    elif kind == "fornum":
        for key in ("start_expr", "limit", "step"):
            if node.get(key) is not None:
                node.fields[key] = transform_expr(node.get(key), callback)
        node.fields["body"] = transform_block(node.get("body", []), callback)
    elif kind == "forin":
        node.fields["iterators"] = [transform_expr(value, callback) for value in node.get("iterators", [])]
        node.fields["body"] = transform_block(node.get("body", []), callback)
    elif kind == "do":
        node.fields["body"] = transform_block(node.get("body", []), callback)
    elif kind == "return":
        node.fields["values"] = [transform_expr(value, callback) for value in node.get("values", [])]
    elif kind in ("break", "continue", "goto", "label", "opaque"):
        return [node]
    else:
        transform_expr(node, callback)
    transformed = callback(node, False)
    if isinstance(transformed, Node) and transformed.kind == "_removed":
        return []
    return [transformed] if isinstance(transformed, Node) else []


def constant_environment(analyzer: Analyzer) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for node in walk(analyzer.root):
        if not isinstance(node, Node) or node.kind != "local":
            continue
        bindings = analyzer.declaration_bindings.get(id(node), [])
        values = node.get("values", [])
        for binding, value in zip(bindings, values):
            if binding.writes != 0:
                continue
            evaluated = literal(value)
            if evaluated is not UNKNOWN:
                result[binding.ident] = evaluated
    return result


def fornum_iterations(node: Node) -> list[int | float] | None:
    """Return the concrete iteration values of a numeric for loop, or None.

    Returns [] when the loop provably runs zero times. Returns None when
    the bounds are not compile-time numerics or the range is too large.
    """
    start = evaluate(node.get("start_expr"))
    limit = evaluate(node.get("limit"))
    step = node.get("step")
    step_value = evaluate(step) if step is not None else 1
    if any(value is UNKNOWN for value in (start, limit, step_value)):
        return None
    if not is_number(start) or not is_number(limit) or not is_number(step_value):
        return None
    if step_value == 0:
        return None
    if step_value > 0 and start > limit:
        return []
    if step_value < 0 and start < limit:
        return []
    count = math.floor((limit - start) / step_value) + 1
    if count < 0:
        return []
    if count > 4096:
        return None
    values: list[int | float] = []
    current = start
    for _ in range(int(count)):
        values.append(current)
        current = current + step_value
        if not math.isfinite(float(current)):
            break
    return values


def prune_fornum_body(node: Node, analyzer: Analyzer, stats: PassStats, environment: dict[int, Any] | None = None) -> bool:
    """Remove always-false if-statements from a numeric for loop body.

    Binds the loop variable to each concrete iteration value and drops
    any branch whose condition is false for every iteration. Returns
    True when the loop body is provably never entered.
    """
    values = fornum_iterations(node)
    if values is None:
        return False
    if not values:
        stats.branches_removed += 1
        return True
    if len(values) > 64:
        return False
    binding = analyzer.loop_bindings.get(id(node), [None])[0]
    if binding is None:
        return False
    if environment is None:
        environment = constant_environment(analyzer)
    changed = [False]

    def prune(statements: list[Node]) -> list[Node]:
        output: list[Node] = []
        for statement in statements:
            kind = statement.kind
            if kind in {"if", "while"}:
                if all(evaluate(statement.get("cond"), {**environment, binding.ident: value}, analyzer) is False for value in values):
                    stats.branches_removed += 1
                    changed[0] = True
                    continue
                if kind == "if":
                    statement.fields["then"] = prune(statement.get("then", []))
                    statement.fields["elifs"] = [(condition, prune(body)) for condition, body in statement.get("elifs", [])]
                    statement.fields["else_"] = prune(statement.get("else_", []))
                else:
                    statement.fields["body"] = prune(statement.get("body", []))
            elif kind == "do":
                statement.fields["body"] = prune(statement.get("body", []))
            elif kind in {"fornum", "forin", "repeat"}:
                statement.fields["body"] = prune(statement.get("body", []))
            output.append(statement)
        return output

    node.fields["body"] = prune(node.get("body", []))
    return not node.get("body") and changed[0]


def fold_constants(root: Node, analyzer: Analyzer, stats: PassStats, rounds: int = 4) -> Node:
    for _ in range(rounds):
        environment = constant_environment(analyzer)
        changed = [False]

        def callback(node: Node, target: bool) -> Node:
            if target:
                return node
            if node.kind == "if":
                condition = evaluate(node.get("cond"), environment, analyzer)
                if condition is not UNKNOWN and not is_phi(condition):
                    selected: list[Node] = []
                    if truthy(condition):
                        selected = node.get("then", [])
                    else:
                        for branch_condition, branch_body in node.get("elifs", []):
                            branch_value = evaluate(branch_condition, environment, analyzer)
                            if branch_value is not UNKNOWN and truthy(branch_value):
                                selected = branch_body
                                break
                        else:
                            selected = node.get("else_", [])
                    changed[0] = True
                    stats.branches_removed += 1
                    return Node("do", node.start, node.end, body=selected)
                return node
            if node.kind == "while":
                condition = evaluate(node.get("cond"), environment, analyzer)
                if condition is not UNKNOWN and not is_phi(condition) and not truthy(condition):
                    # The body of a while loop never runs when the leading
                    # condition is provably false; the loop is dead code.
                    changed[0] = True
                    stats.branches_removed += 1
                    return Node("_removed")
                return node
            if node.kind == "repeat":
                condition = evaluate(node.get("cond"), environment, analyzer)
                body = node.get("body", [])
                has_edges = any(leaf.kind in {"break", "continue"} for leaf in iter_nodes(body))
                if condition is not UNKNOWN and not is_phi(condition) and not truthy(condition) and not has_edges:
                    # repeat ... until false runs the body exactly once.
                    changed[0] = True
                    stats.branches_removed += 1
                    return Node("do", node.start, node.end, body=body)
                return node
            if node.kind == "fornum":
                if prune_fornum_body(node, analyzer, stats, environment):
                    changed[0] = True
                    return Node("_removed")
                return node
            if node.kind in {"number", "string", "bool", "nil", "name", "vararg", "interpolated", "opaque"}:
                return node
            result = evaluate(node, environment, analyzer)
            if result is not UNKNOWN and not is_phi(result):
                replacement = value_node(result)
                replacement.start = node.start
                replacement.end = node.end
                stats.folded += 1
                changed[0] = True
                return replacement
            return node

        root.fields["body"] = transform_block(root.get("body", []), callback)
        if not changed[0]:
            break
    return root


def propagate_locals(root: Node, analyzer: Analyzer, stats: PassStats) -> Node:
    environment = constant_environment(analyzer)

    def callback(node: Node, target: bool) -> Node:
        if target or node.kind != "name":
            return node
        binding = binding_for(analyzer, node)
        if binding is None or binding.ident not in environment:
            return node
        replacement = value_node(environment[binding.ident])
        replacement.start = node.start
        replacement.end = node.end
        stats.propagated += 1
        return replacement

    root.fields["body"] = transform_block(root.get("body", []), callback)
    return root


def restore_library_aliases(root: Node, analyzer: Analyzer, stats: PassStats) -> Node:
    allowed = {"bit32", "buffer", "math", "os", "string", "table", "task", "coroutine", "utf8"}
    aliases: dict[int, Node] = {}
    for node in walk(root):
        if not isinstance(node, Node) or node.kind != "local":
            continue
        bindings = analyzer.declaration_bindings.get(id(node), [])
        values = node.get("values", [])
        if len(bindings) != 1 or len(values) != 1 or bindings[0].writes:
            continue
        value = values[0]
        if value.kind != "indexname":
            continue
        base = value.get("obj")
        if isinstance(base, Node) and base.kind == "name" and base.get("name") in allowed:
            aliases[bindings[0].ident] = value.clone()
    if not aliases:
        return root

    def callback(node: Node, target: bool) -> Node:
        if target or node.kind != "name":
            return node
        binding = binding_for(analyzer, node)
        if binding is None or binding.ident not in aliases:
            return node
        stats.aliases_restored += 1
        return aliases[binding.ident].clone()

    root.fields["body"] = transform_block(root.get("body", []), callback)
    return root


def remove_unused_locals(root: Node, analyzer: Analyzer, stats: PassStats) -> Node:
    fold_env = constant_environment(analyzer)
    memo_eligible = memo_guard_bindings(root, analyzer)

    def all_dead(bindings: list, values: list) -> bool:
        return all(binding.reads == 0 and binding.writes == 0 for binding in bindings) and all(is_pure(value) for value in values)

    def process_functions(node: Node | None) -> None:
        """Recurse dead-code cleanup into closures nested in expressions."""
        if node is None:
            return
        if node.kind == "function":
            node.fields["body"] = process(node.get("body", []))
            return
        for field in node.fields.values():
            if isinstance(field, Node):
                process_functions(field)
            elif isinstance(field, (list, tuple)):
                for item in field:
                    if isinstance(item, Node):
                        process_functions(item)
                    elif isinstance(item, (list, tuple)):
                        for nested in item:
                            if isinstance(nested, Node):
                                process_functions(nested)

    def process(statements: list[Node]) -> list[Node]:
        output: list[Node] = []
        for position, statement in enumerate(statements):
            kind = statement.kind
            if kind == "local":
                bindings = analyzer.declaration_bindings.get(id(statement), [])
                values = statement.get("values", [])
                for value in values:
                    process_functions(value)
                if bindings and len(bindings) == len(statement.get("names", [])):
                    if all_dead(bindings, values):
                        stats.dead_locals += len(bindings)
                        continue
            elif kind == "assign":
                targets = statement.get("targets", [])
                values = statement.get("values", [])
                for value in values:
                    process_functions(value)
                # Dead store removal. An assignment is only removable when
                # every target is provably unobservable: the binding's ONLY
                # reads anywhere in its scope are inside the stored value
                # itself (self-referential), and all values are pure. This
                # preserves loop counters like `i = i + 1` (read again by the
                # loop condition on the next iteration).
                if targets and len(targets) == len(values) and all(is_pure(value) for value in values):
                    all_dead_targets = True
                    for target, value in zip(targets, values):
                        if target.kind != "name":
                            all_dead_targets = False
                            break
                        binding = binding_for(analyzer, target)
                        if binding is None:
                            all_dead_targets = False
                            break
                        internal_reads = sum(1 for node in walk(value) if node.kind == "name" and binding_for(analyzer, node) is binding)
                        if binding.reads != internal_reads:
                            all_dead_targets = False
                            break
                    if all_dead_targets:
                        stats.dead_locals += len(targets)
                        continue
            elif kind == "do":
                statement.fields["body"] = process(statement.get("body", []))
                if not statement.get("body", []):
                    continue
            elif kind == "if":
                statement.fields["then"] = process(statement.get("then", []))
                statement.fields["elifs"] = [(condition, process(body)) for condition, body in statement.get("elifs", [])]
                statement.fields["else_"] = process(statement.get("else_", []))
                if not statement.get("then", []) and not statement.get("elifs", []) and not statement.get("else_", []) and is_pure(statement.get("cond")):
                    continue
                if self_dead_guard(statement, statements, position, analyzer, fold_env):
                    stats.branches_removed += 1
                    continue
                if remove_memoization_store(statement, analyzer):
                    stats.dead_locals += 1
                    continue
            elif kind in {"while", "repeat"}:
                statement.fields["body"] = process(statement.get("body", []))
                if kind == "while":
                    cond = statement.get("cond")
                    if not statement.get("body", []) and is_pure(cond) and evaluate(cond, fold_env, analyzer) is False:
                        continue
            elif kind in {"fornum", "forin"}:
                statement.fields["body"] = process(statement.get("body", []))
            elif kind in {"localfunc", "funcdef", "function"}:
                statement.fields["body"] = process(statement.get("body", []))
            output.append(statement)
        return output

    root.fields["body"] = process(root.get("body", []))
    return root


def self_dead_guard(statement: Node, statements: list[Node], position: int, analyzer: Analyzer, environment: dict[int, Any]) -> bool:
    """Flow-resolve an `if` guard against the most recent prior value of
    the variable it tests.

    LUAST wraps junk table stores in `if not X then ... end` where X was
    already initialized to a truthy constant earlier in the same block.
    The guard is provably false even though X is written elsewhere, which
    defeats the scope-wide constant environment. Returns True when the
    guard is provably false and the if has no other branches."""
    cond = statement.get("cond")
    if statement.get("elifs") or statement.get("else_"):
        return False
    tested = cond
    negate = False
    if isinstance(tested, Node) and tested.kind == "unop" and tested.get("op") == "not":
        tested = tested.get("expr")
        negate = True
    if not (isinstance(tested, Node) and tested.kind == "name"):
        return False
    binding = binding_for(analyzer, tested)
    if binding is None:
        return False
    prior = _nearest_prior_write(binding, statements, position, analyzer)
    if prior is UNKNOWN:
        return False
    guard_holds = truthy(prior)
    return guard_holds if negate else not guard_holds


def _nearest_prior_write(binding: Binding, statements: list[Node], position: int, analyzer: Analyzer) -> Any:
    """Most recent known value of binding before `position`, or UNKNOWN.

    nil literals report as None; a declaration without a value also
    reports None (the variable is nil from that point on)."""
    for index in range(position - 1, -1, -1):
        statement = statements[index]
        if statement.kind == "local":
            bindings = analyzer.declaration_bindings.get(id(statement), [])
            values = statement.get("values", [])
            for slot, (bound, _) in enumerate(zip(bindings, statement.get("names", []))):
                if bound is binding:
                    if slot < len(values):
                        return literal(values[slot])
                    return None
        elif statement.kind == "assign":
            targets = statement.get("targets", [])
            values = statement.get("values", [])
            for target, value in zip(targets, values):
                if target.kind == "name" and binding_for(analyzer, target) is binding:
                    return literal(value)
    return UNKNOWN


def memo_guard_bindings(root: Node, analyzer: Analyzer) -> set[int]:
    """Bindings whose every read is either an `if not X` guard or a
    self-reference inside a table stored back into X.

    For these bindings, dropping the guarded junk stores is unobservable
    no matter what value X holds at runtime, which unlocks constant pools
    that only escaped through the stored tables (commonly nested inside
    decoder closures)."""
    guard_count: dict[int, int] = {}
    external: dict[int, bool] = {}
    chain: list[Node] = []

    def visit(node: Node) -> None:
        if node.kind == "name":
            binding = binding_for(analyzer, node)
            if binding is not None:
                parent = chain[-1] if chain else None
                if isinstance(parent, Node) and parent.kind == "unop" and parent.get("op") == "not":
                    grand = chain[-2] if len(chain) >= 2 else None
                    if isinstance(grand, Node) and grand.kind == "if" and grand.get("cond") is parent:
                        guard_count[binding.ident] = guard_count.get(binding.ident, 0) + 1
                        chain.append(node)
                        chain.pop()
                        return
                if isinstance(parent, Node) and parent.kind == "table":
                    table_parent = chain[-2] if len(chain) >= 2 else None
                    if isinstance(table_parent, Node) and table_parent.kind == "assign":
                        targets = table_parent.get("targets", [])
                        if len(targets) == 1 and isinstance(targets[0], Node) and targets[0].kind == "name" and binding_for(analyzer, targets[0]) is binding:
                            # self-reference inside a stored table: unobservable
                            return
                external[binding.ident] = True
        chain.append(node)
        for value in node.fields.values():
            if isinstance(value, Node):
                visit(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        visit(item)
                    elif isinstance(item, (list, tuple)):
                        for nested in item:
                            if isinstance(nested, Node):
                                visit(nested)
        chain.pop()

    visit(root)
    return {ident for ident, count in guard_count.items() if count >= 1 and not external.get(ident)}


def remove_memoization_store(statement: Node, analyzer: Analyzer, memo_eligible: set[int] | None = None) -> bool:
    """Detect the junk memoization idiom `if not X then X = <pure> end`.

    When X's only reads anywhere are such guards plus self-references
    inside the stored values (see memo_guard_bindings), the store is
    never observable and the whole if-statement is dead. Removing it also
    un-pins values (e.g. constant pools) that only escaped through the
    stored table.
    """
    cond = statement.get("cond")
    if not (isinstance(cond, Node) and cond.kind == "unop" and cond.get("op") == "not"):
        return False
    guard_expr = cond.get("expr")
    if not (isinstance(guard_expr, Node) and guard_expr.kind == "name"):
        return False
    guard_binding = binding_for(analyzer, guard_expr)
    if guard_binding is None:
        return False
    if memo_eligible is not None and guard_binding.ident not in memo_eligible:
        return False
    if statement.get("elifs") or statement.get("else_"):
        return False
    body = statement.get("then", [])
    if len(body) != 1:
        return False
    store = body[0]
    if store.kind != "assign" or len(store.get("targets", [])) != 1 or len(store.get("values", [])) != 1:
        return False
    target = store.get("targets", [])[0]
    if target.kind != "name" or binding_for(analyzer, target) is not guard_binding:
        return False
    value = store.get("values", [])[0]
    if not is_pure(value):
        return False
    internal_reads = 0
    for node in iter_nodes(cond):
        if isinstance(node, Node) and node.kind == "name" and binding_for(analyzer, node) is guard_binding:
            internal_reads += 1
    for node in iter_nodes(value):
        if isinstance(node, Node) and node.kind == "name" and binding_for(analyzer, node) is guard_binding:
            internal_reads += 1
    return guard_binding.writes == 1 and guard_binding.reads == internal_reads


def parent_map_of(root: Node) -> dict[int, Node]:
    result: dict[int, Node] = {}

    def visit(node: Node) -> None:
        for value in node.fields.values():
            if isinstance(value, Node):
                if id(value) not in result:
                    result[id(value)] = node
                    visit(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        if id(item) not in result:
                            result[id(item)] = node
                            visit(item)
                    elif isinstance(item, (list, tuple)):
                        for nested in item:
                            if isinstance(nested, Node):
                                if id(nested) not in result:
                                    result[id(nested)] = node
                                    visit(nested)

    visit(root)
    return result


def remove_dead_pool_writes(root: Node, analyzer: Analyzer, stats: PassStats) -> Node:
    """Drop constant-keyed writes to string/number constant pools whose
    reads were already substituted by PoolResolver, plus pool tables that
    are no longer read at all."""
    allowed = {"bit32", "buffer", "math", "os", "string", "table", "task", "coroutine", "utf8"}
    library_aliases: set[int] = set()
    for node in walk(root):
        if node.kind != "local":
            continue
        bindings = analyzer.declaration_bindings.get(id(node), [])
        values = node.get("values", [])
        if len(bindings) == 1 and len(values) == 1 and values[0].kind == "indexname":
            base = values[0].get("obj")
            if isinstance(base, Node) and base.kind == "name" and base.get("name") in allowed:
                library_aliases.add(bindings[0].ident)
    roots: dict[int, None] = {}
    aliases: dict[int, int] = {}
    for node in walk(root):
        if node.kind != "local":
            continue
        bindings = analyzer.declaration_bindings.get(id(node), [])
        values = node.get("values", [])
        if len(bindings) != 1 or len(values) != 1:
            continue
        value = values[0]
        if value.kind == "table" and not bindings[0].writes and bindings[0].ident not in library_aliases:
            roots[bindings[0].ident] = None
        elif value.kind == "name":
            source = binding_for(analyzer, value)
            if source is not None:
                aliases[bindings[0].ident] = source.ident
    changed = True
    while changed:
        changed = False
        for alias, target in list(aliases.items()):
            if target in aliases:
                aliases[alias] = aliases[target]
                changed = True
    for node in walk(root):
        if node.kind != "assign" or len(node.get("targets", [])) != 1 or len(node.get("values", [])) != 1:
            continue
        target = node.get("targets", [])[0]
        value = node.get("values", [])[0]
        if target.kind == "name" and value.kind == "table":
            binding = binding_for(analyzer, target)
            if binding is not None and binding.writes == 1:
                declaration = binding.declaration
                if isinstance(declaration, Node) and declaration.kind == "local" and not declaration.get("values", []):
                    root_id = aliases.get(binding.ident, binding.ident)
                    if root_id not in roots and binding.ident not in library_aliases:
                        roots[binding.ident] = None
    if not roots:
        return root

    # Occurrences that keep a pool alive: any value-position read of the
    # pool object or of a slot whose key cannot be resolved.
    alive: set[int] = set()
    # Key reads tracked per owning statement: a write in statement S is
    # dead only if no read outside S observes that key. Lua evaluates all
    # right-hand values before performing any assignment, so reads inside
    # the same assignment observe the pre-statement state. Reads inside
    # function bodies execute at call time and pin everything.
    deferred: set[int] = set()
    for node in walk(root):
        if node.kind == "function" and id(node) not in deferred:
            for descendant in children(node):
                deferred.add(id(descendant))
    key_reads: dict[int, dict[Any, set[int | None]]] = {}
    chain: list[Node] = []

    def visit_names(node: Node) -> None:
        if node.kind == "name":
            binding = binding_for(analyzer, node)
            if binding is not None:
                root_id = aliases.get(binding.ident, binding.ident)
                if root_id in roots:
                    parent = chain[-1] if chain else None
                    if isinstance(parent, Node) and parent.kind == "index" and parent.get("obj") is node:
                        grand = chain[-2] if len(chain) >= 2 else None
                        in_target = isinstance(grand, Node) and grand.kind == "assign" and any(target is parent for target in grand.get("targets", []))
                        if not in_target:
                            key = literal(parent.get("key"))
                            if key is UNKNOWN:
                                alive.add(root_id)
                            else:
                                if id(parent) in deferred:
                                    owner = None
                                else:
                                    owner = None
                                    for ancestor in reversed(chain):
                                        if ancestor.kind == "function":
                                            owner = None
                                            break
                                        if ancestor.kind in STATEMENT_KINDS:
                                            owner = id(ancestor)
                                            break
                                key_reads.setdefault(root_id, {}).setdefault(pool_key(key), set()).add(owner)
                        chain.append(node)
                        chain.pop()
                        return
                    if isinstance(parent, Node) and parent.kind == "local" and any(value is node for value in parent.get("values", [])):
                        # alias declaration; tracked through the alias map
                        return
                    if isinstance(parent, Node) and parent.kind == "assign" and any(target is node for target in parent.get("targets", [])):
                        return
                    alive.add(root_id)
        chain.append(node)
        for value in node.fields.values():
            if isinstance(value, Node):
                visit_names(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        visit_names(item)
                    elif isinstance(item, (list, tuple)):
                        for nested in item:
                            if isinstance(nested, Node):
                                visit_names(nested)
        chain.pop()

    visit_names(root)

    def pool_root_of(node: Node | None) -> int | None:
        if not isinstance(node, Node) or node.kind != "name":
            return None
        binding = binding_for(analyzer, node)
        if binding is None:
            return None
        root_id = aliases.get(binding.ident, binding.ident)
        return root_id if root_id in roots else None

    removed = 0

    def process(statements: list[Node]) -> list[Node]:
        nonlocal removed
        output: list[Node] = []
        for statement in statements:
            kind = statement.kind
            if kind == "assign":
                targets = statement.get("targets", [])
                values = statement.get("values", [])
                if targets and len(targets) == len(values):
                    kept_targets: list[Node] = []
                    kept_values: list[Node] = []
                    droppable = True
                    for target, value in zip(targets, values):
                        if target.kind == "name":
                            binding = binding_for(analyzer, target)
                            if binding is not None and binding.reads == 0 and binding.writes == 1:
                                root_id = aliases.get(binding.ident, binding.ident)
                                if root_id in roots and root_id not in alive:
                                    removed += 1
                                    continue
                            droppable = False
                            kept_targets.append(target)
                            kept_values.append(value)
                            continue
                        if target.kind == "index":
                            root_id = pool_root_of(target.get("obj"))
                            if root_id is not None and root_id not in alive and is_pure(value):
                                key = literal(target.get("key"))
                                if key is not UNKNOWN:
                                    owners = key_reads.get(root_id, {}).get(pool_key(key), set())
                                    if not owners - {id(statement)}:
                                        removed += 1
                                        continue
                        droppable = False
                        kept_targets.append(target)
                        kept_values.append(value)
                    if droppable:
                        continue
                    if len(kept_targets) != len(targets):
                        if kept_targets:
                            output.append(Node("assign", statement.start, statement.end, targets=kept_targets, values=kept_values))
                        continue
            elif kind == "local":
                bindings = analyzer.declaration_bindings.get(id(statement), [])
                values = statement.get("values", [])
                if len(bindings) == 1 and len(values) == 1:
                    binding = bindings[0]
                    root_id = aliases.get(binding.ident, binding.ident)
                    if values[0].kind == "table" and root_id in roots and root_id not in alive:
                        removed += 1
                        continue
                    if values[0].kind == "name" and binding.ident in aliases and aliases[binding.ident] in roots and aliases[binding.ident] not in alive:
                        removed += 1
                        continue
            elif kind in {"do", "while", "repeat", "if", "fornum", "forin", "localfunc", "funcdef", "function"}:
                for field in ("body", "then", "else_"):
                    if isinstance(statement.fields.get(field), list):
                        statement.fields[field] = process(statement.fields[field])
                if isinstance(statement.fields.get("elifs"), list):
                    statement.fields["elifs"] = [(condition, process(body)) for condition, body in statement.fields["elifs"]]
            output.append(statement)
        return output

    root.fields["body"] = process(root.get("body", []))
    if removed:
        stats.dead_locals += removed
    return root


def pool_key(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ("nil",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("number", value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return ("number", int(value))
    if isinstance(value, bytes):
        return ("string", value)
    return ("other", str(value))


STATEMENT_KINDS = {"assign", "local", "call", "methodcall", "return", "if", "while", "repeat", "fornum", "forin", "do", "localfunc", "funcdef", "goto", "label"}


def key_reads_owner(parents: dict[int, Node], index_node: Node) -> int | None:
    """Identify the nearest enclosing statement of a pool read.

    Reads lexically inside a function body execute at call time, so they
    are attributed to None (they pin writes from every statement)."""
    current: Node | None = index_node
    for _ in range(256):
        current = parents.get(id(current)) if current is not None else None
        if current is None:
            return None
        if current.kind == "function":
            return None
        if current.kind in STATEMENT_KINDS:
            return id(current)
    return None


@dataclass
class BufferValue:
    data: bytes


def walk_unique(root: Node) -> Iterator[Node]:
    """Lean walker for freshly-parsed trees: no seen-set, no allocation.

    PoolResolver runs on trees where every node is unique (direct parser
    output or clone-based transforms), so the defensive dedup set in
    walk() — one set entry per node, several hundred MB on multi-MB
    inputs — is pure overhead here."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in children(node):
            stack.append(child)


class PoolResolver:
    def __init__(self, root: Node, analyzer: Analyzer, source: str, allow_escape: bool = False):
        self.root = root
        self.analyzer = analyzer
        self.source = source
        self.allow_escape = allow_escape
        self.roots: dict[int, Node] = {}
        self.aliases: dict[int, int] = {}
        self.declaration_start: dict[int, int] = {}
        self.events: list[tuple[int, int, Node, Node, bool]] = []
        self.reads: list[Node] = []
        self.statement_nodes: set[int] = set()
        self.parents: dict[int, Node] = {}
        self.targets: set[int] = set()
        self.value_nodes: dict[int, Node] = {}
        self.replacements: dict[int, Node] = {}
        self.local_values: dict[int, Any] = {}
        self.base_states: dict[int, dict[tuple[Any, ...], Node]] = {}
        self.invalid_after: int | None = None
        self.decoder_cache: dict[int, str | None] = {}
        self.alias_candidates: list[tuple[int, Node, int]] = []
        self.alias_bases: dict[int, Node] = {}
        self.alias_base_decl: dict[int, int] = {}
        self.alias_keys: dict[int, tuple[int, tuple[tuple[Any, ...], ...]]] = {}
        self.alias_stable: dict[int, bool] = {}
        self.nested_overlays: dict[tuple[int, tuple[tuple[Any, ...], ...]], dict[tuple[Any, ...], Node]] = {}
        self.nested_writes: list[tuple[int, int]] = []
        self.table_state_memo: dict[int, dict[tuple[Any, ...], Node]] = {}
        self.bare_local_candidates = False
        self.safe = True
        self.discover()

    def invalidate(self, position: int) -> None:
        if self.invalid_after is None or position < self.invalid_after:
            self.invalid_after = position
        if not self.allow_escape:
            self.safe = False

    def discover(self) -> None:
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
        for node in walk_unique(self.root):
            if node.kind != "local":
                continue
            bindings = self.analyzer.declaration_bindings.get(id(node), [])
            values = node.get("values", [])
            if len(bindings) == 1 and len(values) == 1 and values[0].kind == "table":
                self.roots[bindings[0].ident] = values[0]
                self.declaration_start[bindings[0].ident] = node.end
            elif len(bindings) == 1 and len(values) == 1 and values[0].kind == "name":
                source_binding = binding_for(self.analyzer, values[0])
                if source_binding is not None:
                    self.aliases[bindings[0].ident] = source_binding.ident
            elif len(bindings) == 1 and len(values) == 1 and values[0].kind == "index":
                # `local x = pool[a][b]` aliases a nested pool table; reads
                # through it resolve against the aliased table literal.
                if not bindings[0].writes:
                    self.alias_candidates.append((bindings[0].ident, values[0], node.end))
            elif len(bindings) >= 1 and not values:
                self.bare_local_candidates = True
        if not self.bare_local_candidates:
            self.bare_local_candidates = any(
                isinstance(node, Node) and node.kind == "assign" and len(node.get("targets", [])) == 1
                and node.get("targets", [])[0].kind == "name" and node.get("values", [])[0].kind == "table"
                for node in walk_unique(self.root)
            )
        use_positions: dict[int, int] = {}
        if self.bare_local_candidates:
            for node in walk_unique(self.root):
                if node.kind != "name":
                    continue
                binding = binding_for(self.analyzer, node)
                if binding is not None:
                    use_positions[binding.ident] = min(use_positions.get(binding.ident, node.start), node.start)
        for node in walk_unique(self.root):
            if node.kind != "assign" or len(node.get("targets", [])) != 1 or len(node.get("values", [])) != 1:
                continue
            target = node.get("targets", [])[0]
            value = node.get("values", [])[0]
            if target.kind != "name" or value.kind != "table":
                continue
            binding = binding_for(self.analyzer, target)
            if binding is None or binding.ident in self.roots:
                continue
            declaration = binding.declaration
            if isinstance(declaration, Node) and declaration.kind == "local":
                names = declaration.get("names", [])
                first_use = use_positions.get(binding.ident)
                if binding.name in names and len(declaration.get("values", [])) == 0 and (first_use is None or first_use >= node.start):
                    self.roots[binding.ident] = value
                    self.declaration_start[binding.ident] = value.end
        changed = True
        while changed:
            changed = False
            for alias, target in list(self.aliases.items()):
                if target in self.aliases:
                    self.aliases[alias] = self.aliases[target]
                    changed = True
        for ident, expression, decl_end in self.alias_candidates:
            root_id = self._index_root(expression)
            path = self._index_path(expression)
            if root_id is None or not path:
                continue
            self.alias_bases[ident] = expression
            self.alias_base_decl[ident] = decl_end
            self.alias_keys[ident] = (root_id, tuple(path))
        for node in walk_unique(self.root):
            if node.kind == "assign":
                targets = node.get("targets", [])
                values = node.get("values", [])
                if targets and len(targets) != len(values):
                    if any(target.kind == "index" for target in targets):
                        self.invalidate(node.start)
                for target_index, target in enumerate(targets):
                    self.targets.add(id(target))
                    if target.kind != "index":
                        continue
                    if target_index >= len(values):
                        self.invalidate(node.start)
                        continue
                    base = target.get("obj")
                    pool_target = False
                    if isinstance(base, Node):
                        if base.kind == "name":
                            binding = binding_for(self.analyzer, base)
                            if binding is not None:
                                root_id = self.aliases.get(binding.ident, binding.ident)
                                pool_target = root_id in self.roots or self.alias_owner_of(base) is not None
                        elif base.kind == "index":
                            nested_root = self._index_root(base)
                            if nested_root is not None:
                                pool_target = True
                                self.nested_writes.append((node.start, nested_root))
                    if not pool_target:
                        continue
                    if literal(target.get("key")) is UNKNOWN:
                        self.invalidate(node.start)
                        continue
                    self.events.append((node.start, node.end, target, values[target_index], True))
            if node.kind == "index":
                if id(node) in self.targets:
                    continue
                base = node.get("obj")
                if not isinstance(base, Node):
                    continue
                if base.kind == "name":
                    binding = binding_for(self.analyzer, base)
                    if binding is None:
                        continue
                    root_id = self.aliases.get(binding.ident, binding.ident)
                    if root_id in self.roots or self.alias_owner_of(base) is not None:
                        self.reads.append(node)
                elif base.kind == "index":
                    if self._index_root(base) is not None:
                        self.reads.append(node)
        self.reads.sort(key=lambda item: item.start)
        self.events.sort(key=lambda item: item[0])
        # An alias of a nested table (fAJ -> fAc[140]) is only usable while
        # the aliased slot is not rewritten and no in-place mutation of the
        # aliased table happens after the alias declaration.
        write_positions: dict[tuple[int, tuple[Any, ...]], list[int]] = {}
        for start, _, target, _, _ in self.events:
            base = target.get("obj")
            if isinstance(base, Node) and base.kind == "name":
                binding = binding_for(self.analyzer, base)
                if binding is not None:
                    root_id = self.aliases.get(binding.ident, binding.ident)
                    key = self.key_for(target.get("key"))
                    if root_id in self.roots and key is not None:
                        write_positions.setdefault((root_id, key), []).append(start)
        for ident, (root_id, path) in self.alias_keys.items():
            decl_end = self.alias_base_decl[ident]
            # In-place nested writes are modeled through overlays, so they do
            # NOT destabilize an alias; only a top-level rewrite of the aliased
            # slot does (the alias captured the old table object).
            stable = not any(start > decl_end for start in write_positions.get((root_id, path[0]), []))
            self.alias_stable[ident] = stable
        self.parents = self.limited_parent_map()
        # With allow_escape the escape walk below is a no-op (it can only
        # clear self.safe, which allow_escape already overrides).
        if self.allow_escape:
            return
        self._escape_check()
        for node in walk(self.root):
            for key in ("body", "then", "else_"):
                value = node.fields.get(key)
                if isinstance(value, list):
                    self.statement_nodes.update(id(item) for item in value if isinstance(item, Node))
            for _, body in node.fields.get("elifs", []):
                if isinstance(body, list):
                    self.statement_nodes.update(id(item) for item in body if isinstance(item, Node))

    def _escape_check(self) -> None:
        """A pool name read outside an index/key or alias position escapes
        the constant model. Single walk carrying the parent chain (no
        full parent map needed)."""
        chain: list[Node] = []

        def visit(node: Node) -> bool:
            if node.kind == "name" and not self.safe:
                return False
            if node.kind == "name":
                binding = binding_for(self.analyzer, node)
                if binding is not None:
                    root_id = self.aliases.get(binding.ident, binding.ident)
                    if root_id in self.roots and id(node) not in self.targets:
                        parent = chain[-1] if chain else None
                        escaped = not (
                            (parent is not None and parent.kind == "index" and parent.get("obj") is node)
                            or (parent is not None and parent.kind == "local" and any(value is node for value in parent.get("values", [])))
                            or (parent is not None and parent.kind == "assign" and any(target is node for target in parent.get("targets", [])))
                        )
                        if escaped:
                            self.safe = False
                            return False
            chain.append(node)
            for value in node.fields.values():
                if isinstance(value, Node):
                    if not visit(value):
                        return False
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, Node):
                            if not visit(item):
                                return False
                        elif isinstance(item, (list, tuple)):
                            for nested in item:
                                if isinstance(nested, Node):
                                    if not visit(nested):
                                        return False
            chain.pop()
            return True

        visit(self.root)

    def limited_parent_map(self) -> dict[int, tuple[Node, ...]]:
        """Parent chains recorded ONLY for pool-read nodes (a full id->node
        map costs hundreds of MB on multi-MB inputs). Chains cover the
        is_object/is_callee position checks (up to 20 levels)."""
        reads = {id(node) for node in self.reads}
        result: dict[int, tuple[Node, ...]] = {}
        chain: list[Node] = []

        def visit(node: Node) -> None:
            if id(node) in reads:
                result[id(node)] = tuple(chain[:20])
            chain.append(node)
            for value in node.fields.values():
                if isinstance(value, Node):
                    visit(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, Node):
                            visit(item)
                        elif isinstance(item, (list, tuple)):
                            for nested in item:
                                if isinstance(nested, Node):
                                    visit(nested)
            chain.pop()

        visit(self.root)
        return result

    def parent_map(self) -> dict[int, Node]:
        result: dict[int, Node] = {}
        for node in walk(self.root):
            for value in node.fields.values():
                if isinstance(value, Node):
                    result[id(value)] = node
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, Node):
                            result[id(item)] = node
                        elif isinstance(item, (list, tuple)):
                            for nested in item:
                                if isinstance(nested, Node):
                                    result[id(nested)] = node
        return result

    def is_object_position(self, node: Node) -> bool:
        chain = self.parents.get(id(node))
        if not chain:
            return False
        parent = chain[0]
        return parent.kind in {"index", "indexname", "methodcall"} and parent.get("obj") is node

    def is_callee_position(self, node: Node) -> bool:
        chain = self.parents.get(id(node))
        if not chain:
            return False
        current = node
        for parent in chain:
            if parent.kind == "call" and parent.get("func") is current:
                return True
            if parent.kind == "methodcall" and parent.get("obj") is current:
                return True
            if parent.kind in {"index", "indexname"} and parent.get("obj") is current:
                current = parent
                continue
            return False
        return False

    def root_for(self, node: Node) -> int | None:
        if node.kind != "name":
            return None
        binding = binding_for(self.analyzer, node)
        if binding is None:
            return None
        root_id = self.aliases.get(binding.ident, binding.ident)
        return root_id if root_id in self.roots else None

    def key_for(self, node: Node | None) -> tuple[Any, ...] | None:
        value = literal(node)
        if value is UNKNOWN:
            return None
        if value is None:
            return ("nil",)
        if isinstance(value, bool):
            return ("bool", value)
        if isinstance(value, int):
            return ("number", value)
        if isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                return None
            return ("number", int(value))
        if isinstance(value, bytes):
            return ("string", value)
        return None

    def initial_state(self, root_id: int) -> dict[tuple[Any, ...], Node]:
        table = self.roots[root_id]
        result: dict[tuple[Any, ...], Node] = {}
        array_index = 1
        for key, value in table.get("items", []):
            if key is None:
                result[("number", array_index)] = value
                array_index += 1
            elif key.kind == "namekey":
                result[("string", key.get("name", "").encode("utf-8"))] = value
            else:
                literal_key = self.key_for(key.get("key"))
                if literal_key is not None:
                    result[literal_key] = value
        return result

    def resolve_value(self, node: Node, state: dict[tuple[Any, ...], Node], seen: set[int] | None = None) -> Node | None:
        seen = set() if seen is None else seen
        if id(node) in seen:
            return None
        seen.add(id(node))
        if node.kind == "index":
            key = self.key_for(node.get("key"))
            if key is None:
                return None
            base = node.get("obj")
            if isinstance(base, Node) and base.kind == "name":
                root_id = self.root_for(base)
                if root_id is not None and key in state:
                    return state[key]
                if self.allow_escape and root_id is not None:
                    fallback = self.base_states.get(root_id, {})
                    if key in fallback:
                        return fallback[key]
            base_value = self.resolve_value(base, state, seen) if isinstance(base, Node) else None
            if base_value is not None and base_value.kind == "table":
                table_state = self.table_state_of(base_value)
                return table_state.get(key)
            return None
        return None

    def table_state_of(self, table: Node) -> dict[tuple[Any, ...], Node]:
        memo = self.table_state_memo.get(id(table))
        if memo is None:
            memo = self.initial_state_for_table(table)
            self.table_state_memo[id(table)] = memo
        return memo

    def _index_root(self, node: Node | None) -> int | None:
        """Root pool id of an index chain, or None when it does not start
        at a known pool (following local aliases)."""
        current = node
        while isinstance(current, Node) and current.kind == "index":
            current = current.get("obj")
        if not isinstance(current, Node) or current.kind != "name":
            return None
        binding = binding_for(self.analyzer, current)
        if binding is None:
            return None
        root_id = self.aliases.get(binding.ident, binding.ident)
        return root_id if root_id in self.roots else None

    def _index_path(self, node: Node | None) -> tuple[tuple[Any, ...], ...] | None:
        """Literal key path of an index chain (outermost key last)."""
        keys: list[tuple[Any, ...]] = []
        current: Node | None = node
        while isinstance(current, Node) and current.kind == "index":
            key = self.key_for(current.get("key"))
            if key is None:
                return None
            keys.append(key)
            current = current.get("obj")
        keys.reverse()
        return tuple(keys)

    def alias_owner_of(self, node: Node) -> int | None:
        """Follow local alias chains to an index-alias declared from a pool."""
        binding = binding_for(self.analyzer, node)
        if binding is None:
            return None
        owner = binding.ident
        hops = 0
        while owner not in self.alias_bases and owner in self.aliases and hops < 8:
            owner = self.aliases[owner]
            hops += 1
        return owner if owner in self.alias_bases else None

    def write_path(self, target: Node) -> tuple[int, tuple[tuple[Any, ...], ...]] | None:
        """(root, key path) a pool write assigns to, covering nested tables
        written directly (pool[a][b] = v) or through an alias (alias[b] = v)."""
        if not isinstance(target, Node) or target.kind != "index":
            return None
        key = self.key_for(target.get("key"))
        if key is None:
            return None
        base = target.get("obj")
        if not isinstance(base, Node):
            return None
        if base.kind == "name":
            owner = self.alias_owner_of(base)
            if owner is not None:
                if not self.alias_stable.get(owner, False):
                    return None
                base_root, base_path = self.alias_keys[owner]
                return base_root, tuple(base_path) + (key,)
            binding = binding_for(self.analyzer, base)
            if binding is None:
                return None
            root_id = self.aliases.get(binding.ident, binding.ident)
            if root_id not in self.roots:
                return None
            return root_id, (key,)
        if base.kind == "index":
            base_root = self._index_root(base)
            base_path = self._index_path(base)
            if base_root is None or not base_path:
                return None
            return base_root, tuple(base_path) + (key,)
        return None

    def materialize_overlay(self, root_id: int, base_path: tuple[tuple[Any, ...], ...], states: dict[int, dict[tuple[Any, ...], Node]]) -> dict[tuple[Any, ...], Node] | None:
        """Mutable copy of the table stored at `base_path`, tracking in-place
        writes without corrupting the shared table literal."""
        overlay_key = (root_id, base_path)
        overlay = self.nested_overlays.get(overlay_key)
        if overlay is not None:
            return overlay
        state = states.get(root_id)
        if state is None:
            return None
        current = state.get(base_path[0])
        for key in base_path[1:]:
            if not isinstance(current, Node) or current.kind != "table":
                return None
            current = self.table_state_of(current).get(key)
        if not isinstance(current, Node) or current.kind != "table":
            return None
        overlay = dict(self.table_state_of(current))
        self.nested_overlays[overlay_key] = overlay
        return overlay

    def resolve_alias_read(self, read: Node, states: dict[int, dict[tuple[Any, ...], Node]]) -> Node | None:
        """Resolve `alias[key]` where the alias local was declared from a
        nested pool table (`local alias = pool[slot]`)."""
        base = read.get("obj")
        if not isinstance(base, Node) or base.kind != "name":
            return None
        owner = self.alias_owner_of(base)
        if owner is None or not self.alias_stable.get(owner, False):
            return None
        key = self.key_for(read.get("key"))
        if key is None:
            return None
        base_root, base_path = self.alias_keys[owner]
        full_path = tuple(base_path) + (key,)
        overlay = self.nested_overlays.get((base_root, full_path[:-1]))
        if overlay is not None:
            return overlay.get(full_path[-1])
        expression = self.alias_bases[owner]
        table_value = self.resolve_value(expression, states[base_root])
        if not isinstance(table_value, Node) or table_value.kind != "table":
            return None
        return self.table_state_of(table_value).get(key)

    def initial_state_for_table(self, table: Node) -> dict[tuple[Any, ...], Node]:
        result: dict[tuple[Any, ...], Node] = {}
        index = 1
        for key, value in table.get("items", []):
            if key is None:
                result[("number", index)] = value
                index += 1
            elif key.kind == "namekey":
                result[("string", key.get("name", "").encode("utf-8"))] = value
            else:
                literal_key = self.key_for(key.get("key"))
                if literal_key is not None:
                    result[literal_key] = value
        return result

    def event_value(self, node: Node, state: dict[tuple[Any, ...], Node]) -> Node:
        direct = self.resolve_value(node, state) if node.kind == "index" else None
        if direct is not None:
            return direct
        if node.kind == "name":
            binding = binding_for(self.analyzer, node)
            if binding is not None:
                return node
        return node

    def build_replacements(self, stats: PassStats) -> None:
        if not self.safe:
            return
        states = {root_id: self.initial_state(root_id) for root_id in self.roots}
        self.base_states = {root_id: dict(state) for root_id, state in states.items()}
        grouped: dict[int, list[tuple[int, int, Node, Node, bool]]] = {}
        for event in self.events:
            grouped.setdefault(event[0], []).append(event)
        event_starts = sorted(grouped)
        event_index = 0
        for read in self.reads:
            while event_index < len(event_starts) and event_starts[event_index] < read.start:
                start = event_starts[event_index]
                pending: list[tuple[int, tuple[Any, ...], Node]] = []
                for _, _, target, event_rhs, _ in grouped[start]:
                    path_info = self.write_path(target)
                    if path_info is None:
                        continue
                    event_root, event_path = path_info
                    event_node_value = self.event_value(event_rhs, states[event_root])
                    if len(event_path) >= 2:
                        overlay = self.materialize_overlay(event_root, event_path[:-1], states)
                        if overlay is not None:
                            overlay[event_path[-1]] = event_node_value
                        continue
                    pending.append((event_root, event_path[0], event_node_value))
                for event_root, key, resolved_value in pending:
                    states[event_root][key] = resolved_value
                    self.nested_overlays.pop((event_root, (key,)), None)
                event_index += 1
            base = read.get("obj")
            root_id = self._index_root(read) if isinstance(base, Node) else None
            resolved = None
            if root_id is not None:
                path = self._index_path(read)
                if path is not None and len(path) >= 2:
                    overlay = self.nested_overlays.get((root_id, path[:-1]))
                    if overlay is not None:
                        resolved = overlay.get(path[-1])
                if resolved is None:
                    resolved = self.resolve_value(read, states[root_id])
            else:
                resolved = self.resolve_alias_read(read, states)
            if resolved is None:
                continue
            value = literal(resolved)
            if value is None:
                continue
            if id(read) in self.statement_nodes:
                continue
            self.value_nodes[id(read)] = resolved
            if self.is_callee_position(read) or self.is_object_position(read):
                continue
            if value is not UNKNOWN:
                self.replacements[id(read)] = value_node(value)
                stats.pool_reads += 1
        stats.pool_values += len(self.replacements)
        self.build_local_values()

    def build_local_values(self) -> None:
        declarations: list[Node] = []
        for node in walk_unique(self.root):
            if node.kind == "local" or (node.kind == "assign" and len(node.get("targets", [])) == 1 and len(node.get("values", [])) == 1):
                declarations.append(node)
        declarations.sort(key=lambda item: item.start)
        for node in declarations:
            if node.kind == "local":
                bindings = self.analyzer.declaration_bindings.get(id(node), [])
                values = node.get("values", [])
                if len(bindings) != 1 or len(values) != 1:
                    continue
                binding = bindings[0]
            else:
                target = node.get("targets", [])[0]
                if target.kind != "name":
                    continue
                binding = binding_for(self.analyzer, target)
                if binding is None:
                    continue
                values = node.get("values", [])
            try:
                value = self.static_eval(values[0], None, 0)
            except (ArithmeticError, TypeError, ValueError, OverflowError):
                continue
            if value is not UNKNOWN:
                self.local_values[binding.ident] = value

    def decoder_formula(self, node: Node) -> str | None:
        identity = id(node)
        if identity in self.decoder_cache:
            return self.decoder_cache[identity]
        text = self.source[node.start:node.end]
        if "16843009" in text or "0x01010101" in text.lower() or "16711935" in text:
            formula = "xor"
        elif "1597" in text and "51749" in text:
            formula = "lcg"
        elif "2654435769" in text and "16807" in text:
            formula = "park"
        elif "2654435769" in text:
            formula = "golden"
        else:
            formula = None
        self.decoder_cache[identity] = formula
        return formula

    def dotted_name(self, node: Node | None) -> str | None:
        if not isinstance(node, Node):
            return None
        if node.kind == "name":
            return node.get("name")
        if node.kind == "indexname":
            base = self.dotted_name(node.get("obj"))
            return base + "." + node.get("name", "") if base else None
        return None

    def static_eval(self, node: Node | None, state: dict[tuple[Any, ...], Node] | None = None, depth: int = 0) -> Any:
        if node is None or depth > 32:
            return UNKNOWN
        direct = literal(node)
        if direct is not UNKNOWN:
            return direct
        if node.kind == "name":
            binding = binding_for(self.analyzer, node)
            if binding is not None and binding.ident in self.local_values:
                return self.local_values[binding.ident]
            return UNKNOWN
        if node.kind == "index":
            resolved = self.value_nodes.get(id(node))
            if resolved is not None and resolved is not node:
                value = literal(resolved)
                if value is not UNKNOWN:
                    return value
                if resolved.kind == "table":
                    return resolved
            return UNKNOWN
        if node.kind == "paren":
            return self.static_eval(node.get("expr"), state, depth + 1)
        if node.kind == "unop":
            value = self.static_eval(node.get("expr"), state, depth + 1)
            if value is UNKNOWN:
                return UNKNOWN
            if node.get("op") == "not":
                return not truthy(value)
            if node.get("op") == "-" and is_number(value):
                return -value
            if node.get("op") == "#" and (isinstance(value, bytes) or isinstance(value, BufferValue)):
                return len(value.data) if isinstance(value, BufferValue) else len(value)
            return UNKNOWN
        if node.kind == "binop":
            operator = node.get("op")
            left = self.static_eval(node.get("left"), state, depth + 1)
            right = self.static_eval(node.get("right"), state, depth + 1)
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            if operator == "and":
                return right if truthy(left) else left
            if operator == "or":
                return left if truthy(left) else right
            if operator == "..":
                return left + right if isinstance(left, bytes) and isinstance(right, bytes) else UNKNOWN
            if operator in {"==", "~=", "<", "<=", ">", ">="}:
                if is_nan(left) or is_nan(right):
                    return operator == "~="
                if numeric_kind(left) != numeric_kind(right) and not (is_number(left) and is_number(right)):
                    return UNKNOWN
                try:
                    if operator == "==":
                        return left == right
                    if operator == "~=":
                        return left != right
                    if operator == "<":
                        return left < right
                    if operator == "<=":
                        return left <= right
                    if operator == ">":
                        return left > right
                    return left >= right
                except (TypeError, ValueError):
                    return UNKNOWN
            return numeric_result(operator, left, right)
        if node.kind == "call":
            return self.static_call(node, state, depth)
        return UNKNOWN

    def static_call(self, node: Node, state: dict[tuple[Any, ...], Node] | None, depth: int) -> Any:
        function_value = self.value_nodes.get(id(node.get("func")))
        derived_name = self.dotted_name(node.get("func"))
        if isinstance(function_value, Node) and function_value.kind in {"name", "indexname"}:
            derived_name = self.dotted_name(function_value) or derived_name
        if isinstance(function_value, Node) and function_value.kind == "function":
            formula = self.decoder_formula(function_value)
            if formula and len(node.get("args", [])) >= 2:
                args = [self.static_eval(arg, state, depth + 1) for arg in node.get("args", [])]
                if len(args) >= 2 and (isinstance(args[0], bytes) or isinstance(args[0], BufferValue)) and is_int(args[1]):
                    cipher = args[0].data if isinstance(args[0], BufferValue) else args[0]
                    trim = args[2] if len(args) > 2 and is_int(args[2]) else 0
                    if 0 <= trim <= len(cipher):
                        if formula == "xor":
                            return xor_decode(cipher, args[1], trim)
                        if formula == "golden":
                            return golden_xor_decode(cipher, args[1], trim)
                        if formula == "lcg":
                            return lcg_subtract_decode(cipher, args[1], trim)
                        if formula == "park":
                            return park_xor_decode(cipher, args[1], trim)
        args = [self.static_eval(argument, state, depth + 1) for argument in node.get("args", [])]
        return builtin_call(derived_name, args)

    def apply(self, root: Node, stats: PassStats) -> Node:
        self.build_replacements(stats)
        if not self.replacements and not self.value_nodes:
            return root

        def callback(node: Node, target: bool) -> Node:
            if target:
                return node
            if node.kind == "call":
                result = self.static_call(node, None, 0)
                if result is not UNKNOWN and isinstance(result, (bytes, str, int, float, bool)) or result is None:
                    stats.decoder_calls += 1
                    return value_node(result)
            replacement = self.replacements.get(id(node))
            if replacement is not None and node.kind == "index":
                if replacement.kind == "function":
                    return node
                stats.pool_reads += 0
                return replacement.clone()
            return node

        root.fields["body"] = transform_block(root.get("body", []), callback)
        return root


def xor_decode(data: bytes, key: int, trim: int = 0) -> bytes:
    end = max(0, len(data) - trim)
    repeated = (key & 0xFF) * 0x01010101
    output = bytearray()
    for offset in range(0, end - end % 4, 4):
        word = int.from_bytes(data[offset:offset + 4], "little") ^ (repeated & 0xFFFFFFFF)
        output.extend(word.to_bytes(4, "little"))
    for index in range(len(output), end):
        output.append(data[index] ^ (key & 0xFF))
    return bytes(output)


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _rol32(value: int, amount: int) -> int:
    amount %= 32
    value = _u32(value)
    return _u32((value << amount) | (value >> (32 - amount))) if amount else value


def avalanche_hash(value: int) -> int:
    value = _u32(value)
    for add, right, left, rotate in (
        (2654435769, 16, 5, 11), (2246822507, 13, 9, 19),
        (3266489909, 15, 7, 23), (668265263, 17, 11, 13),
        (374761393, 11, 13, 7), (4283543511, 14, 6, 17),
    ):
        value = _u32(value + add)
        value = _u32(value ^ (value >> right))
        value = _u32(value ^ (value << left))
        value = _rol32(value, rotate)
    return value


def golden_xor_decode(data: bytes, seed: int, trim: int = 0) -> bytes:
    end = max(0, len(data) - trim)
    state = _u32(seed)
    output = bytearray(data)
    offset = 0
    while offset + 4 <= end:
        state = _u32(state + 2654435769)
        word = int.from_bytes(data[offset:offset + 4], "little") ^ state
        output[offset:offset + 4] = _u32(word).to_bytes(4, "little")
        offset += 4
    if offset < end:
        state = _u32(state + 2654435769)
        for index in range(offset, end):
            output[index] = (data[index] ^ ((state >> (8 * (index - offset))) & 0xFF)) & 0xFF
    return bytes(output[:end])


def lcg_subtract_decode(data: bytes, seed: int, trim: int = 0) -> bytes:
    end = max(0, len(data) - trim)
    state = _u32(seed)
    output = bytearray(data)
    offset = 0
    while offset + 4 <= end:
        state = _u32(state * 1597 + 51749)
        word = (int.from_bytes(data[offset:offset + 4], "little") - state) & 0xFFFFFFFF
        output[offset:offset + 4] = word.to_bytes(4, "little")
        offset += 4
    if offset < end:
        state = _u32(state * 1597 + 51749)
        for index in range(offset, end):
            output[index] = (data[index] - ((state >> (8 * (index - offset))) & 0xFF)) & 0xFF
    return bytes(output[:end])


def park_xor_decode(data: bytes, seed: int, trim: int = 0) -> bytes:
    end = max(0, len(data) - trim)
    state = avalanche_hash(seed) % 2147483646 + 1
    state = (state * 16807) % 2147483647
    output = bytearray(data)
    for offset in range(0, end - (end % 4), 4):
        word = int.from_bytes(data[offset:offset + 4], "little") ^ state
        output[offset:offset + 4] = _u32(word).to_bytes(4, "little")
    for index in range(end - (end % 4), end):
        lane = (state >> (8 * (index - (end - (end % 4))))) & 0xFF
        output[index] = data[index] ^ lane
    return bytes(output[:end])


def builtin_call(name: str | None, args: list[Any]) -> Any:
    if name is None:
        return UNKNOWN
    if name == "string.byte" and args and isinstance(args[0], bytes):
        start = int(args[1]) if len(args) > 1 and is_int(args[1]) else 1
        if start < 1 or start > len(args[0]):
            return UNKNOWN
        return args[0][start - 1]
    if name == "string.char" and args and all(is_int(value) and 0 <= value <= 255 for value in args):
        return bytes(args)
    if name == "tostring" and len(args) == 1 and isinstance(args[0], bytes):
        return args[0]
    if name == "buffer.fromstring" and len(args) == 1 and isinstance(args[0], bytes):
        return BufferValue(args[0])
    if name == "buffer.readstring" and len(args) >= 2 and isinstance(args[0], BufferValue):
        offset = args[1]
        length = args[2] if len(args) > 2 else len(args[0].data) - offset
        if not is_int(offset) or not is_int(length) or offset < 0 or length < 0 or offset + length > len(args[0].data):
            return UNKNOWN
        return args[0].data[offset:offset + length]
    if name == "buffer.readu8" and len(args) >= 2 and isinstance(args[0], BufferValue) and is_int(args[1]):
        if args[1] < 0 or args[1] >= len(args[0].data):
            return UNKNOWN
        return args[0].data[args[1]]
    if name == "buffer.readu32" and len(args) >= 2 and isinstance(args[0], BufferValue) and is_int(args[1]):
        if args[1] < 0 or args[1] + 4 > len(args[0].data):
            return UNKNOWN
        return int.from_bytes(args[0].data[args[1]:args[1] + 4], "little")
    if name == "bit32.bxor" and args and all(is_int(value) for value in args):
        result = 0
        for value in args:
            result ^= value & 0xFFFFFFFF
        return result & 0xFFFFFFFF
    if name == "bit32.band" and args and all(is_int(value) for value in args):
        result = 0xFFFFFFFF
        for value in args:
            result &= value & 0xFFFFFFFF
        return result & 0xFFFFFFFF
    if name == "bit32.bor" and args and all(is_int(value) for value in args):
        result = 0
        for value in args:
            result |= value & 0xFFFFFFFF
        return result & 0xFFFFFFFF
    if name == "bit32.rshift" and len(args) == 2 and is_int(args[0]) and is_int(args[1]):
        return (args[0] & 0xFFFFFFFF) >> min(31, max(0, args[1]))
    if name == "bit32.lshift" and len(args) == 2 and is_int(args[0]) and is_int(args[1]):
        return (args[0] << min(31, max(0, args[1]))) & 0xFFFFFFFF
    if name == "bit32.rrotate" and len(args) == 2 and is_int(args[0]) and is_int(args[1]):
        shift = args[1] % 32
        value = args[0] & 0xFFFFFFFF
        return ((value >> shift) | (value << (32 - shift))) & 0xFFFFFFFF if shift else value
    if name == "bit32.lrotate" and len(args) == 2 and is_int(args[0]) and is_int(args[1]):
        shift = args[1] % 32
        value = args[0] & 0xFFFFFFFF
        return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF if shift else value
    if name == "math.floor" and len(args) == 1 and is_number(args[0]):
        return math.floor(args[0])
    if name == "math.abs" and len(args) == 1 and is_number(args[0]):
        return abs(args[0])
    if name == "math.max" and args and all(is_number(value) for value in args):
        return max(args)
    if name == "math.min" and args and all(is_number(value) for value in args):
        return min(args)
    return UNKNOWN


def rename_bindings(root: Node, analyzer: Analyzer, stats: PassStats) -> Node:
    used_names = set(analyzer.global_names)
    for node in walk(root):
        if node.kind == "name":
            used_names.add(node.get("name", ""))
    counters = {"var": 0, "func": 0, "param": 0, "loop": 0}
    names: dict[int, str] = {}
    for binding in sorted(analyzer.bindings.values(), key=lambda item: item.order):
        if binding.name in {"_ENV", "self"}:
            continue
        if len(binding.name) > 3 and not any(character.isdigit() for character in binding.name):
            continue
        category = "param" if binding.kind == "param" else "func" if binding.kind == "localfunc" else "loop" if binding.kind == "loop" else "var"
        while True:
            index = counters[category] + 1
            counters[category] = index
            candidate = f"{category}_{index}"
            if candidate not in used_names:
                break
        used_names.add(candidate)
        names[binding.ident] = candidate
    if not names:
        return root
    for node in walk(root):
        if node.kind == "local":
            bindings = analyzer.declaration_bindings.get(id(node), [])
            for index, binding in enumerate(bindings):
                if binding.ident in names and index < len(node.get("names", [])):
                    node.fields["names"][index] = names[binding.ident]
        elif node.kind == "localfunc":
            bindings = analyzer.declaration_bindings.get(id(node), [])
            if bindings and bindings[0].ident in names:
                node.fields["name"] = names[bindings[0].ident]
        elif node.kind == "fornum":
            bindings = analyzer.loop_bindings.get(id(node), [])
            if bindings and bindings[0].ident in names:
                node.fields["var"] = names[bindings[0].ident]
        elif node.kind == "forin":
            values = node.fields.get("vars", [])
            for index, binding in enumerate(analyzer.loop_bindings.get(id(node), [])):
                if binding.ident in names and index < len(values):
                    values[index] = names[binding.ident]
        elif node.kind in {"function", "localfunc", "funcdef"}:
            for index, binding in enumerate(analyzer.parameter_bindings.get(id(node), [])):
                if binding.ident in names and index < len(node.get("params", [])):
                    node.fields["params"][index] = names[binding.ident]

    def callback(node: Node, target: bool) -> Node:
        if node.kind != "name":
            return node
        binding = binding_for(analyzer, node)
        if binding is not None and binding.ident in names:
            node.fields["name"] = names[binding.ident]
            stats.names_renamed += 1
        return node

    root.fields["body"] = transform_block(root.get("body", []), callback)
    return root

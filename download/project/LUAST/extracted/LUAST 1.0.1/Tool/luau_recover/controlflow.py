from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from .analysis import Analyzer, Binding, binding_for
from .emitter import Emitter
from .model import Node, clone_value, count_nodes, walk
from .passes import UNKNOWN, bool_const, constant_environment, evaluate, is_number, is_int, numeric_result, truthy, builtin_call, value_node, is_phi, phi_apply, phi_binop, phi_from_leaves, unphi


class StateSubstitutionError(Exception):
    pass


def _junk_truth(node: Node | None, base_eval) -> bool | None:
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


@dataclass
class DispatchPath:
    conditions: list[tuple[Node, bool]]
    actions: list[Node]
    outcome: tuple[Any, ...]
    env: dict[int, Any] = field(default_factory=dict)


@dataclass
class Dispatcher:
    loop: Node
    statements: list[Node]
    index: int
    state: Binding
    initial: int | float
    transform: tuple[str, int | float]
    tree: list[Node]


class DispatcherBail(Exception):
    pass


class CycleHit(Exception):
    def __init__(self, key: Any):
        self.key = key
        super().__init__("cycle")


class DispatcherPass:
    def __init__(self, root: Node, analyzer: Analyzer, source: str, stats):
        self.root = root
        self.analyzer = analyzer
        self.source = source
        self.stats = stats
        self.max_states = 4096
        self.max_paths = 96
        self.max_work = 400000
        self.emit_budget = 40000
        # Emitted-tree node cap: DAG-shaped state graphs tree-expand during
        # emit (shared successors re-emitted per predecessor), so the final
        # tree can dwarf the graph itself. Bail oversized dispatchers.
        self.max_emit_nodes = 150000
        self.work = 0
        self.base_env = constant_environment(analyzer)
        self.bailed: set[int] = set()

    def apply(self, rounds: int = 8) -> int:
        completed = 0
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
        for _ in range(rounds):
            changed = False
            lists = self.statement_lists(self.root)
            candidates: list[Dispatcher] = []
            for statements in lists:
                for index, statement in enumerate(statements):
                    if statement.kind != "while":
                        continue
                    if id(statement) in self.bailed:
                        continue
                    candidate = self.recognize(statements, index, statement)
                    if candidate is not None:
                        candidates.append(candidate)
            candidates.sort(key=lambda item: item.loop.start, reverse=True)
            for candidate in candidates:
                try:
                    replacement = self.recover(candidate)
                except DispatcherBail:
                    self.bailed.add(id(candidate.loop))
                    continue
                except (RecursionError, OverflowError, ValueError, TypeError):
                    self.bailed.add(id(candidate.loop))
                    continue
                if replacement is None:
                    self.bailed.add(id(candidate.loop))
                    continue
                candidate.statements[candidate.index] = replacement
                self.stats.dispatchers_removed += 1
                completed += 1
                changed = True
            if not changed:
                break
        return completed

    def statement_lists(self, root: Node) -> list[list[Node]]:
        result: list[list[Node]] = []
        seen: set[int] = set()

        def add(statements: object) -> None:
            if isinstance(statements, list) and id(statements) not in seen:
                seen.add(id(statements))
                result.append(statements)

        add(root.fields.get("body", []))
        for node in walk(root):
            for key in ("body", "then", "else_"):
                add(node.fields.get(key))
            for _, body in node.fields.get("elifs", []):
                add(body)
        return result

    def recognize(self, statements: list[Node], index: int, loop: Node) -> Dispatcher | None:
        if not self.is_true(loop.get("cond")):
            return None
        body = loop.get("body", [])
        if not body:
            return None
        head = body[0]
        state = None
        transform: tuple[str, int | float] = ("identity", 0)
        tree = body
        if head.kind == "assign" and len(head.get("targets", [])) == 1 and len(head.get("values", [])) == 1:
            target = head.get("targets", [])[0]
            if target.kind == "name":
                candidate = binding_for(self.analyzer, target)
                if candidate is not None:
                    found = self.transition(head.get("values", [])[0], candidate)
                    if found is not None:
                        state = candidate
                        transform = found
                        tree = body[1:]
        if state is None:
            # Style B: no loop-top transform; the state variable is dispatched
            # directly (`while true do if s < K then ... end end`) and its
            # initial value comes from an earlier assignment (often a resolved
            # pool read). Infer the variable from the dispatch comparisons.
            state = self.infer_state_var(body)
            if state is None:
                return None
        initial = self.initial_value(statements, index, state)
        if initial is None:
            return None
        if len(tree) == 1 and tree[0].kind == "do":
            tree = tree[0].get("body", [])
        if not self.looks_like_dispatch(tree, state):
            return None
        return Dispatcher(loop, statements, index, state, initial, transform, tree)

    def infer_state_var(self, tree: list[Node]) -> Binding | None:
        """Find the dispatcher state variable for transform-less loops.

        Candidates are plain locals compared against number literals inside
        the tree that also receive assignments within it. The candidate with
        the most comparisons wins; ties prefer the one assigned most often.
        Ordinary `while true` loops without a numeric dispatch pattern are
        rejected here, so they are never mistaken for dispatchers."""
        comparisons: dict[int, tuple[Binding, int]] = {}
        assignments: dict[int, int] = {}

        def visit(node: Node) -> None:
            if node.kind == "binop" and node.get("op") in {"<", "<=", ">", ">=", "==", "~="}:
                for side in (node.get("left"), node.get("right")):
                    if isinstance(side, Node) and side.kind == "name":
                        binding = binding_for(self.analyzer, side)
                        if binding is not None:
                            ident = binding.ident
                            entry = comparisons.get(ident)
                            if entry is None:
                                comparisons[ident] = (binding, 1)
                            else:
                                comparisons[ident] = (binding, entry[1] + 1)
            elif node.kind == "assign":
                for value in node.get("values", []):
                    if isinstance(value, Node) and value.kind in {"number", "ifexpr"}:
                        for target in node.get("targets", []):
                            if isinstance(target, Node) and target.kind == "name":
                                binding = binding_for(self.analyzer, target)
                                if binding is not None:
                                    assignments[binding.ident] = assignments.get(binding.ident, 0) + 1
            elif node.kind == "if":
                for value in (node.get("cond"),):
                    if isinstance(value, Node) and value.kind == "ifexpr":
                        pass
            elif node.kind in {"function", "localfunc"}:
                return
            for value in node.fields.values():
                if isinstance(value, Node):
                    visit(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, Node):
                            visit(item)

        for statement in tree:
            visit(statement)
        best: Binding | None = None
        best_score: tuple[int, int] = (0, 0)
        for ident, (binding, count) in comparisons.items():
            assigned = assignments.get(ident, 0)
            if assigned == 0:
                continue
            score = (count, assigned)
            if score > best_score:
                best_score = score
                best = binding
        return best

    def is_true(self, node: Node) -> bool:
        return isinstance(node, Node) and node.kind == "bool" and node.get("value") is True

    def transition(self, expression: Node, state: Binding) -> tuple[str, int | float] | None:
        if expression.kind == "paren":
            inner = expression.get("expr")
            if isinstance(inner, Node):
                return self.transition(inner, state)
        if expression.kind == "binop" and expression.get("op") == "-":
            left = expression.get("left")
            right = expression.get("right")
            if self.is_state(right, state) and self.is_number_node(left):
                return ("complement", left.get("value"))
            if self.is_state(left, state) and self.is_number_node(right):
                return ("negate", right.get("value"))
        if expression.kind == "call":
            name = self.dotted_name(expression.get("func"))
            args = expression.get("args", [])
            if name == "bit32.bxor" and len(args) == 2 and self.is_state(args[0], state) and self.is_number_node(args[1]):
                return ("xor", args[1].get("value"))
            if name == "bit32.bxor" and len(args) == 2 and self.is_state(args[1], state) and self.is_number_node(args[0]):
                return ("xor", args[0].get("value"))
        return None

    def dotted_name(self, node: Node | None) -> str | None:
        if not isinstance(node, Node):
            return None
        if node.kind == "name":
            return node.get("name")
        if node.kind == "indexname":
            base = self.dotted_name(node.get("obj"))
            return base + "." + node.get("name", "") if base else None
        return None

    def is_state(self, node: Node | None, state: Binding) -> bool:
        return isinstance(node, Node) and node.kind == "name" and binding_for(self.analyzer, node) is state

    def is_number_node(self, node: Node | None) -> bool:
        return isinstance(node, Node) and node.kind == "number" and is_number(node.get("value"))

    def initial_value(self, statements: list[Node], index: int, state: Binding) -> int | float | None:
        for statement in reversed(statements[:index]):
            if statement.kind == "local":
                bindings = self.analyzer.declaration_bindings.get(id(statement), [])
                names = statement.get("names", [])
                for binding, value in zip(bindings, statement.get("values", [])):
                    if binding is state and self.is_number_node(value):
                        return value.get("value")
                if state.name in names:
                    return None
            if statement.kind == "assign":
                for target, value in zip(statement.get("targets", []), statement.get("values", [])):
                    if target.kind == "name" and binding_for(self.analyzer, target) is state:
                        return value.get("value") if self.is_number_node(value) else None
            if any(child.kind == "name" and binding_for(self.analyzer, child) is state for child in self.node_children(statement)):
                return None
        return None

    def node_children(self, node: Node) -> list[Node]:
        result: list[Node] = []
        for value in node.fields.values():
            if isinstance(value, Node):
                result.append(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        result.append(item)
                    elif isinstance(item, (list, tuple)):
                        result.extend(item for item in item if isinstance(item, Node))
        return result

    def looks_like_dispatch(self, tree: list[Node], state: Binding) -> bool:
        found = False
        for node in tree:
            for child in self.node_children(node):
                if child.kind == "binop" and child.get("op") in {"<", "<=", ">", ">=", "==", "~="}:
                    if self.is_state(child.get("left"), state) or self.is_state(child.get("right"), state):
                        found = True
        if found:
            return True
        return any(node.kind in {"break", "continue", "return"} for node in tree)

    def apply_transform(self, value: int | float, transform: tuple[str, int | float]) -> int | float:
        kind, constant = transform
        if kind == "identity":
            return value
        if kind == "complement":
            return constant - value
        if kind == "negate":
            return value - constant
        return int(constant) ^ int(value)

    def eval_data(self, node: Node | None, state: Binding, value: int | float, env: dict[int, Any]) -> Any:
        if node is None:
            return UNKNOWN
        if self.is_state(node, state):
            return value
        if node.kind == "number" or node.kind == "string" or node.kind == "bool":
            return node.get("value")
        if node.kind == "nil":
            return None
        if node.kind == "name":
            binding = binding_for(self.analyzer, node)
            if binding is not None and binding.ident in env:
                return env[binding.ident]
            if binding is not None and binding.ident in self.base_env:
                return self.base_env[binding.ident]
            return UNKNOWN
        if node.kind == "paren":
            return self.eval_data(node.get("expr"), state, value, env)
        if node.kind == "unop":
            inner = self.eval_data(node.get("expr"), state, value, env)
            if is_phi(inner):
                if node.get("op") == "not":
                    return phi_apply(lambda item: not truthy(item), inner)
                if node.get("op") == "-":
                    return phi_apply(lambda item: -item if is_number(item) else UNKNOWN, inner)
                return UNKNOWN
            if inner is UNKNOWN:
                resolved = bool_const(node)
                return resolved if resolved is not None else UNKNOWN
            if node.get("op") == "not":
                return not truthy(inner)
            if node.get("op") == "-":
                return -inner
            if node.get("op") == "#" and isinstance(inner, bytes):
                return len(inner)
            return UNKNOWN
        if node.kind == "binop":
            operator = node.get("op")
            if operator == "and":
                left = self.eval_data(node.get("left"), state, value, env)
                if left is UNKNOWN or is_phi(left):
                    resolved = bool_const(node)
                    return resolved if resolved is not None else UNKNOWN
                return self.eval_data(node.get("right"), state, value, env) if truthy(left) else left
            if operator == "or":
                left = self.eval_data(node.get("left"), state, value, env)
                if left is UNKNOWN or is_phi(left):
                    resolved = bool_const(node)
                    return resolved if resolved is not None else UNKNOWN
                return left if truthy(left) else self.eval_data(node.get("right"), state, value, env)
            left = self.eval_data(node.get("left"), state, value, env)
            right = self.eval_data(node.get("right"), state, value, env)
            if is_phi(left) or is_phi(right):
                return phi_binop(operator, left, right)
            if operator in {"==", "~=", "<", "<=", ">", ">="} and (left is UNKNOWN or right is UNKNOWN):
                folded = _junk_truth(node, lambda item: unphi(self.eval_data(item, state, value, env)))
                if folded is not None:
                    return folded
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            if operator == "..":
                return left + right if isinstance(left, bytes) and isinstance(right, bytes) else UNKNOWN
            if operator in {"==", "~=", "<", "<=", ">", ">="}:
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
            name = self.dotted_name(node.get("func"))
            args = [self.eval_data(argument, state, value, env) for argument in node.get("args", [])]
            return builtin_call(name, args)
        if node.kind == "ifexpr":
            all_leaves = [node.get("then")] + [branch_value for _, branch_value in node.get("elifs", [])] + [node.get("else_")]
            condition = self.eval_data(node.get("cond"), state, value, env)
            if condition is UNKNOWN or is_phi(condition):
                return phi_from_leaves(all_leaves, lambda leaf: self.eval_data(leaf, state, value, env))
            if truthy(condition):
                return self.eval_data(node.get("then"), state, value, env)
            pending = list(node.get("elifs", []))
            index = 0
            while index < len(pending):
                branch_condition, branch_value = pending[index]
                branch_result = self.eval_data(branch_condition, state, value, env)
                if branch_result is UNKNOWN or is_phi(branch_result):
                    remaining = [branch_value] + [item for _, item in pending[index + 1:]] + [node.get("else_")]
                    return phi_from_leaves(remaining, lambda leaf: self.eval_data(leaf, state, value, env))
                if truthy(branch_result):
                    return self.eval_data(branch_value, state, value, env)
                index += 1
            return self.eval_data(node.get("else_"), state, value, env)
        return UNKNOWN

    def eval_state(self, node: Node, state: Binding, value: int | float, env: dict[int, Any]) -> int | float:
        result = self.eval_data(node, state, value, env)
        if result is UNKNOWN or not is_number(result):
            raise DispatcherBail("dynamic state value")
        return result

    def eval_condition(self, node: Node, state: Binding, value: int | float, env: dict[int, Any]) -> bool:
        result = self.eval_data(node, state, value, env)
        if result is UNKNOWN or is_phi(result):
            raise DispatcherBail("dynamic dispatcher condition")
        return truthy(result)

    def substitute_state(self, node: Node, state: Binding, value: int | float) -> Node:
        """Return a clone of node with every reference to the dispatcher
        state replaced by its known numeric value at this program point."""
        replacement = value_node(value)

        def visit_pair(original: Node | None, clone: Node | None) -> None:
            if original is None or clone is None:
                return
            if original.kind == "function":
                raise StateSubstitutionError("state captured in payload closure")
            if original.kind == "name" and binding_for(self.analyzer, original) is state:
                clone.kind = replacement.kind
                clone.fields = dict(replacement.fields)
                return
            for key, field in original.fields.items():
                clone_field = clone.fields.get(key)
                if isinstance(field, Node) and isinstance(clone_field, Node):
                    visit_pair(field, clone_field)
                elif isinstance(field, (list, tuple)) and isinstance(clone_field, (list, tuple)):
                    visit_list(field, clone_field)

        def visit_list(originals: Any, clones: Any) -> None:
            for original, clone in zip(originals, clones):
                if isinstance(original, Node) and isinstance(clone, Node):
                    visit_pair(original, clone)
                elif isinstance(original, (list, tuple)) and isinstance(clone, (list, tuple)):
                    visit_list(original, clone)

        clone = node.clone()
        visit_pair(node, clone)
        return clone

    def record_condition_value(self, condition: Node | None, value: bool, env: dict[int, Any], state: Binding | None = None) -> None:
        """Remember a branch decision in the path environment so re-tests of
        the same binding along this path resolve instead of forking again
        (luast emits `if X then A else if X then B end` chains whose inner
        tests are provably dead on their path)."""
        current = condition
        while isinstance(current, Node) and current.kind == "paren":
            current = current.get("expr")
        if not isinstance(current, Node):
            return
        if current.kind == "name":
            binding = binding_for(self.analyzer, current)
            if binding is not None and (state is None or binding is not state):
                env[binding.ident] = value
        elif current.kind == "unop" and current.get("op") == "not":
            inner = current.get("expr")
            if isinstance(inner, Node) and inner.kind == "name":
                binding = binding_for(self.analyzer, inner)
                if binding is not None and (state is None or binding is not state):
                    env[binding.ident] = not value

    def execute(self, statements: list[Node], state: Binding, value: int | float, conditions: list[tuple[Node, bool]], actions: list[Node], depth: int = 0, env: dict[int, Any] | None = None) -> list[DispatchPath]:
        self.work += 1
        if self.work > self.max_work:
            raise DispatcherBail("work limit")
        if depth > 100:
            raise DispatcherBail("dispatcher nesting limit")
        paths: list[DispatchPath] = []
        current_value = value
        current_conditions = list(conditions)
        current_actions = list(actions)
        current_env = dict(env or {})
        for statement_index, statement in enumerate(statements):
            kind = statement.kind
            if kind == "assign":
                targets = statement.get("targets", [])
                values = statement.get("values", [])
                state_targets = [target for target in targets if target.kind == "name" and binding_for(self.analyzer, target) is state]
                if state_targets:
                    if len(targets) != 1 or len(values) != 1:
                        raise DispatcherBail("state assignment mixed with other targets")
                    evaluated = self.eval_data(values[0], state, current_value, current_env)
                    if evaluated is UNKNOWN or not is_number(evaluated):
                        if values[0].kind == "ifexpr":
                            return self.expand_state_ifexpr(values[0], statements, statement_index, state, current_value, current_conditions, current_actions, current_env, depth)
                        if is_phi(evaluated):
                            if all(type(item) is type(evaluated.values[0]) and item == evaluated.values[0] for item in evaluated.values):
                                evaluated = evaluated.values[0]
                            else:
                                raise DispatcherBail("phi state value")
                        if evaluated is UNKNOWN:
                            raise DispatcherBail("dynamic state value")
                        # Non-number sentinel (LUAST junk transition, e.g.
                        # `s = "hwid"`): the dispatch cannot continue, so the
                        # path dead-ends instead of killing the recovery.
                        paths.append(DispatchPath(current_conditions, current_actions, ("dead",), dict(current_env)))
                        return paths
                    current_value = evaluated
                    continue
                if self.contains_state(statement, state):
                    try:
                        current_actions.append(self.substitute_state(statement, state, current_value))
                    except StateSubstitutionError as exc:
                        raise DispatcherBail(str(exc))
                    if len(targets) == 1 and len(values) == 1 and targets[0].kind == "name":
                        target_binding = binding_for(self.analyzer, targets[0])
                        if target_binding is not None:
                            assigned = self.eval_data(values[0], state, current_value, current_env)
                            if assigned is UNKNOWN:
                                current_env.pop(target_binding.ident, None)
                            else:
                                current_env[target_binding.ident] = assigned
                    continue
                if len(targets) == 1 and len(values) == 1 and targets[0].kind == "name":
                    target_binding = binding_for(self.analyzer, targets[0])
                    if target_binding is not None:
                        assigned = self.eval_data(values[0], state, current_value, current_env)
                        if assigned is UNKNOWN:
                            current_env.pop(target_binding.ident, None)
                        else:
                            current_env[target_binding.ident] = assigned
                current_actions.append(statement)
                continue
            if kind == "local":
                if any(name in statement.get("names", []) for name in [state.name]) or self.contains_state(statement, state):
                    if self.contains_state(statement, state):
                        try:
                            current_actions.append(self.substitute_state(statement, state, current_value))
                        except StateSubstitutionError as exc:
                            raise DispatcherBail(str(exc))
                        for local_binding, local_value in zip(self.analyzer.declaration_bindings.get(id(statement), []), statement.get("values", [])):
                            assigned = self.eval_data(local_value, state, current_value, current_env)
                            if assigned is UNKNOWN:
                                current_env.pop(local_binding.ident, None)
                            else:
                                current_env[local_binding.ident] = assigned
                        continue
                    raise DispatcherBail("state in local declaration")
                for local_binding, local_value in zip(self.analyzer.declaration_bindings.get(id(statement), []), statement.get("values", [])):
                    assigned = self.eval_data(local_value, state, current_value, current_env)
                    if assigned is UNKNOWN:
                        current_env.pop(local_binding.ident, None)
                    else:
                        current_env[local_binding.ident] = assigned
                current_actions.append(statement)
                continue
            if kind == "if":
                try:
                    condition_value = self.eval_condition(statement.get("cond"), state, current_value, current_env)
                except DispatcherBail:
                    if self.contains_state(statement.get("cond"), state):
                        raise
                    return self.fork_if(statement, state, current_value, current_conditions, current_actions, depth, current_env)
                if condition_value:
                    selected = statement.get("then", [])
                else:
                    selected = self.else_chain(statement)
                self.record_condition_value(statement.get("cond"), condition_value, current_env, state)
                paths.extend(self.execute(selected, state, current_value, current_conditions, current_actions, depth + 1, current_env))
                return paths
            if kind == "do":
                paths.extend(self.execute(statement.get("body", []), state, current_value, current_conditions, current_actions, depth + 1, current_env))
                return paths
            if kind in {"while", "repeat", "fornum", "forin"}:
                if self.contains_state(statement, state):
                    try:
                        current_actions.append(self.substitute_state(statement, state, current_value))
                    except StateSubstitutionError as exc:
                        raise DispatcherBail(str(exc))
                    continue
                current_actions.append(statement)
                continue
            if kind in {"call", "methodcall"}:
                if self.contains_state(statement, state):
                    try:
                        current_actions.append(self.substitute_state(statement, state, current_value))
                    except StateSubstitutionError as exc:
                        raise DispatcherBail(str(exc))
                    continue
                current_actions.append(statement)
                continue
            if kind == "return":
                if any(self.contains_state(value, state) for value in statement.get("values", [])):
                    substituted = []
                    try:
                        for value_node_item in statement.get("values", []):
                            substituted.append(self.substitute_state(value_node_item, state, current_value))
                    except StateSubstitutionError as exc:
                        raise DispatcherBail(str(exc))
                    paths.append(DispatchPath(current_conditions, current_actions, ("return", substituted), dict(current_env)))
                    return paths
                paths.append(DispatchPath(current_conditions, current_actions, ("return", statement.get("values", [])), dict(current_env)))
                return paths
            if kind == "break":
                paths.append(DispatchPath(current_conditions, current_actions, ("break",), dict(current_env)))
                return paths
            if kind == "continue":
                paths.append(DispatchPath(current_conditions, current_actions, ("edge", current_value), dict(current_env)))
                return paths
            raise DispatcherBail("unsupported dispatcher statement " + kind)
        paths.append(DispatchPath(current_conditions, current_actions, ("edge", current_value), dict(current_env)))
        return paths

    def expand_state_ifexpr(self, expression: Node, statements: list[Node], index: int, state: Binding, value: int | float, conditions: list[tuple[Node, bool]], actions: list[Node], env: dict[int, Any], depth: int) -> list[DispatchPath]:
        branches: list[tuple[Node | None, Node, list[tuple[Node, bool]]]] = []
        prior: list[tuple[Node, bool]] = []
        first_condition = expression.get("cond")
        branches.append((first_condition, expression.get("then"), prior + [(first_condition, True)]))
        prior = prior + [(first_condition, False)]
        for branch_condition, branch_value in expression.get("elifs", []):
            branches.append((branch_condition, branch_value, prior + [(branch_condition, True)]))
            prior = prior + [(branch_condition, False)]
        branches.append((None, expression.get("else_"), list(prior)))
        result: list[DispatchPath] = []
        for condition, leaf, branch_conditions in branches:
            if condition is None:
                selected: bool | None = True
            else:
                try:
                    selected = self.eval_condition(condition, state, value, env)
                except DispatcherBail:
                    selected = None
            if selected is False:
                continue
            branch_env = dict(env)
            if condition is not None:
                self.record_condition_value(condition, True, branch_env, state)
            if leaf is None:
                # `state = if c then v` with a false condition leaves the
                # state unchanged; treat it as an edge to the same state.
                leaf_value = value
            else:
                leaf_eval = self.eval_data(leaf, state, value, branch_env)
                if leaf_eval is UNKNOWN or not is_number(leaf_eval):
                    # Non-number leaf: junk transition, drop this branch.
                    continue
                leaf_value = leaf_eval
            branch_path = list(conditions) + (branch_conditions if selected is None else [])
            result.extend(self.execute(statements[index + 1:], state, leaf_value, branch_path, list(actions), depth + 1, branch_env))
        return result

    def else_chain(self, statement: Node) -> list[Node]:
        if not statement.get("elifs"):
            return statement.get("else_", [])
        condition, body = statement.get("elifs", [])[0]
        nested = Node("if", condition.start, condition.end, cond=condition, then=body, elifs=statement.get("elifs", [])[1:], else_=statement.get("else_", []))
        return [nested]

    def fork_if(self, statement: Node, state: Binding, value: int | float, conditions: list[tuple[Node, bool]], actions: list[Node], depth: int, env: dict[int, Any] | None = None) -> list[DispatchPath]:
        condition = statement.get("cond")
        branch_env = dict(env or {})
        result: list[DispatchPath] = []
        then_env = dict(branch_env)
        self.record_condition_value(condition, True, then_env, state)
        result.extend(self.execute(statement.get("then", []), state, value, conditions + [(condition, True)], list(actions), depth + 1, then_env))
        current = list(statement.get("elifs", []))
        prior = condition
        for branch_condition, branch_body in current:
            elif_env = dict(branch_env)
            self.record_condition_value(prior, False, elif_env, state)
            self.record_condition_value(branch_condition, True, elif_env, state)
            result.extend(self.execute(branch_body, state, value, conditions + [(prior, False), (branch_condition, True)], list(actions), depth + 1, elif_env))
            prior = branch_condition
        else_env = dict(branch_env)
        self.record_condition_value(prior, False, else_env, state)
        result.extend(self.execute(statement.get("else_", []), state, value, conditions + [(prior, False)], list(actions), depth + 1, else_env))
        return result

    def contains_state(self, node: Node | None, state: Binding) -> bool:
        if node is None:
            return False
        stack = [node]
        while stack:
            current = stack.pop()
            if current.kind == "name" and binding_for(self.analyzer, current) is state:
                return True
            for value in current.fields.values():
                if isinstance(value, Node):
                    stack.append(value)
                elif isinstance(value, (list, tuple)):
                    stack.extend(item for item in value if isinstance(item, Node))
                    for item in value:
                        if isinstance(item, (list, tuple)):
                            stack.extend(nested for nested in item if isinstance(nested, Node))
        return False

    def recover(self, dispatcher: Dispatcher) -> Node | None:
        self.work = 0
        self.emit_budget = 40000
        self.emit_nodes = 0
        graph: dict[Any, list[DispatchPath]] = {}
        initial = self.apply_transform(dispatcher.initial, dispatcher.transform)
        queue: list[tuple[int | float, dict[int, Any]]] = [(initial, dict(self.base_env))]
        seen: dict[Any, dict[int, Any]] = {}
        requeues: dict[Any, int] = {}
        graph_actions = 0
        while queue:
            value, incoming_env = queue.pop(0)
            key = self.key(value)
            if key in seen:
                existing = seen[key]
                if existing == incoming_env:
                    continue
                # Environment conflict: the state is reachable with
                # different knowledge. Weaken to the shared subset and
                # reprocess instead of giving up on the dispatcher.
                merged = {k: v for k, v in incoming_env.items() if k in existing and existing[k] == v}
                if merged == existing or requeues.get(key, 0) >= 3:
                    continue
                requeues[key] = requeues.get(key, 0) + 1
                seen[key] = merged
                incoming_env = merged
            else:
                if len(seen) >= self.max_states:
                    raise DispatcherBail("state limit")
                seen[key] = dict(incoming_env)
            paths = self.execute(dispatcher.tree, dispatcher.state, value, [], [], env=incoming_env)
            normalized: list[DispatchPath] = []
            for path in paths:
                if path.outcome[0] == "edge":
                    next_value = self.apply_transform(path.outcome[1], dispatcher.transform)
                    normalized.append(DispatchPath(path.conditions, path.actions, ("edge", next_value), path.env))
                    next_key = self.key(next_value)
                    if next_key not in seen or seen[next_key] != path.env:
                        queue.append((next_value, path.env))
                else:
                    normalized.append(path)
                if len(normalized) > self.max_paths:
                    raise DispatcherBail("path limit")
            # Bound the resident graph: every path holds cloned statement
            # subtrees, so a pathological dispatcher can otherwise pin
            # gigabytes. Count real nodes, not just top-level statements.
            graph_actions += sum(count_nodes(action) for path in normalized for action in path.actions)
            graph_actions += len(normalized)
            if graph_actions > 800000:
                raise DispatcherBail("graph budget")
            graph[key] = normalized
        self.stats.dispatchers_found += 1
        self.stats.states_recovered += len(graph)
        statements: list[Node] | None = None
        try:
            statements = self.emit_state(initial, graph, set(), 0)
        except CycleHit:
            for header in graph:
                try:
                    statements = self.emit_state(initial, graph, set(), 0, loop_header=header)
                except CycleHit:
                    continue
                if statements is not None:
                    break
        if statements is None:
            raise DispatcherBail("cyclic or ambiguous graph")
        return Node("do", dispatcher.loop.start, dispatcher.loop.end, body=statements, states=len(graph))

    def key(self, value: int | float) -> tuple[str, int | float]:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return (type(value).__name__, value)

    def emit_state(self, value: int | float, graph: dict[Any, list[DispatchPath]], stack: set[Any], depth: int, loop_header: Any | None = None, in_loop: bool = False) -> list[Node] | None:
        if depth > self.max_paths:
            return None
        key = self.key(value)
        if key in stack:
            if loop_header is not None and key == loop_header and in_loop:
                return [Node("continue")]
            raise CycleHit(key)
        paths = graph.get(key)
        if not paths:
            return None
        next_stack = set(stack)
        next_stack.add(key)
        if loop_header is not None and key == loop_header and not in_loop:
            body = self.emit_paths(paths, graph, next_stack, depth + 1, True, loop_header)
            if body is None:
                return None
            return [Node("while", cond=Node("bool", value=True), body=body)]
        return self.emit_paths(paths, graph, next_stack, depth, in_loop, loop_header)

    def emit_paths(self, paths: list[DispatchPath], graph: dict[Any, list[DispatchPath]], stack: set[Any], depth: int, in_loop: bool = False, loop_header: Any | None = None) -> list[Node] | None:
        if not paths:
            return []
        self.emit_budget -= 1
        if self.emit_budget < 0:
            raise DispatcherBail("emit budget")
        if len(paths) == 1 and not paths[0].conditions:
            return self.emit_path(paths[0], graph, stack, depth, in_loop, loop_header)
        unconditional = [path for path in paths if not path.conditions]
        conditional = [path for path in paths if path.conditions]
        if unconditional and conditional:
            condition = conditional[0].conditions[0][0]
            true_paths = [path for path in conditional if path.conditions[0][0] is condition and path.conditions[0][1]]
            false_paths = [path for path in conditional if path.conditions[0][0] is condition and not path.conditions[0][1]]
            if not true_paths and not false_paths:
                return None
            if not false_paths and len(unconditional) == 1:
                false_paths = unconditional
            if not true_paths or not false_paths or len(unconditional) > 1:
                return None
            true_paths = [DispatchPath(path.conditions[1:], path.actions, path.outcome, path.env) for path in true_paths]
            false_paths = [DispatchPath(path.conditions[1:], path.actions, path.outcome, path.env) for path in false_paths]
            then_body = self.emit_paths(true_paths, graph, set(stack), depth + 1, in_loop, loop_header)
            else_body = self.emit_paths(false_paths, graph, set(stack), depth + 1, in_loop, loop_header)
            if then_body is None or else_body is None:
                return None
            return [Node("if", condition.start, condition.end, cond=condition, then=then_body, elifs=[], else_=else_body)]
        condition = paths[0].conditions[0][0] if paths[0].conditions else None
        if condition is None:
            return None
        true_paths = [path for path in paths if path.conditions and path.conditions[0][0] is condition and path.conditions[0][1]]
        false_paths = [path for path in paths if path.conditions and path.conditions[0][0] is condition and not path.conditions[0][1]]
        if not true_paths or not false_paths:
            return None
        true_paths = [DispatchPath(path.conditions[1:], path.actions, path.outcome, path.env) for path in true_paths]
        false_paths = [DispatchPath(path.conditions[1:], path.actions, path.outcome, path.env) for path in false_paths]
        then_body = self.emit_paths(true_paths, graph, set(stack), depth + 1, in_loop, loop_header)
        else_body = self.emit_paths(false_paths, graph, set(stack), depth + 1, in_loop, loop_header)
        if then_body is None or else_body is None:
            return None
        return [Node("if", condition.start, condition.end, cond=condition, then=then_body, elifs=[], else_=else_body)]

    def emit_path(self, path: DispatchPath, graph: dict[Any, list[DispatchPath]], stack: set[Any], depth: int, in_loop: bool = False, loop_header: Any | None = None) -> list[Node] | None:
        self.emit_budget -= len(path.actions) + 2
        if self.emit_budget < 0:
            raise DispatcherBail("emit budget")
        actions = [action.clone() for action in path.actions]
        self.emit_nodes += sum(count_nodes(action) for action in actions) + 1
        if self.emit_nodes > self.max_emit_nodes:
            raise DispatcherBail("emit size")
        if actions and any(action.kind in {"local", "localfunc"} for action in actions):
            actions = [Node("do", actions[0].start, actions[-1].end, body=actions)]
        outcome = path.outcome
        if outcome[0] == "dead":
            return actions
        if outcome[0] == "break":
            if in_loop:
                actions.append(Node("break"))
            return actions
        if outcome[0] == "return":
            actions.append(Node("return", actions[-1].end if actions else 0, values=[value.clone() for value in outcome[1]]))
            return actions
        if outcome[0] != "edge":
            return None
        successor = self.emit_state(outcome[1], graph, stack, depth + 1, loop_header, in_loop)
        if successor is None:
            return None
        return actions + successor

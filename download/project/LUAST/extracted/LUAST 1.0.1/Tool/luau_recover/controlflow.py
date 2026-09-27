from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from .analysis import Analyzer, Binding, binding_for
from .emitter import Emitter
from .model import Node, clone_value, walk
from .passes import UNKNOWN, bool_const, constant_environment, evaluate, is_number, is_int, numeric_result, truthy, builtin_call, value_node


class StateSubstitutionError(Exception):
    pass


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
        self.work = 0
        self.base_env = constant_environment(analyzer)

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
                    candidate = self.recognize(statements, index, statement)
                    if candidate is not None:
                        candidates.append(candidate)
            candidates.sort(key=lambda item: item.loop.start, reverse=True)
            for candidate in candidates:
                try:
                    replacement = self.recover(candidate)
                except DispatcherBail:
                    continue
                except (RecursionError, OverflowError, ValueError, TypeError):
                    continue
                if replacement is None:
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
        if head.kind != "assign" or len(head.get("targets", [])) != 1 or len(head.get("values", [])) != 1:
            return None
        target = head.get("targets", [])[0]
        if target.kind != "name":
            return None
        state = binding_for(self.analyzer, target)
        if state is None:
            return None
        transform = self.transition(head.get("values", [])[0], state)
        if transform is None:
            return None
        initial = self.initial_value(statements, index, state)
        if initial is None:
            return None
        tree = body[1:]
        if len(tree) == 1 and tree[0].kind == "do":
            tree = tree[0].get("body", [])
        if not self.looks_like_dispatch(tree, state):
            return None
        return Dispatcher(loop, statements, index, state, initial, transform, tree)

    def is_true(self, node: Node) -> bool:
        return isinstance(node, Node) and node.kind == "bool" and node.get("value") is True

    def transition(self, expression: Node, state: Binding) -> tuple[str, int | float] | None:
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
                if left is UNKNOWN:
                    resolved = bool_const(node)
                    return resolved if resolved is not None else UNKNOWN
                return self.eval_data(node.get("right"), state, value, env) if truthy(left) else left
            if operator == "or":
                left = self.eval_data(node.get("left"), state, value, env)
                if left is UNKNOWN:
                    resolved = bool_const(node)
                    return resolved if resolved is not None else UNKNOWN
                return left if truthy(left) else self.eval_data(node.get("right"), state, value, env)
            left = self.eval_data(node.get("left"), state, value, env)
            right = self.eval_data(node.get("right"), state, value, env)
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
            condition = self.eval_data(node.get("cond"), state, value, env)
            if condition is UNKNOWN:
                return UNKNOWN
            if truthy(condition):
                return self.eval_data(node.get("then"), state, value, env)
            for branch_condition, branch_value in node.get("elifs", []):
                branch = self.eval_data(branch_condition, state, value, env)
                if branch is UNKNOWN:
                    return UNKNOWN
                if truthy(branch):
                    return self.eval_data(branch_value, state, value, env)
            return self.eval_data(node.get("else_"), state, value, env)
        return UNKNOWN

    def eval_state(self, node: Node, state: Binding, value: int | float, env: dict[int, Any]) -> int | float:
        result = self.eval_data(node, state, value, env)
        if result is UNKNOWN or not is_number(result):
            raise DispatcherBail("dynamic state value")
        return result

    def eval_condition(self, node: Node, state: Binding, value: int | float, env: dict[int, Any]) -> bool:
        result = self.eval_data(node, state, value, env)
        if result is UNKNOWN:
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
                    try:
                        current_value = self.eval_state(values[0], state, current_value, current_env)
                    except DispatcherBail:
                        if values[0].kind == "ifexpr":
                            return self.expand_state_ifexpr(values[0], statements, statement_index, state, current_value, current_conditions, current_actions, current_env, depth)
                        raise
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
            if leaf is None:
                # `state = if c then v` with a false condition leaves the
                # state unchanged; treat it as an edge to the same state.
                leaf_value = value
            else:
                try:
                    leaf_value = self.eval_state(leaf, state, value, branch_env)
                except DispatcherBail:
                    continue
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
        result.extend(self.execute(statement.get("then", []), state, value, conditions + [(condition, True)], list(actions), depth + 1, branch_env))
        current = list(statement.get("elifs", []))
        prior = condition
        for branch_condition, branch_body in current:
            result.extend(self.execute(branch_body, state, value, conditions + [(prior, False), (branch_condition, True)], list(actions), depth + 1, branch_env))
            prior = branch_condition
        result.extend(self.execute(statement.get("else_", []), state, value, conditions + [(prior, False)], list(actions), depth + 1, branch_env))
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
        graph: dict[Any, list[DispatchPath]] = {}
        initial = self.apply_transform(dispatcher.initial, dispatcher.transform)
        queue: list[tuple[int | float, dict[int, Any]]] = [(initial, dict(self.base_env))]
        seen: dict[Any, dict[int, Any]] = {}
        requeues: dict[Any, int] = {}
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
        actions = [action.clone() for action in path.actions]
        if actions and any(action.kind in {"local", "localfunc"} for action in actions):
            actions = [Node("do", actions[0].start, actions[-1].end, body=actions)]
        outcome = path.outcome
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

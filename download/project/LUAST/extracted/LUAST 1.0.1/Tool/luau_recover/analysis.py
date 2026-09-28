from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Node, iter_nodes, walk


@dataclass
class Binding:
    ident: int
    name: str
    kind: str
    declaration: Node | None
    scope: "Scope"
    reads: int = 0
    writes: int = 0
    order: int = 0


@dataclass
class Scope:
    ident: int
    parent: "Scope | None"
    names: dict[str, Binding] = field(default_factory=dict)


class Analyzer:
    def __init__(self, root: Node):
        self.root = root
        self.scopes: list[Scope] = []
        self.bindings: dict[int, Binding] = {}
        self.binding_by_node: dict[int, Binding] = {}
        self.declaration_bindings: dict[int, list[Binding]] = {}
        self.parameter_bindings: dict[int, list[Binding]] = {}
        self.function_bindings: dict[int, Binding | None] = {}
        self.loop_bindings: dict[int, list[Binding]] = {}
        self.scope_by_node: dict[int, Scope] = {}
        self.global_names: set[str] = set()
        self.order = 0
        self.binding_counter = 0
        self.scope_counter = 0

    def build(self) -> "Analyzer":
        root_scope = self.new_scope(None)
        self.scope_by_node[id(self.root)] = root_scope
        self.block(self.root.get("body", []), root_scope, self.root)
        return self

    def new_scope(self, parent: Scope | None) -> Scope:
        scope = Scope(self.scope_counter, parent)
        self.scope_counter += 1
        self.scopes.append(scope)
        return scope

    def declare(self, scope: Scope, name: str, kind: str, declaration: Node | None) -> Binding:
        binding = Binding(self.binding_counter, name, kind, declaration, scope, order=self.order)
        self.binding_counter += 1
        self.order += 1
        self.bindings[binding.ident] = binding
        scope.names[name] = binding
        if declaration is not None:
            self.binding_by_node[id(declaration)] = binding
        return binding

    def lookup(self, scope: Scope, name: str) -> Binding | None:
        current: Scope | None = scope
        while current is not None:
            found = current.names.get(name)
            if found is not None:
                return found
            current = current.parent
        return None

    def block(self, statements: list[Node], scope: Scope, owner: Node) -> None:
        for statement in statements:
            self.statement(statement, scope, owner)

    def statement(self, node: Node, scope: Scope, owner: Node) -> None:
        self.scope_by_node[id(node)] = scope
        kind = node.kind
        if kind == "local":
            for value in node.get("values", []):
                self.expr(value, scope, owner)
            bindings = [self.declare(scope, name, "local", node) for name in node.get("names", [])]
            self.declaration_bindings[id(node)] = bindings
            for binding, value in zip(bindings, node.get("values", [])):
                if binding.ident not in self.binding_by_node:
                    self.binding_by_node[id(binding.declaration)] = binding
            return
        if kind == "localfunc":
            binding = self.declare(scope, node.get("name", "?"), "localfunc", node)
            self.declaration_bindings[id(node)] = [binding]
            self.function_scope(node, scope, binding)
            return
        if kind == "funcdef":
            target = node.get("target")
            self.function_target(target, scope, owner)
            self.function_scope(node, scope, None)
            return
        if kind == "assign":
            for target in node.get("targets", []):
                self.assignment_target(target, scope, owner)
            for value in node.get("values", []):
                self.expr(value, scope, owner)
            return
        if kind in ("call", "methodcall"):
            self.expr(node, scope, owner)
            return
        if kind == "if":
            self.expr(node.get("cond"), scope, owner)
            self.block(node.get("then", []), self.new_scope(scope), node)
            for condition, body in node.get("elifs", []):
                child = self.new_scope(scope)
                self.expr(condition, scope, owner)
                self.block(body, child, node)
            self.block(node.get("else_", []), self.new_scope(scope), node)
            return
        if kind == "while":
            self.expr(node.get("cond"), scope, owner)
            self.block(node.get("body", []), self.new_scope(scope), node)
            return
        if kind == "repeat":
            child = self.new_scope(scope)
            self.block(node.get("body", []), child, node)
            self.expr(node.get("cond"), child, owner)
            return
        if kind == "fornum":
            for value in (node.get("start_expr"), node.get("limit"), node.get("step")):
                if value is not None:
                    self.expr(value, scope, owner)
            child = self.new_scope(scope)
            binding = self.declare(child, node.get("var", "_"), "loop", node)
            self.loop_bindings[id(node)] = [binding]
            self.block(node.get("body", []), child, node)
            return
        if kind == "forin":
            for value in node.get("iterators", []):
                self.expr(value, scope, owner)
            child = self.new_scope(scope)
            bindings = [self.declare(child, name, "loop", node) for name in node.get("vars", [])]
            self.loop_bindings[id(node)] = bindings
            self.block(node.get("body", []), child, node)
            return
        if kind == "do":
            self.block(node.get("body", []), self.new_scope(scope), node)
            return
        if kind == "return":
            for value in node.get("values", []):
                self.expr(value, scope, owner)
            return
        if kind in ("break", "continue", "goto", "label", "opaque"):
            return
        self.expr(node, scope, owner)

    def function_target(self, target: Node | None, scope: Scope, owner: Node) -> None:
        if target is None:
            return
        if target.kind == "name":
            binding = self.lookup(scope, target.get("name", ""))
            if binding is None:
                self.global_names.add(target.get("name", ""))
            else:
                binding.writes += 1
                self.binding_by_node[id(target)] = binding
            return
        if target.kind in ("indexname", "index", "methodname"):
            self.expr(target.get("obj"), scope, owner)
            if target.kind == "index":
                self.expr(target.get("key"), scope, owner)

    def assignment_target(self, target: Node, scope: Scope, owner: Node) -> None:
        if target.kind == "name":
            binding = self.lookup(scope, target.get("name", ""))
            if binding is None:
                self.global_names.add(target.get("name", ""))
            else:
                binding.writes += 1
                self.binding_by_node[id(target)] = binding
            return
        if target.kind in ("index", "indexname", "methodname"):
            self.expr(target.get("obj"), scope, owner)
            if target.kind == "index":
                self.expr(target.get("key"), scope, owner)

    def function_scope(self, node: Node, outer: Scope, function_binding: Binding | None) -> None:
        function_scope = self.new_scope(outer)
        self.scope_by_node[id(node)] = function_scope
        parameters = [self.declare(function_scope, name, "param", node) for name in node.get("params", [])]
        self.parameter_bindings[id(node)] = parameters
        self.function_bindings[id(node)] = function_binding
        self.block(node.get("body", []), function_scope, node)

    def expr(self, node: Node | None, scope: Scope, owner: Node) -> None:
        if node is None:
            return
        self.scope_by_node[id(node)] = scope
        if node.kind == "name":
            binding = self.lookup(scope, node.get("name", ""))
            if binding is None:
                self.global_names.add(node.get("name", ""))
            else:
                binding.reads += 1
                self.binding_by_node[id(node)] = binding
            return
        if node.kind == "function":
            self.function_scope(node, scope, None)
            return
        if node.kind == "call":
            self.expr(node.get("func"), scope, owner)
            for value in node.get("args", []):
                self.expr(value, scope, owner)
            return
        if node.kind == "methodcall":
            self.expr(node.get("obj"), scope, owner)
            for value in node.get("args", []):
                self.expr(value, scope, owner)
            return
        if node.kind == "index":
            self.expr(node.get("obj"), scope, owner)
            self.expr(node.get("key"), scope, owner)
            return
        if node.kind == "indexname":
            self.expr(node.get("obj"), scope, owner)
            return
        if node.kind == "methodname":
            self.expr(node.get("obj"), scope, owner)
            return
        if node.kind == "binop":
            self.expr(node.get("left"), scope, owner)
            self.expr(node.get("right"), scope, owner)
            return
        if node.kind == "unop":
            self.expr(node.get("expr"), scope, owner)
            return
        if node.kind == "paren" or node.kind == "typecast":
            self.expr(node.get("expr"), scope, owner)
            return
        if node.kind == "ifexpr":
            self.expr(node.get("cond"), scope, owner)
            self.expr(node.get("then"), scope, owner)
            for condition, value in node.get("elifs", []):
                self.expr(condition, scope, owner)
                self.expr(value, scope, owner)
            self.expr(node.get("else_"), scope, owner)
            return
        if node.kind == "table":
            for key, value in node.get("items", []):
                if key is not None and key.kind == "indexkey":
                    self.expr(key.get("key"), scope, owner)
                self.expr(value, scope, owner)
            return
        for value in node.fields.values():
            if isinstance(value, Node):
                self.expr(value, scope, owner)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        self.expr(item, scope, owner)
                    elif isinstance(item, (list, tuple)):
                        for nested in item:
                            if isinstance(nested, Node):
                                self.expr(nested, scope, owner)


def analyze(root: Node) -> Analyzer:
    return Analyzer(root).build()


def binding_for(analyzer: Analyzer, node: Node) -> Binding | None:
    return analyzer.binding_by_node.get(id(node))


def is_pure(node: Node | None) -> bool:
    if node is None:
        return True
    kind = node.kind
    if kind == "function" or kind == "opaque":
        return True
    if kind == "table":
        for key, value in node.get("items", []):
            if key is not None and key.kind == "indexkey" and not is_pure(key.get("key")):
                return False
            if not is_pure(value):
                return False
        return True
    if kind in ("number", "string", "interpolated", "bool", "nil", "vararg"):
        return True
    if kind == "name":
        return True
    if kind == "paren" or kind == "typecast":
        return is_pure(node.get("expr"))
    if kind == "unop":
        return is_pure(node.get("expr"))
    if kind == "binop":
        return is_pure(node.get("left")) and is_pure(node.get("right"))
    if kind == "ifexpr":
        return all(is_pure(value) for value in (node.get("cond"), node.get("then"), node.get("else_"))) and all(is_pure(c) and is_pure(v) for c, v in node.get("elifs", []))
    return False


def contains_name(node: Node | None, binding: Binding, analyzer: Analyzer) -> bool:
    if node is None:
        return False
    for child in iter_nodes(node):
        if isinstance(child, Node) and child.kind == "name" and binding_for(analyzer, child) is binding:
            return True
    return False

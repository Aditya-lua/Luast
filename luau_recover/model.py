from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Diagnostic:
    message: str
    start: int = 0
    end: int = 0
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.severity}: {self.message} @ {self.start}:{self.end}"


class Node:
    __slots__ = ("kind", "start", "end", "fields", "uid")

    def __init__(self, kind: str, start: int = 0, end: int = 0, **fields: Any):
        self.kind = kind
        self.start = start
        self.end = end
        self.fields = fields
        self.uid = -1

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self.fields[name]

    def __contains__(self, name: str) -> bool:
        return name in self.fields

    def clone(self, memo: dict[int, Node] | None = None) -> Node:
        if memo is None:
            memo = {}
        old = id(self)
        if old in memo:
            return memo[old]
        result = Node(self.kind, self.start, self.end)
        result.uid = self.uid
        memo[old] = result
        for key, value in self.fields.items():
            result.fields[key] = clone_value(value, memo)
        return result

    def __repr__(self) -> str:
        return f"Node({self.kind!r}, {self.start}:{self.end})"


def clone_value(value: Any, memo: dict[int, Node] | None = None) -> Any:
    if memo is None:
        memo = {}
    if isinstance(value, Node):
        return value.clone(memo)
    if isinstance(value, list):
        return [clone_value(item, memo) for item in value]
    if isinstance(value, tuple):
        return tuple(clone_value(item, memo) for item in value)
    if isinstance(value, dict):
        return {key: clone_value(item, memo) for key, item in value.items()}
    return value


def children(node: Node) -> Iterator[Node]:
    for value in node.fields.values():
        yield from iter_nodes(value)


def iter_nodes(value: Any) -> Iterator[Node]:
    if isinstance(value, Node):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_nodes(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_nodes(item)


def walk(root: Node) -> Iterator[Node]:
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        yield node
        for child in children(node):
            stack.append(child)


def count_nodes(root: Node) -> int:
    return sum(1 for _ in walk(root))


def make_number(value: int | float, raw: str | None = None) -> Node:
    return Node("number", value=value, raw=raw)


def make_string(value: bytes, raw: str | None = None) -> Node:
    return Node("string", value=value, raw=raw)


def make_bool(value: bool) -> Node:
    return Node("bool", value=value)


def make_nil() -> Node:
    return Node("nil")


def is_node(value: Any, kind: str) -> bool:
    return isinstance(value, Node) and value.kind == kind


def node_name(node: Node) -> str | None:
    if isinstance(node, Node) and node.kind == "name":
        return node.fields.get("name")
    return None


def number_value(node: Node) -> int | float | None:
    if isinstance(node, Node) and node.kind == "number" and isinstance(node.get("value"), (int, float)):
        return node.get("value")
    return None


def truthy(value: Any) -> bool:
    return value is not None and value is not False

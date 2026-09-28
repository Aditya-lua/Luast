from __future__ import annotations

import math

from .model import Node
from .parser import PRECEDENCE


SPACED_OPERATORS = {"==", "~=", "<=", ">=", "<", ">", "..", "//", "<<", ">>", "and", "or"}


class Emitter:
    def __init__(self, source: str = ""):
        self.source = source

    def root(self, root: Node) -> str:
        lines: list[str] = []
        self.block(root.get("body", []), 0, lines)
        return "\n".join(lines) + ("\n" if lines else "")

    def block(self, statements: list[Node], depth: int, output: list[str]) -> None:
        for statement in statements:
            rendered = self.statement(statement, depth)
            if rendered:
                output.extend(rendered.splitlines() or [""])

    def indent(self, depth: int) -> str:
        return "    " * depth

    def statement(self, node: Node, depth: int) -> str:
        kind = node.kind
        if kind in {"string", "number", "bool", "nil", "name", "index", "indexname", "binop", "unop", "table", "ifexpr"}:
            return ""
        pad = self.indent(depth)
        if kind == "local":
            names = list(node.get("names", []))
            attrs = list(node.get("attrs", []))
            rendered = []
            for index, name in enumerate(names):
                attr = attrs[index] if index < len(attrs) else ""
                rendered.append(name + (f" <{attr}>" if attr else ""))
            text = "local " + ", ".join(rendered)
            values = list(node.get("values", []))
            if values:
                if len(values) < len(names):
                    values = values + [Node("nil")] * (len(names) - len(values))
                text += " = " + ", ".join(self.expr(value) for value in values)
            return pad + text + ";"
        if kind == "localfunc":
            return "\n".join(self.function_lines(node, depth, "local function " + node.get("name", "?")))
        if kind == "funcdef":
            return "\n".join(self.function_lines(node, depth, "function " + self.target(node.get("target"))))
        if kind == "assign":
            targets = ", ".join(self.expr(target) for target in node.get("targets", []))
            values = ", ".join(self.expr(value) for value in node.get("values", []))
            operator = node.get("op") or "="
            suffix = (" = " + values) if operator == "=" else (" " + operator + " " + values)
            return pad + targets + suffix + ";"
        if kind in ("call", "methodcall"):
            return pad + self.expr(node) + ";"
        if kind == "if":
            lines = [pad + "if " + self.expr(node.get("cond")) + " then"]
            self.block(node.get("then", []), depth + 1, lines)
            for condition, body in node.get("elifs", []):
                lines.append(pad + "elseif " + self.expr(condition) + " then")
                self.block(body, depth + 1, lines)
            if node.get("else_"):
                lines.append(pad + "else")
                self.block(node.get("else_", []), depth + 1, lines)
            lines.append(pad + "end")
            return "\n".join(lines)
        if kind == "while":
            lines = [pad + "while " + self.expr(node.get("cond")) + " do"]
            self.block(node.get("body", []), depth + 1, lines)
            lines.append(pad + "end")
            return "\n".join(lines)
        if kind == "repeat":
            lines = [pad + "repeat"]
            self.block(node.get("body", []), depth + 1, lines)
            lines.append(pad + "until " + self.expr(node.get("cond")))
            return "\n".join(lines)
        if kind == "fornum":
            head = pad + "for " + node.get("var", "_") + " = " + self.expr(node.get("start_expr")) + ", " + self.expr(node.get("limit"))
            if node.get("step") is not None:
                head += ", " + self.expr(node.get("step"))
            lines = [head + " do"]
            self.block(node.get("body", []), depth + 1, lines)
            lines.append(pad + "end")
            return "\n".join(lines)
        if kind == "forin":
            head = pad + "for " + ", ".join(node.get("vars", [])) + " in " + ", ".join(self.expr(item) for item in node.get("iterators", [])) + " do"
            lines = [head]
            self.block(node.get("body", []), depth + 1, lines)
            lines.append(pad + "end")
            return "\n".join(lines)
        if kind == "do":
            lines = [pad + "do"]
            self.block(node.get("body", []), depth + 1, lines)
            lines.append(pad + "end")
            return "\n".join(lines)
        if kind == "return":
            values = node.get("values", [])
            return pad + ("return " + ", ".join(self.expr(value) for value in values) if values else "return") + ";"
        if kind == "break":
            return pad + "break;"
        if kind == "continue":
            return pad + "continue;"
        if kind == "goto":
            return pad + "goto " + node.get("name", "?") + ";"
        if kind == "label":
            return pad + "::" + node.get("name", "?") + "::"
        if kind == "opaque":
            return pad + node.get("raw", "")
        return pad + self.expr(node)

    def function_lines(self, node: Node, depth: int, header: str) -> list[str]:
        params = list(node.get("params", []))
        if node.get("vararg"):
            params.append("...")
        suffix = ""
        if node.get("return_type"):
            suffix = ": " + node.get("return_type")
        lines = [self.indent(depth) + header + "(" + ", ".join(params) + ")" + suffix]
        self.block(node.get("body", []), depth + 1, lines)
        lines.append(self.indent(depth) + "end")
        return lines

    def target(self, node: Node) -> str:
        if node.kind == "name":
            return node.get("name", "?")
        if node.kind == "indexname":
            return self.prefix(node.get("obj")) + "." + node.get("name", "?")
        if node.kind == "methodname":
            return self.prefix(node.get("obj")) + ":" + node.get("name", "?")
        return self.expr(node)

    def prefix(self, node: Node) -> str:
        text = self.expr(node, 100)
        if node.kind in {"name", "index", "indexname", "call", "methodcall", "paren"}:
            return text
        return "(" + text + ")"

    def expr(self, node: Node, parent: int = 0, depth: int = 0) -> str:
        kind = node.kind
        if kind == "number":
            return node.get("raw") if node.get("raw") is not None else format_number(node.get("value"))
        if kind == "string":
            raw = node.get("raw")
            return raw if raw is not None else quote_bytes(node.get("value", b""))
        if kind == "interpolated":
            return node.get("raw", '""')
        if kind == "bool":
            return "true" if node.get("value") else "false"
        if kind == "nil":
            return "nil"
        if kind == "name":
            return node.get("name", "?")
        if kind == "vararg":
            return "..."
        if kind == "paren":
            return "(" + self.expr(node.get("expr")) + ")"
        if kind == "index":
            return self.prefix(node.get("obj")) + "[" + self.expr(node.get("key")) + "]"
        if kind == "indexname":
            return self.prefix(node.get("obj")) + "." + node.get("name", "?")
        if kind == "methodname":
            return self.prefix(node.get("obj")) + ":" + node.get("name", "?")
        if kind == "call":
            return self.prefix(node.get("func")) + "(" + ", ".join(self.expr(arg) for arg in node.get("args", [])) + ")"
        if kind == "methodcall":
            return self.prefix(node.get("obj")) + ":" + node.get("method", "?") + "(" + ", ".join(self.expr(arg) for arg in node.get("args", [])) + ")"
        if kind == "typecast":
            return self.expr(node.get("expr"), 100) + " :: " + node.get("type_text", "any")
        if kind == "unop":
            operator = node.get("op", "")
            text = operator + (" " if operator == "not" else "") + self.expr(node.get("expr"), 11)
            return text if parent <= 11 else "(" + text + ")"
        if kind == "binop":
            operator = node.get("op", "")
            precedence, associativity = PRECEDENCE[operator]
            left_parent = precedence if associativity == "right" else precedence
            right_parent = precedence if associativity == "right" else precedence + 1
            spaced = operator in SPACED_OPERATORS
            text = self.expr(node.get("left"), left_parent) + (" " if spaced else "") + operator + (" " if spaced else "") + self.expr(node.get("right"), right_parent)
            return text if precedence >= parent else "(" + text + ")"
        if kind == "function":
            return "\n".join(self.function_lines(node, depth, "function"))
        if kind == "table":
            return self.table(node, depth)
        if kind == "ifexpr":
            text = "if " + self.expr(node.get("cond")) + " then " + self.expr(node.get("then"))
            for condition, value in node.get("elifs", []):
                text += " elseif " + self.expr(condition) + " then " + self.expr(value)
            text += " else " + self.expr(node.get("else_"))
            return text
        if kind == "opaque":
            return node.get("raw", "nil")
        return "nil"

    def table(self, node: Node, depth: int) -> str:
        items = node.get("items", [])
        rendered: list[str] = []
        complex_item = False
        for key, value in items:
            if key is None:
                text = self.expr(value, depth=depth + 1)
            elif key.kind == "namekey":
                text = key.get("name", "?") + " = " + self.expr(value, depth=depth + 1)
            else:
                text = "[" + self.expr(key.get("key"), depth=depth + 1) + "] = " + self.expr(value, depth=depth + 1)
            if "\n" in text or len(text) > 180:
                complex_item = True
            rendered.append(text)
        if not rendered:
            return "{}"
        inline = "{ " + ", ".join(rendered) + " }"
        if not complex_item and len(inline) <= 240:
            return inline
        lines = ["{"]
        pad = self.indent(depth + 1)
        for text in rendered:
            lines.append(pad + text + ",")
        lines.append(self.indent(depth) + "}")
        return "\n".join(lines)


def quote_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        value = str(value).encode("utf-8", "surrogateescape")
    result = ['"']
    for byte in value:
        if byte == 34:
            result.append('\\"')
        elif byte == 92:
            result.append("\\\\")
        elif byte == 10:
            result.append("\\n")
        elif byte == 13:
            result.append("\\r")
        elif byte == 9:
            result.append("\\t")
        elif 32 <= byte < 127:
            result.append(chr(byte))
        else:
            result.append(f"\\{byte:03d}")
    result.append('"')
    return "".join(result)


def format_number(value: int | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isnan(value):
        return "0/0"
    if isinstance(value, float) and math.isinf(value):
        return "1/0" if value > 0 else "-1/0"
    if not math.isfinite(value):
        return repr(value)
    text = repr(value)
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def emit(root: Node, source: str = "") -> str:
    return Emitter(source).root(root)


def emit_expr_cached(node: Node) -> str:
    """Render a single expression node (no cache: AST nodes are mutated
    in-place by passes, so caching by id would risk stale renderings)."""
    return Emitter().expr(node)

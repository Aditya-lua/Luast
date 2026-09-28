from __future__ import annotations

from typing import Any

from .lexer import Token, lex
from .model import Diagnostic, Node


PRECEDENCE = {
    "or": (1, "left"), "and": (2, "left"),
    "<": (3, "left"), ">": (3, "left"), "<=": (3, "left"), ">=": (3, "left"),
    "==": (3, "left"), "~=": (3, "left"),
    "|": (4, "left"), "~": (5, "left"), "&": (6, "left"),
    "<<": (7, "left"), ">>": (7, "left"),
    "..": (8, "right"), "+": (9, "left"), "-": (9, "left"),
    "*": (10, "left"), "/": (10, "left"), "//": (10, "left"), "%": (10, "left"),
    "^": (12, "right"),
}
UNARY = {"not", "#", "-", "~"}
COMPOUND = {"+=", "-=", "*=", "/=", "//=", "%=", "^=", "..=", "|=", "&=", "<<=", ">>="}
BLOCK_STOPS = {"end", "else", "elseif", "until"}
TYPE_START = {"type", "typeof", "export"}


class Parser:
    def __init__(self, source: str, tokens: list[Token], errors: list[Diagnostic]):
        self.source = source
        self.tokens = tokens
        self.errors = errors
        self.index = 0

    def peek(self, offset: int = 0) -> Token:
        position = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[position]

    def previous(self) -> Token:
        return self.tokens[max(0, self.index - 1)]

    def take(self) -> Token:
        token = self.peek()
        if token.kind != "eof":
            self.index += 1
        return token

    def at(self, kind: str, value: object = None) -> bool:
        token = self.peek()
        return token.kind == kind and (value is None or token.value == value)

    def at_any(self, kind: str, values: set[object]) -> bool:
        token = self.peek()
        return token.kind == kind and token.value in values

    def accept(self, kind: str, value: object = None) -> Token | None:
        if self.at(kind, value):
            return self.take()
        return None

    def expect(self, kind: str, value: object = None) -> Token:
        if self.at(kind, value):
            return self.take()
        token = self.peek()
        self.error(f"expected {value or kind}", token)
        return Token(kind, value, token.start, token.start, "")

    def error(self, message: str, token: Token | None = None) -> None:
        token = token or self.peek()
        self.errors.append(Diagnostic(message, token.start, token.end))

    def make(self, kind: str, start: int, end: int | None = None, **fields: Any) -> Node:
        if end is None:
            end = self.previous().end
        return Node(kind, start, end, **fields)

    def parse(self) -> Node:
        body = self.parse_block(())
        if not self.at("eof"):
            self.error("unexpected token after chunk", self.peek())
        return self.make("chunk", 0, self.peek().end, body=body)

    def parse_block(self, stops: set[str]) -> list[Node]:
        result: list[Node] = []
        while not self.at("eof") and not (self.peek().kind == "keyword" and self.peek().value in stops):
            if self.accept("symbol", ";"):
                continue
            before = self.index
            statement = self.parse_statement()
            if statement is not None:
                result.append(statement)
            if self.index == before:
                self.error("unable to parse statement", self.peek())
                self.take()
        return result

    def parse_statement(self) -> Node | None:
        token = self.peek()
        if token.kind == "keyword":
            word = token.value
            if word == "local":
                return self.parse_local()
            if word == "function":
                return self.parse_function_statement()
            if word == "if":
                return self.parse_if_statement()
            if word == "while":
                return self.parse_while()
            if word == "repeat":
                return self.parse_repeat()
            if word == "for":
                return self.parse_for()
            if word == "return":
                return self.parse_return()
            if word == "break":
                self.take()
                return self.make("break", token.start)
            if word == "continue":
                self.take()
                return self.make("continue", token.start)
            if word == "do":
                self.take()
                body = self.parse_block({"end"})
                self.expect("keyword", "end")
                return self.make("do", token.start, body=body)
            if word == "goto":
                self.take()
                name = self.expect("name").value
                return self.make("goto", token.start, name=name)
        if token.kind == "name" and token.value == "export" and self.peek(1).kind == "name" and self.peek(1).value == "type":
            return self.parse_type_declaration()
        if token.kind == "name" and token.value == "type" and self.peek(1).kind == "name":
            return self.parse_type_declaration()
        if self.at("symbol", "::"):
            self.take()
            name = self.expect("name").value
            self.expect("symbol", "::")
            return self.make("label", token.start, name=name)
        return self.parse_expression_statement()

    def parse_type_declaration(self) -> Node:
        start = self.peek().start
        if self.at("name", "export"):
            self.take()
        self.take()
        self.expect("name")
        if self.at("symbol", "<"):
            self.skip_balanced()
        if self.accept("symbol", "="):
            depth = 0
            consumed_value = False
            previous_end = self.previous().end
            while not self.at("eof"):
                token = self.peek()
                if token.kind == "symbol" and token.value == ";" and depth == 0:
                    self.take()
                    break
                previous = self.previous().value
                continuation = {"->", "|", "&", "?", "<", ">", "(", "[", ",", "."}
                if depth == 0 and consumed_value and token.start > previous_end and token.kind in {"name", "keyword"} and previous not in continuation:
                    break
                if token.kind == "symbol" and token.value in "([{<":
                    depth += 1
                elif token.kind == "symbol" and token.value in ")]}>":
                    depth = max(0, depth - 1)
                self.take()
                consumed_value = True
                previous_end = self.previous().end
        return self.make("opaque", start, raw=self.source[start:self.previous().end])

    def parse_local(self) -> Node:
        start = self.take().start
        if self.at("keyword", "function"):
            self.take()
            name = self.expect("name").value
            params, vararg = self.parse_parameters()
            return_type = self.parse_return_type()
            self.bind_function_scope(name, params, vararg)
            body = self.parse_block({"end"})
            self.expect("keyword", "end")
            return self.make("localfunc", start, name=name, params=params, vararg=vararg, return_type=return_type, body=body)
        names: list[str] = []
        attrs: list[str] = []
        while True:
            if self.peek().kind != "name":
                self.error("expected local name", self.peek())
                break
            names.append(self.take().value)
            if self.at("symbol", "<"):
                self.skip_balanced()
            self.skip_type_annotation()
            attrs.append("")
            if not self.accept("symbol", ","):
                break
        values: list[Node] = []
        if self.accept("symbol", "="):
            values = self.parse_expression_list()
        self.declare_locals(names)
        return self.make("local", start, names=names, attrs=attrs, values=values)

    def bind_function_scope(self, name: str, params: list[str], vararg: bool) -> None:
        return None

    def parse_function_statement(self) -> Node:
        start = self.take().start
        target = self.parse_function_name()
        params, vararg = self.parse_parameters()
        return_type = self.parse_return_type()
        body = self.parse_block({"end"})
        self.expect("keyword", "end")
        return self.make("funcdef", start, target=target, params=params, vararg=vararg, return_type=return_type, body=body)

    def parse_function_name(self) -> Node:
        token = self.peek()
        if token.kind != "name":
            self.error("expected function name", token)
            return self.make("name", token.start, name="?")
        node = self.make("name", token.start, name=self.take().value)
        while True:
            if self.accept("symbol", "."):
                field = self.expect("name")
                node = self.make("indexname", node.start, obj=node, name=field.value)
                continue
            if self.accept("symbol", ":"):
                method = self.expect("name")
                node = self.make("methodname", node.start, obj=node, name=method.value)
            break
        return node

    def parse_parameters(self) -> tuple[list[str], bool]:
        self.expect("symbol", "(")
        params: list[str] = []
        vararg = False
        first = True
        while not self.at("symbol", ")") and not self.at("eof"):
            if not first and not self.accept("symbol", ","):
                self.error("expected comma in parameter list", self.peek())
                break
            first = False
            if self.accept("symbol", "..."):
                vararg = True
                break
            if self.peek().kind != "name":
                self.error("expected parameter name", self.peek())
                break
            params.append(self.take().value)
            if self.at("symbol", "..."):
                self.take()
                vararg = True
                break
            self.skip_type_annotation()
        self.expect("symbol", ")")
        return params, vararg

    def parse_return_type(self) -> str | None:
        if not self.at("symbol", ":"):
            return None
        colon_start = self.take().start
        end = self.skip_type(colon_start)
        return self.source[colon_start + 1:end].lstrip()

    def parse_if_statement(self) -> Node:
        start = self.take().start
        condition = self.parse_expression()
        self.expect("keyword", "then")
        then_body = self.parse_block({"elseif", "else", "end"})
        elifs: list[tuple[Node, list[Node]]] = []
        while self.at("keyword", "elseif"):
            self.take()
            condition_part = self.parse_expression()
            self.expect("keyword", "then")
            elifs.append((condition_part, self.parse_block({"elseif", "else", "end"})))
        else_body: list[Node] = []
        if self.accept("keyword", "else"):
            else_body = self.parse_block({"end"})
        self.expect("keyword", "end")
        return self.make("if", start, cond=condition, then=then_body, elifs=elifs, else_=else_body)

    def parse_while(self) -> Node:
        start = self.take().start
        condition = self.parse_expression()
        self.expect("keyword", "do")
        body = self.parse_block({"end"})
        self.expect("keyword", "end")
        return self.make("while", start, cond=condition, body=body)

    def parse_repeat(self) -> Node:
        start = self.take().start
        body = self.parse_block({"until"})
        self.expect("keyword", "until")
        condition = self.parse_expression()
        return self.make("repeat", start, body=body, cond=condition)

    def parse_for(self) -> Node:
        start = self.take().start
        first = self.expect("name").value
        if self.accept("symbol", "="):
            begin = self.parse_expression()
            self.expect("symbol", ",")
            limit = self.parse_expression()
            step = self.parse_expression() if self.accept("symbol", ",") else None
            self.expect("keyword", "do")
            body = self.parse_block({"end"})
            self.expect("keyword", "end")
            return self.make("fornum", start, var=first, start_expr=begin, limit=limit, step=step, body=body)
        names = [first]
        while self.accept("symbol", ","):
            if self.peek().kind != "name":
                self.error("expected loop variable", self.peek())
                break
            names.append(self.take().value)
        self.expect("keyword", "in")
        iterators = self.parse_expression_list()
        self.expect("keyword", "do")
        body = self.parse_block({"end"})
        self.expect("keyword", "end")
        return self.make("forin", start, vars=names, iterators=iterators, body=body)

    def parse_return(self) -> Node:
        start = self.take().start
        values: list[Node] = []
        if not self.at_statement_end():
            values = self.parse_expression_list()
        return self.make("return", start, values=values)

    def parse_expression_statement(self) -> Node | None:
        start = self.peek().start
        first = self.parse_expression()
        if first is None:
            return None
        if self.at("symbol", "=") or self.at_any("symbol", COMPOUND) or self.at("symbol", ","):
            targets = [first]
            while self.accept("symbol", ","):
                targets.append(self.parse_expression())
            operator = self.peek().value
            if self.peek().kind == "symbol" and (operator == "=" or operator in COMPOUND):
                self.take()
            else:
                self.error("expected assignment operator", self.peek())
            values = [] if self.at_statement_end() else self.parse_expression_list()
            if not values or any(target.kind not in {"name", "index", "indexname"} for target in targets):
                self.error("invalid assignment", self.peek())
            return self.make("assign", start, targets=targets, values=values, op=operator)
        if first.kind not in ("call", "methodcall"):
            self.error("expected assignment or function call", self.peek())
        return first

    def parse_expression_list(self) -> list[Node]:
        result = [self.parse_expression()]
        while self.accept("symbol", ","):
            result.append(self.parse_expression())
        return result

    def parse_expression(self, minimum: int = 0) -> Node:
        token = self.peek()
        if (token.kind == "keyword" and token.value in UNARY) or (token.kind == "symbol" and token.value in UNARY):
            self.take()
            operand = self.parse_expression(11)
            left: Node = self.make("unop", token.start, op=token.value, expr=operand)
        else:
            left = self.parse_primary()
        while True:
            token = self.peek()
            operator: str | None = None
            if token.kind == "keyword" and token.value in ("and", "or"):
                operator = token.value
            elif token.kind == "symbol" and token.value in PRECEDENCE:
                operator = token.value
            if operator is None:
                break
            precedence, associativity = PRECEDENCE[operator]
            if precedence < minimum:
                break
            self.take()
            next_minimum = precedence + 1 if associativity == "left" else precedence
            right = self.parse_expression(next_minimum)
            left = self.make("binop", left.start, op=operator, left=left, right=right)
        return left

    def parse_primary(self) -> Node:
        token = self.peek()
        if token.kind == "number":
            self.take()
            return self.make("number", token.start, value=token.value, raw=token.raw)
        if token.kind == "string":
            self.take()
            return self.parse_suffix(self.make("string", token.start, value=token.value, raw=token.raw))
        if token.kind == "interpolated":
            self.take()
            return self.parse_suffix(self.make("interpolated", token.start, raw=token.raw))
        if token.kind == "name":
            self.take()
            node = self.make("name", token.start, name=token.value)
            return self.parse_suffix(node)
        if token.kind == "keyword":
            if token.value == "nil":
                self.take()
                return self.make("nil", token.start)
            if token.value == "true":
                self.take()
                return self.make("bool", token.start, value=True)
            if token.value == "false":
                self.take()
                return self.make("bool", token.start, value=False)
            if token.value == "function":
                return self.parse_function_expression()
            if token.value == "if":
                return self.parse_if_expression()
        if token.kind == "symbol" and token.value == "(":
            self.take()
            expression = self.parse_expression()
            self.expect("symbol", ")")
            return self.parse_suffix(self.make("paren", token.start, expr=expression))
        if token.kind == "symbol" and token.value == "{":
            return self.parse_table()
        if token.kind == "symbol" and token.value == "...":
            self.take()
            return self.make("vararg", token.start)
        self.error("expected expression", token)
        if token.kind != "eof":
            self.take()
        return self.make("nil", token.start)

    def parse_function_expression(self) -> Node:
        start = self.take().start
        params, vararg = self.parse_parameters()
        return_type = self.parse_return_type()
        body = self.parse_block({"end"})
        self.expect("keyword", "end")
        return self.make("function", start, params=params, vararg=vararg, return_type=return_type, body=body)

    def parse_if_expression(self) -> Node:
        start = self.take().start
        condition = self.parse_expression()
        self.expect("keyword", "then")
        then_value = self.parse_expression()
        elifs: list[tuple[Node, Node]] = []
        while self.at("keyword", "elseif"):
            self.take()
            branch_condition = self.parse_expression()
            self.expect("keyword", "then")
            elifs.append((branch_condition, self.parse_expression()))
        else_value = self.make("nil", self.peek().start)
        if self.accept("keyword", "else"):
            else_value = self.parse_expression()
        else:
            self.error("if expression requires else branch", self.peek())
        return self.make("ifexpr", start, cond=condition, then=then_value, elifs=elifs, else_=else_value)

    def parse_suffix(self, node: Node) -> Node:
        while True:
            if self.accept("symbol", "."):
                field = self.expect("name")
                node = self.make("indexname", node.start, obj=node, name=field.value)
                continue
            if self.accept("symbol", "["):
                key = self.parse_expression()
                self.expect("symbol", "]")
                node = self.make("index", node.start, obj=node, key=key)
                continue
            if self.accept("symbol", ":"):
                method = self.expect("name")
                arguments = self.parse_call_arguments()
                node = self.make("methodcall", node.start, obj=node, method=method.value, args=arguments)
                continue
            if self.at("symbol", "(") or self.at("symbol", "{") or self.peek().kind in ("string", "interpolated"):
                arguments = self.parse_call_arguments()
                node = self.make("call", node.start, func=node, args=arguments)
                continue
            if self.accept("symbol", "::"):
                type_start = self.previous().start
                type_end = self.skip_type(type_start)
                node = self.make("typecast", node.start, expr=node, type_text=self.source[type_start + 2:type_end].lstrip())
            else:
                break
        return node

    def parse_call_arguments(self) -> list[Node]:
        if self.accept("symbol", "("):
            args: list[Node] = []
            if not self.at("symbol", ")"):
                args = self.parse_expression_list()
            self.expect("symbol", ")")
            return args
        if self.at("symbol", "{"):
            return [self.parse_table()]
        if self.peek().kind in ("string", "interpolated"):
            token = self.take()
            if token.kind == "string":
                return [self.make("string", token.start, value=token.value, raw=token.raw)]
            return [self.make("interpolated", token.start, raw=token.raw)]
        self.error("expected call arguments", self.peek())
        return []

    def parse_table(self) -> Node:
        start = self.take().start
        items: list[tuple[Node | None, Node]] = []
        while not self.at("symbol", "}") and not self.at("eof"):
            key: Node | None = None
            if self.accept("symbol", "["):
                key_expression = self.parse_expression()
                self.expect("symbol", "]")
                self.expect("symbol", "=")
                key = self.make("indexkey", key_expression.start, key=key_expression)
            elif self.peek().kind == "name" and self.peek(1).kind == "symbol" and self.peek(1).value == "=":
                name_token = self.take()
                self.take()
                key = self.make("namekey", name_token.start, name=name_token.value)
            value = self.parse_expression()
            items.append((key, value))
            if not self.accept("symbol", ",") and not self.accept("symbol", ";"):
                break
        self.expect("symbol", "}")
        return self.make("table", start, items=items)

    def skip_type_annotation(self) -> None:
        if self.at("symbol", ":"):
            self.take()
            self.skip_type(self.previous().start)

    def skip_type(self, start: int) -> int:
        depth = 0
        while not self.at("eof"):
            token = self.peek()
            if depth == 0:
                if token.kind == "symbol" and token.value in {",", "=", ")", "]", "}", ";"}:
                    break
                if token.kind == "keyword" and token.value in {
                    "then", "do", "in", "end", "else", "elseif", "until", "if", "while",
                    "for", "repeat", "return", "break", "continue", "goto", "function", "local",
                }:
                    break
            if token.kind == "symbol" and token.value in "([{<":
                depth += 1
            elif token.kind == "symbol" and token.value in ")]}>":
                if depth == 0:
                    break
                depth -= 1
            self.take()
        return self.previous().end if self.index > 0 else start

    def skip_balanced(self) -> None:
        token = self.peek()
        opening = token.value
        closing = {"<": ">", "(": ")", "[": "]", "{": "}"}.get(opening)
        if closing is None:
            return
        self.take()
        depth = 1
        while depth and not self.at("eof"):
            if self.at("symbol", opening):
                depth += 1
            elif self.at("symbol", closing):
                depth -= 1
            self.take()

    def at_statement_end(self) -> bool:
        token = self.peek()
        return token.kind == "eof" or (token.kind == "keyword" and token.value in BLOCK_STOPS) or self.at("symbol", ";")

    def declare_locals(self, names: list[str]) -> None:
        return None

    def parse_statement_or_expression(self) -> Node | None:
        return self.parse_statement()


def parse(source: str) -> tuple[Node, list[Diagnostic], list[tuple[int, int]]]:
    tokens, errors, comments = lex(source)
    parser = Parser(source, tokens, errors)
    root = parser.parse()
    return root, errors, comments

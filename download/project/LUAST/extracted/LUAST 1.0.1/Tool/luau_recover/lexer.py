from __future__ import annotations

from dataclasses import dataclass

from .model import Diagnostic


@dataclass(slots=True)
class Token:
    kind: str
    value: object
    start: int
    end: int
    raw: str


KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while", "continue",
}

THREE = ("...", "..=", "//=", "<<=", ">>=")
TWO = (
    "==", "~=", "<=", ">=", "..", "//", "+=", "-=", "*=", "/=", "%=", "^=",
    "..=", "->", "::", "<<", ">>", "|=", "&=", "^=",
)
ONE = set("+-*/%^#&|~<>=(){}[];:,.?")


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.length = len(source)
        self.index = 0
        self.tokens: list[Token] = []
        self.errors: list[Diagnostic] = []
        self.comments: list[tuple[int, int]] = []

    def error(self, message: str, start: int, end: int | None = None) -> None:
        self.errors.append(Diagnostic(message, start, self.index if end is None else end))

    def advance(self, end: int) -> None:
        self.index = end

    def add(self, kind: str, value: object, start: int, end: int) -> None:
        self.tokens.append(Token(kind, value, start, end, self.source[start:end]))

    def lex(self) -> tuple[list[Token], list[Diagnostic]]:
        if self.source.startswith("#!"):
            end = self.source.find("\n")
            if end < 0:
                end = self.length
            self.index = end
        while self.index < self.length:
            char = self.source[self.index]
            if char.isspace():
                self.advance(self.index + 1)
                continue
            if char == "-" and self.source.startswith("--", self.index):
                self.read_comment()
                continue
            if char == "[":
                long = self.long_delimiter(self.index)
                if long is not None:
                    level, content_start, content_end, token_end = long
                    raw = self.source[self.index:token_end]
                    content = self.source[content_start:content_end]
                    if content.startswith("\n"):
                        content = content[1:]
                    self.add("string", content.encode("utf-8", "surrogateescape"), self.index, token_end)
                    self.advance(token_end)
                    continue
            if char in "'\"":
                self.read_quoted(char)
                continue
            if char == "`":
                self.read_interpolated()
                continue
            if char.isalpha() or char == "_":
                self.read_name()
                continue
            if char.isdigit() or (char == "." and self.index + 1 < self.length and self.source[self.index + 1].isdigit()):
                self.read_number()
                continue
            if self.read_operator():
                continue
            self.error(f"unexpected character {char!r}", self.index, self.index + 1)
            self.advance(self.index + 1)
        self.add("eof", None, self.length, self.length)
        return self.tokens, self.errors

    def read_comment(self) -> None:
        start = self.index
        if self.source.startswith("--[", start):
            long = self.long_delimiter(start + 2)
            if long is not None:
                self.comments.append((start, long[3]))
                self.advance(long[3])
                return
        end = self.source.find("\n", start)
        if end < 0:
            end = self.length
        self.comments.append((start, end))
        self.advance(end)

    def long_delimiter(self, start: int) -> tuple[int, int, int, int] | None:
        if start >= self.length or self.source[start] != "[":
            return None
        index = start + 1
        while index < self.length and self.source[index] == "=":
            index += 1
        if index >= self.length or self.source[index] != "[":
            return None
        level = index - start - 1
        content_start = index + 1
        close = "]" + "=" * level + "]"
        close_start = self.source.find(close, content_start)
        if close_start < 0:
            self.error("unterminated long string", start, self.length)
            return level, content_start, self.length, self.length
        return level, content_start, close_start, close_start + len(close)

    def read_quoted(self, quote: str) -> None:
        start = self.index
        index = start + 1
        body = bytearray()
        closed = False
        while index < self.length:
            char = self.source[index]
            if char == quote:
                closed = True
                index += 1
                break
            if char in "\r\n":
                break
            if char != "\\":
                body.extend(self.bytes_for_char(char))
                index += 1
                continue
            index += 1
            if index >= self.length:
                break
            escaped = self.source[index]
            simple = {
                "a": b"\a", "b": b"\b", "f": b"\f", "n": b"\n", "r": b"\r",
                "t": b"\t", "v": b"\v", "\\": b"\\", '"': b'"', "'": b"'",
            }
            if escaped in simple:
                body.extend(simple[escaped])
                index += 1
                continue
            if escaped == "\n" or escaped == "\r":
                if escaped == "\r" and index + 1 < self.length and self.source[index + 1] == "\n":
                    index += 1
                index += 1
                continue
            if escaped == "z":
                index += 1
                while index < self.length and self.source[index].isspace():
                    index += 1
                continue
            if escaped == "x":
                digits = self.source[index + 1:index + 3]
                if len(digits) == 2 and all(c in "0123456789abcdefABCDEF" for c in digits):
                    body.append(int(digits, 16))
                    index += 3
                else:
                    self.error("invalid hexadecimal escape", index - 1, index + 1)
                    body.extend(b"x")
                    index += 1
                continue
            if escaped.isdigit():
                end = index
                while end < self.length and end < index + 3 and self.source[end].isdigit():
                    end += 1
                body.append(int(self.source[index:end], 10) % 256)
                index = end
                continue
            if escaped == "u" and index + 1 < self.length and self.source[index + 1] == "{":
                end = self.source.find("}", index + 2)
                if end < 0:
                    self.error("unterminated unicode escape", index - 1, index + 1)
                    body.extend(b"u")
                    index += 1
                    continue
                try:
                    body.extend(chr(int(self.source[index + 2:end], 16)).encode("utf-8"))
                except (ValueError, OverflowError):
                    self.error("invalid unicode escape", index - 1, end + 1)
                index = end + 1
                continue
            body.extend(self.bytes_for_char(escaped))
            index += 1
        if not closed:
            self.error("unterminated string", start, index)
        self.add("string", bytes(body), start, index)
        self.advance(index)

    def read_interpolated(self) -> None:
        start = self.index
        index = start + 1
        escaped = False
        while index < self.length:
            char = self.source[index]
            if char == "`" and not escaped:
                index += 1
                self.add("interpolated", self.source[start:index], start, index)
                self.advance(index)
                return
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
            index += 1
        self.error("unterminated interpolated string", start, self.length)
        self.add("interpolated", self.source[start:], start, self.length)
        self.advance(self.length)

    def read_name(self) -> None:
        start = self.index
        index = start + 1
        while index < self.length and (self.source[index].isalnum() or self.source[index] == "_"):
            index += 1
        value = self.source[start:index]
        self.add("keyword" if value in KEYWORDS else "name", value, start, index)
        self.advance(index)

    def read_number(self) -> None:
        start = self.index
        index = start
        if self.source.startswith(("0x", "0X"), index):
            index += 2
            while index < self.length and (self.source[index] in "0123456789abcdefABCDEF_"):
                index += 1
            raw = self.source[start:index]
            try:
                value: int | float = int(raw.replace("_", ""), 16)
            except ValueError:
                value = 0
                self.error(f"invalid hexadecimal number {raw}", start, index)
        elif self.source.startswith(("0b", "0B"), index):
            index += 2
            while index < self.length and (self.source[index] in "01_"):
                index += 1
            raw = self.source[start:index]
            try:
                value = int(raw.replace("_", ""), 2)
            except ValueError:
                value = 0
                self.error(f"invalid binary number {raw}", start, index)
        else:
            while index < self.length and (self.source[index].isdigit() or self.source[index] == "_"):
                index += 1
            if index < self.length and self.source[index] == "." and not self.source.startswith("..", index):
                index += 1
                while index < self.length and (self.source[index].isdigit() or self.source[index] == "_"):
                    index += 1
            if index < self.length and self.source[index] in "eE":
                probe = index + 1
                if probe < self.length and self.source[probe] in "+-":
                    probe += 1
                if probe < self.length and self.source[probe].isdigit():
                    index = probe
                    while index < self.length and (self.source[index].isdigit() or self.source[index] == "_"):
                        index += 1
            raw = self.source[start:index]
            try:
                value = float(raw.replace("_", "")) if any(c in raw for c in ".eE") else int(raw.replace("_", ""), 10)
            except ValueError:
                value = 0
                self.error(f"invalid number {raw}", start, index)
        self.add("number", value, start, index)
        self.advance(index)

    def read_operator(self) -> bool:
        for size in (3, 2, 1):
            value = self.source[self.index:self.index + size]
            if size == 3 and value in THREE:
                self.add("symbol", value, self.index, self.index + 3)
                self.advance(self.index + 3)
                return True
            if size == 2 and value in TWO:
                self.add("symbol", value, self.index, self.index + 2)
                self.advance(self.index + 2)
                return True
            if size == 1 and value in ONE:
                self.add("symbol", value, self.index, self.index + 1)
                self.advance(self.index + 1)
                return True
        return False

    def bytes_for_char(self, char: str) -> bytes:
        code = ord(char)
        if 0xDC80 <= code <= 0xDCFF:
            return bytes((code - 0xDC00,))
        return char.encode("utf-8", "surrogateescape")


def lex(source: str) -> tuple[list[Token], list[Diagnostic], list[tuple[int, int]]]:
    lexer = Lexer(source)
    tokens, errors = lexer.lex()
    return tokens, errors, lexer.comments

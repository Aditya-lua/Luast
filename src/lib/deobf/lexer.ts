export type TokenType = "name" | "num" | "str" | "op" | "kw" | "cmt";

export interface Token {
  t: TokenType;
  v: string;
}

export const KEYWORDS = new Set([
  "and",
  "break",
  "do",
  "else",
  "elseif",
  "end",
  "false",
  "for",
  "function",
  "if",
  "in",
  "local",
  "nil",
  "not",
  "or",
  "repeat",
  "return",
  "then",
  "true",
  "until",
  "while",
  "continue",
]);

// Longest operators first.
const OPS = [
  "...",
  "..=",
  "//=",
  "..",
  "==",
  "~=",
  "<=",
  ">=",
  "//",
  "::",
  "->",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "^=",
  "+",
  "-",
  "*",
  "/",
  "%",
  "^",
  "#",
  "&",
  "~",
  "|",
  "<",
  ">",
  "=",
  "(",
  ")",
  "{",
  "}",
  "[",
  "]",
  ";",
  ":",
  ",",
  ".",
  "?",
];

const isNameStart = (c: string) => /[A-Za-z_]/.test(c);
const isNameChar = (c: string) => /[A-Za-z0-9_]/.test(c);
const isDigit = (c: string) => c >= "0" && c <= "9";

export function tok(t: TokenType, v: string): Token {
  return { t, v };
}

export const T = {
  name: (v: string) => tok("name", v),
  num: (v: string | number) => tok("num", String(v)),
  str: (v: string) => tok("str", quoteString(v)),
  op: (v: string) => tok("op", v),
  kw: (v: string) => tok("kw", v),
  cmt: (v: string) => tok("cmt", v),
};

export function quoteString(s: string): string {
  let out = '"';
  for (const ch of s) {
    const code = ch.codePointAt(0)!;
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch === "\0") out += "\\0";
    else if (code < 0x20 || code === 0x7f) out += "\\" + code;
    else out += ch;
  }
  return out + '"';
}

/** Decode a Lua string literal token (any quote style) into its runtime value. */
export function decodeStringLiteral(raw: string): string | null {
  if (raw.startsWith("[")) {
    const m = /^\[(=*)\[/.exec(raw);
    if (!m) return null;
    const close = "]" + m[1] + "]";
    if (!raw.endsWith(close)) return null;
    let body = raw.slice(m[0].length, raw.length - close.length);
    if (body.startsWith("\r\n")) body = body.slice(2);
    else if (body.startsWith("\n")) body = body.slice(1);
    return body;
  }
  const q = raw[0];
  if ((q !== '"' && q !== "'") || raw[raw.length - 1] !== q) return null;
  const body = raw.slice(1, -1);
  let out = "";
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (c !== "\\") {
      out += c;
      continue;
    }
    i++;
    const e = body[i];
    if (e === undefined) return null;
    switch (e) {
      case "n":
        out += "\n";
        break;
      case "t":
        out += "\t";
        break;
      case "r":
        out += "\r";
        break;
      case "a":
        out += "\x07";
        break;
      case "b":
        out += "\b";
        break;
      case "f":
        out += "\f";
        break;
      case "v":
        out += "\v";
        break;
      case "\\":
        out += "\\";
        break;
      case '"':
        out += '"';
        break;
      case "'":
        out += "'";
        break;
      case "\n":
        out += "\n";
        break;
      case "z": {
        while (i + 1 < body.length && /\s/.test(body[i + 1])) i++;
        break;
      }
      case "x": {
        const hex = body.slice(i + 1, i + 3);
        if (!/^[0-9a-fA-F]{2}$/.test(hex)) return null;
        out += String.fromCharCode(parseInt(hex, 16));
        i += 2;
        break;
      }
      case "u": {
        if (body[i + 1] !== "{") return null;
        const end = body.indexOf("}", i + 2);
        if (end < 0) return null;
        const cp = parseInt(body.slice(i + 2, end), 16);
        if (Number.isNaN(cp)) return null;
        out += String.fromCodePoint(cp);
        i = end;
        break;
      }
      default: {
        if (isDigit(e)) {
          let digits = e;
          while (digits.length < 3 && i + 1 < body.length && isDigit(body[i + 1])) {
            i++;
            digits += body[i];
          }
          const n = parseInt(digits, 10);
          if (n > 255) return null;
          out += String.fromCharCode(n);
        } else {
          out += e;
        }
      }
    }
  }
  return out;
}

export class LexError extends Error {}

export function lex(src: string): Token[] {
  const tokens: Token[] = [];
  const n = src.length;
  let i = 0;
  // Skip shebang
  if (src.startsWith("#!")) {
    while (i < n && src[i] !== "\n") i++;
  }

  const readLongBracket = (start: number): number => {
    // start points at '['; returns end index (exclusive) or -1
    let j = start + 1;
    let level = 0;
    while (src[j] === "=") {
      level++;
      j++;
    }
    if (src[j] !== "[") return -1;
    const close = "]" + "=".repeat(level) + "]";
    const end = src.indexOf(close, j + 1);
    if (end < 0) throw new LexError("Unterminated long bracket");
    return end + close.length;
  };

  while (i < n) {
    const c = src[i];
    if (c === " " || c === "\t" || c === "\r" || c === "\n" || c === "\f" || c === "\v") {
      i++;
      continue;
    }
    // Comments
    if (c === "-" && src[i + 1] === "-") {
      if (src[i + 2] === "[") {
        const end = readLongBracket(i + 2);
        if (end >= 0) {
          tokens.push(tok("cmt", src.slice(i, end)));
          i = end;
          continue;
        }
      }
      let j = i;
      while (j < n && src[j] !== "\n") j++;
      tokens.push(tok("cmt", src.slice(i, j)));
      i = j;
      continue;
    }
    // Names / keywords
    if (isNameStart(c)) {
      let j = i + 1;
      while (j < n && isNameChar(src[j])) j++;
      const word = src.slice(i, j);
      tokens.push(tok(KEYWORDS.has(word) ? "kw" : "name", word));
      i = j;
      continue;
    }
    // Numbers
    if (isDigit(c) || (c === "." && isDigit(src[i + 1] ?? ""))) {
      let j = i;
      if (c === "0" && (src[i + 1] === "x" || src[i + 1] === "X")) {
        j = i + 2;
        while (j < n && /[0-9a-fA-F_]/.test(src[j])) j++;
      } else if (c === "0" && (src[i + 1] === "b" || src[i + 1] === "B")) {
        j = i + 2;
        while (j < n && /[01_]/.test(src[j])) j++;
      } else {
        while (j < n && (isDigit(src[j]) || src[j] === "_")) j++;
        if (src[j] === ".") {
          j++;
          while (j < n && (isDigit(src[j]) || src[j] === "_")) j++;
        }
        if (src[j] === "e" || src[j] === "E") {
          let k = j + 1;
          if (src[k] === "+" || src[k] === "-") k++;
          if (isDigit(src[k] ?? "")) {
            j = k;
            while (j < n && isDigit(src[j])) j++;
          }
        }
      }
      tokens.push(tok("num", src.slice(i, j)));
      i = j;
      continue;
    }
    // Strings
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && src[j] !== c) {
        if (src[j] === "\\") j++;
        if (src[j] === "\n") throw new LexError("Unterminated string");
        j++;
      }
      if (j >= n) throw new LexError("Unterminated string");
      tokens.push(tok("str", src.slice(i, j + 1)));
      i = j + 1;
      continue;
    }
    if (c === "`") {
      let j = i + 1;
      let depth = 0;
      while (j < n) {
        if (src[j] === "\\") {
          j += 2;
          continue;
        }
        if (src[j] === "{") depth++;
        else if (src[j] === "}") depth--;
        else if (src[j] === "`" && depth <= 0) break;
        j++;
      }
      if (j >= n) throw new LexError("Unterminated interpolated string");
      tokens.push(tok("str", src.slice(i, j + 1)));
      i = j + 1;
      continue;
    }
    if (c === "[" && (src[i + 1] === "[" || src[i + 1] === "=")) {
      const end = readLongBracket(i);
      if (end >= 0) {
        tokens.push(tok("str", src.slice(i, end)));
        i = end;
        continue;
      }
    }
    // Operators
    let matched = false;
    for (const op of OPS) {
      if (src.startsWith(op, i)) {
        tokens.push(tok("op", op));
        i += op.length;
        matched = true;
        break;
      }
    }
    if (matched) continue;
    throw new LexError(`Unexpected character '${c}' at offset ${i}`);
  }
  return tokens;
}

import { Token, T } from "./lexer";

export type Node = Token | Struct;
export type Body = Node[];

export interface IfClause {
  cond: Body;
  body: Body;
}

export type Struct =
  | { kind: "if"; clauses: IfClause[]; elseBody: Body | null }
  | { kind: "while"; cond: Body; body: Body }
  | { kind: "do"; body: Body }
  | { kind: "for"; header: Body; body: Body }
  | { kind: "repeat"; body: Body; cond: Body }
  | { kind: "function"; header: Body; body: Body; isExpr: boolean }
  | { kind: "exprif"; clauses: IfClause[]; elseBody: Body };

export const isTok = (n: Node | undefined): n is Token =>
  !!n && "t" in (n as Token);
export const isStruct = (n: Node | undefined): n is Struct =>
  !!n && "kind" in (n as Struct);

export const isOp = (n: Node | undefined, v?: string) =>
  isTok(n) && n.t === "op" && (v === undefined || n.v === v);
export const isKw = (n: Node | undefined, v?: string) =>
  isTok(n) && n.t === "kw" && (v === undefined || n.v === v);
export const isName = (n: Node | undefined, v?: string) =>
  isTok(n) && n.t === "name" && (v === undefined || n.v === v);
export const isNum = (n: Node | undefined) => isTok(n) && n.t === "num";
export const isStr = (n: Node | undefined) => isTok(n) && n.t === "str";

export class ParseError extends Error {}

/** Tokens after which an `if` or `function` keyword must be an expression. */
const EXPR_PREV_OPS = new Set([
  "=",
  "(",
  ",",
  "[",
  "{",
  "+",
  "-",
  "*",
  "/",
  "%",
  "^",
  "..",
  "==",
  "~=",
  "<",
  ">",
  "<=",
  ">=",
  "//",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "^=",
  "..=",
  "//=",
  "#",
  "&",
  "|",
  "~",
  ":",
  "->",
]);
const EXPR_PREV_KW = new Set(["return", "and", "or", "not", "then", "else", "local", "in", "until"]);

function isExprPosition(prev: Node | undefined, prevPrev: Node | undefined): boolean {
  if (!prev) return false;
  if (isTok(prev)) {
    if (prev.t === "op") return EXPR_PREV_OPS.has(prev.v);
    if (prev.t === "kw") {
      if (prev.v === "local") return false;
      if (prev.v === "then" || prev.v === "else") {
        // `then`/`else` inside an exprif are expression positions. The parser
        // handles that case directly, so treat top-level occurrences as statements.
        return false;
      }
      return EXPR_PREV_KW.has(prev.v);
    }
    return false;
  }
  void prevPrev;
  return false;
}

const STATEMENT_KW = new Set([
  "local",
  "return",
  "break",
  "continue",
  "if",
  "while",
  "for",
  "do",
  "repeat",
  "function",
  "end",
  "else",
  "elseif",
  "until",
]);

/** Parse a flat token list into a structural tree. */
export function parseTree(tokens: Token[]): Body {
  let i = 0;
  const n = tokens.length;

  const peek = () => tokens[i];
  const next = () => tokens[i++];

  function expectKw(v: string) {
    const t = tokens[i];
    if (!t || t.t !== "kw" || t.v !== v) {
      throw new ParseError(
        `Expected '${v}' but found '${t ? t.v : "<eof>"}' near token ${i}`,
      );
    }
    i++;
  }

  // Parse a body until one of the terminator keywords is found (not consumed).
  function parseBody(terminators: Set<string>, exprMode = false): Body {
    const out: Body = [];
    let depth = 0; // paren/bracket depth for exprif termination
    while (i < n) {
      const t = peek();
      if (t.t === "kw" && terminators.has(t.v) && depth <= 0) break;
      if (exprMode && t.t === "op") {
        if (t.v === "(" || t.v === "[" || t.v === "{") depth++;
        else if (t.v === ")" || t.v === "]" || t.v === "}") {
          if (depth === 0) break;
          depth--;
        } else if ((t.v === ";" || t.v === ",") && depth === 0) break;
      }
      if (exprMode && depth === 0 && t.t === "kw" && STATEMENT_KW.has(t.v) && t.v !== "function" && t.v !== "if") {
        break;
      }
      if (t.t === "kw") {
        const prev = out[out.length - 1];
        const prevPrev = out[out.length - 2];
        switch (t.v) {
          case "if": {
            if (exprMode || isExprPosition(prev, prevPrev)) {
              out.push(parseExprIf());
            } else {
              out.push(parseIf());
            }
            continue;
          }
          case "while": {
            next();
            const cond = parseBody(new Set(["do"]));
            expectKw("do");
            const body = parseBody(new Set(["end"]));
            expectKw("end");
            out.push({ kind: "while", cond, body });
            continue;
          }
          case "for": {
            next();
            const header = parseBody(new Set(["do"]));
            expectKw("do");
            const body = parseBody(new Set(["end"]));
            expectKw("end");
            out.push({ kind: "for", header, body });
            continue;
          }
          case "do": {
            next();
            const body = parseBody(new Set(["end"]));
            expectKw("end");
            out.push({ kind: "do", body });
            continue;
          }
          case "repeat": {
            next();
            const body = parseBody(new Set(["until"]));
            expectKw("until");
            const cond = parseBody(new Set(STATEMENT_KW), true);
            out.push({ kind: "repeat", body, cond });
            continue;
          }
          case "function": {
            const isExpr = exprMode || isExprPosition(prev, prevPrev);
            next();
            const header: Body = [];
            // name part until '('
            while (i < n && !(peek().t === "op" && peek().v === "(")) header.push(next());
            if (i >= n) throw new ParseError("Expected '(' in function");
            let pd = 0;
            // params
            do {
              const tk = next();
              header.push(tk);
              if (tk.t === "op" && tk.v === "(") pd++;
              else if (tk.t === "op" && tk.v === ")") pd--;
            } while (i < n && pd > 0);
            const body = parseBody(new Set(["end"]));
            expectKw("end");
            // Anonymous function (header starts with '(') that is not `local function`
            // is always an expression.
            const anonymous = header.length > 0 && isOp(header[0], "(") && !isKw(prev, "local");
            out.push({ kind: "function", header, body, isExpr: isExpr || anonymous });
            continue;
          }
          default:
            break;
        }
      }
      out.push(next());
    }
    return out;
  }

  function parseIf(): Struct {
    expectKw("if");
    const clauses: IfClause[] = [];
    let cond = parseBody(new Set(["then"]));
    expectKw("then");
    let body = parseBody(new Set(["elseif", "else", "end"]));
    clauses.push({ cond, body });
    let elseBody: Body | null = null;
    while (i < n) {
      const t = peek();
      if (t.t === "kw" && t.v === "elseif") {
        next();
        cond = parseBody(new Set(["then"]));
        expectKw("then");
        body = parseBody(new Set(["elseif", "else", "end"]));
        clauses.push({ cond, body });
      } else if (t.t === "kw" && t.v === "else") {
        next();
        elseBody = parseBody(new Set(["end"]));
      } else break;
    }
    expectKw("end");
    return { kind: "if", clauses, elseBody };
  }

  function parseExprIf(): Struct {
    expectKw("if");
    const clauses: IfClause[] = [];
    let cond = parseBody(new Set(["then"]), true);
    expectKw("then");
    let value = parseBody(new Set(["elseif", "else"]), true);
    clauses.push({ cond, body: value });
    while (i < n && peek().t === "kw" && peek().v === "elseif") {
      next();
      cond = parseBody(new Set(["then"]), true);
      expectKw("then");
      value = parseBody(new Set(["elseif", "else"]), true);
      clauses.push({ cond, body: value });
    }
    expectKw("else");
    const elseBody = parseBody(new Set(STATEMENT_KW), true);
    return { kind: "exprif", clauses, elseBody };
  }

  const body = parseBody(new Set());
  if (i < n) throw new ParseError(`Unexpected '${tokens[i].v}' at token ${i}`);
  return body;
}

/** Flatten a tree back into tokens. */
export function flatten(body: Body, out: Token[] = []): Token[] {
  for (const node of body) {
    if (isTok(node)) {
      out.push(node);
      continue;
    }
    switch (node.kind) {
      case "if":
        node.clauses.forEach((c, idx) => {
          out.push(T.kw(idx === 0 ? "if" : "elseif"));
          flatten(c.cond, out);
          out.push(T.kw("then"));
          flatten(c.body, out);
        });
        if (node.elseBody) {
          out.push(T.kw("else"));
          flatten(node.elseBody, out);
        }
        out.push(T.kw("end"));
        break;
      case "exprif":
        node.clauses.forEach((c, idx) => {
          out.push(T.kw(idx === 0 ? "if" : "elseif"));
          flatten(c.cond, out);
          out.push(T.kw("then"));
          flatten(c.body, out);
        });
        out.push(T.kw("else"));
        flatten(node.elseBody, out);
        break;
      case "while":
        out.push(T.kw("while"));
        flatten(node.cond, out);
        out.push(T.kw("do"));
        flatten(node.body, out);
        out.push(T.kw("end"));
        break;
      case "do":
        out.push(T.kw("do"));
        flatten(node.body, out);
        out.push(T.kw("end"));
        break;
      case "for":
        out.push(T.kw("for"));
        flatten(node.header, out);
        out.push(T.kw("do"));
        flatten(node.body, out);
        out.push(T.kw("end"));
        break;
      case "repeat":
        out.push(T.kw("repeat"));
        flatten(node.body, out);
        out.push(T.kw("until"));
        flatten(node.cond, out);
        break;
      case "function":
        out.push(T.kw("function"));
        flatten(node.header, out);
        flatten(node.body, out);
        out.push(T.kw("end"));
        break;
    }
  }
  return out;
}

/** All child bodies of a struct (for recursive walking). */
export function childBodies(s: Struct): Body[] {
  switch (s.kind) {
    case "if":
      return [...s.clauses.flatMap((c) => [c.cond, c.body]), ...(s.elseBody ? [s.elseBody] : [])];
    case "exprif":
      return [...s.clauses.flatMap((c) => [c.cond, c.body]), s.elseBody];
    case "while":
      return [s.cond, s.body];
    case "do":
      return [s.body];
    case "for":
      return [s.header, s.body];
    case "repeat":
      return [s.body, s.cond];
    case "function":
      return [s.header, s.body];
  }
}

/** Set a child body by index (mirrors childBodies ordering). */
export function setChildBody(s: Struct, index: number, body: Body) {
  switch (s.kind) {
    case "if": {
      const nClause = s.clauses.length * 2;
      if (index < nClause) {
        if (index % 2 === 0) s.clauses[index / 2].cond = body;
        else s.clauses[(index - 1) / 2].body = body;
      } else s.elseBody = body;
      return;
    }
    case "exprif": {
      const nClause = s.clauses.length * 2;
      if (index < nClause) {
        if (index % 2 === 0) s.clauses[index / 2].cond = body;
        else s.clauses[(index - 1) / 2].body = body;
      } else s.elseBody = body;
      return;
    }
    case "while":
      if (index === 0) s.cond = body;
      else s.body = body;
      return;
    case "do":
      s.body = body;
      return;
    case "for":
      if (index === 0) s.header = body;
      else s.body = body;
      return;
    case "repeat":
      if (index === 0) s.body = body;
      else s.cond = body;
      return;
    case "function":
      if (index === 0) s.header = body;
      else s.body = body;
      return;
  }
}

/**
 * Visit every body (token array) in the tree, bottom-up. The visitor may return
 * a replacement body.
 */
export function mapBodies(body: Body, fn: (b: Body, parent: Struct | null) => Body, parent: Struct | null = null): Body {
  for (const node of body) {
    if (isStruct(node)) {
      const children = childBodies(node);
      children.forEach((child, idx) => {
        const replaced = mapBodies(child, fn, node);
        if (replaced !== child) setChildBody(node, idx, replaced);
      });
    }
  }
  const replaced = fn(body, parent);
  if (replaced !== body && parent === null) {
    replaceContents(body, replaced);
    return body;
  }
  return replaced;
}

/** Replace the contents of `target` with `source` in place (chunked push). */
export function replaceContents<T>(target: T[], source: T[]) {
  const copy = source === target ? source.slice() : source;
  target.length = 0;
  const CHUNK = 20000;
  for (let i = 0; i < copy.length; i += CHUNK) {
    Array.prototype.push.apply(target, copy.slice(i, i + CHUNK));
  }
}

/** Visit every token in the tree. */
export function forEachToken(body: Body, fn: (t: Token, body: Body, index: number) => void) {
  for (let idx = 0; idx < body.length; idx++) {
    const node = body[idx];
    if (isTok(node)) fn(node, body, idx);
    else for (const child of childBodies(node)) forEachToken(child, fn);
  }
}

/** Split a body into statements. Semicolons are dropped. */
export function splitStatements(body: Body): Body[] {
  const stmts: Body[] = [];
  let cur: Body = [];
  const flush = () => {
    if (cur.length) stmts.push(cur);
    cur = [];
  };
  for (let idx = 0; idx < body.length; idx++) {
    const node = body[idx];
    if (isOp(node, ";")) {
      flush();
      continue;
    }
    if (isStruct(node)) {
      const startsStmt = node.kind !== "exprif" && !(node.kind === "function" && node.isExpr);
      if (startsStmt) {
        flush();
        cur.push(node);
        // A statement-struct ends the statement unless it is a function
        // expression used in a call chain (handled by isExpr).
        flush();
        continue;
      }
      cur.push(node);
      continue;
    }
    // Statement-start keywords begin a new statement.
    if (node.t === "kw" && (node.v === "local" || node.v === "return" || node.v === "break" || node.v === "continue")) {
      // `local function` is handled: 'local' followed by function struct.
      flush();
      cur.push(node);
      continue;
    }
    if (node.t === "cmt") {
      flush();
      stmts.push([node]);
      continue;
    }
    cur.push(node);
  }
  flush();
  return stmts;
}

export function joinStatements(stmts: Body[]): Body {
  const out: Body = [];
  for (const s of stmts) {
    out.push(...s);
    const last = s[s.length - 1];
    if (isTok(last) && last.t === "cmt") continue;
    out.push(T.op(";"));
  }
  return out;
}

export function deepClone(body: Body): Body {
  return body.map((n) => {
    if (isTok(n)) return { ...n };
    switch (n.kind) {
      case "if":
        return { kind: "if", clauses: n.clauses.map((c) => ({ cond: deepClone(c.cond), body: deepClone(c.body) })), elseBody: n.elseBody ? deepClone(n.elseBody) : null };
      case "exprif":
        return { kind: "exprif", clauses: n.clauses.map((c) => ({ cond: deepClone(c.cond), body: deepClone(c.body) })), elseBody: deepClone(n.elseBody) };
      case "while":
        return { kind: "while", cond: deepClone(n.cond), body: deepClone(n.body) };
      case "do":
        return { kind: "do", body: deepClone(n.body) };
      case "for":
        return { kind: "for", header: deepClone(n.header), body: deepClone(n.body) };
      case "repeat":
        return { kind: "repeat", body: deepClone(n.body), cond: deepClone(n.cond) };
      case "function":
        return { kind: "function", header: deepClone(n.header), body: deepClone(n.body), isExpr: n.isExpr };
    }
  });
}

export function tokensEqual(a: Node, b: Node): boolean {
  return isTok(a) && isTok(b) && a.t === b.t && a.v === b.v;
}

export function bodyToString(body: Body): string {
  return flatten(body)
    .map((t) => t.v)
    .join(" ");
}

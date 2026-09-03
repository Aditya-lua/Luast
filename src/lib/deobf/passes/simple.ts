import { decodeStringLiteral, KEYWORDS, quoteString, T, Token } from "../lexer";
import {
  Body,
  forEachToken,
  isKw,
  isName,
  isNum,
  isOp,
  isStr,
  isStruct,
  isTok,
  joinStatements,
  mapBodies,
  Node,
  splitStatements,
  Struct,
} from "../tree";

export function parseLuaNumber(raw: string): number | null {
  const s = raw.replace(/_/g, "");
  if (/^0[xX]/.test(s)) {
    const v = parseInt(s.slice(2), 16);
    return Number.isNaN(v) ? null : v;
  }
  if (/^0[bB]/.test(s)) {
    const v = parseInt(s.slice(2), 2);
    return Number.isNaN(v) ? null : v;
  }
  const v = Number(s.endsWith(".") ? s.slice(0, -1) : s);
  return Number.isNaN(v) ? null : v;
}

export function formatLuaNumber(v: number): string {
  if (Number.isInteger(v) && Math.abs(v) < 1e15) return String(v);
  if (!Number.isFinite(v)) return v > 0 ? "math.huge" : "-math.huge";
  return String(v);
}

/** `3198.` -> `3198`, `0x1F` -> `31`, `1e3` -> `1000`. */
export function normalizeNumbers(tree: Body): number {
  let count = 0;
  forEachToken(tree, (t) => {
    if (t.t !== "num") return;
    const v = parseLuaNumber(t.v);
    if (v === null) return;
    const formatted = formatLuaNumber(v);
    if (formatted !== t.v && !formatted.includes("math")) {
      t.v = formatted;
      count++;
    }
  });
  return count;
}

/** Decode escape-heavy string literals into readable literals. */
export function decodeStrings(tree: Body): number {
  let count = 0;
  forEachToken(tree, (t) => {
    if (t.t !== "str") return;
    if (t.v.startsWith("`")) return; // interpolated
    const decoded = decodeStringLiteral(t.v);
    if (decoded === null) return;
    if (t.v.startsWith("[")) return; // long strings are already readable
    const re = quoteString(decoded);
    if (re !== t.v) {
      t.v = re;
      count++;
    }
  });
  return count;
}

const PRECEDENCE: Record<string, number> = {
  or: 1,
  and: 2,
  "<": 3,
  ">": 3,
  "<=": 3,
  ">=": 3,
  "~=": 3,
  "==": 3,
  "..": 5,
  "+": 6,
  "-": 6,
  "*": 7,
  "/": 7,
  "//": 7,
  "%": 7,
  not: 8,
  "#": 8,
  "^": 10,
};

const isBinOpTok = (n: Node | undefined): n is Token =>
  isTok(n) && ((n.t === "op" && n.v in PRECEDENCE) || (n.t === "kw" && (n.v === "and" || n.v === "or")));

function isUnaryMinusContext(prev: Node | undefined): boolean {
  if (!prev) return true;
  if (isTok(prev)) {
    if (prev.t === "op") return !(prev.v === ")" || prev.v === "]" || prev.v === "}");
    if (prev.t === "kw") return prev.v !== "end" && prev.v !== "true" && prev.v !== "false" && prev.v !== "nil";
    return false;
  }
  return prev.kind === "exprif" ? false : true;
}

function literalValue(n: Node | undefined): { kind: "num" | "str" | "bool"; v: number | string | boolean } | null {
  if (!n || !isTok(n)) return null;
  if (n.t === "num") {
    const v = parseLuaNumber(n.v);
    return v === null ? null : { kind: "num", v };
  }
  if (n.t === "str") {
    if (n.v.startsWith("`")) return null;
    const v = decodeStringLiteral(n.v);
    return v === null ? null : { kind: "str", v };
  }
  if (n.t === "kw" && (n.v === "true" || n.v === "false")) return { kind: "bool", v: n.v === "true" };
  return null;
}

function literalToken(v: number | string | boolean): Token {
  if (typeof v === "number") return T.num(formatLuaNumber(v));
  if (typeof v === "string") return T.str(v);
  return T.kw(v ? "true" : "false");
}

function luaMod(a: number, b: number) {
  return a - Math.floor(a / b) * b;
}

/**
 * Fold literal expressions: `(5)` -> `5`, `2 + 3` -> `5`, `"a" .. "b"` -> `"ab"`,
 * `not true` -> `false`, `1 == 1` -> `true`, `if true then a else b` -> `a`.
 */
export function foldLiterals(tree: Body): number {
  let total = 0;
  for (let round = 0; round < 8; round++) {
    let changed = 0;
    mapBodies(tree, (body) => {
      // exprif folding
      for (let i = 0; i < body.length; i++) {
        const node = body[i];
        if (isStruct(node) && node.kind === "exprif") {
          const folded = foldExprIf(node);
          if (folded) {
            body.splice(i, 1, ...folded);
            changed++;
          }
        }
        if (isStruct(node) && node.kind === "if") {
          const c0 = node.clauses[0];
          if (c0.cond.length === 1 && isKw(c0.cond[0], "true")) {
            body.splice(i, 1, { kind: "do", body: c0.body });
            changed++;
          } else if (c0.cond.length === 1 && isKw(c0.cond[0], "false")) {
            node.clauses.shift();
            if (node.clauses.length === 0) {
              body.splice(i, 1, ...(node.elseBody ? [{ kind: "do", body: node.elseBody } as Struct] : []));
            }
            changed++;
          }
        }
      }
      // token-level folding
      for (let i = 0; i < body.length; i++) {
        const prev = body[i - 1];
        const a = body[i];
        const op = body[i + 1];
        const b = body[i + 2];
        const after = body[i + 3];

        // ( literal )
        if (isOp(a, "(") && literalValue(op) && isOp(b, ")")) {
          const callable = isTok(prev) && (prev.t === "name" || prev.t === "str" || (prev.t === "op" && (prev.v === ")" || prev.v === "]")));
          if (!callable && !(isStruct(prev) && prev.kind === "function")) {
            body.splice(i, 3, op as Token);
            changed++;
            continue;
          }
        }
        // not literal
        if (isKw(a, "not")) {
          const lv = literalValue(op);
          if (lv && !(isOp(b, ".") || isOp(b, "[") || isOp(b, "(") || isOp(b, ":") || isOp(b, "^"))) {
            const truthy = lv.kind === "bool" ? (lv.v as boolean) : true;
            body.splice(i, 2, T.kw(truthy ? "false" : "true"));
            changed++;
            continue;
          }
        }
        // unary minus on number literal followed by nothing binding tighter
        if (isOp(a, "-") && isNum(op) && isUnaryMinusContext(prev) && !isOp(b, "^")) {
          const v = parseLuaNumber((op as Token).v);
          if (v !== null && v !== 0 && !(op as Token).v.startsWith("-")) {
            body.splice(i, 2, T.num("-" + formatLuaNumber(v)));
            changed++;
            continue;
          }
        }
        // literal OP literal
        const la = literalValue(a);
        const lb = literalValue(b);
        if (la && lb && isBinOpTok(op)) {
          const p = PRECEDENCE[(op as Token).v];
          // guard: previous binary operator with >= precedence binds `a` first
          if (isBinOpTok(prev) && PRECEDENCE[prev.v] >= p && !(isOp(prev, "-") && isUnaryMinusContext(body[i - 2]))) continue;
          if (isOp(prev, "^") || isKw(prev, "not") || isOp(prev, "#")) continue;
          // guard: following operator with higher precedence binds `b` first
          if (isBinOpTok(after) && PRECEDENCE[after.v] > p) continue;
          if (isOp(after, "(") || isOp(after, "[") || isOp(after, ".") || isOp(after, ":")) continue;
          if (isOp(prev, "-") && isNum(a) && isUnaryMinusContext(body[i - 2])) continue;
          const result = evalBinary(la, (op as Token).v, lb);
          if (result !== null) {
            body.splice(i, 3, literalToken(result));
            changed++;
          }
        }
      }
      return body;
    });
    total += changed;
    if (!changed) break;
  }
  return total;
}

function evalBinary(
  a: { kind: string; v: number | string | boolean },
  op: string,
  b: { kind: string; v: number | string | boolean },
): number | string | boolean | null {
  if (a.kind === "num" && b.kind === "num") {
    const x = a.v as number;
    const y = b.v as number;
    switch (op) {
      case "+":
        return x + y;
      case "-":
        return x - y;
      case "*":
        return x * y;
      case "/":
        return y === 0 ? null : x / y;
      case "//":
        return y === 0 ? null : Math.floor(x / y);
      case "%":
        return y === 0 ? null : luaMod(x, y);
      case "^":
        return Math.pow(x, y);
      case "==":
        return x === y;
      case "~=":
        return x !== y;
      case "<":
        return x < y;
      case ">":
        return x > y;
      case "<=":
        return x <= y;
      case ">=":
        return x >= y;
      case "..":
        return formatLuaNumber(x) + formatLuaNumber(y);
    }
    return null;
  }
  if (a.kind === "str" && b.kind === "str") {
    const x = a.v as string;
    const y = b.v as string;
    switch (op) {
      case "..":
        return x + y;
      case "==":
        return x === y;
      case "~=":
        return x !== y;
    }
    return null;
  }
  if (a.kind === "bool" && b.kind === "bool") {
    const x = a.v as boolean;
    const y = b.v as boolean;
    switch (op) {
      case "==":
        return x === y;
      case "~=":
        return x !== y;
      case "and":
        return x && y;
      case "or":
        return x || y;
    }
  }
  if (op === "==" && a.kind !== b.kind) return false;
  if (op === "~=" && a.kind !== b.kind) return true;
  return null;
}

function foldExprIf(node: Extract<Struct, { kind: "exprif" }>): Body | null {
  const c0 = node.clauses[0];
  if (c0.cond.length !== 1) return null;
  if (isKw(c0.cond[0], "true")) return wrapIfNeeded(c0.body);
  if (isKw(c0.cond[0], "false")) {
    node.clauses.shift();
    if (node.clauses.length === 0) return wrapIfNeeded(node.elseBody);
    return [node];
  }
  return null;
}

function wrapIfNeeded(body: Body): Body {
  if (body.length === 1) return body;
  return [T.op("("), ...body, T.op(")")];
}

const isIdent = (s: string) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(s) && !KEYWORDS.has(s);

/** `t["name"]` -> `t.name`, `t["name"](t, ...)` stays as-is (semantics equal). */
export function cleanIndexing(tree: Body): number {
  let count = 0;
  mapBodies(tree, (body) => {
    for (let i = 0; i + 2 < body.length; i++) {
      const prev = body[i - 1];
      if (!isOp(body[i], "[") || !isStr(body[i + 1]) || !isOp(body[i + 2], "]")) continue;
      // Only when indexing something (not a table constructor key like { ["x"] = 1 })
      const indexable = isTok(prev) && (prev.t === "name" || (prev.t === "op" && (prev.v === ")" || prev.v === "]")) || prev.t === "str");
      const inTableCtor = isOp(prev, "{") || isOp(prev, ",") || isOp(prev, ";");
      const s = decodeStringLiteral((body[i + 1] as Token).v);
      if (s === null || !isIdent(s)) continue;
      if (indexable && !inTableCtor) {
        body.splice(i, 3, T.op("."), T.name(s));
        count++;
      } else if (inTableCtor && isOp(body[i + 3], "=")) {
        body.splice(i, 3, T.name(s));
        count++;
      }
    }
    return body;
  });
  return count;
}

/** Count name references across the entire tree. */
export function countNames(tree: Body): Map<string, number> {
  const counts = new Map<string, number>();
  forEachToken(tree, (t, body, idx) => {
    if (t.t !== "name") return;
    const prev = body[idx - 1];
    if (isOp(prev, ".") || isOp(prev, ":")) return; // field access, not a variable
    counts.set(t.v, (counts.get(t.v) ?? 0) + 1);
  });
  return counts;
}

/**
 * Remove `local x = nil`, `local a, b = nil, nil`, `local x;` and `x = nil`
 * statements for names that are never referenced anywhere else.
 */
export function removeDeadLocals(tree: Body): number {
  let removed = 0;
  for (let round = 0; round < 3; round++) {
    const counts = countNames(tree);
    let changedThisRound = 0;
    mapBodies(tree, (body) => {
      const stmts = splitStatements(body);
      const kept: Body[] = [];
      let changed = false;
      for (const stmt of stmts) {
        const r = simplifyDeclaration(stmt, counts);
        if (r === null) {
          changed = true;
          changedThisRound++;
          continue;
        }
        if (r !== stmt) {
          changed = true;
          changedThisRound++;
        }
        kept.push(r);
      }
      return changed ? joinStatements(kept) : body;
    });
    removed += changedThisRound;
    if (!changedThisRound) break;
  }
  return removed;
}

/**
 * Re-sugar short-circuit lowering:
 *   `X = A; if X then X = B end`      -> `X = A and B`
 *   `X = A; if not X then X = B end`  -> `X = A or B`
 */
export function resugarShortCircuit(tree: Body): number {
  let total = 0;
  for (let round = 0; round < 6; round++) {
    let changed = 0;
    mapBodies(tree, (body) => {
      const stmts = splitStatements(body);
      let localChanged = false;
      for (let i = 0; i + 1 < stmts.length; i++) {
        const a = stmts[i];
        const b = stmts[i + 1];
        if (!(isName(a[0]) && isOp(a[1], "=") && a.length > 2)) continue;
        if (!(b.length === 1 && isStruct(b[0]) && b[0].kind === "if")) continue;
        const ifs = b[0];
        if (ifs.clauses.length !== 1 || ifs.elseBody) continue;
        const name = (a[0] as Token).v;
        const cond = ifs.clauses[0].cond;
        let op: "and" | "or";
        if (cond.length === 1 && isName(cond[0], name)) op = "and";
        else if (cond.length === 2 && isKw(cond[0], "not") && isName(cond[1], name)) op = "or";
        else continue;
        const inner = splitStatements(ifs.clauses[0].body);
        if (inner.length !== 1) continue;
        const s2 = inner[0];
        if (!(isName(s2[0], name) && isOp(s2[1], "=") && s2.length > 2)) continue;
        const lhsExpr = a.slice(2);
        const rhsExpr = s2.slice(2);
        // Avoid rewriting when X is a multi-target assignment or rhs references X
        if (a.some((n) => isOp(n, ",")) && !balancedComma(a)) continue;
        const wrapL = needsParen(lhsExpr, op);
        const wrapR = needsParen(rhsExpr, op, true);
        const merged: Body = [
          T.name(name),
          T.op("="),
          ...(wrapL ? [T.op("("), ...lhsExpr, T.op(")")] : lhsExpr),
          T.kw(op),
          ...(wrapR ? [T.op("("), ...rhsExpr, T.op(")")] : rhsExpr),
        ];
        stmts.splice(i, 2, merged);
        localChanged = true;
        changed++;
        i--;
      }
      return localChanged ? joinStatements(stmts) : body;
    });
    total += changed;
    if (!changed) break;
  }
  return total;
}

/** `else if ... end end` -> `elseif`, and `not (not X)` -> `X`. */
export function tidyStructure(tree: Body): number {
  let count = 0;
  mapBodies(tree, (body) => {
    for (const node of body) {
      if (!isStruct(node) || node.kind !== "if") continue;
      // merge nested else-if chains
      for (;;) {
        if (!node.elseBody) break;
        const inner = splitStatements(node.elseBody);
        if (inner.length !== 1 || inner[0].length !== 1) break;
        const child = inner[0][0];
        if (!isStruct(child) || child.kind !== "if") break;
        node.clauses.push(...child.clauses);
        node.elseBody = child.elseBody;
        count++;
      }
      for (const c of node.clauses) {
        const simplified = stripDoubleNot(c.cond);
        if (simplified) {
          c.cond = simplified;
          count++;
        }
      }
    }
    // double negation inside token runs
    for (let i = 0; i + 4 < body.length; i++) {
      if (isKw(body[i], "not") && isOp(body[i + 1], "(") && isKw(body[i + 2], "not") && isOp(body[i + 3], "(")) {
        // find matching close for body[i+3]
        let depth = 0;
        let j = i + 3;
        for (; j < body.length; j++) {
          if (isOp(body[j], "(")) depth++;
          else if (isOp(body[j], ")")) {
            depth--;
            if (depth === 0) break;
          }
        }
        if (j + 1 < body.length && isOp(body[j + 1], ")")) {
          const inner = body.slice(i + 3, j + 1);
          body.splice(i, j + 2 - i, ...inner);
          count++;
        }
      }
    }
    return body;
  });
  return count;
}

function stripDoubleNot(cond: Body): Body | null {
  if (cond.length >= 4 && isKw(cond[0], "not") && isOp(cond[1], "(") && isKw(cond[2], "not") && isOp(cond[cond.length - 1], ")")) {
    // ensure the parens at 1 and end match
    let depth = 0;
    for (let i = 1; i < cond.length; i++) {
      if (isOp(cond[i], "(")) depth++;
      else if (isOp(cond[i], ")")) depth--;
      if (depth === 0 && i !== cond.length - 1) return null;
    }
    const inner = cond.slice(3, cond.length - 1);
    if (inner.length === 1) return inner;
    if (isOp(inner[0], "(") && isOp(inner[inner.length - 1], ")")) return inner;
    return inner.some((n) => isKw(n, "and") || isKw(n, "or")) ? inner : inner;
  }
  if (cond.length === 3 && isKw(cond[0], "not") && isKw(cond[1], "not")) return [cond[2]];
  return null;
}

function balancedComma(stmt: Body): boolean {
  // true when commas are only inside parens/brackets/braces (i.e. not multi-assign)
  let depth = 0;
  for (const n of stmt) {
    if (isOp(n, "(") || isOp(n, "[") || isOp(n, "{")) depth++;
    else if (isOp(n, ")") || isOp(n, "]") || isOp(n, "}")) depth--;
    else if (isOp(n, ",") && depth === 0) return false;
  }
  return true;
}

function needsParen(expr: Body, op: "and" | "or", right = false): boolean {
  if (expr.length === 1) return false;
  let depth = 0;
  for (const n of expr) {
    if (isStruct(n)) {
      if (n.kind === "exprif") return true;
      if (n.kind === "function" && right) return false;
      continue;
    }
    if (isOp(n, "(") || isOp(n, "[") || isOp(n, "{")) depth++;
    else if (isOp(n, ")") || isOp(n, "]") || isOp(n, "}")) depth--;
    else if (depth === 0 && n.t === "kw") {
      if (n.v === "or") return true;
      if (n.v === "and" && op === "and" && right) return false;
    }
  }
  return false;
}

function isPureChain(expr: Body): boolean {
  return expr.length > 0 && expr.every((n) => isTok(n) && (n.t === "name" || n.t === "str" || n.t === "num" || isOp(n, ".") || (n.t === "kw" && ["true", "false", "nil"].includes(n.v))));
}

function simplifyDeclaration(stmt: Body, counts: Map<string, number>): Body | null {
  // `local a, b, c` or `local a, b = nil, nil` or `local a = nil`
  if (isKw(stmt[0], "local")) {
    const names: string[] = [];
    let i = 1;
    while (i < stmt.length) {
      if (!isName(stmt[i])) return stmt;
      names.push((stmt[i] as Token).v);
      i++;
      if (isOp(stmt[i], ",")) {
        i++;
        continue;
      }
      break;
    }
    if (names.length === 0) return stmt;
    if (i === stmt.length) {
      // pure declaration
      const live = names.filter((n) => (counts.get(n) ?? 0) > 1);
      if (live.length === names.length) return stmt;
      if (live.length === 0) return null;
      return [T.kw("local"), ...live.flatMap((n, k) => (k ? [T.op(","), T.name(n)] : [T.name(n)]))];
    }
    if (!isOp(stmt[i], "=")) return stmt;
    const rhs = stmt.slice(i + 1);
    const allNil = rhs.every((n, k) => (k % 2 === 0 ? isKw(n, "nil") : isOp(n, ",")));
    if (!allNil) {
      // `local X = pure.access.chain` with X unused: side-effect free, drop it.
      if (names.length === 1 && isPureChain(rhs) && (counts.get(names[0]) ?? 0) <= 1) return null;
      return stmt;
    }
    const live = names.filter((n) => (counts.get(n) ?? 0) > 1);
    if (live.length === 0) return null;
    if (live.length === names.length) return stmt;
    return [T.kw("local"), ...live.flatMap((n, k) => (k ? [T.op(","), T.name(n)] : [T.name(n)]))];
  }
  // `x = nil` where x is only referenced in such statements
  if (stmt.length === 3 && isName(stmt[0]) && isOp(stmt[1], "=") && isKw(stmt[2], "nil")) {
    const name = (stmt[0] as Token).v;
    if ((counts.get(name) ?? 0) <= 1) return null;
  }
  return stmt;
}

export { isTok as _isTok };

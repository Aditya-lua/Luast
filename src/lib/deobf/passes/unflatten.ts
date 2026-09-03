import { T, Token } from "../lexer";
import {
  Body,
  IfClause,
  isKw,
  isName,
  isNum,
  isOp,
  isStruct,
  isTok,
  joinStatements,
  mapBodies,
  Node,
  splitStatements,
  Struct,
  forEachToken,
  deepClone,
} from "../tree";
import { parseLuaNumber } from "./simple";

const EXIT = Number.NEGATIVE_INFINITY;

type Terminal =
  | { kind: "goto"; to: number }
  | { kind: "cond"; cond: Body; a: number; b: number }
  | { kind: "exit" }
  | { kind: "return"; stmt: Body }
  | { kind: "unknown" };

interface Block {
  state: number;
  stmts: Body[];
  term: Terminal;
}

interface Leaf {
  lo: number;
  hi: number;
  exact: number | null;
  body: Body;
}

export interface UnflattenStats {
  dispatchersFound: number;
  dispatchersUnflattened: number;
  statesRecovered: number;
  arithmeticBranchesDecoded: number;
  warnings: string[];
}

class Overflow extends Error {}

export function unflattenControlFlow(tree: Body): UnflattenStats {
  const stats: UnflattenStats = {
    dispatchersFound: 0,
    dispatchersUnflattened: 0,
    statesRecovered: 0,
    arithmeticBranchesDecoded: 0,
    warnings: [],
  };
  mapBodies(tree, (body) => {
    const stmts = splitStatements(body);
    let changed = false;
    for (let i = 0; i + 1 < stmts.length; i++) {
      const init = matchAssignNumber(stmts[i]);
      if (!init) continue;
      const w = stmts[i + 1];
      if (!(w.length === 1 && isStruct(w[0]) && w[0].kind === "while")) continue;
      const wh = w[0];
      if (!(wh.cond.length === 1 && isKw(wh.cond[0], "true"))) continue;
      const disp = analyseDispatcher(init.name, wh.body);
      if (!disp) continue;
      stats.dispatchersFound++;
      const K = disp.K;
      const dec = (m: number) => (K === null ? m : K - m);
      const entry = dec(init.value);
      const graph = buildGraph(init.name, disp.leaves, entry, dec, K, stats);
      if (!graph) {
        stats.warnings.push(`Dispatcher on '${init.name}' has an unsupported state shape; left as-is.`);
        continue;
      }
      stats.statesRecovered += graph.size;
      let structured: Body | null = null;
      try {
        structured = structure(graph, entry);
      } catch (e) {
        if (!(e instanceof Overflow)) throw e;
      }
      if (!structured) {
        stats.warnings.push(`Dispatcher on '${init.name}' (${graph.size} states) could not be fully structured; annotated fallback emitted.`);
        structured = annotatedFallback(graph, entry);
      }
      stats.dispatchersUnflattened++;
      stmts.splice(i, 2, ...splitStatements(structured));
      changed = true;
      i--;
    }
    return changed ? joinStatements(stmts) : body;
  });
  return stats;
}

function matchAssignNumber(stmt: Body): { name: string; value: number } | null {
  if (stmt.length === 3 && isName(stmt[0]) && isOp(stmt[1], "=") && isNum(stmt[2])) {
    const v = parseLuaNumber((stmt[2] as Token).v);
    if (v === null) return null;
    return { name: (stmt[0] as Token).v, value: v };
  }
  if (stmt.length === 4 && isKw(stmt[0], "local") && isName(stmt[1]) && isOp(stmt[2], "=") && isNum(stmt[3])) {
    const v = parseLuaNumber((stmt[3] as Token).v);
    if (v === null) return null;
    return { name: (stmt[1] as Token).v, value: v };
  }
  return null;
}

function analyseDispatcher(S: string, whileBody: Body): { K: number | null; leaves: Leaf[] } | null {
  const stmts = splitStatements(whileBody);
  let K: number | null = null;
  let idx = 0;
  // Optional `S = K - S`
  const first = stmts[0];
  if (first && first.length === 5 && isName(first[0], S) && isOp(first[1], "=") && isNum(first[2]) && isOp(first[3], "-") && isName(first[4], S)) {
    K = parseLuaNumber((first[2] as Token).v);
    idx = 1;
  }
  if (stmts.length !== idx + 1) return null;
  let node = stmts[idx];
  if (node.length !== 1 || !isStruct(node[0])) return null;
  let root = node[0];
  if (root.kind === "do") {
    const inner = splitStatements(root.body);
    if (inner.length !== 1 || inner[0].length !== 1 || !isStruct(inner[0][0])) return null;
    root = inner[0][0];
    node = inner[0];
  }
  if (root.kind !== "if") return null;
  if (!isDispatchIf(root, S)) return null;
  const leaves: Leaf[] = [];
  collectLeaves(root, S, -Infinity, Infinity, leaves);
  if (leaves.length < 2) return null;
  return { K, leaves };
}

function parseCond(cond: Body, S: string): { op: string; n: number } | null {
  if (cond.length !== 3 || !isName(cond[0], S) || !isTok(cond[1]) || cond[1].t !== "op" || !isNum(cond[2])) return null;
  const n = parseLuaNumber((cond[2] as Token).v);
  if (n === null) return null;
  if (!["<", "<=", "==", ">", ">="].includes(cond[1].v)) return null;
  return { op: cond[1].v, n };
}

function isDispatchIf(node: Struct, S: string): boolean {
  if (node.kind !== "if") return false;
  return node.clauses.every((c) => parseCond(c.cond, S) !== null);
}

function collectLeaves(node: Extract<Struct, { kind: "if" }>, S: string, lo: number, hi: number, out: Leaf[]) {
  let remLo = lo;
  let remHi = hi;
  for (const clause of node.clauses) {
    const c = parseCond(clause.cond, S)!;
    let clo = remLo;
    let chi = remHi;
    let exact: number | null = null;
    switch (c.op) {
      case "<":
        chi = Math.min(chi, c.n);
        remLo = Math.max(remLo, c.n);
        break;
      case "<=":
        chi = Math.min(chi, c.n + 1);
        remLo = Math.max(remLo, c.n + 1);
        break;
      case ">":
        clo = Math.max(clo, c.n + 1);
        remHi = Math.min(remHi, c.n + 1);
        break;
      case ">=":
        clo = Math.max(clo, c.n);
        remHi = Math.min(remHi, c.n);
        break;
      case "==":
        clo = c.n;
        chi = c.n + 1;
        exact = c.n;
        break;
    }
    addLeafOrRecurse(clause.body, S, clo, chi, exact, out);
  }
  if (node.elseBody) addLeafOrRecurse(node.elseBody, S, remLo, remHi, null, out);
}

function addLeafOrRecurse(body: Body, S: string, lo: number, hi: number, exact: number | null, out: Leaf[]) {
  const stmts = splitStatements(body);
  if (stmts.length === 1 && stmts[0].length === 1 && isStruct(stmts[0][0]) && stmts[0][0].kind === "if" && isDispatchIf(stmts[0][0], S)) {
    collectLeaves(stmts[0][0] as Extract<Struct, { kind: "if" }>, S, lo, hi, out);
    return;
  }
  out.push({ lo, hi, exact, body });
}

function resolveLeaf(leaves: Leaf[], s: number): Leaf | null {
  let best: Leaf | null = null;
  for (const l of leaves) {
    if (l.exact !== null) {
      if (l.exact === s) return l;
      continue;
    }
    if (s >= l.lo && s < l.hi) {
      if (!best || l.hi - l.lo < best.hi - best.lo) best = l;
    }
  }
  return best;
}

function buildGraph(
  S: string,
  leaves: Leaf[],
  entry: number,
  dec: (m: number) => number,
  K: number | null,
  stats: UnflattenStats,
): Map<number, Block> | null {
  const blocks = new Map<number, Block>();
  const queue = [entry];
  // Names assigned inside the whole dispatcher, used to validate temp elimination.
  const nameUse = new Map<string, number>();
  for (const l of leaves) {
    forEachToken(l.body, (t, body, idx) => {
      if (t.t === "name" && !isOp(body[idx - 1], ".") && !isOp(body[idx - 1], ":")) nameUse.set(t.v, (nameUse.get(t.v) ?? 0) + 1);
    });
  }
  while (queue.length) {
    const s = queue.shift()!;
    if (blocks.has(s) || s === EXIT) continue;
    const leaf = resolveLeaf(leaves, s);
    if (!leaf) return null;
    const block = analyseLeaf(S, s, leaf.body, dec, K, nameUse, stats);
    if (!block) return null;
    blocks.set(s, block);
    for (const succ of successors(block)) queue.push(succ);
    if (blocks.size > 5000) return null;
  }
  return blocks;
}

function successors(b: Block): number[] {
  switch (b.term.kind) {
    case "goto":
      return [b.term.to];
    case "cond":
      return [b.term.a, b.term.b];
    default:
      return [];
  }
}

function exprIfNumbers(node: Node): { cond: Body; a: number; b: number } | null {
  if (!isStruct(node) || node.kind !== "exprif" || node.clauses.length !== 1) return null;
  const c = node.clauses[0];
  if (c.body.length !== 1 || !isNum(c.body[0]) || node.elseBody.length !== 1 || !isNum(node.elseBody[0])) return null;
  const a = parseLuaNumber((c.body[0] as Token).v);
  const b = parseLuaNumber((node.elseBody[0] as Token).v);
  if (a === null || b === null) return null;
  return { cond: c.cond, a, b };
}

function analyseLeaf(
  S: string,
  s: number,
  body: Body,
  dec: (m: number) => number,
  K: number | null,
  nameUse: Map<string, number>,
  stats: UnflattenStats,
): Block | null {
  let stmts = splitStatements(body).filter((st) => st.length > 0);
  // drop trailing `continue`
  if (stmts.length && stmts[stmts.length - 1].length === 1 && isKw(stmts[stmts.length - 1][0], "continue")) {
    stmts = stmts.slice(0, -1);
  }
  const last = stmts[stmts.length - 1];
  if (!last) {
    if (K === null) return null;
    return { state: s, stmts: [], term: { kind: "goto", to: dec(K - s) } };
  }
  // S = num
  const asg = matchAssignNumber(last);
  if (asg && asg.name === S) {
    return { state: s, stmts: stmts.slice(0, -1), term: { kind: "goto", to: dec(asg.value) } };
  }
  // S = if c then a else b
  if (last.length === 3 && isName(last[0], S) && isOp(last[1], "=")) {
    const ei = exprIfNumbers(last[2]);
    if (ei) {
      const pre = stmts.slice(0, -1);
      const decoded = decodeArithmeticBranch(pre, ei, nameUse);
      if (decoded) {
        stats.arithmeticBranchesDecoded++;
        if (decoded.a === decoded.b) return { state: s, stmts: decoded.rest, term: { kind: "goto", to: dec(decoded.a) } };
        return { state: s, stmts: decoded.rest, term: { kind: "cond", cond: decoded.cond, a: dec(decoded.a), b: dec(decoded.b) } };
      }
      return { state: s, stmts: pre, term: { kind: "cond", cond: ei.cond, a: dec(ei.a), b: dec(ei.b) } };
    }
    return null;
  }
  if (last.length === 1 && isKw(last[0], "break")) {
    return { state: s, stmts: stmts.slice(0, -1), term: { kind: "exit" } };
  }
  if (isKw(last[0], "return")) {
    return { state: s, stmts: stmts.slice(0, -1), term: { kind: "return", stmt: last } };
  }
  // No state assignment: fall through re-encodes S at the loop head.
  if (K !== null) {
    // Ensure S is not written elsewhere in the block.
    const writesS = stmts.some((st) => isName(st[0], S) && isOp(st[1], "="));
    if (!writesS) return { state: s, stmts, term: { kind: "goto", to: dec(K - s) } };
  }
  return null;
}

// ---------- arithmetic branch decoding ----------

function decodeArithmeticBranch(
  pre: Body[],
  ei: { cond: Body; a: number; b: number },
  nameUse: Map<string, number>,
): { cond: Body; a: number; b: number; rest: Body[] } | null {
  // Find the last `V = if C then n1 else n2` statement in pre.
  let startIdx = -1;
  let seed: { name: string; cond: Body; n1: number; n2: number } | null = null;
  for (let i = pre.length - 1; i >= 0; i--) {
    const st = pre[i];
    if (st.length === 3 && isName(st[0]) && isOp(st[1], "=")) {
      const e = exprIfNumbers(st[2]);
      if (e) {
        startIdx = i;
        seed = { name: (st[0] as Token).v, cond: e.cond, n1: e.a, n2: e.b };
        break;
      }
    }
  }
  if (!seed) return null;
  const assigns = pre.slice(startIdx + 1);
  const temps = new Set<string>([seed.name]);
  const parsedAssigns: { name: string; expr: Body }[] = [];
  for (const st of assigns) {
    if (!(isName(st[0]) && isOp(st[1], "="))) return null;
    const expr = st.slice(2);
    if (!isArithmetic(expr, temps)) return null;
    const nm = (st[0] as Token).v;
    temps.add(nm);
    parsedAssigns.push({ name: nm, expr });
  }
  if (!isArithmetic(ei.cond, temps)) return null;
  // Temps must be private to this leaf (each referenced only in these statements).
  const localCount = new Map<string, number>();
  const countIn = (b: Body) =>
    forEachToken(b, (t, body, idx) => {
      if (t.t === "name" && temps.has(t.v) && !isOp(body[idx - 1], ".") && !isOp(body[idx - 1], ":")) localCount.set(t.v, (localCount.get(t.v) ?? 0) + 1);
    });
  countIn(pre[startIdx]);
  assigns.forEach(countIn);
  countIn(ei.cond);
  for (const tname of temps) {
    if ((nameUse.get(tname) ?? 0) !== (localCount.get(tname) ?? 0)) return null;
  }
  const evalFor = (seedVal: number): number | null => {
    const env = new Map<string, number>([[seed.name, seedVal]]);
    for (const a of parsedAssigns) {
      const v = evalExpr(a.expr, env);
      if (typeof v !== "number") return null;
      env.set(a.name, v);
    }
    const c = evalExpr(ei.cond, env);
    if (typeof c !== "boolean") return null;
    return c ? ei.a : ei.b;
  };
  const whenTrue = evalFor(seed.n1);
  const whenFalse = evalFor(seed.n2);
  if (whenTrue === null || whenFalse === null) return null;
  return { cond: seed.cond, a: whenTrue, b: whenFalse, rest: pre.slice(0, startIdx) };
}

const ARITH_OPS = new Set(["+", "-", "*", "/", "%", "//", "^", "(", ")", "==", "~=", "<", "<=", ">", ">="]);

function isArithmetic(expr: Body, allowedNames: Set<string>): boolean {
  return expr.every((n) => {
    if (!isTok(n)) return false;
    if (n.t === "num") return true;
    if (n.t === "op") return ARITH_OPS.has(n.v);
    if (n.t === "name") return allowedNames.has(n.v);
    if (n.t === "kw") return n.v === "and" || n.v === "or" || n.v === "not";
    return false;
  });
}

type Val = number | boolean | undefined;

/** Tiny evaluator for arithmetic/comparison expressions over numeric variables. */
export function evalExpr(expr: Body, env: Map<string, number>): Val {
  const toks = expr.filter(isTok) as Token[];
  if (toks.length !== expr.length) return undefined;
  let pos = 0;
  const peek = () => toks[pos];
  const eat = () => toks[pos++];

  const parseOr = (): Val => {
    let l = parseAnd();
    while (peek() && isKw(peek(), "or")) {
      eat();
      const r = parseAnd();
      l = l !== false && l !== undefined ? l : r;
    }
    return l;
  };
  const parseAnd = (): Val => {
    let l = parseCmp();
    while (peek() && isKw(peek(), "and")) {
      eat();
      const r = parseCmp();
      l = l === false || l === undefined ? l : r;
    }
    return l;
  };
  const parseCmp = (): Val => {
    let l = parseAdd();
    while (peek() && peek().t === "op" && ["==", "~=", "<", "<=", ">", ">="].includes(peek().v)) {
      const op = eat().v;
      const r = parseAdd();
      if (typeof l !== "number" || typeof r !== "number") return undefined;
      switch (op) {
        case "==":
          l = l === r;
          break;
        case "~=":
          l = l !== r;
          break;
        case "<":
          l = l < r;
          break;
        case "<=":
          l = l <= r;
          break;
        case ">":
          l = l > r;
          break;
        case ">=":
          l = l >= r;
          break;
      }
    }
    return l;
  };
  const parseAdd = (): Val => {
    let l = parseMul();
    while (peek() && peek().t === "op" && (peek().v === "+" || peek().v === "-")) {
      const op = eat().v;
      const r = parseMul();
      if (typeof l !== "number" || typeof r !== "number") return undefined;
      l = op === "+" ? l + r : l - r;
    }
    return l;
  };
  const parseMul = (): Val => {
    let l = parseUnary();
    while (peek() && peek().t === "op" && ["*", "/", "%", "//"].includes(peek().v)) {
      const op = eat().v;
      const r = parseUnary();
      if (typeof l !== "number" || typeof r !== "number") return undefined;
      if (op === "*") l = l * r;
      else if (op === "/") l = l / r;
      else if (op === "//") l = Math.floor(l / r);
      else l = l - Math.floor(l / r) * r;
    }
    return l;
  };
  const parseUnary = (): Val => {
    if (peek() && isOp(peek(), "-")) {
      eat();
      const v = parseUnary();
      return typeof v === "number" ? -v : undefined;
    }
    if (peek() && isKw(peek(), "not")) {
      eat();
      const v = parseUnary();
      return v === undefined ? undefined : !v;
    }
    return parsePow();
  };
  const parsePow = (): Val => {
    const base = parseAtom();
    if (peek() && isOp(peek(), "^")) {
      eat();
      const e = parseUnary();
      if (typeof base !== "number" || typeof e !== "number") return undefined;
      return Math.pow(base, e);
    }
    return base;
  };
  const parseAtom = (): Val => {
    const t = eat();
    if (!t) return undefined;
    if (t.t === "num") return parseLuaNumber(t.v) ?? undefined;
    if (t.t === "name") return env.get(t.v);
    if (t.t === "kw" && t.v === "true") return true;
    if (t.t === "kw" && t.v === "false") return false;
    if (isOp(t, "(")) {
      const v = parseOr();
      if (!peek() || !isOp(peek(), ")")) return undefined;
      eat();
      return v;
    }
    return undefined;
  };
  const v = parseOr();
  if (pos !== toks.length) return undefined;
  return v;
}

// ---------- structuring ----------

function distances(graph: Map<number, Block>, from: number, avoid: number | null): Map<number, number> {
  const dist = new Map<number, number>();
  const queue: number[] = [from];
  dist.set(from, 0);
  while (queue.length) {
    const s = queue.shift()!;
    if (s === EXIT || s === avoid) continue;
    const b = graph.get(s);
    if (!b) continue;
    const succ = successors(b);
    if (succ.length === 0) {
      if (!dist.has(EXIT)) dist.set(EXIT, dist.get(s)! + 1);
      continue;
    }
    for (const n of succ) {
      if (!dist.has(n)) {
        dist.set(n, dist.get(s)! + 1);
        queue.push(n);
      }
    }
  }
  return dist;
}

function findJoin(graph: Map<number, Block>, a: number, b: number, avoid: number | null): number {
  const da = distances(graph, a, avoid);
  const db = distances(graph, b, avoid);
  let best = EXIT;
  let bestScore = Infinity;
  for (const [s, d] of da) {
    const d2 = db.get(s);
    if (d2 === undefined) continue;
    const score = d + d2;
    if (score < bestScore || (score === bestScore && s !== EXIT && best === EXIT)) {
      best = s;
      bestScore = score;
    }
  }
  return best;
}

function negate(cond: Body): Body {
  if (cond.length === 2 && isKw(cond[0], "not")) return [cond[1]];
  if (cond.length === 4 && isKw(cond[0], "not") && isOp(cond[1], "(") && isOp(cond[3], ")")) return [cond[2]];
  if (cond.length === 3 && isTok(cond[1]) && cond[1].t === "op") {
    const flip: Record<string, string> = { "==": "~=", "~=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">" };
    if (flip[cond[1].v]) return [cond[0], T.op(flip[cond[1].v]), cond[2]];
  }
  if (cond.length === 1) return [T.kw("not"), cond[0]];
  return [T.kw("not"), T.op("("), ...cond, T.op(")")];
}

function collapseForwarding(graph: Map<number, Block>, entry: number): number {
  // Redirect edges through empty goto-only blocks.
  const resolve = (s: number): number => {
    let cur = s;
    const seen = new Set<number>();
    while (cur !== EXIT) {
      const b = graph.get(cur);
      if (!b || b.stmts.length > 0 || b.term.kind !== "goto" || seen.has(cur)) break;
      seen.add(cur);
      cur = b.term.to;
    }
    return cur;
  };
  for (const b of graph.values()) {
    if (b.term.kind === "goto") b.term.to = resolve(b.term.to);
    else if (b.term.kind === "cond") {
      b.term.a = resolve(b.term.a);
      b.term.b = resolve(b.term.b);
    }
  }
  const newEntry = resolve(entry);
  // Drop unreachable blocks.
  const reachable = new Set<number>();
  const stack = [newEntry];
  while (stack.length) {
    const s = stack.pop()!;
    if (s === EXIT || reachable.has(s)) continue;
    reachable.add(s);
    const b = graph.get(s);
    if (b) stack.push(...successors(b));
  }
  for (const k of [...graph.keys()]) if (!reachable.has(k)) graph.delete(k);
  return newEntry;
}

type EndReason = "stop" | "header" | "loopexit" | "exit" | "return" | "jump";

interface LoopCtx {
  header: number;
  exit: number;
}

/** Immediate post-dominators over the state graph (EXIT is the virtual sink). */
function computePostDominators(graph: Map<number, Block>): Map<number, number> {
  const nodes = [...graph.keys()];
  const preds = new Map<number, number[]>();
  const addPred = (to: number, from: number) => {
    const arr = preds.get(to);
    if (arr) arr.push(from);
    else preds.set(to, [from]);
  };
  for (const [s, b] of graph) {
    const succ = successors(b);
    if (succ.length === 0) addPred(EXIT, s);
    for (const n of succ) addPred(n, s);
  }
  // Reverse post-order on the reverse graph starting from EXIT.
  const order: number[] = [];
  const seen = new Set<number>();
  const stack: { n: number; i: number }[] = [{ n: EXIT, i: 0 }];
  seen.add(EXIT);
  while (stack.length) {
    const top = stack[stack.length - 1];
    const ps = preds.get(top.n) ?? [];
    if (top.i < ps.length) {
      const p = ps[top.i++];
      if (!seen.has(p)) {
        seen.add(p);
        stack.push({ n: p, i: 0 });
      }
    } else {
      order.push(top.n);
      stack.pop();
    }
  }
  order.reverse(); // RPO: EXIT first
  const rpoIndex = new Map<number, number>();
  order.forEach((n, i) => rpoIndex.set(n, i));
  const ipdom = new Map<number, number>();
  ipdom.set(EXIT, EXIT);
  const intersect = (a: number, b: number): number => {
    let x = a;
    let y = b;
    while (x !== y) {
      while (rpoIndex.get(x)! > rpoIndex.get(y)!) x = ipdom.get(x)!;
      while (rpoIndex.get(y)! > rpoIndex.get(x)!) y = ipdom.get(y)!;
    }
    return x;
  };
  let changed = true;
  while (changed) {
    changed = false;
    for (const n of order) {
      if (n === EXIT) continue;
      const b = graph.get(n)!;
      const succ = successors(b);
      const cands = (succ.length ? succ : [EXIT]).filter((x) => ipdom.has(x));
      if (!cands.length) continue;
      let nd = cands[0];
      for (let i = 1; i < cands.length; i++) nd = intersect(nd, cands[i]);
      if (ipdom.get(n) !== nd) {
        ipdom.set(n, nd);
        changed = true;
      }
    }
  }
  void nodes;
  return ipdom;
}

function structure(graph: Map<number, Block>, entryIn: number): Body {
  const entry = collapseForwarding(graph, entryIn);
  let budget = Math.max(600, graph.size * 12);
  const ipdom = computePostDominators(graph);

  const joinFor = (h: number, a: number, b: number, stop: number | null, ctx: LoopCtx | null): number => {
    let j = ipdom.get(h);
    if (j === undefined) j = findJoin(graph, a, b, stop);
    if (ctx) {
      if (j === ctx.header || j === ctx.exit) return stop ?? j;
      // join outside the loop region -> fall back to the loop stop
      const region = reachableSet(graph, h, ctx.header);
      if (j !== EXIT && !region.has(j)) return stop ?? j;
    }
    return j;
  };

  const emitChain = (start: number, stop: number | null, path: Set<number>, ctx: LoopCtx | null): { stmts: Body[]; end: EndReason } => {
    let out: Body[] = [];
    let state = start;
    const localPath = new Set(path);
    const chain: number[] = [];
    const offsets = new Map<number, number>();
    let end: EndReason = "stop";
    for (;;) {
      if (state === stop) {
        end = "stop";
        break;
      }
      if (ctx && state === ctx.header) {
        end = "header";
        break;
      }
      if (ctx && state === ctx.exit) {
        out.push([T.kw("break")]);
        end = "loopexit";
        break;
      }
      if (state === EXIT) {
        end = "exit";
        if (ctx) out.push([T.kw("break")], [T.cmt("-- [deobf] exits enclosing dispatcher")]);
        break;
      }
      if (localPath.has(state)) {
        if (offsets.has(state)) {
          const from = offsets.get(state)!;
          const body = out.splice(from);
          out.push([{ kind: "while", cond: [T.kw("true")], body: joinStatements(body) }]);
          end = "exit";
        } else {
          out.push([T.cmt(`-- [deobf] jump to state ${state} (unstructured back-edge)`)]);
          end = "jump";
        }
        break;
      }
      if (--budget < 0) throw new Overflow();
      localPath.add(state);
      chain.push(state);
      offsets.set(state, out.length);
      const b = graph.get(state);
      if (!b) {
        out.push([T.cmt(`-- [deobf] missing state ${state}`)]);
        end = "jump";
        break;
      }
      out.push(...b.stmts.map(cloneStmt));
      const term = b.term;
      if (term.kind === "exit") {
        if (ctx) out.push([T.kw("break")], [T.cmt("-- [deobf] exits enclosing dispatcher")]);
        end = "exit";
        break;
      }
      if (term.kind === "return") {
        out.push(cloneStmt(term.stmt));
        end = "return";
        break;
      }
      if (term.kind === "unknown") {
        out.push([T.cmt(`-- [deobf] unknown terminal at state ${state}`)]);
        end = "jump";
        break;
      }
      if (term.kind === "goto") {
        state = term.to;
        continue;
      }
      const header = state;
      const reachSetA = reachableSet(graph, term.a, stop);
      const reachSetB = reachableSet(graph, term.b, stop);
      const entryA = chain.find((s) => reachSetA.has(s));
      const entryB = chain.find((s) => reachSetB.has(s));
      const loopsA = entryA !== undefined;
      const loopsB = entryB !== undefined;
      if (loopsA !== loopsB) {
        const loopEntry = (loopsA ? entryA : entryB)!;
        const loopSucc = loopsA ? term.a : term.b;
        const exitSucc = loopsA ? term.b : term.a;
        const cond = loopsA ? term.cond : negate(term.cond);
        const entryOffset = offsets.get(loopEntry)!;
        const innerPath = new Set([...localPath].filter((x) => !offsets.has(x) || offsets.get(x)! < entryOffset));
        const inner = emitChain(loopSucc, loopEntry, innerPath, { header: loopEntry, exit: exitSucc });
        const bodyStmts = inner.stmts;
        if (loopEntry === header && b.stmts.length === 0) {
          out.push([{ kind: "while", cond: cloneBody(cond), body: joinStatements(bodyStmts) }]);
        } else {
          const pre = out.splice(entryOffset);
          const brk: Struct = { kind: "if", clauses: [{ cond: negate(cond), body: [T.kw("break")] }], elseBody: null };
          out.push([{ kind: "while", cond: [T.kw("true")], body: [...joinStatements(pre), brk, T.op(";"), ...joinStatements(bodyStmts)] }]);
        }
        for (const s of chain) if (offsets.has(s) && offsets.get(s)! >= out.length) offsets.delete(s);
        state = exitSucc;
        continue;
      }
      const join = joinFor(header, term.a, term.b, stop, ctx);
      const thenRes = emitChain(term.a, join, localPath, ctx);
      const elseRes = emitChain(term.b, join, localPath, ctx);
      const finish = (res: { stmts: Body[]; end: EndReason }) => {
        if (res.end === "header" && ctx && join !== ctx.header && join !== stop) res.stmts.push([T.kw("continue")]);
        return res.stmts;
      };
      const thenStmts = finish(thenRes);
      const elseStmts = finish(elseRes);
      if (thenStmts.length === 0 && elseStmts.length === 0) {
        state = join;
        continue;
      }
      let clauses: IfClause[];
      let elseBody: Body | null = null;
      if (thenStmts.length === 0) {
        clauses = [{ cond: negate(term.cond), body: joinStatements(elseStmts) }];
      } else {
        clauses = [{ cond: cloneBody(term.cond), body: joinStatements(thenStmts) }];
        if (elseStmts.length) elseBody = joinStatements(elseStmts);
      }
      out.push([{ kind: "if", clauses, elseBody }]);
      // If both branches terminated without reaching the join, the chain ends here.
      const terminal = (r: EndReason) => r !== "stop";
      if (terminal(thenRes.end) && terminal(elseRes.end)) {
        end = thenRes.end === elseRes.end ? thenRes.end : "exit";
        break;
      }
      state = join;
    }
    return { stmts: out, end };
  };

  const res = emitChain(entry, null, new Set(), null);
  return joinStatements(res.stmts);
}

function reachableSet(graph: Map<number, Block>, from: number, avoid: number | null): Set<number> {
  const seen = new Set<number>();
  const stack = [from];
  while (stack.length) {
    const s = stack.pop()!;
    if (s === EXIT || seen.has(s) || s === avoid) continue;
    seen.add(s);
    const b = graph.get(s);
    if (!b) continue;
    for (const n of successors(b)) stack.push(n);
  }
  return seen;
}

function annotatedFallback(graph: Map<number, Block>, entry: number): Body {
  const stmts: Body[] = [];
  stmts.push([T.cmt(`-- [deobf] state machine (${graph.size} states), entry = state ${entry}`)]);
  const order = [...graph.keys()].sort((a, b) => a - b);
  for (const s of order) {
    const b = graph.get(s)!;
    stmts.push([T.cmt(`-- state ${s}:`)]);
    stmts.push(...b.stmts.map(cloneStmt));
    switch (b.term.kind) {
      case "goto":
        stmts.push([T.cmt(`--   -> state ${b.term.to}`)]);
        break;
      case "cond":
        stmts.push([T.cmt(`--   -> if ${b.term.cond.map((n) => (isTok(n) ? n.v : "...")).join(" ")} then state ${b.term.a} else state ${b.term.b}`)]);
        break;
      case "exit":
        stmts.push([T.cmt(`--   -> exit`)]);
        break;
      case "return":
        stmts.push(cloneStmt(b.term.stmt));
        break;
      default:
        stmts.push([T.cmt(`--   -> ?`)]);
    }
  }
  return [{ kind: "do", body: joinStatements(stmts) }];
}

function cloneStmt(s: Body): Body {
  return deepClone(s);
}

function cloneBody(b: Body): Body {
  return deepClone(b);
}

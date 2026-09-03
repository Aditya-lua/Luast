import { T, Token } from "../lexer";
import {
  Body,
  forEachToken,
  isKw,
  isName,
  isNum,
  isOp,
  isStruct,
  isTok,
  joinStatements,
  mapBodies,
  Node,
  replaceContents,
  splitStatements,
} from "../tree";
import { countNames, parseLuaNumber } from "./simple";

type Entry =
  | { kind: "literal"; token: Token }
  | { kind: "function"; nodes: Body }
  | { kind: "expr"; nodes: Body };

interface TableCandidate {
  body: Body;
  stmtIndex: number;
  name: string;
  isLocal: boolean;
  entries: Map<number, Entry>;
  size: number;
}

export interface ConstantPassResult {
  found: boolean;
  tableName?: string;
  tableSize: number;
  resolved: number;
  hoisted: number;
  aliasesRemoved: number;
  swapsApplied: number;
  warnings: string[];
}

const MIN_TABLE_SIZE = 8;

function parseTableLiteral(nodes: Body): Map<number, Entry> | null {
  // nodes: everything between `{` and matching `}` (exclusive)
  const entries = new Map<number, Entry>();
  let depth = 0;
  let cur: Body = [];
  let pos = 1;
  const items: Body[] = [];
  for (const n of nodes) {
    if (isTok(n) && n.t === "op") {
      if (n.v === "(" || n.v === "[" || n.v === "{") depth++;
      else if (n.v === ")" || n.v === "]" || n.v === "}") depth--;
      else if ((n.v === "," || n.v === ";") && depth === 0) {
        items.push(cur);
        cur = [];
        continue;
      }
    }
    cur.push(n);
  }
  if (cur.length) items.push(cur);
  for (const item of items) {
    if (item.length === 0) continue;
    let key = pos;
    let value = item;
    if (isOp(item[0], "[") && isNum(item[1]) && isOp(item[2], "]") && isOp(item[3], "=")) {
      const k = parseLuaNumber((item[1] as Token).v);
      if (k === null) return null;
      key = k;
      value = item.slice(4);
    } else if (isName(item[0]) && isOp(item[1], "=")) {
      return null; // named fields: not a constant pool
    } else {
      pos++;
    }
    entries.set(key, classify(value));
  }
  return entries;
}

function classify(value: Body): Entry {
  if (value.length === 1) {
    const v = value[0];
    if (isTok(v) && (v.t === "num" || v.t === "str" || (v.t === "kw" && (v.v === "true" || v.v === "false" || v.v === "nil")))) {
      return { kind: "literal", token: v };
    }
    if (isStruct(v) && v.kind === "function") return { kind: "function", nodes: value };
  }
  if (value.length === 2 && isOp(value[0], "-") && isNum(value[1])) {
    return { kind: "literal", token: T.num("-" + (value[1] as Token).v) };
  }
  return { kind: "expr", nodes: value };
}

function findCandidates(tree: Body): TableCandidate[] {
  const found: TableCandidate[] = [];
  mapBodies(tree, (body) => {
    const stmts = splitStatements(body);
    stmts.forEach((stmt, idx) => {
      let i = 0;
      let isLocal = false;
      if (isKw(stmt[i], "local")) {
        isLocal = true;
        i++;
      }
      if (!isName(stmt[i]) || !isOp(stmt[i + 1], "=") || !isOp(stmt[i + 2], "{")) return;
      if (!isOp(stmt[stmt.length - 1], "}")) return;
      const inner = stmt.slice(i + 3, stmt.length - 1);
      // balanced check
      let depth = 0;
      for (const n of inner) {
        if (isOp(n, "{")) depth++;
        else if (isOp(n, "}")) depth--;
        if (depth < 0) return;
      }
      if (depth !== 0) return;
      const entries = parseTableLiteral(inner);
      if (!entries || entries.size < MIN_TABLE_SIZE) return;
      let literals = 0;
      for (const e of entries.values()) if (e.kind === "literal") literals++;
      if (literals < entries.size * 0.5) return;
      found.push({ body, stmtIndex: idx, name: (stmt[i] as Token).v, isLocal, entries, size: entries.size });
    });
    return body;
  });
  return found.sort((a, b) => b.size - a.size);
}

interface IndexRef {
  body: Body;
  index: number; // position of the name token
  key: number;
}

/** Match `NAME [ num ]` at body[i] (name not preceded by . or :). */
function matchIndex(body: Body, i: number, names: Set<string>): number | null {
  const n = body[i];
  if (!isName(n) || !names.has((n as Token).v)) return null;
  const prev = body[i - 1];
  if (isOp(prev, ".") || isOp(prev, ":")) return null;
  if (!isOp(body[i + 1], "[") || !isNum(body[i + 2]) || !isOp(body[i + 3], "]")) return null;
  return parseLuaNumber((body[i + 2] as Token).v);
}

export function resolveConstantTable(tree: Body): ConstantPassResult {
  const result: ConstantPassResult = {
    found: false,
    tableSize: 0,
    resolved: 0,
    hoisted: 0,
    aliasesRemoved: 0,
    swapsApplied: 0,
    warnings: [],
  };
  const candidates = findCandidates(tree);
  for (const cand of candidates) {
    const r = processCandidate(tree, cand);
    result.found = true;
    result.tableName = result.tableName ?? cand.name;
    result.tableSize += cand.size;
    result.resolved += r.resolved;
    result.hoisted += r.hoisted;
    result.aliasesRemoved += r.aliasesRemoved;
    result.swapsApplied += r.swapsApplied;
    result.warnings.push(...r.warnings);
  }
  return result;
}

function processCandidate(tree: Body, cand: TableCandidate) {
  const warnings: string[] = [];
  const names = new Set<string>([cand.name]);
  const entries = new Map(cand.entries);

  // 1. Collect aliases: `local X = NAME`
  const aliasStmts: { body: Body; stmt: Body }[] = [];
  mapBodies(tree, (body) => {
    for (const stmt of splitStatements(body)) {
      if (stmt.length === 4 && isKw(stmt[0], "local") && isName(stmt[1]) && isOp(stmt[2], "=") && isName(stmt[3], cand.name)) {
        names.add((stmt[1] as Token).v);
        aliasStmts.push({ body, stmt });
      }
    }
    return body;
  });

  // 2. Find writes: swaps (`A[i], A[j] = A[k], A[l]`) and single writes (`A[i] = expr`).
  const dynamic = new Set<number>();
  let keepTable = false;
  let swapsApplied = 0;
  const swapBodies = new Set<Body>();
  mapBodies(tree, (body) => {
    const stmts = splitStatements(body);
    let changed = false;
    const kept: Body[] = [];
    for (const stmt of stmts) {
      const swap = parseSwap(stmt, names);
      if (swap) {
        // apply permutation: new[lhs[k]] = old[rhs[k]]
        const snapshot = swap.rhs.map((k) => entries.get(k));
        swap.lhs.forEach((k, idx) => {
          const v = snapshot[idx];
          if (v === undefined) entries.delete(k);
          else entries.set(k, v);
        });
        swapsApplied++;
        swapBodies.add(body);
        changed = true;
        continue;
      }
      // single write A[num] = ... or compound
      if (stmt.length >= 5) {
        const k = matchIndex(stmt, 0, names);
        if (k !== null && isTok(stmt[4]) && stmt[4].t === "op" && /^(=|\+=|-=|\*=|\/=|%=|\^=|\.\.=)$/.test(stmt[4].v)) {
          dynamic.add(k);
        }
      }
      kept.push(stmt);
    }
    return changed ? joinStatements(kept) : body;
  });
  if (swapBodies.size > 1) {
    warnings.push(
      `Constant pool '${cand.name}' is shuffled in ${swapBodies.size} separate blocks; swaps were applied in source order which may not match runtime order.`,
    );
  }

  // 3. Detect uses that require the table to stay materialised.
  forEachToken(tree, (t, body, idx) => {
    if (t.t !== "name" || !names.has(t.v)) return;
    const prev = body[idx - 1];
    if (isOp(prev, ".") || isOp(prev, ":")) return;
    const nxt = body[idx + 1];
    if (isOp(nxt, "[")) {
      if (isNum(body[idx + 2]) && isOp(body[idx + 3], "]")) return; // constant index read
      keepTable = true; // dynamic index
      return;
    }
    if (isOp(nxt, ".") || isOp(nxt, ":")) {
      keepTable = true;
      return;
    }
    // Declarations / alias definitions / the table statement itself are fine.
    if (isKw(prev, "local")) return;
    if (isOp(nxt, "=") && (isKw(prev, "local") || idx === 0 || isOp(prev, ";"))) return;
    if (isOp(prev, "=") && isKw(body[idx - 3], "local")) return; // alias rhs
    keepTable = true; // passed around as a value
  });
  if (keepTable) {
    warnings.push(`Constant pool '${cand.name}' is accessed dynamically; the table was kept (reordered) alongside inlined literals.`);
  }

  // 4. Replace reads.
  const hoistedNames = new Map<number, string>();
  const hoistName = (k: number, e: Entry) => {
    let n = hoistedNames.get(k);
    if (!n) {
      n = (e.kind === "function" ? "fn_" : "const_") + k;
      hoistedNames.set(k, n);
    }
    return n;
  };
  let resolved = 0;
  mapBodies(tree, (body) => {
    for (let i = 0; i < body.length; i++) {
      const k = matchIndex(body, i, names);
      if (k === null) continue;
      // Skip if this is an assignment target
      const after = body[i + 4];
      if (isTok(after) && after.t === "op" && /^(=|\+=|-=|\*=|\/=|%=|\^=|\.\.=)$/.test(after.v)) continue;
      if (isMultiAssignTarget(body, i, names)) continue;
      if (dynamic.has(k)) continue;
      const e = entries.get(k);
      if (!e) continue;
      if (e.kind === "literal") {
        const lit = e.token;
        const negative = lit.t === "num" && lit.v.startsWith("-");
        const repl: Node[] = negative ? [T.op("("), { ...lit }, T.op(")")] : [{ ...lit }];
        body.splice(i, 4, ...repl);
        resolved++;
      } else if (e.kind === "expr" && isSimpleExpr(e.nodes)) {
        body.splice(i, 4, ...cloneNodes(e.nodes));
        resolved++;
      } else {
        body.splice(i, 4, T.name(hoistName(k, e)));
        resolved++;
      }
    }
    return body;
  });

  // 4b. Any remaining indexed reads (dynamic keys, unknown entries) require the table.
  forEachToken(tree, (t, body, idx) => {
    if (t.t !== "name" || !names.has(t.v)) return;
    if (isOp(body[idx - 1], ".") || isOp(body[idx - 1], ":")) return;
    if (isOp(body[idx + 1], "[")) keepTable = true;
  });

  // 5. Rebuild the table statement: hoisted definitions (+ residual table if needed).
  const stmts = splitStatements(cand.body);
  const original = stmts[cand.stmtIndex];
  const replacement: Body[] = [];
  const hoistKeys = [...hoistedNames.keys()].sort((a, b) => a - b);
  const useTable = hoistKeys.length > 150;
  if (hoistKeys.length) {
    if (useTable) {
      replacement.push([T.kw("local"), T.name("H"), T.op("="), T.op("{"), T.op("}")]);
    } else {
      // forward declare so mutual references work
      const decl: Body = [T.kw("local")];
      hoistKeys.forEach((k, idx) => {
        if (idx) decl.push(T.op(","));
        decl.push(T.name(hoistedNames.get(k)!));
      });
      replacement.push(decl);
    }
    for (const k of hoistKeys) {
      const e = entries.get(k)!;
      const nodes = e.kind === "literal" ? [e.token] : e.nodes;
      const name = hoistedNames.get(k)!;
      const lhs: Body = useTable ? [T.name("H"), T.op("."), T.name(name)] : [T.name(name)];
      if (e.kind === "function" && !useTable) {
        // `function fn_12(...) ... end`
        const fn = nodes[0];
        if (isStruct(fn) && fn.kind === "function") {
          replacement.push([{ kind: "function", header: [T.name(name), ...fn.header], body: fn.body, isExpr: false }]);
          continue;
        }
      }
      replacement.push([...lhs, T.op("="), ...nodes]);
    }
    if (useTable) {
      // rewrite references fn_x -> H.fn_x
      const set = new Set(hoistedNames.values());
      mapBodies(tree, (body) => {
        for (let i = 0; i < body.length; i++) {
          const n = body[i];
          if (isName(n) && set.has((n as Token).v) && !isOp(body[i - 1], ".")) {
            body.splice(i, 1, T.name("H"), T.op("."), n);
            i += 2;
          }
        }
        return body;
      });
    }
  }
  if (keepTable) {
    const keys = [...entries.keys()].sort((a, b) => a - b);
    const tbl: Body = [];
    if (cand.isLocal) tbl.push(T.kw("local"));
    tbl.push(T.name(cand.name), T.op("="), T.op("{"));
    keys.forEach((k, idx) => {
      if (idx) tbl.push(T.op(","));
      const e = entries.get(k)!;
      tbl.push(T.op("["), T.num(k), T.op("]"), T.op("="));
      if (hoistedNames.has(k)) tbl.push(...(useTable ? [T.name("H"), T.op("."), T.name(hoistedNames.get(k)!)] : [T.name(hoistedNames.get(k)!)]));
      else if (e.kind === "literal") tbl.push(e.token);
      else tbl.push(...e.nodes);
    });
    tbl.push(T.op("}"));
    replacement.push(tbl);
  } else if (cand.isLocal || replacement.length === 0) {
    // Table removed entirely; keep a comment noting the pool.
    replacement.unshift([T.cmt(`-- constant pool '${cand.name}' (${cand.size} entries) resolved and removed`)]);
  } else {
    replacement.unshift([T.cmt(`-- constant pool '${cand.name}' (${cand.size} entries) resolved and removed`)]);
  }
  const idx = stmts.indexOf(original);
  stmts.splice(idx, 1, ...replacement);
  replaceContents(cand.body, joinStatements(stmts));

  // 6. Remove alias statements whose alias is no longer referenced.
  let aliasesRemoved = 0;
  const counts = countNames(tree);
  const aliasNames = new Set([...names].filter((n) => n !== cand.name));
  mapBodies(tree, (body) => {
    const ss = splitStatements(body);
    let changed = false;
    const kept = ss.filter((stmt) => {
      if (stmt.length === 4 && isKw(stmt[0], "local") && isName(stmt[1]) && isOp(stmt[2], "=") && isName(stmt[3], cand.name)) {
        const a = (stmt[1] as Token).v;
        if (aliasNames.has(a) && (counts.get(a) ?? 0) <= aliasStmts.filter((s) => (s.stmt[1] as Token).v === a).length) {
          aliasesRemoved++;
          changed = true;
          return false;
        }
      }
      return true;
    });
    return changed ? joinStatements(kept) : body;
  });
  // Also drop `local NAME;` forward declaration if the table was removed
  if (!keepTable) {
    const c2 = countNames(tree);
    if ((c2.get(cand.name) ?? 0) <= 1) {
      mapBodies(tree, (body) => {
        const ss = splitStatements(body);
        const kept = ss.filter((s) => !(s.length === 2 && isKw(s[0], "local") && isName(s[1], cand.name)));
        return kept.length !== ss.length ? joinStatements(kept) : body;
      });
    }
  }

  return { resolved, hoisted: hoistKeys.length, aliasesRemoved, swapsApplied, warnings };
}

/** True when body[i] starts `A[k]` inside a multi-target assignment list `A[a], A[b], x = ...`. */
function isMultiAssignTarget(body: Body, i: number, names: Set<string>): boolean {
  // Walk backwards over `NAME [ num ] ,` groups (or plain `name ,`) to a statement start.
  let j = i - 1;
  while (j >= 0) {
    const n = body[j];
    if (isOp(n, ";") || isStruct(n) || isKw(n)) break;
    if (!isOp(n, ",")) return false;
    // preceding target
    if (isOp(body[j - 1], "]") && isNum(body[j - 2]) && isOp(body[j - 3], "[") && isName(body[j - 4])) j -= 5;
    else if (isName(body[j - 1])) j -= 2;
    else return false;
  }
  // Walk forward over `, target` groups until `=`.
  j = i + 4;
  while (j < body.length) {
    const n = body[j];
    if (isOp(n, "=")) return true;
    if (!isOp(n, ",")) return false;
    const k = matchIndex(body, j + 1, names);
    if (k !== null) j += 5;
    else if (isName(body[j + 1]) && (isOp(body[j + 2], ",") || isOp(body[j + 2], "="))) j += 2;
    else return false;
  }
  return false;
}

function parseSwap(stmt: Body, names: Set<string>): { lhs: number[]; rhs: number[] } | null {
  const eq = stmt.findIndex((n) => isOp(n, "="));
  if (eq < 0) return null;
  const parseSide = (side: Body): number[] | null => {
    const keys: number[] = [];
    let i = 0;
    while (i < side.length) {
      const k = matchIndex(side, i, names);
      if (k === null) return null;
      keys.push(k);
      i += 4;
      if (i < side.length) {
        if (!isOp(side[i], ",")) return null;
        i++;
      }
    }
    return keys.length ? keys : null;
  };
  const lhs = parseSide(stmt.slice(0, eq));
  const rhs = parseSide(stmt.slice(eq + 1));
  if (!lhs || !rhs || lhs.length !== rhs.length) return null;
  if (lhs.length === 1 && rhs.length === 1 && lhs[0] === rhs[0]) return null;
  return { lhs, rhs };
}

function isSimpleExpr(nodes: Body): boolean {
  if (nodes.length > 9) return false;
  return nodes.every((n) => isTok(n) && (n.t === "name" || n.t === "num" || n.t === "str" || (n.t === "op" && (n.v === "." || n.v === "-")) || (n.t === "kw" && ["true", "false", "nil"].includes(n.v))));
}

function cloneNodes(nodes: Body): Body {
  return nodes.map((n) => (isTok(n) ? { ...n } : n));
}

import { decodeStringLiteral, KEYWORDS, Token } from "../lexer";
import { Body, forEachToken, isKw, isName, isOp, isStr, isStruct, isTok, mapBodies, splitStatements } from "../tree";

const LUA_GLOBALS = new Set([
  "game",
  "workspace",
  "script",
  "print",
  "warn",
  "error",
  "pcall",
  "xpcall",
  "type",
  "typeof",
  "pairs",
  "ipairs",
  "next",
  "select",
  "tostring",
  "tonumber",
  "unpack",
  "rawget",
  "rawset",
  "rawequal",
  "setmetatable",
  "getmetatable",
  "require",
  "task",
  "math",
  "string",
  "table",
  "os",
  "bit32",
  "utf8",
  "coroutine",
  "vector",
  "buffer",
  "Instance",
  "Vector3",
  "Vector2",
  "CFrame",
  "Color3",
  "UDim2",
  "UDim",
  "Enum",
  "TweenInfo",
  "Ray",
  "Region3",
  "NumberRange",
  "NumberSequence",
  "ColorSequence",
  "wait",
  "spawn",
  "delay",
  "tick",
  "time",
  "loadstring",
  "getgenv",
  "getfenv",
  "setfenv",
  "shared",
  "_G",
  "_ENV",
  "self",
  "assert",
  "collectgarbage",
  "newproxy",
  "gcinfo",
  "H",
]);

const isObfuscatedName = (s: string) => /^[A-Za-z_][A-Za-z0-9_]{0,3}$/.test(s) && !LUA_GLOBALS.has(s) && !/^(fn|const)_\d+$/.test(s) && !/^(i|j|k|n|x|y|z|a|b|c|v|t|s|fn|cb|id|str|num|key|val|obj|self|arg|idx|len|min|max|pos|dir|dt)$/.test(s);

function lowerFirst(s: string) {
  return s.charAt(0).toLowerCase() + s.slice(1);
}

function sanitize(s: string): string | null {
  const cleaned = s.replace(/[^A-Za-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
  if (!cleaned || /^[0-9]/.test(cleaned) || KEYWORDS.has(cleaned)) return null;
  return cleaned.slice(0, 40);
}

/** Try to derive a meaningful name from the right-hand side of an assignment. */
function hintFromRhs(rhs: Body): string | null {
  // game:GetService("X") / game.GetService(game, "X")
  for (let i = 0; i + 4 < rhs.length; i++) {
    if (isName(rhs[i], "GetService") && isOp(rhs[i + 1], "(")) {
      let j = i + 2;
      if (isName(rhs[j], "game") && isOp(rhs[j + 1], ",")) j += 2;
      if (isStr(rhs[j])) {
        const s = decodeStringLiteral((rhs[j] as Token).v);
        if (s) return sanitize(s);
      }
    }
  }
  // Instance.new("Frame")
  for (let i = 0; i + 4 < rhs.length; i++) {
    if (isName(rhs[i], "Instance") && isOp(rhs[i + 1], ".") && isName(rhs[i + 2], "new") && isOp(rhs[i + 3], "(") && isStr(rhs[i + 4])) {
      const s = decodeStringLiteral((rhs[i + 4] as Token).v);
      if (s) return sanitize(lowerFirst(s));
    }
  }
  // require(a.b.c.Name) -> Name
  if (isName(rhs[0], "require")) {
    for (let i = rhs.length - 1; i > 0; i--) {
      if (isName(rhs[i]) && isOp(rhs[i - 1], ".")) return sanitize((rhs[i] as Token).v);
      if (isStr(rhs[i]) && isOp(rhs[i - 1], "[")) {
        const s = decodeStringLiteral((rhs[i] as Token).v);
        if (s) return sanitize(s);
      }
    }
  }
  // X.LocalPlayer / X.Character etc: take last field name of a pure access chain
  if (rhs.length >= 3 && rhs.every((n) => isTok(n) && (n.t === "name" || isOp(n, ".")))) {
    const last = rhs[rhs.length - 1] as Token;
    if (last.t === "name" && last.v.length > 3) return sanitize(lowerFirst(last.v));
  }
  // X:WaitForChild("Name") / X:FindFirstChild("Name")
  for (let i = 0; i + 3 < rhs.length; i++) {
    if (isOp(rhs[i], ":") && isName(rhs[i + 1]) && /^(WaitForChild|FindFirstChild)$/.test((rhs[i + 1] as Token).v) && isOp(rhs[i + 2], "(") && isStr(rhs[i + 3])) {
      const s = decodeStringLiteral((rhs[i + 3] as Token).v);
      if (s) return sanitize(lowerFirst(s));
    }
  }
  return null;
}

export function renameVariables(tree: Body): number {
  const declared = new Set<string>();
  const hints = new Map<string, string>();
  const allNames = new Set<string>();

  forEachToken(tree, (t) => {
    if (t.t === "name") allNames.add(t.v);
  });

  // Collect declarations: locals, function params, for-vars, function names.
  mapBodies(tree, (body, parent) => {
    const stmts = splitStatements(body);
    for (const stmt of stmts) {
      if (isKw(stmt[0], "local")) {
        let i = 1;
        if (isStruct(stmt[1]) && stmt[1].kind === "function") {
          const nm = stmt[1].header[0];
          if (isName(nm)) declared.add((nm as Token).v);
          continue;
        }
        const names: string[] = [];
        while (isName(stmt[i])) {
          names.push((stmt[i] as Token).v);
          i++;
          if (isOp(stmt[i], ":")) {
            // type annotation: skip until , or =
            while (i < stmt.length && !isOp(stmt[i], ",") && !isOp(stmt[i], "=")) i++;
          }
          if (isOp(stmt[i], ",")) i++;
          else break;
        }
        names.forEach((n) => declared.add(n));
        if (names.length === 1 && isOp(stmt[i], "=")) {
          const h = hintFromRhs(stmt.slice(i + 1));
          if (h && !hints.has(names[0])) hints.set(names[0], h);
        }
      } else if (isName(stmt[0]) && isOp(stmt[1], "=") && stmt.length > 2) {
        const nm = (stmt[0] as Token).v;
        const h = hintFromRhs(stmt.slice(2));
        if (h && !hints.has(nm)) hints.set(nm, h);
      } else if (stmt.length === 1 && isStruct(stmt[0]) && stmt[0].kind === "function") {
        const h = stmt[0].header;
        if (isName(h[0]) && isOp(h[1], "(")) declared.add((h[0] as Token).v);
      }
    }
    if (parent && parent.kind === "function" && body === parent.header) {
      // params between ( and )
      let inParams = false;
      for (const n of body) {
        if (isOp(n, "(")) inParams = true;
        else if (isOp(n, ")")) inParams = false;
        else if (inParams && isName(n)) declared.add((n as Token).v);
      }
    }
    if (parent && parent.kind === "for" && body === parent.header) {
      for (const n of body) {
        if (isKw(n, "in") || isOp(n, "=")) break;
        if (isName(n)) declared.add((n as Token).v);
      }
    }
    return body;
  });

  const mapping = new Map<string, string>();
  const used = new Set(allNames);
  const counters = new Map<string, number>();
  const fresh = (base: string) => {
    let candidate = base;
    if (used.has(candidate)) {
      let n = counters.get(base) ?? 1;
      do {
        n++;
        candidate = `${base}_${n}`;
      } while (used.has(candidate));
      counters.set(base, n);
    }
    used.add(candidate);
    return candidate;
  };

  let varIndex = 0;
  const sortedDeclared = [...declared].filter(isObfuscatedName).sort();
  for (const name of sortedDeclared) {
    const hint = hints.get(name);
    if (hint) mapping.set(name, fresh(hint));
    else mapping.set(name, fresh(`var_${++varIndex}`));
  }

  let renamed = 0;
  forEachToken(tree, (t, body, idx) => {
    if (t.t !== "name") return;
    const prev = body[idx - 1];
    if (isOp(prev, ".") || isOp(prev, ":")) return;
    // table constructor key `{ Key = v }` is not a variable reference
    if ((isOp(prev, "{") || isOp(prev, ",")) && isOp(body[idx + 1], "=")) return;
    const m = mapping.get(t.v);
    if (m) {
      t.v = m;
      renamed++;
    }
  });
  return renamed;
}

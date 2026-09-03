import { Token } from "./lexer";
import { Body, isKw, isOp, isStruct, isTok, Node, splitStatements, Struct } from "./tree";

const NO_SPACE_BEFORE = new Set([",", ";", ")", "]", "}", ".", ":", "(", "["]);
const NO_SPACE_AFTER = new Set(["(", "[", "{", ".", ":", "#", "~"]);
const BINARY_OPS = new Set([
  "+",
  "-",
  "*",
  "/",
  "//",
  "%",
  "^",
  "..",
  "==",
  "~=",
  "<",
  ">",
  "<=",
  ">=",
  "=",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "^=",
  "..=",
  "//=",
  "&",
  "|",
  "->",
]);

function isUnaryContext(prev: Token | null): boolean {
  if (!prev) return true;
  if (prev.t === "op") return !(prev.v === ")" || prev.v === "]" || prev.v === "}");
  if (prev.t === "kw") return !["end", "true", "false", "nil"].includes(prev.v);
  return false;
}

/** Render a flat token run (a single statement / expression) with sensible spacing. */
export function renderTokens(nodes: Body, indent: number): string {
  let out = "";
  let prev: Token | null = null;
  const push = (text: string, spaceBefore: boolean) => {
    if (out.length && spaceBefore && !out.endsWith(" ") && !out.endsWith("\n")) out += " ";
    out += text;
  };
  for (const node of nodes) {
    if (isStruct(node)) {
      const rendered = renderStructInline(node, indent);
      push(rendered, needsSpaceBeforeStruct(prev));
      prev = { t: "kw", v: "end" };
      continue;
    }
    const t = node;
    let space = true;
    if (t.t === "op") {
      if (NO_SPACE_BEFORE.has(t.v)) space = false;
      if (t.v === "(" || t.v === "[") {
        // call/index: no space if previous is a name/closer/string; space after keyword/op
        space = !(prev && (prev.t === "name" || prev.t === "str" || (prev.t === "op" && (prev.v === ")" || prev.v === "]" || prev.v === "}"))));
        if (prev && prev.t === "kw" && ["end", "true", "false", "nil"].includes(prev.v)) space = false;
        if (prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v)) space = false;
      }
      if (t.v === "{") space = !(prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v));
      if (prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v)) space = false;
      if (t.v === "-" && isUnaryContext(prev)) {
        push("-", !(prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v)));
        prev = { t: "op", v: "#" }; // treat as unary: no space after
        continue;
      }
      if (t.v === "#" || t.v === "~") {
        push(t.v, !(prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v)));
        prev = t;
        continue;
      }
      if (BINARY_OPS.has(t.v)) space = true;
      push(t.v, space);
      prev = t;
      continue;
    }
    if (t.t === "cmt") {
      push(t.v, true);
      prev = t;
      continue;
    }
    if (prev && prev.t === "op" && NO_SPACE_AFTER.has(prev.v)) space = false;
    push(t.v, space);
    prev = t;
  }
  return out;
}

function needsSpaceBeforeStruct(prev: Token | null): boolean {
  if (!prev) return false;
  if (prev.t === "op") return !NO_SPACE_AFTER.has(prev.v);
  return true;
}

function renderStructInline(s: Struct, indent: number): string {
  if (s.kind === "exprif") {
    const parts: string[] = [];
    s.clauses.forEach((c, i) => {
      parts.push(`${i === 0 ? "if" : "elseif"} ${renderTokens(c.cond, indent)} then ${renderTokens(c.body, indent)}`);
    });
    parts.push(`else ${renderTokens(s.elseBody, indent)}`);
    return parts.join(" ");
  }
  if (s.kind === "function") {
    const header = renderTokens(s.header, indent);
    const body = renderBody(s.body, indent + 1);
    if (!body.trim()) return `function${header.startsWith("(") ? "" : " "}${header} end`;
    return `function${header.startsWith("(") ? "" : " "}${header}\n${body}\n${pad(indent)}end`;
  }
  // Block statements used inline (shouldn't happen) — render as block.
  return renderStatement([s], indent).trimStart();
}

const pad = (n: number) => "    ".repeat(Math.max(0, n));

function renderStatement(stmt: Body, indent: number): string {
  if (stmt.length === 1 && isStruct(stmt[0]) && stmt[0].kind !== "exprif" && !(stmt[0].kind === "function" && stmt[0].isExpr)) {
    return renderBlockStruct(stmt[0], indent);
  }
  return pad(indent) + renderTokens(stmt, indent);
}

function renderBlockStruct(s: Struct, indent: number): string {
  const p = pad(indent);
  switch (s.kind) {
    case "if": {
      let out = "";
      s.clauses.forEach((c, i) => {
        out += `${i === 0 ? p + "if" : p + "elseif"} ${renderTokens(c.cond, indent)} then\n`;
        out += renderBody(c.body, indent + 1);
        if (!out.endsWith("\n")) out += "\n";
      });
      if (s.elseBody && s.elseBody.length) {
        out += `${p}else\n${renderBody(s.elseBody, indent + 1)}`;
        if (!out.endsWith("\n")) out += "\n";
      }
      return out + `${p}end`;
    }
    case "while": {
      const body = renderBody(s.body, indent + 1);
      return `${p}while ${renderTokens(s.cond, indent)} do\n${body}${body.endsWith("\n") || !body ? "" : "\n"}${p}end`;
    }
    case "do": {
      const body = renderBody(s.body, indent + 1);
      return `${p}do\n${body}${body.endsWith("\n") || !body ? "" : "\n"}${p}end`;
    }
    case "for": {
      const body = renderBody(s.body, indent + 1);
      return `${p}for ${renderTokens(s.header, indent)} do\n${body}${body.endsWith("\n") || !body ? "" : "\n"}${p}end`;
    }
    case "repeat": {
      const body = renderBody(s.body, indent + 1);
      return `${p}repeat\n${body}${body.endsWith("\n") || !body ? "" : "\n"}${p}until ${renderTokens(s.cond, indent)}`;
    }
    case "function": {
      const header = renderTokens(s.header, indent);
      const body = renderBody(s.body, indent + 1);
      return `${p}function ${header}\n${body}${body.endsWith("\n") || !body ? "" : "\n"}${p}end`;
    }
    case "exprif":
      return p + renderStructInline(s, indent);
  }
}

export function renderBody(body: Body, indent: number): string {
  const stmts = splitStatements(body);
  const lines: string[] = [];
  for (const stmt of stmts) {
    if (stmt.length === 0) continue;
    // `local function name(...)` prints on one line with the function block
    if (isKw(stmt[0], "local") && isStruct(stmt[1]) && stmt[1].kind === "function") {
      const fn = stmt[1];
      const header = renderTokens(fn.header, indent);
      const inner = renderBody(fn.body, indent + 1);
      lines.push(`${pad(indent)}local function ${header}\n${inner}${inner.endsWith("\n") || !inner ? "" : "\n"}${pad(indent)}end`);
      continue;
    }
    lines.push(renderStatement(stmt, indent));
  }
  return lines.join("\n");
}

export function format(tree: Body): string {
  return renderBody(tree, 0).replace(/\n{3,}/g, "\n\n") + "\n";
}

export { isTok as _isTok };
export type { Node as _Node };

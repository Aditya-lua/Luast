import { createHash, randomBytes } from "node:crypto";
import { desc, eq, sql } from "drizzle-orm";
import { db } from "@/db";
import { deobfJobs, type JobStats } from "@/db/schema";
import { deobfuscate, type DeobfOptions } from "@/lib/deobf";

export const MAX_INPUT_BYTES = 4 * 1024 * 1024; // 4 MB

export type JobSource = "web" | "discord" | "api";

export interface RunJobInput {
  source: JobSource;
  code: string;
  title?: string | null;
  requester?: string | null;
  options?: DeobfOptions;
  persist?: boolean;
}

export interface RunJobResult {
  publicId: string | null;
  output: string;
  stats: JobStats;
  inputSize: number;
  outputSize: number;
}

export function publicId(): string {
  return randomBytes(9).toString("base64url");
}

export function sha256(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

export async function runJob(input: RunJobInput): Promise<RunJobResult> {
  const code = input.code.replace(/\r\n/g, "\n");
  if (!code.trim()) throw new Error("Empty script");
  if (Buffer.byteLength(code) > MAX_INPUT_BYTES) throw new Error("Script exceeds the 4 MB limit");

  const result = deobfuscate(code, input.options);
  const inputSize = Buffer.byteLength(code);
  const outputSize = Buffer.byteLength(result.output);

  if (input.persist === false) {
    return { publicId: null, output: result.output, stats: result.stats, inputSize, outputSize };
  }

  const id = publicId();
  await db.insert(deobfJobs).values({
    publicId: id,
    source: input.source,
    requester: input.requester ?? null,
    title: (input.title ?? null)?.slice(0, 200) ?? null,
    inputHash: sha256(code),
    inputSize,
    outputSize,
    obfuscator: result.stats.obfuscator,
    input: code,
    output: result.output,
    stats: result.stats,
  });
  return { publicId: id, output: result.output, stats: result.stats, inputSize, outputSize };
}

export async function getJob(id: string) {
  const rows = await db.select().from(deobfJobs).where(eq(deobfJobs.publicId, id)).limit(1);
  return rows[0] ?? null;
}

export async function listRecentJobs(limit = 20) {
  return db
    .select({
      publicId: deobfJobs.publicId,
      source: deobfJobs.source,
      title: deobfJobs.title,
      obfuscator: deobfJobs.obfuscator,
      inputSize: deobfJobs.inputSize,
      outputSize: deobfJobs.outputSize,
      stats: deobfJobs.stats,
      createdAt: deobfJobs.createdAt,
    })
    .from(deobfJobs)
    .orderBy(desc(deobfJobs.createdAt))
    .limit(limit);
}

export async function jobSummary() {
  const rows = await db
    .select({
      total: sql<number>`count(*)::int`,
      bytes: sql<number>`coalesce(sum(${deobfJobs.inputSize}), 0)::bigint`,
      discord: sql<number>`count(*) filter (where ${deobfJobs.source} = 'discord')::int`,
    })
    .from(deobfJobs);
  const r = rows[0];
  return { total: Number(r?.total ?? 0), bytes: Number(r?.bytes ?? 0), discord: Number(r?.discord ?? 0) };
}

/** Fetch a remote script (raw GitHub, pastebin raw, etc.) with size limits. */
export async function fetchRemoteScript(url: string): Promise<{ code: string; title: string }> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("Invalid URL");
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") throw new Error("Only http(s) URLs are supported");
  const host = parsed.hostname;
  if (/^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.|\[?::1)/.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) {
    throw new Error("Refusing to fetch private network addresses");
  }
  // Convert github blob URLs to raw
  if (host === "github.com") {
    parsed = new URL(parsed.href.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/"));
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch(parsed.href, {
      signal: controller.signal,
      headers: { "user-agent": "luast-deobf/1.0", accept: "text/plain,*/*" },
      redirect: "follow",
    });
    if (!res.ok) throw new Error(`Remote server responded ${res.status}`);
    const len = Number(res.headers.get("content-length") ?? 0);
    if (len > MAX_INPUT_BYTES) throw new Error("Remote script exceeds the 4 MB limit");
    const text = await res.text();
    if (Buffer.byteLength(text) > MAX_INPUT_BYTES) throw new Error("Remote script exceeds the 4 MB limit");
    const title = parsed.pathname.split("/").filter(Boolean).pop() ?? "remote.lua";
    return { code: text, title };
  } finally {
    clearTimeout(timer);
  }
}

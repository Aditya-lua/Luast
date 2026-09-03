import { NextResponse } from "next/server";
import { fetchRemoteScript, MAX_INPUT_BYTES, runJob } from "@/lib/jobs";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * POST /api/deobfuscate
 * Body (JSON): { code?: string, url?: string, title?: string, options?: { rename?, unflatten?, removeDeadCode? }, persist?: boolean }
 * or multipart/form-data with a `file` field.
 */
export async function POST(req: Request) {
  try {
    let code = "";
    let title: string | null = null;
    let options: Record<string, boolean> | undefined;
    let persist = true;

    const ctype = req.headers.get("content-type") ?? "";
    if (ctype.includes("multipart/form-data")) {
      const form = await req.formData();
      const file = form.get("file");
      if (file && typeof file !== "string") {
        if (file.size > MAX_INPUT_BYTES) return NextResponse.json({ error: "File exceeds the 4 MB limit" }, { status: 413 });
        code = await file.text();
        title = file.name;
      } else if (typeof form.get("code") === "string") {
        code = String(form.get("code"));
      }
      const t = form.get("title");
      if (typeof t === "string" && t) title = t;
    } else if (ctype.includes("application/json")) {
      const body = (await req.json()) as { code?: string; url?: string; title?: string; options?: Record<string, boolean>; persist?: boolean };
      if (body.url) {
        const remote = await fetchRemoteScript(body.url);
        code = remote.code;
        title = body.title ?? remote.title;
      } else {
        code = body.code ?? "";
        title = body.title ?? null;
      }
      options = body.options;
      if (body.persist === false) persist = false;
    } else {
      code = await req.text();
    }

    if (!code.trim()) return NextResponse.json({ error: "No script provided. Send `code`, `url`, or a `file`." }, { status: 400 });

    const result = await runJob({ source: "api", code, title, options, persist });
    return NextResponse.json({
      id: result.publicId,
      output: result.output,
      stats: result.stats,
      inputSize: result.inputSize,
      outputSize: result.outputSize,
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Deobfuscation failed";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}

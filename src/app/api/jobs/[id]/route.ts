import { NextResponse } from "next/server";
import { getJob } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const job = await getJob(id);
  if (!job) return NextResponse.json({ error: "Job not found" }, { status: 404 });
  const url = new URL(req.url);
  const format = url.searchParams.get("format");
  if (format === "raw" || format === "download") {
    const name = (job.title ?? `job-${job.publicId}`).replace(/\.lua$/i, "") + ".deobf.lua";
    return new Response(job.output, {
      headers: {
        "content-type": "text/plain; charset=utf-8",
        ...(format === "download" ? { "content-disposition": `attachment; filename="${name}"` } : {}),
      },
    });
  }
  if (format === "input") {
    return new Response(job.input, { headers: { "content-type": "text/plain; charset=utf-8" } });
  }
  return NextResponse.json({
    id: job.publicId,
    source: job.source,
    title: job.title,
    obfuscator: job.obfuscator,
    inputSize: job.inputSize,
    outputSize: job.outputSize,
    stats: job.stats,
    output: job.output,
    createdAt: job.createdAt,
  });
}

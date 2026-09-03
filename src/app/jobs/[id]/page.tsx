import Link from "next/link";
import { notFound } from "next/navigation";
import { StatsPanel } from "@/components/StatsPanel";
import { getJob } from "@/lib/jobs";

export const dynamic = "force-dynamic";

const PREVIEW = 250_000;

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const job = await getJob(id);
  if (!job) notFound();
  const input = job.input.length > PREVIEW ? job.input.slice(0, PREVIEW) + "\n\n-- [truncated preview]" : job.input;
  const output = job.output.length > PREVIEW ? job.output.slice(0, PREVIEW) + "\n\n-- [truncated preview: download for full output]" : job.output;
  return (
    <main className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/jobs" className="text-xs text-zinc-500 hover:text-zinc-300">
            ← all jobs
          </Link>
          <h1 className="text-xl font-bold">{job.title ?? job.publicId}</h1>
          <p className="text-xs text-zinc-500">
            {job.source} · {new Date(job.createdAt).toLocaleString()} · id {job.publicId}
          </p>
        </div>
        <div className="flex gap-2 text-sm">
          <a href={`/api/jobs/${job.publicId}?format=raw`} className="rounded-md border border-zinc-700 px-3 py-1.5 hover:bg-zinc-800">
            Raw output
          </a>
          <a href={`/api/jobs/${job.publicId}?format=download`} className="rounded-md bg-violet-600 px-3 py-1.5 font-medium text-white hover:bg-violet-500">
            Download .lua
          </a>
        </div>
      </div>
      <StatsPanel stats={job.stats} inputSize={job.inputSize} outputSize={job.outputSize} />
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50">
          <div className="border-b border-zinc-800 px-4 py-2 text-sm font-medium text-zinc-400">Obfuscated input</div>
          <pre className="scrollbar-thin max-h-[75vh] overflow-auto whitespace-pre-wrap break-all p-4 text-[11px] leading-relaxed text-zinc-400">{input}</pre>
        </div>
        <div className="rounded-xl border border-violet-500/30 bg-zinc-900/50">
          <div className="border-b border-zinc-800 px-4 py-2 text-sm font-medium text-violet-300">Deobfuscated output</div>
          <pre className="scrollbar-thin max-h-[75vh] overflow-auto p-4 text-xs leading-relaxed text-zinc-200">{output}</pre>
        </div>
      </div>
    </main>
  );
}

import Link from "next/link";
import { listRecentJobs } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export default async function JobsPage() {
  const jobs = await listRecentJobs(50);
  return (
    <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <h1 className="text-2xl font-bold">Recent jobs</h1>
      <p className="mt-1 text-sm text-zinc-400">Every run from the web UI, API or Discord is stored with its report.</p>
      <div className="mt-6 overflow-hidden rounded-xl border border-zinc-800">
        <table className="w-full text-sm">
          <thead className="bg-zinc-900 text-left text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Source</th>
              <th className="px-4 py-2">Obfuscator</th>
              <th className="px-4 py-2">Dispatchers</th>
              <th className="px-4 py-2">Confidence</th>
              <th className="px-4 py-2">Size</th>
              <th className="px-4 py-2">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {jobs.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                  No jobs yet — run one from the <Link href="/" className="text-violet-400 underline">deobfuscator</Link>.
                </td>
              </tr>
            )}
            {jobs.map((j) => (
              <tr key={j.publicId} className="hover:bg-zinc-900/60">
                <td className="px-4 py-2">
                  <Link href={`/jobs/${j.publicId}`} className="text-violet-300 hover:underline">
                    {j.title ?? j.publicId}
                  </Link>
                </td>
                <td className="px-4 py-2 text-zinc-400">{j.source}</td>
                <td className="px-4 py-2 text-zinc-400">{j.obfuscator ?? "unknown"}</td>
                <td className="px-4 py-2 text-zinc-400">
                  {j.stats.dispatchersUnflattened}/{j.stats.dispatchersFound}
                </td>
                <td className="px-4 py-2 text-zinc-400">{j.stats.confidence}%</td>
                <td className="px-4 py-2 text-zinc-400">
                  {(j.inputSize / 1024).toFixed(0)} → {(j.outputSize / 1024).toFixed(0)} KB
                </td>
                <td className="px-4 py-2 text-zinc-500">{new Date(j.createdAt).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

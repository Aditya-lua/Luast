import Link from "next/link";
import { DeobfTool } from "@/components/DeobfTool";
import { jobSummary } from "@/lib/jobs";

export const dynamic = "force-dynamic";

const LAYERS = [
  ["Constant pool", "Resolves the giant literal table, simulates runtime swap-shuffles, removes aliases and hoists closures."],
  ["Control flow", "Rebuilds `S = K - S` dispatchers into if/else and while loops using post-dominator analysis."],
  ["Opaque branches", "Evaluates arithmetic-encoded conditions (`(a*x + b*y) % m == c`) back to their original predicate."],
  ["Cleanup", "Folds literals, re-sugars and/or, removes dead locals, `t[\"k\"]` → `t.k`, heuristic renaming."],
];

export default async function HomePage() {
  const summary = await jobSummary().catch(() => ({ total: 0, bytes: 0, discord: 0 }));
  return (
    <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
      <section className="mb-10 grid gap-8 lg:grid-cols-[1.3fr_1fr] lg:items-end">
        <div>
          <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-300">
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400" /> luast v1.x · Luau · static analysis
          </p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Turn obfuscated Luau back into <span className="bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">readable source</span>.
          </h1>
          <p className="mt-4 max-w-2xl text-zinc-400">
            A multi-pass deobfuscation engine with a web UI, a REST API and a Discord bot. Paste a script, drop a file or point it at a raw URL — the engine
            strips the constant pool, unflattens the control-flow state machines and rebuilds structured code.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {[
            ["Scripts processed", summary.total.toLocaleString()],
            ["Bytes analysed", `${(summary.bytes / 1024 / 1024).toFixed(1)} MB`],
            ["Via Discord", summary.discord.toLocaleString()],
          ].map(([l, v]) => (
            <div key={l} className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="text-2xl font-semibold">{v}</div>
              <div className="text-xs text-zinc-500">{l}</div>
            </div>
          ))}
        </div>
      </section>

      <DeobfTool />

      <section className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {LAYERS.map(([title, desc]) => (
          <div key={title} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
            <h3 className="font-semibold text-zinc-100">{title}</h3>
            <p className="mt-2 text-sm text-zinc-400">{desc}</p>
          </div>
        ))}
      </section>

      <section className="mt-10 rounded-xl border border-amber-500/20 bg-amber-500/5 p-5 text-sm text-amber-100/90">
        <strong className="text-amber-200">Honest limits.</strong> Obfuscation is lossy: original variable names, comments and formatting are destroyed at
        obfuscation time and no tool can recover them. This engine restores the <em>structure and semantics</em> of the program and reconstructs
        identifiers heuristically (e.g. <code>game:GetService(&quot;Players&quot;)</code> → <code>Players</code>). New obfuscator versions may
        introduce layers that need new passes — see the <Link className="underline" href="/docs">engine docs</Link>.
      </section>
    </main>
  );
}

import type { JobStats } from "@/db/schema";

export function StatsPanel({ stats, inputSize, outputSize }: { stats: JobStats; inputSize: number; outputSize: number }) {
  const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;
  const items: { label: string; value: string; hint?: string }[] = [
    { label: "Obfuscator", value: `${stats.obfuscator ?? "unknown"}${stats.obfuscatorVersion ? ` v${stats.obfuscatorVersion}` : ""}` },
    { label: "Confidence", value: `${stats.confidence}%`, hint: "Structural recovery estimate" },
    { label: "Duration", value: `${stats.durationMs} ms` },
    { label: "Size", value: `${kb(inputSize)} → ${kb(outputSize)}` },
    { label: "Constants inlined", value: `${stats.constantsResolved}`, hint: `pool of ${stats.constantTableSize} entries` },
    { label: "Closures hoisted", value: `${stats.functionsHoisted}` },
    { label: "Aliases removed", value: `${stats.aliasesRemoved}`, hint: `${stats.swapsApplied} shuffle statements simulated` },
    { label: "Dispatchers", value: `${stats.dispatchersUnflattened}/${stats.dispatchersFound}`, hint: `${stats.statesRecovered} states recovered` },
    { label: "Opaque branches", value: `${stats.arithmeticBranchesDecoded}`, hint: "arithmetic-encoded conditions decoded" },
    { label: "Literal folds", value: `${stats.literalsFolded}` },
    { label: "Dead locals", value: `${stats.deadLocalsRemoved}` },
    { label: "Identifiers renamed", value: `${stats.variablesRenamed}` },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((it) => (
          <div key={it.label} className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
            <div className="text-[11px] uppercase tracking-wide text-zinc-500">{it.label}</div>
            <div className="mt-1 truncate font-semibold text-zinc-100" title={it.value}>
              {it.value}
            </div>
            {it.hint && <div className="mt-0.5 truncate text-[11px] text-zinc-500">{it.hint}</div>}
          </div>
        ))}
      </div>
      {stats.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
          <div className="mb-1 font-semibold">Warnings</div>
          <ul className="list-disc space-y-0.5 pl-5">
            {stats.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

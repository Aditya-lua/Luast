import { headers } from "next/headers";
import { DiscordSetup } from "@/components/DiscordSetup";
import { COMMANDS } from "@/lib/discord/engine";
import { discordConfig } from "@/lib/discord/verify";

export const dynamic = "force-dynamic";

export default async function DiscordPage() {
  const cfg = discordConfig();
  const h = await headers();
  const proto = h.get("x-forwarded-proto") ?? "http";
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const origin = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") ?? `${proto}://${host}`;
  const status = {
    publicKey: Boolean(cfg.publicKey),
    applicationId: cfg.applicationId || null,
    botToken: Boolean(cfg.botToken),
    adminTokenRequired: Boolean(cfg.adminToken),
    commands: COMMANDS.map((c) => c.name),
  };
  return (
    <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
      <h1 className="text-2xl font-bold">Discord bot</h1>
      <p className="mt-1 max-w-2xl text-sm text-zinc-400">
        The bot runs on Discord&apos;s HTTP Interactions API — no gateway process to babysit. Discord POSTs signed interactions to this app, the engine
        runs, and the result is delivered as a file attachment with a report embed.
      </p>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_1.1fr]">
        <DiscordSetup status={status} origin={origin} />
        <div className="space-y-6">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
            <h2 className="font-semibold">Commands</h2>
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="font-mono text-violet-300">/deobf file:&lt;attachment&gt; | url:&lt;raw url&gt; | code:&lt;inline&gt;</dt>
                <dd className="text-zinc-400">Deobfuscates the script and replies with <code>name.deobf.lua</code> plus a stats embed. Optional <code>rename</code> / <code>unflatten</code> toggles.</dd>
              </div>
              <div>
                <dt className="font-mono text-violet-300">Right-click message → Apps → Deobfuscate script</dt>
                <dd className="text-zinc-400">Works on any message that has a <code>.lua</code>/<code>.txt</code> attachment or a ```lua code block.</dd>
              </div>
              <div>
                <dt className="font-mono text-violet-300">/deobf-status</dt>
                <dd className="text-zinc-400">Shows the engine&apos;s supported layers.</dd>
              </div>
            </dl>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5 text-sm">
            <h2 className="font-semibold">Setup in 4 steps</h2>
            <ol className="mt-3 list-decimal space-y-2 pl-5 text-zinc-400">
              <li>Create an application at discord.com/developers → copy <b>Public Key</b> and <b>Application ID</b>; create a bot and copy its <b>Token</b>.</li>
              <li>Set <code>DISCORD_PUBLIC_KEY</code>, <code>DISCORD_APPLICATION_ID</code>, <code>DISCORD_BOT_TOKEN</code> (and optionally <code>ADMIN_TOKEN</code>, <code>NEXT_PUBLIC_SITE_URL</code>) as environment variables.</li>
              <li>Paste the Interactions Endpoint URL shown on the left into the portal — Discord sends a PING which this app answers.</li>
              <li>Click <b>Register commands</b>, invite the bot, and run <code>/deobf</code>.</li>
            </ol>
          </div>
        </div>
      </div>
    </main>
  );
}

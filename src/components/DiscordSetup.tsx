"use client";

import { useState } from "react";

type Status = { publicKey: boolean; applicationId: string | null; botToken: boolean; adminTokenRequired: boolean; commands: string[] };

export function DiscordSetup({ status, origin }: { status: Status; origin: string }) {
  const [adminToken, setAdminToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const register = async () => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const res = await fetch("/api/discord/register", { method: "POST", headers: adminToken ? { "x-admin-token": adminToken } : {} });
      const data = (await res.json()) as { ok?: boolean; commands?: string[]; error?: string };
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      setMsg(`Registered: ${data.commands?.join(", ")}. Global commands can take up to an hour to propagate.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const invite = status.applicationId
    ? `https://discord.com/oauth2/authorize?client_id=${status.applicationId}&scope=applications.commands%20bot&permissions=274877975552`
    : null;

  const Row = ({ ok, label }: { ok: boolean; label: string }) => (
    <li className="flex items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-400" : "bg-zinc-600"}`} />
      <span className={ok ? "text-zinc-200" : "text-zinc-500"}>{label}</span>
    </li>
  );

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="font-semibold">Configuration</h2>
        <ul className="mt-3 space-y-1.5 text-sm">
          <Row ok={status.publicKey} label="DISCORD_PUBLIC_KEY (verifies interaction signatures)" />
          <Row ok={Boolean(status.applicationId)} label={`DISCORD_APPLICATION_ID ${status.applicationId ? `(${status.applicationId})` : ""}`} />
          <Row ok={status.botToken} label="DISCORD_BOT_TOKEN (needed only to register commands)" />
        </ul>
        <div className="mt-4 rounded-lg bg-zinc-950 p-3 text-xs text-zinc-400">
          <div className="mb-1 text-zinc-500">Interactions Endpoint URL (paste into the Discord Developer Portal → General Information):</div>
          <code className="select-all text-violet-300">{origin}/api/discord/interactions</code>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="font-semibold">Register slash commands</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Publishes <code>/deobf</code>, <code>/deobf-status</code> and the <em>Deobfuscate script</em> message command globally.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {status.adminTokenRequired && (
            <input
              value={adminToken}
              onChange={(e) => setAdminToken(e.target.value)}
              placeholder="ADMIN_TOKEN"
              type="password"
              className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm outline-none focus:border-violet-500"
            />
          )}
          <button
            type="button"
            disabled={busy || !status.botToken}
            onClick={register}
            className="rounded-md bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
          >
            {busy ? "Registering…" : "Register commands"}
          </button>
          {invite && (
            <a href={invite} target="_blank" rel="noreferrer" className="rounded-md border border-zinc-700 px-4 py-1.5 text-sm hover:bg-zinc-800">
              Invite bot to a server ↗
            </a>
          )}
        </div>
        {msg && <p className="mt-3 text-sm text-emerald-300">{msg}</p>}
        {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
      </div>
    </div>
  );
}

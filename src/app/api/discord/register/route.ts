import { NextResponse } from "next/server";
import { COMMANDS } from "@/lib/discord/engine";
import { fetchApplicationInfo, registerGlobalCommands } from "@/lib/discord/api";
import { discordConfig } from "@/lib/discord/verify";

export const dynamic = "force-dynamic";

/** POST /api/discord/register — (re)registers the global slash commands. */
export async function POST(req: Request) {
  const cfg = discordConfig();
  if (cfg.adminToken) {
    const provided = req.headers.get("x-admin-token") ?? "";
    if (provided !== cfg.adminToken) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  if (!cfg.botToken) return NextResponse.json({ error: "DISCORD_BOT_TOKEN is not configured" }, { status: 503 });
  let appId = cfg.applicationId;
  if (!appId) {
    const info = await fetchApplicationInfo(cfg.botToken);
    if (!info) return NextResponse.json({ error: "Could not resolve application id; set DISCORD_APPLICATION_ID" }, { status: 503 });
    appId = info.id;
  }
  try {
    const registered = await registerGlobalCommands(appId, cfg.botToken, COMMANDS as unknown as unknown[]);
    return NextResponse.json({ ok: true, applicationId: appId, commands: registered.map((c) => c.name) });
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "registration failed" }, { status: 502 });
  }
}

export async function GET() {
  const cfg = discordConfig();
  return NextResponse.json({
    publicKey: Boolean(cfg.publicKey),
    applicationId: cfg.applicationId || null,
    botToken: Boolean(cfg.botToken),
    adminTokenRequired: Boolean(cfg.adminToken),
    commands: COMMANDS.map((c) => c.name),
  });
}

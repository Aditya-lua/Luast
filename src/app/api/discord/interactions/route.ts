import { NextResponse, after } from "next/server";
import { dispatchInteraction, type Interaction } from "@/lib/discord/engine";
import { discordConfig, verifyDiscordRequest } from "@/lib/discord/verify";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/** Discord Interactions endpoint — set this URL in the Developer Portal. */
export async function POST(req: Request) {
  const { publicKey } = discordConfig();
  if (!publicKey) {
    return NextResponse.json({ error: "DISCORD_PUBLIC_KEY is not configured" }, { status: 503 });
  }
  const body = await req.text();
  const ok = verifyDiscordRequest(publicKey, req.headers.get("x-signature-ed25519"), req.headers.get("x-signature-timestamp"), body);
  if (!ok) return NextResponse.json({ error: "invalid request signature" }, { status: 401 });

  let interaction: Interaction;
  try {
    interaction = JSON.parse(body) as Interaction;
  } catch {
    return NextResponse.json({ error: "malformed body" }, { status: 400 });
  }

  const result = dispatchInteraction(interaction);
  if (result.background) {
    const work = result.background;
    after(async () => {
      await work();
    });
  }
  return NextResponse.json(result.response);
}

export async function GET() {
  const { publicKey, applicationId } = discordConfig();
  return NextResponse.json({
    ok: true,
    configured: Boolean(publicKey && applicationId),
    hint: "Point the Discord 'Interactions Endpoint URL' at this route.",
  });
}

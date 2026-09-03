import nacl from "tweetnacl";

/** Verify a Discord interaction request signature (Ed25519). */
export function verifyDiscordRequest(publicKey: string, signature: string | null, timestamp: string | null, body: string): boolean {
  if (!signature || !timestamp) return false;
  try {
    return nacl.sign.detached.verify(
      Buffer.from(timestamp + body),
      Buffer.from(signature, "hex"),
      Buffer.from(publicKey, "hex"),
    );
  } catch {
    return false;
  }
}

export function discordConfig() {
  return {
    publicKey: process.env.DISCORD_PUBLIC_KEY ?? "",
    applicationId: process.env.DISCORD_APPLICATION_ID ?? process.env.DISCORD_CLIENT_ID ?? "",
    botToken: process.env.DISCORD_BOT_TOKEN ?? "",
    adminToken: process.env.ADMIN_TOKEN ?? "",
  };
}

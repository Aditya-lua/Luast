const API = "https://discord.com/api/v10";

export interface DiscordEmbed {
  title?: string;
  description?: string;
  color?: number;
  fields?: { name: string; value: string; inline?: boolean }[];
  footer?: { text: string };
  url?: string;
  timestamp?: string;
}

export interface FollowupPayload {
  content?: string;
  embeds?: DiscordEmbed[];
  files?: { name: string; content: string }[];
  flags?: number;
}

function buildBody(payload: FollowupPayload): { body: BodyInit; headers: Record<string, string> } {
  const json = { content: payload.content, embeds: payload.embeds, flags: payload.flags };
  if (!payload.files?.length) {
    return { body: JSON.stringify(json), headers: { "content-type": "application/json" } };
  }
  const form = new FormData();
  form.append(
    "payload_json",
    JSON.stringify({
      ...json,
      attachments: payload.files.map((f, i) => ({ id: i, filename: f.name })),
    }),
  );
  payload.files.forEach((f, i) => {
    form.append(`files[${i}]`, new Blob([f.content], { type: "text/plain" }), f.name);
  });
  return { body: form, headers: {} };
}

/** Edit the original deferred interaction response. */
export async function editOriginalResponse(applicationId: string, token: string, payload: FollowupPayload) {
  const { body, headers } = buildBody(payload);
  const res = await fetch(`${API}/webhooks/${applicationId}/${token}/messages/@original`, { method: "PATCH", body, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Discord followup failed (${res.status}): ${text.slice(0, 300)}`);
  }
}

/** Register global application commands (overwrites the set). */
export async function registerGlobalCommands(applicationId: string, botToken: string, commands: unknown[]) {
  const res = await fetch(`${API}/applications/${applicationId}/commands`, {
    method: "PUT",
    headers: { authorization: `Bot ${botToken}`, "content-type": "application/json" },
    body: JSON.stringify(commands),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(`Command registration failed (${res.status}): ${JSON.stringify(data).slice(0, 400)}`);
  return data as { id: string; name: string }[];
}

export async function fetchApplicationInfo(botToken: string) {
  const res = await fetch(`${API}/applications/@me`, { headers: { authorization: `Bot ${botToken}` } });
  if (!res.ok) return null;
  return (await res.json()) as { id: string; name: string };
}

import { db } from "@/db";
import { discordGuilds, type JobStats } from "@/db/schema";
import { sql } from "drizzle-orm";
import { fetchRemoteScript, runJob } from "@/lib/jobs";
import { editOriginalResponse, type DiscordEmbed } from "./api";

// ---- Command definitions (registered with Discord) ----

export const COMMANDS = [
  {
    name: "deobf",
    type: 1,
    description: "Deobfuscate a luast / Luau obfuscated script and get readable source",
    options: [
      { name: "file", description: "Upload the obfuscated .lua/.txt file", type: 11, required: false },
      { name: "url", description: "Raw URL (GitHub raw, pastebin raw, etc.)", type: 3, required: false },
      { name: "code", description: "Paste a short script inline", type: 3, required: false },
      { name: "rename", description: "Rename obfuscated identifiers (default: true)", type: 5, required: false },
      { name: "unflatten", description: "Restructure control-flow dispatchers (default: true)", type: 5, required: false },
    ],
  },
  {
    name: "deobf-status",
    type: 1,
    description: "Show engine status and supported obfuscation layers",
  },
  {
    name: "Deobfuscate script",
    type: 3, // message context menu
  },
] as const;

// ---- Interaction types ----

interface InteractionOption {
  name: string;
  type: number;
  value?: string | number | boolean;
}

interface Attachment {
  id: string;
  filename: string;
  size: number;
  url: string;
}

export interface Interaction {
  id: string;
  type: number;
  token: string;
  application_id: string;
  guild_id?: string;
  channel_id?: string;
  member?: { user: { id: string; username: string } };
  user?: { id: string; username: string };
  data?: {
    id: string;
    name: string;
    type: number;
    options?: InteractionOption[];
    target_id?: string;
    resolved?: {
      attachments?: Record<string, Attachment>;
      messages?: Record<string, { id: string; content: string; attachments: Attachment[] }>;
    };
  };
}

export type InteractionResponse =
  | { type: 1 }
  | { type: 4; data: { content?: string; embeds?: DiscordEmbed[]; flags?: number } }
  | { type: 5; data?: { flags?: number } };

export interface DispatchResult {
  response: InteractionResponse;
  /** Background work to run after the response is sent (deferred followups). */
  background?: () => Promise<void>;
}

const EPHEMERAL = 64;
const COLOR_OK = 0x22c55e;
const COLOR_ERR = 0xef4444;
const COLOR_INFO = 0x6366f1;

function optionValue<T = string>(interaction: Interaction, name: string): T | undefined {
  const opt = interaction.data?.options?.find((o) => o.name === name);
  return opt?.value as T | undefined;
}

function requester(i: Interaction) {
  const u = i.member?.user ?? i.user;
  return u ? `${u.username}#${u.id}` : "unknown";
}

function siteUrl(): string {
  return (process.env.NEXT_PUBLIC_SITE_URL ?? process.env.SITE_URL ?? "").replace(/\/$/, "");
}

function extractCodeBlock(content: string): string | null {
  const m = /```(?:lua|luau)?\n?([\s\S]*?)```/.exec(content);
  if (m) return m[1];
  return null;
}

/** Route an interaction to the right handler. Pure (no I/O) except for deferred background work. */
export function dispatchInteraction(interaction: Interaction): DispatchResult {
  if (interaction.type === 1) return { response: { type: 1 } };
  if (interaction.type !== 2 || !interaction.data) {
    return { response: { type: 4, data: { content: "Unsupported interaction.", flags: EPHEMERAL } } };
  }
  const name = interaction.data.name;
  if (name === "deobf-status") return statusCommand();
  if (name === "deobf") return deobfSlashCommand(interaction);
  if (name === "Deobfuscate script") return deobfMessageCommand(interaction);
  return { response: { type: 4, data: { content: `Unknown command \`${name}\`.`, flags: EPHEMERAL } } };
}

function statusCommand(): DispatchResult {
  const embed: DiscordEmbed = {
    title: "Luast Deobfuscator Engine — status",
    color: COLOR_INFO,
    description:
      "Multi-pass static deobfuscator for luast-style Luau obfuscation.\n\n" +
      "**Layers handled**\n" +
      "• Watermark / obfuscator detection\n" +
      "• Constant-pool resolution (aliases, runtime shuffles, hoisted closures)\n" +
      "• Number & string literal normalisation\n" +
      "• Literal folding, `[\"key\"]` → `.key`\n" +
      "• Control-flow unflattening (state machines → if/while)\n" +
      "• Arithmetic-encoded branch decoding\n" +
      "• Short-circuit re-sugaring, dead-local removal\n" +
      "• Heuristic identifier renaming\n\n" +
      "Names and comments are destroyed by obfuscation and cannot be recovered 1:1 — they are reconstructed heuristically.",
    footer: { text: "Use /deobf with a file, url or code" },
  };
  return { response: { type: 4, data: { embeds: [embed] } } };
}

function deobfSlashCommand(interaction: Interaction): DispatchResult {
  const code = optionValue<string>(interaction, "code");
  const url = optionValue<string>(interaction, "url");
  const fileId = optionValue<string>(interaction, "file");
  const attachment = fileId ? interaction.data?.resolved?.attachments?.[fileId] : undefined;
  const rename = optionValue<boolean>(interaction, "rename");
  const unflatten = optionValue<boolean>(interaction, "unflatten");

  if (!code && !url && !attachment) {
    return {
      response: {
        type: 4,
        data: { content: "Provide one of `file`, `url` or `code`. Example: `/deobf url:https://raw.githubusercontent.com/.../script.lua`", flags: EPHEMERAL },
      },
    };
  }
  return deferred(interaction, async () => {
    let source = code ?? "";
    let title = "inline.lua";
    if (attachment) {
      const remote = await fetchRemoteScript(attachment.url);
      source = remote.code;
      title = attachment.filename;
    } else if (url) {
      const remote = await fetchRemoteScript(url);
      source = remote.code;
      title = remote.title;
    }
    return { source, title, options: { rename: rename ?? true, unflatten: unflatten ?? true } };
  });
}

function deobfMessageCommand(interaction: Interaction): DispatchResult {
  const targetId = interaction.data?.target_id;
  const message = targetId ? interaction.data?.resolved?.messages?.[targetId] : undefined;
  if (!message) return { response: { type: 4, data: { content: "Could not read the target message.", flags: EPHEMERAL } } };
  const luaAttachment = message.attachments.find((a) => /\.(lua|luau|txt)$/i.test(a.filename)) ?? message.attachments[0];
  const block = extractCodeBlock(message.content) ?? (message.content.length > 40 ? message.content : null);
  if (!luaAttachment && !block) {
    return { response: { type: 4, data: { content: "That message has no script attachment or code block.", flags: EPHEMERAL } } };
  }
  return deferred(interaction, async () => {
    if (luaAttachment) {
      const remote = await fetchRemoteScript(luaAttachment.url);
      return { source: remote.code, title: luaAttachment.filename, options: {} };
    }
    return { source: block!, title: "message.lua", options: {} };
  });
}

function deferred(
  interaction: Interaction,
  load: () => Promise<{ source: string; title: string; options: { rename?: boolean; unflatten?: boolean } }>,
): DispatchResult {
  const appId = interaction.application_id;
  const token = interaction.token;
  return {
    response: { type: 5 },
    background: async () => {
      try {
        const { source, title, options } = await load();
        const result = await runJob({ source: "discord", code: source, title, requester: requester(interaction), options });
        await bumpGuild(interaction.guild_id);
        const base = siteUrl();
        const link = result.publicId && base ? `${base}/jobs/${result.publicId}` : null;
        const outName = title.replace(/\.(lua|luau|txt)$/i, "") + ".deobf.lua";
        const tooBig = Buffer.byteLength(result.output) > 8 * 1024 * 1024;
        await editOriginalResponse(appId, token, {
          embeds: [resultEmbed(title, result.stats, result.inputSize, result.outputSize, link)],
          files: tooBig ? undefined : [{ name: outName, content: result.output }],
          content: tooBig ? `Output is larger than Discord's upload limit${link ? ` — download it here: ${link}` : "."}` : undefined,
        });
      } catch (e) {
        const message = e instanceof Error ? e.message : "Unknown error";
        await editOriginalResponse(appId, token, {
          embeds: [{ title: "Deobfuscation failed", description: `\`\`\`\n${message.slice(0, 1500)}\n\`\`\``, color: COLOR_ERR }],
        }).catch(() => undefined);
      }
    },
  };
}

function resultEmbed(title: string, stats: JobStats, inputSize: number, outputSize: number, link: string | null): DiscordEmbed {
  const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;
  return {
    title: `Deobfuscated: ${title}`,
    url: link ?? undefined,
    color: stats.warnings.length ? 0xf59e0b : COLOR_OK,
    fields: [
      { name: "Obfuscator", value: `${stats.obfuscator ?? "unknown"}${stats.obfuscatorVersion ? ` v${stats.obfuscatorVersion}` : ""}`, inline: true },
      { name: "Confidence", value: `${stats.confidence}%`, inline: true },
      { name: "Time", value: `${stats.durationMs} ms`, inline: true },
      { name: "Constants", value: `${stats.constantsResolved} resolved / ${stats.constantTableSize} pool`, inline: true },
      { name: "Dispatchers", value: `${stats.dispatchersUnflattened}/${stats.dispatchersFound} (${stats.statesRecovered} states)`, inline: true },
      { name: "Size", value: `${kb(inputSize)} → ${kb(outputSize)}`, inline: true },
      { name: "Cleanups", value: `${stats.functionsHoisted} closures hoisted · ${stats.literalsFolded} folds · ${stats.deadLocalsRemoved} dead locals · ${stats.variablesRenamed} renames`, inline: false },
      ...(stats.warnings.length ? [{ name: "Warnings", value: stats.warnings.slice(0, 3).map((w) => `• ${w}`).join("\n").slice(0, 1000) }] : []),
    ],
    footer: { text: link ? "Open the link for a side-by-side view" : "Luast Deobfuscator Engine" },
    timestamp: new Date().toISOString(),
  };
}

async function bumpGuild(guildId?: string) {
  if (!guildId) return;
  try {
    await db
      .insert(discordGuilds)
      .values({ guildId, totalJobs: 1 })
      .onConflictDoUpdate({
        target: discordGuilds.guildId,
        set: { totalJobs: sql`${discordGuilds.totalJobs} + 1`, lastSeenAt: new Date() },
      });
  } catch {
    // non-fatal
  }
}

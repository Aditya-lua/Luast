import {
  integer,
  jsonb,
  pgTable,
  serial,
  text,
  timestamp,
  varchar,
} from "drizzle-orm/pg-core";

export type JobStats = {
  obfuscator: string | null;
  obfuscatorVersion: string | null;
  constantsResolved: number;
  constantTableSize: number;
  functionsHoisted: number;
  aliasesRemoved: number;
  swapsApplied: number;
  dispatchersFound: number;
  dispatchersUnflattened: number;
  statesRecovered: number;
  arithmeticBranchesDecoded: number;
  literalsFolded: number;
  deadLocalsRemoved: number;
  variablesRenamed: number;
  stringsDecoded: number;
  numbersNormalized: number;
  warnings: string[];
  confidence: number;
  durationMs: number;
};

export const deobfJobs = pgTable("deobf_jobs", {
  id: serial("id").primaryKey(),
  publicId: varchar("public_id", { length: 32 }).notNull().unique(),
  source: varchar("source", { length: 16 }).notNull(), // web | discord | api
  requester: varchar("requester", { length: 128 }),
  title: varchar("title", { length: 200 }),
  inputHash: varchar("input_hash", { length: 64 }).notNull(),
  inputSize: integer("input_size").notNull(),
  outputSize: integer("output_size").notNull(),
  obfuscator: varchar("obfuscator", { length: 64 }),
  input: text("input").notNull(),
  output: text("output").notNull(),
  stats: jsonb("stats").$type<JobStats>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
});

export const discordGuilds = pgTable("discord_guilds", {
  id: serial("id").primaryKey(),
  guildId: varchar("guild_id", { length: 32 }).notNull().unique(),
  guildName: varchar("guild_name", { length: 128 }),
  totalJobs: integer("total_jobs").default(0).notNull(),
  lastSeenAt: timestamp("last_seen_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
});

export type DeobfJob = typeof deobfJobs.$inferSelect;

/**
 * CLI runner: npx tsx scripts/run-deobf.ts <input.lua> [output.lua]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { deobfuscate } from "../src/lib/deobf/index";

const file = process.argv[2];
if (!file) {
  console.error("usage: npx tsx scripts/run-deobf.ts <input.lua> [output.lua]");
  process.exit(1);
}
const src = readFileSync(file, "utf8");
const res = deobfuscate(src);
const out = process.argv[3] ?? file.replace(/\.lua$/i, "") + ".deobf.lua";
writeFileSync(out, res.output);
console.log(JSON.stringify(res.stats, null, 2));
console.log(`wrote ${out} (${res.output.length} bytes)`);

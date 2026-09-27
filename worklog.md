# Worklog

---
Task ID: 1
Agent: Super Z (main)
Task: Download LUAST project from Google Drive, analyze, and improve the deobfuscator for the LUAST v1.0.1 obfuscator.

Work Log:
- Installed gdown into venv, downloaded `LUAST 1.0.1.zip` (41.7 MB) from the Drive folder, extracted.
- Explored project: `SAMPLES/` (~374 obfuscated Roblox Luau scripts, 319 with luast header), `Tool/` (deobfuscator: parser/lexer/passes/controlflow/pipeline), `ROBLOX_ENV/` (ARM binaries - replaced with x86_64 luau/luau-ast from GitHub releases for native validation), `DEOBFUSCATED/` (prior outputs + JSONL reports).
- Analyzed prior reports: squirrelescape.luau only recovered 6/111 dispatchers vs ps2.luau 648. Instrumented DispatcherPass: 103 dispatchers bailed with "state in payload assignment" (obfuscator threads state var into payload/seed computations), 2 with "state environment conflict".
- Also identified: `while false`/junk fornum loops survive, dead pool fills + memoization junk guards (`if not X then X = {pool,...} end` inside decoder closures) pin pools alive.
- Found second obfuscator in samples: MoonVeil v1.4.5 (anime-rng.lua) - not handled at all (future work).

Improvements implemented in `Tool/luau_recover/`:
- controlflow.py: (1) state-value substitution into payload statements instead of bailing (closures still bail); (2) env-conflict merging with monotone requeue instead of DispatcherBail; (3) bool tautology resolution (x or not x etc.) in eval_data; (4) None-leaf ifexpr edges; (5) raised limits (states 4096, paths 96, work 400k) + per-dispatcher emit budget (40k statements) to prevent output explosion; (6) recursion headroom.
- passes.py: (1) bool_const/neg_equal structural tautology helpers wired into evaluate(); (2) while-false removal + repeat-until-false unwrap in fold_constants; (3) fornum dead-iteration pruning (proves loop never runs or all body branches false per-iteration); (4) remove_unused_locals: multi-name locals, dead name-assigns, self-referential dead stores, empty shells, flow-sensitive self_dead_guard, memo-guard junk store removal (memo_guard_bindings), recursion into closures nested in expressions; (5) new remove_dead_pool_writes pass: per-key dead pool write elimination with same-statement read exemption, fully-dead pool removal, alias chains; (6) parent_map_of helper.
- pipeline.py: added pool-cleanup/dead-code-2/fold-final/dead-code-3 phases; removed phases[:max_iterations] truncation bug.
- Verified: test suite 7/7 passed after every change.

Stage Summary:
- squirrelescape.luau: 111/111 dispatchers recovered (was 6), output 3.25 MB -> 244 KB (13x reduction), luau-ast validation PASSED.
- Deliverable (single file per user request): /home/z/my-project/download/squirrelescape_DEOBFUSCATED.luau
- Improved tool lives at /home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool/ (original zip intact as backup).
- Known remaining work: MoonVeil obfuscator support; second pool (euH) retained due to dynamic-key reads; decoder-call static evaluation could be extended.

---
Task ID: 3
Agent: Super Z (main)
Task: Fix everything in the deobfuscator, delete stored sample scripts (fetch live via raw GitHub only), and deobfuscate the user-pasted LUAST v1.0.1 level-3 script.

Work Log:
- Re-verified the remove_unused_locals dead-store fix with minimal repros (loop counters / state vars preserved; genuinely dead locals still removed). Test suite 7/7 green.
- SAMPLES (374 obfuscated scripts) confirmed deleted; also deleted 19MB download/deobf_results cache + scratch/github_live (2.6MB) so no sample scripts remain in storage.
- Built a LUAST-L3 static emulator (scripts/lua_rt.py + scripts/luast3_deobf.py, integrated into the tool as luau_recover/lua_rt.py + luau_recover/luast_l3.py): a mini-Luau interpreter with Roblox shims (bit32, buffer, string, math, table, debug.info with real Luau builtin semantics verified against the ROBLOX_ENV luau binary, Enum, Font, Vector2int16, HttpService:JSONDecode, game:GetService, pcall, typeof, ...).
- It statically executes the whole obfuscated chunk: pool literal -> runtime permutation multi-assigns, 64-state dispatcher (init 67, K=13638), opaque predicates (Z==Z, table.isfrozen on frozen library tables - verified frozen=true on real luau, string.rep == ""["rep"], debug.info line/name-length tricks), Font/Enum weighted Z contributions, and the LCG+bit32 keystream payload decoder.
- Cross-validated end-to-end against the real Luau VM (ROBLOX_ENV/luau + pure-Lua Roblox prelude): interpreter reproduces the VM bit-exactly.
- Residual Roblox-shim drift (-1,259,923 on Z vs real Roblox) compensated by a constrained seed-recovery fallback: printable-plaintext filter (numpy scan over 95^4 first-word candidates) + inversion of the round function (add/xorshift/rotate chain) + LCG inversion -> true aq0/ak/Z. Candidate ranking by Z-drift plausibility (|dZ| <= 50M) + dictionary coverage.
- RESULT: payload "CONGRATS AGENT!" recovered (Z_true = 54497205, ae=13593, cipher = 15-byte pool string, LCG 16807/2^31-1). Deliverable: download/luast_l3_DEOBFUSCATED.luau = print("CONGRATS AGENT!") + full trace report download/luast_l3_DEOBFUSCATED.l3report.txt.
- cli.py: new --fetch NAME (live fetch from https://raw.githubusercontent.com/joustingmatch/Ouroboros/main/games/<name>), auto-detection of luast-L3 inputs routing to the emulator (ok-l3 status, graceful fallback), -o now applies to fetched files. Live-tested: fetch of a real repo script works.
- Known open item: exact Roblox-side source of the -1.26M Z drift (Vector2int16 int16-wrap and library-freeze semantics verified correct; drift absorbed by the solver regardless).

Stage Summary:
- Level-3 LUAST is now deobfuscatable end-to-end; user's script yields print("CONGRATS AGENT!").
- Tool entry: Tool/deobfuscate.py [input|--fetch NAME] [-o out]; test suite 7/7.

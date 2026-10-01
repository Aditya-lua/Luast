# LUAST Deobfuscator

Deobfuscator for LUAST v1.0.1 obfuscated Roblox Luau scripts, including
**level-3** (state-machine + encrypted payload) support via a static Luau
emulator (`luau_recover/lua_rt.py` + `luau_recover/luast_l3.py`).

## Features

- Full LUAST v1.0.1 control-flow recovery: flattened dispatcher reconstruction,
  opaque-predicate resolution, junk/dead-code elimination, pool cleanup
- Level-3 support: statically executes the obfuscated state machine
  (pool permutation, xxHash-style avalanche + LCG keystream payload decoder)
  and recovers the original payload — including a constrained seed-recovery
  solver when Roblox-shim drift affects runtime accumulators
- Live fetch mode: pulls scripts straight from the Ouroboros raw GitHub repo
- Optional syntax/VM validation against the real Luau binaries


## What's New (v1.1)

- **Nested constant pools**: resolves `pool[slot][key]` reads, local aliases of sub-tables
  (`local a = pool[slot]`), and in-place writes through aliases via copy-on-write overlays.
  The pool pass iterates to a fixpoint so slot-to-slot copy chains resolve transitively.
- **Phi symbolic values**: opaque junk predicates built on `if <unknown> then k1 else k2`
  arithmetic collapse to constants during dispatcher recovery instead of forking the search
  exponentially (`phi_apply`/`phi_binop` component-wise evaluation with collapse-on-equal).
- **Path-condition memory**: dispatcher recovery records branch decisions in the path
  environment, so re-tests of the same binding (`if X then A else if X then B`) prune dead
  subtrees instead of duplicating them.
- **Emission budgets**: per-dispatcher graph and emitted-node caps keep DAG-shaped state
  graphs from tree-expanding into gigabyte outputs; oversized dispatchers keep their
  compact original form.
- **Lua-exact semantics**: float `%` uses Lua floor-modulo (not C fmod) so junk arithmetic
  evaluates bit-identically.
- **Huge-input mode hardened**: limited parent maps, lean tree walks, and chain-carrying
  analysis walks cut peak RSS ~35% (a 4.1 MB / 21k-entry-pool script now processes in ~3 GB).

## Requirements

- Python 3.10+ (stdlib only for the core path; `numpy` optional, used by the
  level-3 seed-recovery fallback for faster scanning)
- No other dependencies

## Usage

```bash
# clone
git clone https://github.com/Aditya-lua/Luast.git
cd Luast

# deobfuscate a local file
python deobfuscate.py path/to/obfuscated.lua -o out.luau

# fetch live from the Ouroboros raw GitHub repo and deobfuscate
python deobfuscate.py --fetch <script-name> -o out.luau

# batch mode
python -m luau_recover.batch --help

# run the regression tests
python tests/test_recovery.py
```

Inputs with a luast level-3 header are auto-routed to the L3 static
emulator (status `ok-l3`); regular LUAST v1.0.1 scripts go through the
parser/control-flow/pipeline recovery passes. If no `-o` is given, results
are written next to the input / into `DEOBFUSCATED/`.

## Layout

- `deobfuscate.py` — CLI entry point
- `luau_recover/` — parser, control-flow recovery, cleanup passes, L3 emulator, CLI
- `tests/` — regression tests

## Validation (optional)

Drop the official `luau` / `luau-ast` binaries (from the
[Luau releases](https://github.com/luau-lang/luau/releases)) into a
`ROBLOX_ENV/` folder to enable automatic syntax/VM validation of outputs.

## Credits

- **@adi.codz** on Discord


## Support / Donate

<div align="center">
  <img src="assets/donate/donate_header.png" width="520" alt="Buy me a coffee — fund future projects">
</div>

If these tools are useful to you, you can help fund future projects by sending
crypto to the addresses below. Thank you for the support. — **@adi.codz** (Discord)

| Network | Address | Scan |
|---|---|---|
| **Bitcoin** (BTC) | `bc1q3nprh6e0y4fz88ft4uu5dad7xsx629z498dkay` | <img src="assets/donate/btc.png" width="120" alt="Bitcoin QR"> |
| **Ethereum** (ERC-20) | `0x11369a1d18eb442581D1e675dAdD94eBA1c4bE52` | <img src="assets/donate/eth.png" width="120" alt="Ethereum QR"> |
| **BNB Smart Chain** (BEP-20) | `0x11369a1d18eb442581D1e675dAdD94eBA1c4bE52` | <img src="assets/donate/bnb.png" width="120" alt="BNB Smart Chain QR"> |
| **Solana** (SOL) | `AVKBDNT2PzYDJ7njVrjt7MoVZ1tY4DvNSsADdujiGKTC` | <img src="assets/donate/sol.png" width="120" alt="Solana QR"> |
| **Litecoin** (LTC) | `ltc1qu4ahsjff622xqjlqwafhlgmacd5ea477sqpx9z` | <img src="assets/donate/ltc.png" width="120" alt="Litecoin QR"> |

> Send each asset only on its own network. ETH and BNB share one EVM address —
> use it for ERC-20 on Ethereum and BEP-20 on BNB Smart Chain. Assets sent on the
> wrong network may be lost.

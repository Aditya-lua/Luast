# Fake Roblox environment (standalone)

The environment a protected Roblox script runs against when you want to *watch*
what it does instead of statically reading it: a fake `game`, `workspace`,
`Instance`, the service tree, Vector3/UDim2/CFrame/Color3, signals, task
spawning, the executor globals, Path2D and Random. Every call is recorded and
rendered back as readable Luau.

It is the same environment the deobfuscator uses, extracted so it can be run on
its own.

## Files

| file | what it is |
|---|---|
| `envlog.luau` | the environment runtime (4,710 lines) |
| `roblox_api.luau` | generated from the Roblox API dump: enums, class tree, member types |
| `datatypes.luau` | CFrame, Vector2, UDim/UDim2, Rect, Ray, Color3, NumberSequence, ... models |
| `unicode_data.luau` | normalization/case tables |
| `build_env.py` | assembles the parts + your script into one `harness.luau` |
| `extract_trace.py` | pulls the readable trace out of a harness run |
| `run.sh` | build + run + extract, in one command |
| `luau` | the patched Luau 0.739 interpreter (aarch64 Linux) |
| `luau-ast` | same build, prints a file's AST as JSON (used for parse checks) |
| `example.lua` | smoke test: touches most of the API |

`harness.luau` is generated, not checked in.

## Use

```sh
chmod +x run.sh luau          # once
./run.sh ../SAMPLES/myscript.lua
```

**On Android:** this folder lives on the sdcard, and Android's FUSE mount does
not allow executing files from it (`Permission denied` on `./luau`). Copy the
folder to real storage first, then run it there:

```sh
cp -r "/storage/emulated/0/ROBLOX PROJECTS/LUAST 1.0.1/ROBLOX_ENV" ~/luast_env
cd ~/luast_env && chmod +x run.sh luau luau-ast && ./run.sh example.lua
```

Options are passed through as `key=value` pairs:

```sh
./run.sh script.lua trace=false max_stmts=50000
./run.sh script.lua "prelude=rawset(G,'key','abc')"
./run.sh script.lua falsy=isexploitclosure
```

The output is the reconstructed behaviour: the calls the script made, in
order, with the values it passed. Branches that were not taken are missing
(conditions only show up in comments), because this is what the script *did*,
not what it could do.

## The pieces it provides

* `game`, `workspace`, `Players.LocalPlayer`, and the service tree by name
* `Instance.new`, parenting, `FindFirstChild`/`WaitForChild`, `Clone`,
  `Destroy`, `IsA`, `AncestryChanged`/`ChildAdded`/… signals, `GetDescendants`
* datatypes with engine-accurate values: `Vector3` (including `Magnitude`,
  `Unit`, `Dot`, `Lerp`), `CFrame`, `UDim2`, `Rect`, `Ray`, `Color3`,
  `NumberSequence`, `Path2D`
* `task.spawn`/`wait`/`delay`, `wait`, `spawn`, `delay`, `tick`, `Random`
  (Roblox's PCG32 generator, bit-exact), `Enum.*`, `os.clock`-based time
* executor globals: `getgenv`, `_G`, `shared`, `getrenv`, `request`/`http_request`,
  `writefile`/`readfile`, `setclipboard`, `hookfunction`/`hookmetamethod`
  (reported as *not* hooked, which is what scripts test for)
* `game:HttpGet` is recorded, never followed: remote code is not fetched

## Limits worth knowing

* It is a model, not the engine. Anything that depends on real physics,
  rendering or a live server is a stand-in.
* A script that gates on a key, a whitelist or the game id can be pushed
  further with `prelude=...` (`G`, `genv`, `shared`, `game`, `env`,
  `setprop(proxy, key, value)`) — but the environment cannot invent a key.
* Scripts that spin in a pure-Luau loop never come back to the runtime, so
  the trace stops; that is a property of the script, not a crash.
* Offsets/values that scripts use to fingerprint libraries (for example
  `math.random` internals) are modelled, but a script built for a different
  VM build may still bail out early.

## Rebuilding the interpreter

`luau` is Luau 0.739 with one patch (the vector metatable is left writable so
the engine's `Vector3` members can be installed on it). Rebuild it with
`build_luau.py` from the deobfuscator, or drop any Luau 0.739 build in here;
a stock build works but silently skips the `Vector3` members.

#!/bin/bash
# Re-fetch fresh obfuscated samples from luast.clv.cloud
set -e
OUT=/home/z/my-project/scratch/work
mkdir -p "$OUT"

cat > "$OUT/input_test.lua" <<'EOF'
local function fib(n)
  if n < 2 then return n end
  return fib(n - 1) + fib(n - 2)
end

local function encode(u)
  local out = {}
  for i = 1, #u do
    out[i] = string.char(bit32.bxor(string.byte(u, i), 42))
  end
  return table.concat(out)
end

local function clamp(x, lo, hi)
  return math.floor(math.max(lo, math.min(hi, x)))
end

print("fib(12) =", fib(12))
print("check:", encode("LuauRocks"))
print("clamp:", clamp(17.6, 3, 10))

local words = {"alpha", "beta", "gamma", "delta"}
for i, w in ipairs(words) do
  print(string.format("%d:%s(%d)", i, w:upper(), #w))
end

local sum = 0
for i = 1, 10 do
  sum += i * 2
  if sum % 7 == 0 then
    sum = sum + 1
  elseif sum > 80 then
    break
  end
end
print("sum:", sum)

local co = coroutine.wrap(function(a, b)
  local p = a * b
  coroutine.yield(p)
  print("pcall:", pcall(function() error("boom") end))
  return p + 100
end)
print("coroutine:", co(6, 7))
print("coroutine:", co())

local config = { name = "flagPlayer", id = 65521, debug = true }
print("config:", config.name, config.id, config.debug)
print("pattern:", string.match("key=value", "(%w+)=(%w+)"))
print("select:", select("#", 1, 2, 3), select(2, "a", "b", "c"))
print("reverse:", ("rsa"):reverse(), ("#"):rep(3))
EOF

for preset in level1 level2 level3; do
  for style in compact pretty single; do
    name="${preset}_${style}"
    echo "== $name =="
    python3 - "$preset" "$style" <<'PYEOF'
import json, subprocess, sys, time
preset, style = sys.argv[1], sys.argv[2]
code = open("/home/z/my-project/scratch/work/input_test.lua").read()
body = json.dumps({"code": code, "preset": preset, "outputStyle": style})
t0 = time.time()
r = subprocess.run(["curl", "-s", "-X", "POST", "https://luast.clv.cloud/api/v1/obfuscate",
  "-H", "Authorization: Bearer 0176c141449fcc32ea220e1146601d42",
  "-H", "Content-Type: application/json", "-d", body, "-o",
  f"/home/z/my-project/scratch/work/resp_{preset}_{style}.json", "-w", "%{http_code}"],
  capture_output=True, text=True, timeout=120)
http = r.stdout.strip()
try:
    j = json.load(open(f"/home/z/my-project/scratch/work/resp_{preset}_{style}.json"))
    res = j.get("result") or ""
    open(f"/home/z/my-project/scratch/work/obf_{preset}_{style}.luau", "w").write(res)
    print(f"  http={http} bytes={len(res)} dt={time.time()-t0:.2f}s err={j.get('error','-')}")
except Exception as e:
    print(f"  http={http} FAIL {e}")
PYEOF
  done
done
ls -la "$OUT" | grep obf_
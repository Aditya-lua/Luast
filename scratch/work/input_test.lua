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

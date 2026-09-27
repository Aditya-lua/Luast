#!/usr/bin/env python3
"""Debug why pool reads like d6[139] are not resolved by PoolResolver."""
import sys

TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)

from luau_recover.parser import parse
from luau_recover.emitter import emit
from luau_recover.analysis import analyze
from luau_recover.passes import PoolResolver, PassStats, UNKNOWN

# Minimal reproduction of the chickenfarm pattern
SRC = """local d5;
local d6;
d5 = {10, 20, "str", 1, 3, 8, 2, function() end, nil, 40};
local function foo(dq)
    local dn = nil;
    d6 = d5;
    dn = d6[139];
    while true do
        if dn < 4 then
            if dn < 1 then
                print(d6[125]);
            else
                dn = d6[282];
            end
        elseif dn < 6 then
            break;
        end
    end
end
foo(1);
"""

root, errors, _ = parse(SRC)
print("parse errors:", errors)
an = analyze(root)
stats = PassStats()
resolver = PoolResolver(root, an, SRC, allow_escape=True)
print("roots:", {k: type(v).__name__ for k, v in resolver.roots.items()})
print("aliases:", resolver.aliases)
print("safe:", resolver.safe)
print("num reads:", len(resolver.reads))
for read in resolver.reads[:10]:
    print("  read:", emit(read, SRC).strip(), "start:", read.start)
resolver.build_replacements(stats)
print("pool_reads stat:", stats.pool_reads, "pool_values:", stats.pool_values)
print("value_nodes:", len(resolver.value_nodes))
print("replacements:", len(resolver.replacements))

# apply manually
root2 = resolver.apply(root, stats)
out = emit(root2, SRC)
print("---- after pool resolve ----")
print(out)

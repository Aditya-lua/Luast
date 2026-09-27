#!/usr/bin/env python3
"""Debug PoolResolver against the real chickenfarm pretty source."""
import sys

TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)

from luau_recover.parser import parse
from luau_recover.emitter import emit
from luau_recover.analysis import analyze
from luau_recover.passes import PoolResolver, PassStats, UNKNOWN

SRC = open("/home/z/my-project/scratch/chickenfarm_pretty.luau", encoding="utf-8", errors="surrogateescape").read()
root, errors, _ = parse(SRC)
print("parse errors:", errors)
an = analyze(root)
stats = PassStats()
resolver = PoolResolver(root, an, SRC, allow_escape=True)
print("roots:", len(resolver.roots), "aliases:", len(resolver.aliases), "safe:", resolver.safe)
print("reads:", len(resolver.reads), "events:", len(resolver.events))

# find the mini-dispatcher reads: d6[139] etc
probe = [r for r in resolver.reads if "139" in emit(r, SRC)]
print("reads of [139]:", len(probe))

resolver.build_replacements(stats)
print("pool_reads:", stats.pool_reads, "pool_values:", stats.pool_values)
print("value_nodes:", len(resolver.value_nodes), "replacements:", len(resolver.replacements))

# what did d6[139] resolve to?
for r in probe[:3]:
    resolved = resolver.value_nodes.get(id(r))
    print("read at", r.start, "->", repr(emit(resolved, SRC)[:60]) if resolved is not None else None)

root2 = resolver.apply(root, stats)
out = emit(root2, SRC)
open("/home/z/my-project/scratch/chickenfarm_pool.luau", "w", encoding="utf-8", errors="surrogateescape").write(out)
# check the mini dispatcher area in output
import re
idx = out.find("dn = ")
print("---- around first 'dn =' ----")
print(out[max(0, idx-200):idx+400] if idx >= 0 else "no 'dn =' found")

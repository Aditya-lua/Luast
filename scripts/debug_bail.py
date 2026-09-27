#!/usr/bin/env python3
"""Trace DispatcherPass bail reasons on chickenfarm."""
import sys

TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)

from luau_recover.parser import parse
from luau_recover.analysis import analyze
from luau_recover.passes import PoolResolver, PassStats, fold_constants, propagate_locals
from luau_recover.controlflow import DispatcherPass, DispatcherBail
from luau_recover.emitter import emit
from collections import Counter

src = open("/home/z/my-project/scratch/chickenfarm_pretty.luau", encoding="utf-8", errors="surrogateescape").read()
root, errors, _ = parse(src)
an = analyze(root)
stats = PassStats()
PoolResolver(root, an, src, allow_escape=True).apply(root, stats)
fold_constants(root, an, stats)
propagate_locals(root, an, stats)

pass_ = DispatcherPass(root, an, src, stats)
reasons = Counter()
recognized = 0
not_recognized = 0

import luau_recover.controlflow as cf

orig_recognize = DispatcherPass.recognize
def spy_recognize(self, statements, index, loop):
    global recognized, not_recognized
    result = orig_recognize(self, statements, index, loop)
    if result is not None:
        recognized += 1
    else:
        not_recognized += 1
    return result
DispatcherPass.recognize = spy_recognize

orig_recover = DispatcherPass.recover
def spy_recover(self, dispatcher):
    try:
        return orig_recover(self, dispatcher)
    except DispatcherBail as exc:
        reasons[str(exc)] += 1
        raise
DispatcherPass.recover = spy_recover

pass_.apply(rounds=2)
print("recognized:", recognized, "not recognized:", not_recognized)
print("bail reasons:", dict(reasons))
print("dispatchers removed:", stats.dispatchers_removed)

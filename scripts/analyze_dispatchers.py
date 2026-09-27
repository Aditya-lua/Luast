#!/usr/bin/env python3
"""Instrument DispatcherPass to find why dispatchers bail on squirrelescape."""
import sys
from pathlib import Path

TOOL = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
sys.path.insert(0, str(TOOL))

from luau_recover.analysis import analyze
from luau_recover.controlflow import DispatcherPass, DispatcherBail
from luau_recover.parser import parse
from luau_recover.passes import PassStats

src = Path(sys.argv[1]).read_text(encoding="utf-8", errors="surrogateescape")
root, errors, _ = parse(src)
print("parse errors:", len(errors))
analyzer = analyze(root)
stats = PassStats()
p = DispatcherPass(root, analyzer, src, stats)

# find candidates the way apply() does
lists = p.statement_lists(root)
print("statement lists:", len(lists))

candidates = []
for statements in lists:
    for index, statement in enumerate(statements):
        if statement.kind != "while":
            continue
        cand = p.recognize(statements, index, statement)
        if cand is not None:
            candidates.append(cand)
print("recognized candidates:", len(candidates))

from collections import Counter
bails = Counter()
success = 0
for cand in candidates[:400]:
    p.work = 0
    try:
        rep = p.recover(cand)
        success += 1
    except DispatcherBail as exc:
        bails[str(exc)] += 1
    except Exception as exc:
        bails[f"EXC {type(exc).__name__}: {str(exc)[:80]}"] += 1
print("success:", success)
for reason, n in bails.most_common(15):
    print(f"  {n:5}  {reason}")

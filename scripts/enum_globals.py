#!/usr/bin/env python3
"""Enumerate every global name read across all samples vs emulator globals."""
import sys, glob
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
from luau_recover.parser import parse
from luau_recover.model import Node
from luau_recover.luast_l3 import Interp

interp = Interp()
known = set(interp.globals_env.vars.keys())

unk = {}
for path in sorted(glob.glob("/home/z/my-project/scratch/luast_web/obf_level*.lua")) + \
            ["/home/z/my-project/scratch/luast_l3_sample.luau"]:
    src = open(path, encoding="utf-8", errors="replace").read()
    root, errs, _ = parse(src)

    reads, writes = set(), set()

    def walk(node, reads_acc, writes_acc):
        if isinstance(node, Node):
            if node.kind == "name":
                reads_acc.add(node.get("name"))
            if node.kind == "assign":
                for t in node.get("targets", []):
                    if t.kind == "name":
                        writes_acc.add(t.get("name"))
            for v in node.fields.values():
                walk(v, reads_acc, writes_acc)
        elif isinstance(node, list):
            for v in node:
                walk(v, reads_acc, writes_acc)

    walk(root, reads, writes)
    for n in sorted(reads):
        if n not in known:
            unk.setdefault(n, set()).add(path.rsplit("/", 1)[-1])

print("UNKNOWN global reads across samples:")
for n, where in sorted(unk.items()):
    print(f"  {n}: {sorted(where)}")

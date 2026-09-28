#!/usr/bin/env python3
"""Debug why the L3 emulator executes 0 states on fresh clv.cloud samples."""
import sys, traceback
sys.path.insert(0, "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")

from luau_recover import luast_l3
from luau_recover.parser import parse

path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()

root, errs, comments = parse(src)
print("[parsed] ok, top-level stmts:", len(root.get("body", [])), "errors:", len(errs))
for i, st in enumerate(root.get("body", [])):
    print(f"  stmt[{i}] kind={st.kind}")

d = luast_l3.find_dispatcher(root)
print("[find_dispatcher] ->", None if d is None else {k: v for k, v in d.items() if k != "node"})

if d is None:
    # inspect the last statements in detail
    body = root.get("body", [])
    for st in body[-3:]:
        print("LAST STMT kind:", st.kind)
        if st.kind == "while":
            print("  cond:", st.get("cond"))
            b = st.get("body", [])
            print("  body len:", len(b))
            if b:
                print("  body[0] kind:", b[0].kind)
                print("  body[0]:", str(b[0])[:400])

# try running the emulation with error surface
try:
    out, report = luast_l3.deobfuscate_l3(src, input_path=path, report_path=None)
    print("[emulate] ok, out bytes:", len(out))
except Exception as e:
    traceback.print_exc()

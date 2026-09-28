#!/usr/bin/env python3
"""Instrument obfuscated source to print (state, Z) at each dispatch, for
ground-truth comparison between real Luau and the emulator."""
import re, sys

path = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else path + ".traced.lua"
src = open(path, encoding="utf-8", errors="replace").read()

# find the dispatcher: `while true do <st> = K - <st>;`
m = re.search(r"while true do ([A-Za-z_][A-Za-z0-9_]*) = ([0-9.]+) - \1;", src)
if not m:
    print("dispatcher not found")
    sys.exit(1)
stvar = m.group(1)
print("state var:", stvar)

# find the Z accumulator: statement like `<z> += ...` inside the dispatcher body
# search after the while loop start
tail = src[m.end():m.end() + 200000]
zm = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*) \+= ", tail)
if not zm:
    print("Z accumulator not found")
    sys.exit(1)
zvar = zm.group(1)
print("Z var:", zvar)

# insert trace right after `while true do <st> = K - <st>;`
insert_at = m.end()
# capture the value of Z AFTER the update; the dispatch reads at loop top.
# We print (new state, current Z) at each loop top: state BEFORE update = old.
trace = 'print("TRACE", %s, %s); ' % (stvar, zvar)
src2 = src[:insert_at] + trace + src[insert_at:]
open(out, "w", encoding="utf-8").write(src2)
print("wrote", out)

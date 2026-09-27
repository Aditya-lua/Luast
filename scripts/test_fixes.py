#!/usr/bin/env python3
"""Tests for junk-predicate folding + style-B dispatcher recovery."""
import sys

TOOL = "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool"
sys.path.insert(0, TOOL)

from luau_recover.parser import parse
from luau_recover.emitter import emit
from luau_recover.analysis import analyze
from luau_recover.passes import PoolResolver, PassStats, fold_constants, propagate_locals
from luau_recover.controlflow import DispatcherPass
from luau_recover.model import walk


def run_pipeline(src: str, name: str) -> str:
    root, errors, _ = parse(src)
    assert not errors, f"{name}: parse errors: {errors}"
    stats = PassStats()
    an = analyze(root)
    PoolResolver(root, an, src, allow_escape=True).apply(root, stats)
    fold_constants(root, an, stats)
    propagate_locals(root, an, stats)
    DispatcherPass(root, an, src, stats).apply(rounds=8)
    fold_constants(root, an, stats)
    out = emit(root, src)
    # re-parse sanity
    root2, errors2, _ = parse(out)
    assert not errors2, f"{name}: output parse errors: {errors2}\n{out}"
    return out


def count_kind(out: str, needle: str) -> int:
    return out.count(needle)


# --- T1: style B mini dispatcher (chickenfarm shape) -------------------------
T1 = """local pool = {2, 1, 4, 0, 3};
local flag = game.Flag;
local dn = nil;
dn = pool[4];
while true do
    if dn < 4 then
        if dn < 1 then
            print("zero");
            dn = if flag then pool[1] else pool[2];
        elseif dn < 3 then
            print("onetwo");
            dn = pool[3];
        else
            print("three");
            dn = pool[2];
        end
    elseif dn < 6 then
        break;
    end
end
"""
out = run_pipeline(T1, "T1")
assert "while true do" not in out, f"T1 dispatcher still present:\n{out}"
assert 'print("zero")' in out and 'print("onetwo")' in out, f"T1 payload lost:\n{out}"
print("T1 style-B dispatcher recovered:")
print(out)

# --- T2: style A driver with self-compare opaque predicate -------------------
T2 = """local q6 = nil;
local s = 1;
while true do
    s = 11818 - s;
    do
        if s < 11816 then
            if s < 10483 then
                break;
            elseif s == 11815 then
                print("payload");
                s = 0.;
            else
                s = 11800;
                continue;
            end
        elseif s < 13525 then
            if s < 11817 then
                if s == 11816 then
                    return;
                else
                    s = 4273;
                    continue;
                end
            elseif s < 11818 then
                if s == 11817 then
                    print("main");
                    s = if q6 == q6 then 2 else 3.;
                else
                    s = 5988;
                    continue;
                end
            else
                break;
            end
        else
            break;
        end
    end
end
"""
out = run_pipeline(T2, "T2")
assert "11818" not in out and "11800" not in out, f"T2 dispatcher remains:\n{out}"
assert 'print("main")' in out and "return" in out, f"T2 flow wrong:\n{out}"
print("T2 driver with self-compare folded:")
print(out)

# --- T3: vector identity opaque predicate ------------------------------------
T3 = """local a = game.A; local b = game.B; local c = game.C;
local s = 1;
while true do
    s = 1000 - s;
    do
        if s == 999 then
            print("A");
            s = if vector.dot(vector.cross(a, b), c) == vector.dot(vector.cross(b, c), a) then 260 else 134;
        elseif s == 740 then
            print("JUNK");
            s = 0;
        elseif s == 866 then
            print("B");
            s = 0;
        else
            break;
        end
    end
end
"""
out = run_pipeline(T3, "T3")
assert 'print("JUNK")' not in out, f"T3 junk branch kept:\n{out}"
assert 'print("A")' in out and 'print("B")' in out, f"T3 flow wrong:\n{out}"
print("T3 vector identity folded (junk branch removed):")
print(out)

# --- T4: angle gap predicate (|K| > pi) --------------------------------------
T4 = """local p = game.P; local q = game.Q; local r = game.R;
local s = 1;
while true do
    s = 1000 - s;
    do
        if s == 999 then
            print("A");
            s = if math.abs((vector.angle(p, q, r))) - math.abs((vector.angle(q, p, r))) == 4.0 then 3. else 158;
        elseif s == 842 then
            print("B");
            s = 0;
        elseif s == 997 then
            print("JUNK");
            s = 0;
        else
            break;
        end
    end
end
"""
out = run_pipeline(T4, "T4")
assert 'print("JUNK")' not in out, f"T4 junk branch kept:\n{out}"
assert 'print("A")' in out and 'print("B")' in out, f"T4 flow wrong:\n{out}"
print("T4 angle-gap folded:")
print(out)

# --- T5: loop increment survives full pipeline --------------------------------
T5 = """local n = 3
local i = 0
local acc = 0
while i < n do
    acc = acc + i
    i = i + 1
end
print(acc)
"""
out = run_pipeline(T5, "T5")
assert "i=i+1" in out.replace(" ", ""), f"T5 increment lost:\n{out}"
print("T5 loop increment survives pipeline")

# --- T6: string doubling predicate --------------------------------------------
T6 = """local s = game.Name;
local s2 = 1;
while true do
    s2 = 500 - s2;
    do
        if s2 == 499 then
            print("A");
            s2 = if s:len() >= s:gsub("(.)", "%1%1", 2):len() then 222 else 111;
        elseif s2 == 389 then
            print("JUNK");
            s2 = 0;
        elseif s2 == 278 then
            print("B");
            s2 = 0;
        else
            break;
        end
    end
end
"""
out = run_pipeline(T6, "T6")
assert 'print("JUNK")' not in out, f"T6 junk branch kept:\n{out}"
assert 'print("A")' in out and 'print("B")' in out, f"T6 flow wrong:\n{out}"
print("T6 string doubling folded")
print("ALL PIPELINE TESTS PASS")

#!/usr/bin/env python3
"""Run the improved deobfuscator over key samples and summarize results."""
import json
import subprocess
import sys
import time
from pathlib import Path

TOOL = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
SAMPLES = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/SAMPLES")
OUT = Path("/home/z/my-project/download/deobf_out")
ROBLOX_ENV = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/ROBLOX_ENV")

files = sys.argv[1:] or [
    "squirrelescape.luau",
    "ps2.luau",
    "anime-rng-defense.lua",
    "CollectTheAlphabet.luau",
    "Karinderya.luau",
    "SpinjitsuEscape.lua",
]

OUT.mkdir(parents=True, exist_ok=True)
for name in files:
    src = SAMPLES / name
    dst = OUT / name
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(TOOL / "deobfuscate.py"), str(src), "-o", str(dst), "--report"],
        capture_output=True, text=True, timeout=1800,
    )
    elapsed = time.monotonic() - started
    status = "ok"
    report = None
    try:
        # stdout contains a human status line followed by the JSON report
        text = proc.stdout
        start = text.find("[")
        if start < 0:
            start = text.find("{")
        payload = json.loads(text[start:]) if start >= 0 else []
        report = payload[0] if isinstance(payload, list) else payload
        status = "ok" if report.get("valid_output") else "fallback"
    except Exception as exc:
        status = f"parse-err ({exc}; stderr: {proc.stderr[-150:]})"
    line = f"{name:34} {status:9} {elapsed:6.1f}s"
    if report:
        s = report.get("stats") or {}
        line += (f" in={report.get('input_bytes', 0):>8} out={report.get('output_bytes', 0):>8}"
                 f" dsp={s.get('dispatchers_removed', 0)}/{s.get('dispatchers_found', 0)}"
                 f" states={s.get('states_recovered', 0)} fold={s.get('folded', 0)}"
                 f" br={s.get('branches_removed', 0)} dead={s.get('dead_locals', 0)}")
    print(line, flush=True)
    # native validation
    if dst.exists():
        check = subprocess.run([str(ROBLOX_ENV / "luau-ast"), str(dst)], capture_output=True, text=True, timeout=300)
        print(f"{'':34} native: {'VALID' if check.returncode == 0 else 'INVALID: ' + check.stderr[-200:]}", flush=True)

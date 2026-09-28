#!/usr/bin/env python3
"""LUAST round-trip matrix harness.

For every (preset x outputStyle x settings) combination:
  1. obfuscate the reference script via the luast.clv.cloud API
  2. deobfuscate with the LUAST tool
  3. run BOTH original and obfuscated in the real Luau VM and compare stdout
  4. validate the deobfuscated output parses
  5. record everything to a checkpointed JSON (safe to re-run for hours)

Settings grid mirrors the luast.clv.cloud UI ("the settings screenshot"):
  string_enc on/off x rate {0.25,0.5,0.75,1.0} x cache on/off
  remove_namecall on/off, preserve_call_depth on/off
"""
import itertools
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "https://luast.clv.cloud/api/v1/obfuscate"
KEY = "0176c141449fcc32ea220e1146601d42"
TOOL = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
LUAU = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/ROBLOX_ENV/luau")
WORK = Path("/home/z/my-project/scratch/matrix")
CKPT = WORK / "results.json"

INPUT = (Path("/home/z/my-project/scratch/luast_web/input_test.lua")).read_text()

PRESETS = ["level1", "level2", "level3"]
STYLES = ["pretty", "compact", "single line"]
RATES = [0.25, 0.5, 0.75, 1.0]


def configs():
    for preset, style in itertools.product(PRESETS, STYLES):
        for enc in (False, True):
            for rate in (RATES if enc else [None]):
                for cache in ((False, True) if enc else [False]):
                    for namecall in (True, False):
                        for depth in (False, True):
                            yield dict(
                                preset=preset, outputStyle=style,
                                string_encrypt=enc, rate=rate, cache=cache,
                                remove_namecall=namecall,
                                preserve_depth=depth)


def api_body(c: dict) -> dict:
    body = {"code": INPUT, "preset": c["preset"], "outputStyle": c["outputStyle"]}
    enable, disable = [], []
    options = {}
    if c["string_encrypt"]:
        enable.append("string_encrypt")
        opt = {}
        if c["rate"] is not None and c["rate"] != 1.0:
            opt["rate"] = c["rate"]
        if c["cache"]:
            opt["cache"] = True
        if opt:
            options["string_encrypt"] = opt
    else:
        disable.append("string_encrypt")
    if not c["remove_namecall"]:
        disable.append("remove_namecall")
    if c["preserve_depth"]:
        enable += ["outline_blocks", "chain_decompose"]
    if enable:
        body["enable"] = enable
    if disable:
        body["disable"] = disable
    if options:
        body["options"] = options
    return body


def api_call(body: dict, retries=3):
    data = json.dumps(body).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(API, data=data, method="POST", headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "User-Agent": "matrix-harness/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except Exception as exc:
            last = exc
            time.sleep(3 * (i + 1))
    return {"error": repr(last)}


def _prelude() -> str:
    prelude = Path("/home/z/my-project/scratch/roblox_prelude.lua").read_text()
    n = prelude.count("\n") + 1
    return prelude.replace("@@OFFSET@@", str(n))


PRELUDE = _prelude()


def run_luau(path: Path, timeout=60):
    """Run a script in real Luau with the Roblox prelude prepended."""
    try:
        combo = path.with_suffix(".combo.lua")
        combo.write_text(PRELUDE + "\n" + path.read_text(encoding="utf-8",
                                                         errors="replace"))
        r = subprocess.run([str(LUAU), str(combo)], capture_output=True,
                           timeout=timeout)
        dec = lambda b: b.decode("utf-8", "replace").strip()
        return r.returncode, dec(r.stdout), dec(r.stderr)[:300]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def deobfuscate(src_path: Path, out_path: Path):
    try:
        r = subprocess.run(
            [sys.executable, str(TOOL / "deobfuscate.py"), str(src_path),
             "-o", str(out_path)],
            capture_output=True, text=True, timeout=300, cwd=str(TOOL))
        return r.returncode == 0 and out_path.exists(), \
            (r.stdout + r.stderr)[-400:]
    except subprocess.TimeoutExpired:
        return False, "deobfuscate timeout"


def normalize(out: str) -> str:
    """Strip script-path prefixes from error messages so comparisons are
    location-independent."""
    import re
    return re.sub(r"[\w./\\-]+\.lua(?:u)?:\d+", "<loc>", out)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    results = {}
    if CKPT.exists():
        results = json.loads(CKPT.read_text())
        print(f"resuming with {len(results)} completed combos")

    # reference behaviour: original script under plain luau
    ref = WORK / "input_test.lua"
    ref.write_text(INPUT)
    rc, ref_out, ref_err = run_luau(ref)
    ref_sig = normalize(ref_out)
    print("reference output lines:", len(ref_sig.splitlines()), "rc:", rc)

    total = 0
    failures = 0
    for c in configs():
        tag = ("{preset}|{outputStyle}|enc={string_encrypt}|rate={rate}|"
               "cache={cache}|nc={remove_namecall}|depth={preserve_depth}").format(**c)
        total += 1
        if tag in results:
            continue
        entry = {"config": c}

        # 1. obfuscate
        resp = api_call(api_body(c))
        if "result" not in resp:
            entry["status"] = "api-error"
            entry["error"] = str(resp)[:300]
            results[tag] = entry
            CKPT.write_text(json.dumps(results, indent=1))
            failures += 1
            print(f"[{total}] {tag} -> API ERROR")
            continue
        obf = WORK / "obf.lua"
        obf.write_text(resp["result"])
        entry["obf_bytes"] = len(resp["result"])
        entry["seed"] = resp.get("seed")
        entry["elapsed_ms"] = resp.get("elapsed_ms")

        # 2. obfuscated behaviour in real Luau
        rc_o, out_o, err_o = run_luau(obf)
        entry["obf_rc"] = rc_o
        entry["obf_stdout"] = out_o[:400]
        entry["obf_match"] = (normalize(out_o) == ref_sig)
        if out_o != ref_sig:
            entry["obf_diff"] = {"expected": ref_sig[:200], "got": out_o[:200],
                                 "stderr": err_o}

        # 3. deobfuscate
        deob = WORK / "deobf.lua"
        ok_d, msg = deobfuscate(obf, deob)
        entry["deob_ok"] = ok_d
        if ok_d:
            entry["deob_bytes"] = deob.stat().st_size
            # 4. deobfuscated output parses (tool parser used inside) and runs
            rc_d, out_d, err_d = run_luau(deob)
            entry["deob_rc"] = rc_d
            entry["deob_stdout"] = out_d[:400]
            entry["deob_match"] = (normalize(out_d) == ref_sig)

        # 5. detector sanity (subprocess: robust dynamic-free invocation)
        try:
            r_det = subprocess.run(
                [sys.executable, str(TOOL / "detector.py"), str(obf), "--json"],
                capture_output=True, text=True, timeout=60)
            djson = json.loads(r_det.stdout)[0]
            entry["detect"] = {"is_luast": djson["is_luast"],
                               "preset": djson["preset"],
                               "style": djson["output_style"],
                               "conf": djson["confidence"]}
        except Exception as exc:
            entry["detect"] = {"error": str(exc)[:200]}

        results[tag] = entry
        CKPT.write_text(json.dumps(results, indent=1))
        status = "OK" if (entry.get("obf_match") and entry.get("deob_ok")) else "PARTIAL"
        failures += 0 if status == "OK" else 1
        print(f"[{total}] {tag} -> {status} "
              f"(obf_match={entry.get('obf_match')} deob={entry.get('deob_ok')} "
              f"deob_match={entry.get('deob_match')})")
        time.sleep(1.0)

    print(f"\nDONE: {total} combos, {failures} not-fully-verified")
    print(f"results -> {CKPT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch obfuscated samples from luast.clv.cloud for every preset/style combo."""
import json, sys, time, urllib.request, pathlib

API = "https://luast.clv.cloud/api/v1/obfuscate"
KEY = "0176c141449fcc32ea220e1146601d42"
OUT = pathlib.Path("/home/z/my-project/scratch/luast_web")
OUT.mkdir(parents=True, exist_ok=True)

def obfuscate(code: str, preset: str, style: str, extra: dict | None = None, retries: int = 3):
    body = {"code": code, "preset": preset, "outputStyle": style}
    if extra:
        body.update(extra)
    data = json.dumps(body).encode()
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(API, data=data, method="POST", headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (research harness)",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    return {"error": repr(last_err)}

def main():
    code = (OUT / "input_test.lua").read_text()
    combos = []
    for preset in ("level1", "level2", "level3"):
        for style in ("pretty", "compact", "single line"):
            combos.append((preset, style))
    results = {}
    for preset, style in combos:
        tag = f"{preset}_{style.replace(' ', '')}"
        print(f"[*] {preset} / {style} ...", flush=True)
        resp = obfuscate(code, preset, style)
        if "result" in resp:
            path = OUT / f"obf_{tag}.lua"
            path.write_text(resp["result"])
            results[tag] = {"ok": True, "bytes": len(resp["result"]),
                            "keys": [k for k in resp.keys() if k != "result"]}
            print(f"    ok -> {path} ({len(resp['result'])} bytes) meta={results[tag]['keys']}")
        else:
            results[tag] = {"ok": False, "resp": str(resp)[:300]}
            print(f"    FAIL: {str(resp)[:300]}")
        time.sleep(1.0)
    (OUT / "fetch_results.json").write_text(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()

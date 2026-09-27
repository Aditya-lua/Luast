#!/usr/bin/env python3
"""Live-fetch obfuscated game scripts from GitHub and run the deobfuscator.

No corpus is stored: scripts are downloaded fresh each run into a transient
scratch dir, processed, and only quality metrics are kept.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = "joustingmatch/Ouroboros"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}/contents/games?ref={BRANCH}"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/games"

TOOL_DIR = Path("/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/Tool")
SCRATCH = Path("/home/z/my-project/scratch/github_live")
RESULTS = Path("/home/z/my-project/download/deobf_results")


def github_listing() -> list[dict]:
    """List games/ via the repo HTML tree page (API is rate-limited)."""
    import re
    import urllib.request

    url = f"https://github.com/{REPO}/tree/{BRANCH}/games"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 luast-deobf-tester"})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    names = sorted(set(re.findall(r'"name"\s*:\s*"([^"]+\.(?:lua|luau|lu))"', html)))
    if not names:
        # fallback: raw repo page links
        names = sorted(set(re.findall(r'href="/' + REPO + r'/blob/' + BRANCH + r'/games/([^"]+\.(?:lua|luau|lu))"', html)))
    return [{"name": name, "size": 0} for name in names]


def fetch(name: str, dest: Path) -> bool:
    import urllib.request

    url = f"{RAW}/{name}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "luast-deobf-tester"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        dest.write_bytes(payload)
        return True
    except Exception as exc:
        print(f"  fetch failed {name}: {exc}", file=sys.stderr)
        return False


def is_luast(source: str) -> bool:
    return "luast" in source[:400]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--count", type=int, default=5, help="how many scripts to test")
    parser.add_argument("--names", nargs="*", help="specific file names")
    parser.add_argument("--any", action="store_true", help="include non-luast scripts")
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    listing = github_listing()
    print(f"repo listing: {len(listing)} files in games/")
    chosen = []
    for item in listing:
        name = item["name"]
        if args.names:
            if name in args.names:
                chosen.append(item)
            continue
        chosen.append(item)
    if not args.names:
        if not args.any:
            picked = []
            for item in chosen:
                probe = SCRATCH / item["name"]
                if fetch(item["name"], probe):
                    head = probe.read_text(encoding="utf-8", errors="surrogateescape")[:400]
                    if is_luast(head):
                        picked.append(item)
                    else:
                        probe.unlink(missing_ok=True)
            chosen = picked
        chosen = chosen[: args.count]

    results = []
    for item in chosen:
        name = item["name"]
        size = item.get("size", 0)
        print(f"== {name} ({size} bytes)")
        raw_path = SCRATCH / name
        if not raw_path.exists() or raw_path.stat().st_size != size:
            if not fetch(name, raw_path):
                results.append({"name": name, "status": "fetch-failed"})
                continue
        out_path = RESULTS / name
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "deobfuscate.py", str(raw_path), "-o", str(out_path), "--report", "--quiet"],
            cwd=TOOL_DIR,
            capture_output=True,
            text=True,
            timeout=900,
        )
        elapsed = time.monotonic() - started
        entry: dict = {"name": name, "input_bytes": size, "elapsed": round(elapsed, 1)}
        if proc.returncode != 0:
            entry["status"] = "tool-error"
            entry["error"] = proc.stderr.strip()[-500:]
            results.append(entry)
            print(f"   tool-error: {entry['error'][:200]}")
            continue
        try:
            report = json.loads(proc.stdout)[-1]
        except Exception as exc:
            entry["status"] = "report-parse-failed"
            entry["error"] = str(exc)
            results.append(entry)
            continue
        entry.update(
            status="ok" if report.get("valid_output") else "fallback",
            output_bytes=report.get("output_bytes"),
            dispatchers_found=report.get("stats", {}).get("dispatchers_found"),
            dispatchers_removed=report.get("stats", {}).get("dispatchers_removed"),
            states_recovered=report.get("stats", {}).get("states_recovered"),
            pool_values=report.get("stats", {}).get("pool_values"),
            rolled_back=[h["name"] for h in report.get("history", []) if h.get("status") == "rolled-back"],
        )
        # quality probe: leftover dispatcher spew / pool dumps
        if out_path.exists():
            text = out_path.read_text(encoding="utf-8", errors="surrogateescape")
            lines = text.splitlines()
            entry["lines"] = len(lines)
            entry["while_true_count"] = sum(1 for line in lines if line.strip() == "while true do")
            entry["continue_count"] = sum(1 for line in lines if line.strip() == "continue;")
            entry["pool_table_lines"] = sum(1 for line in lines[:2000] if "\\x" in line)
            entry["var_reads"] = text.count("var_1[")
        results.append(entry)
        print(f"   {entry['status']} -> {out_path.name}: {entry.get('output_bytes')} bytes in {elapsed:.1f}s "
              f"(dispatchers {entry.get('dispatchers_removed')}/{entry.get('dispatchers_found')}, states {entry.get('states_recovered')})")

    (RESULTS / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

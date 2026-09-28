#!/usr/bin/env python3
"""
Pull the readable trace out of a harness run: strips the machine-readable
\\0NAME lines and the heartbeats, and keeps what is between the
\\0ENVLOG-BEGIN / \\0ENVLOG-END sentinels.

    ./luau harness.luau | python3 extract_trace.py [-o trace.lua]
"""
import argparse
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="", help="write the trace here (default stdout)")
    args = ap.parse_args()
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    raw = re.sub("\x00HB\\r?\\n {16384}\\r?\\n", "", raw)
    raw = raw.replace("\r\n", "\n")
    m = re.search("\x00ENVLOG-BEGIN\n(.*?)\x00ENVLOG-END", raw, re.S)
    if m:
        body = m.group(1)
    else:
        # no sentinels: the runtime died before rendering - keep whatever it printed
        body = "\n".join(l for l in raw.splitlines() if not l.startswith("\x00"))
    out = open(args.out, "w", encoding="utf-8", newline="\n") if args.out else sys.stdout
    out.write(body)
    if args.out:
        out.close()
        print("[+] wrote %s (%d bytes)" % (args.out, len(body)))


if __name__ == "__main__":
    main()

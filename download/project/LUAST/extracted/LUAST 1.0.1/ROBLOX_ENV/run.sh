#!/bin/sh
# run.sh <script.lua> [cfg key=value ...]
# Builds a harness for the script, runs it in the fake Roblox environment with
# the bundled Luau, and prints the readable behaviour trace.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$1
shift
if [ -z "$SCRIPT" ]; then
	echo "usage: $0 <script.lua> [cfg key=value ...]" >&2
	exit 1
fi
CFG=""
for kv in "$@"; do CFG="$CFG --cfg $kv"; done
python3 "$HERE/build_env.py" "$SCRIPT" -o "$HERE/harness.luau" $CFG
cd "$HERE"
timeout 120 ./luau harness.luau 2>/dev/null | python3 "$HERE/extract_trace.py"

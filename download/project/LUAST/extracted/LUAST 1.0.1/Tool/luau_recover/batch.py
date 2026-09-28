from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .environment import RobloxEnvironment
from .native import validate_luau
from .pipeline import process_file


class BatchTimeout(BaseException):
    pass


def alarm_handler(signum: int, frame: Any) -> None:
    raise BatchTimeout("batch timeout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luau-recover-batch")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-iterations", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--error-log", type=Path)
    parser.add_argument("--no-aggressive", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--env-run", action="store_true")
    parser.add_argument("--env-dir", type=Path)
    parser.add_argument("--env-trace-dir", type=Path)
    parser.add_argument("--env-timeout", type=int, default=120)
    parser.add_argument("--env-config", action="append", default=[])
    parser.add_argument("--env-prelude")
    parser.add_argument("--env-falsy", action="append", default=[])
    parser.add_argument("--native-validate", action="store_true")
    parser.add_argument("--native-timeout", type=int, default=120)
    return parser


def files_from(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(item for item in path.iterdir() if item.is_file()))
        else:
            result.append(path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGALRM, alarm_handler)
    files = files_from(args.inputs)
    if args.limit > 0:
        files = files[:args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_handle = args.report.open("w", encoding="utf-8") if args.report else None
    environment = RobloxEnvironment(args.env_dir, timeout=args.env_timeout) if args.env_run else None
    total = len(files)
    failures = 0
    started = time.monotonic()
    try:
        for index, source in enumerate(files, 1):
            target = args.output_dir / source.name
            entry: dict[str, Any] = {"input": str(source), "output": str(target)}
            if target.exists() and not args.force:
                entry.update({"status": "skipped-existing"})
                if report_handle:
                    report_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    report_handle.flush()
                continue
            signal.setitimer(signal.ITIMER_REAL, max(1, args.timeout))
            try:
                report = process_file(source, target, max_iterations=args.max_iterations, aggressive=not args.no_aggressive, error_log=args.error_log)
                entry.update(report.as_dict())
                entry["status"] = "ok" if report.valid_output else "fallback"
                if not report.valid_output:
                    failures += 1
            except BatchTimeout as exc:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                entry.update({"status": "timeout", "error": str(exc), "valid_output": False})
                failures += 1
            except Exception as exc:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                entry.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "valid_output": False})
                failures += 1
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            if args.native_validate and target.exists():
                project_env = args.env_dir or Path(__file__).resolve().parents[2] / "ROBLOX_ENV"
                native = validate_luau(target, project_env, args.native_timeout)
                entry["native_validation"] = native
                if native.get("available") and not native.get("valid"):
                    shutil.copyfile(source, target)
                    entry["status"] = "native-invalid"
                    entry["valid_output"] = False
                    failures += 1
            if environment is not None:
                trace_dir = args.env_trace_dir or args.output_dir / "ENV_TRACES"
                trace_path = trace_dir / (target.stem + ".trace.luau")
                env_report = environment.run(source, trace_path, config=args.env_config, prelude=args.env_prelude, falsy=args.env_falsy)
                entry["environment"] = env_report.as_dict()
            if report_handle:
                report_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                report_handle.flush()
            elapsed = time.monotonic() - started
            print(f"[{index}/{total}] {entry.get('status')} {source.name} ({elapsed:.1f}s)", flush=True)
    finally:
        if report_handle:
            report_handle.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

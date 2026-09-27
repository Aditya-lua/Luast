from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .environment import RobloxEnvironment
from .native import validate_luau
from .pipeline import Pipeline, process_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luau-recover", description="Recover readable Luau from LuaST-style obfuscation")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-iterations", type=int, default=15)
    parser.add_argument("--error-log", type=Path)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--no-aggressive", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--env-run", action="store_true")
    parser.add_argument("--env-only", action="store_true")
    parser.add_argument("--env-dir", type=Path)
    parser.add_argument("--env-trace-dir", type=Path)
    parser.add_argument("--env-timeout", type=int, default=120)
    parser.add_argument("--env-config", action="append", default=[])
    parser.add_argument("--env-prelude")
    parser.add_argument("--env-falsy", action="append", default=[])
    parser.add_argument("--native-validate", action="store_true")
    parser.add_argument("--native-timeout", type=int, default=120)
    return parser


def input_files(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(item for item in path.iterdir() if item.is_file()))
        else:
            result.append(path)
    return result


def destination(source: Path, args: argparse.Namespace, index: int) -> Path:
    if args.output and len(args.inputs) == 1 and args.inputs[0].is_file():
        return args.output
    if args.output_dir:
        return args.output_dir / source.name
    if args.output and len(args.inputs) == 1:
        return args.output
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "DEOBFUSCATED" / source.name


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = input_files(args.inputs)
    if not files:
        print("no input files", file=sys.stderr)
        return 2
    failures = 0
    reports = []
    for index, source in enumerate(files):
        if not source.is_file():
            print(f"missing input: {source}", file=sys.stderr)
            failures += 1
            continue
        target = destination(source, args, index)
        entry: dict[str, object] = {"input": str(source), "output": str(target)}
        try:
            if not args.env_only:
                report = process_file(source, target, max_iterations=args.max_iterations, aggressive=not args.no_aggressive, error_log=args.error_log)
                entry.update(report.as_dict())
                if not args.quiet:
                    status = "ok" if report.valid_output else "fallback"
                    print(f"{status} {source.name} -> {target} ({report.output_bytes} bytes, {report.elapsed_seconds:.2f}s)")
                if not report.valid_output:
                    failures += 1
            if args.native_validate and target.exists():
                native = validate_luau(target, args.env_dir or Path(__file__).resolve().parents[2] / "ROBLOX_ENV", args.native_timeout)
                entry["native_validation"] = native
                if native.get("available") and not native.get("valid"):
                    shutil.copyfile(source, target)
                    entry["status"] = "native-invalid"
                    entry["valid_output"] = False
                    failures += 1
            if args.env_run or args.env_only:
                project_env = Path(__file__).resolve().parents[2] / "ROBLOX_ENV"
                environment = RobloxEnvironment(args.env_dir or project_env, timeout=args.env_timeout)
                project_root = Path(__file__).resolve().parents[2]
                trace_dir = args.env_trace_dir or project_root / "DEOBFUSCATED" / "ENV_TRACES"
                trace_path = trace_dir / (target.stem + ".trace.luau")
                env_report = environment.run(source, trace_path, config=args.env_config, prelude=args.env_prelude, falsy=args.env_falsy)
                entry["environment"] = env_report.as_dict()
                if not args.quiet:
                    print(f"env {env_report.status} {source.name} -> {trace_path} ({env_report.trace_bytes} bytes)")
        except Exception as exc:
            failures += 1
            print(f"{source}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        reports.append(entry)
    if args.report:
        json.dump(reports, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    if args.strict and failures:
        return 1
    return 0

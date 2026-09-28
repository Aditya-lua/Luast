from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EnvironmentReport:
    status: str = "not-run"
    trace_path: str | None = None
    trace_bytes: int = 0
    statements: int | None = None
    globals_touched: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    remotes: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trace_path": self.trace_path,
            "trace_bytes": self.trace_bytes,
            "statements": self.statements,
            "globals_touched": self.globals_touched,
            "urls": self.urls,
            "remotes": self.remotes,
            "error": self.error,
        }


class RobloxEnvironment:
    def __init__(self, env_dir: Path, cache_dir: Path | None = None, timeout: int = 120):
        self.source_dir = env_dir
        self.cache_dir = cache_dir or Path(tempfile.gettempdir()) / "luau_recover_roblox_env"
        self.timeout = max(1, int(timeout))
        self.runtime_dir: Path | None = None

    def prepare(self) -> Path:
        luau = self.source_dir / "luau"
        if os.access(luau, os.X_OK):
            self.runtime_dir = self.source_dir
            return self.runtime_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / "runtime"
        if not target.exists():
            shutil.copytree(self.source_dir, target)
        for name in ("run.sh", "luau", "luau-ast"):
            path = target / name
            if path.exists():
                path.chmod(path.stat().st_mode | 0o111)
        self.runtime_dir = target
        return target

    def run(self, source_path: Path, trace_path: Path, config: list[str] | None = None, prelude: str | None = None, falsy: list[str] | None = None) -> EnvironmentReport:
        report = EnvironmentReport()
        work: Path | None = None
        try:
            runtime = self.prepare()
            build_script = runtime / "build_env.py"
            interpreter = runtime / "luau"
            if not build_script.exists() or not interpreter.exists():
                report.status = "unavailable"
                report.error = "environment is missing build_env.py or luau"
                return report
            work = Path(tempfile.mkdtemp(prefix="luau-recover-env-"))
            harness = work / "harness.luau"
            options = list(config or [])
            if prelude:
                options.append("prelude=" + prelude)
            if falsy:
                options.append("falsy=" + ",".join(falsy))
            build_command = [sys.executable, str(build_script), str(source_path), "-o", str(harness)]
            for option in options:
                build_command.extend(["--cfg", option])
            built = subprocess.run(build_command, capture_output=True, timeout=60)
            if built.returncode != 0:
                report.status = "build-failed"
                report.error = built.stderr.decode("utf-8", "replace")[-2000:]
                return report
            executed = subprocess.run([str(interpreter), str(harness)], capture_output=True, timeout=self.timeout)
            raw = executed.stdout.decode("utf-8", "replace")
            trace = self.extract_trace(raw)
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(trace, encoding="utf-8", errors="replace")
            report.trace_path = str(trace_path)
            report.trace_bytes = len(trace.encode("utf-8", "replace"))
            self.parse_metadata(trace, report)
            if report.status == "not-run":
                report.status = "finished" if executed.returncode == 0 else "runtime-error"
            if executed.returncode != 0:
                report.error = executed.stderr.decode("utf-8", "replace")[-2000:]
            return report
        except subprocess.TimeoutExpired as exc:
            report.status = "timeout"
            report.error = f"environment exceeded {self.timeout} seconds: {exc}"
            return report
        except (OSError, ValueError, RuntimeError) as exc:
            report.status = "error"
            report.error = f"{type(exc).__name__}: {exc}"
            return report
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)

    def extract_trace(self, raw: str) -> str:
        raw = re.sub("\x00HB\r?\n {16384}\r?\n", "", raw)
        raw = raw.replace("\r\n", "\n")
        match = re.search("\x00ENVLOG-BEGIN\n(.*?)\x00ENVLOG-END", raw, re.S)
        if match:
            return match.group(1)
        return "\n".join(line for line in raw.splitlines() if not line.startswith("\x00"))

    def parse_metadata(self, trace: str, report: EnvironmentReport) -> None:
        statement_match = re.search(r"-- (\d+) statements recorded", trace)
        if statement_match:
            report.statements = int(statement_match.group(1))
        status_match = re.search(r"-- run status: (.*)", trace)
        if status_match:
            report.status = status_match.group(1).strip()
        globals_match = re.search(r"-- non-standard globals touched: (.*)", trace)
        if globals_match:
            report.globals_touched = [item.strip() for item in globals_match.group(1).split(",") if item.strip()]
        urls_match = re.search(r"-- URLs requested:\n(.*?)(?:\n\n|\Z)", trace, re.S)
        if urls_match:
            report.urls = [line.strip("- ").strip() for line in urls_match.group(1).splitlines() if line.strip()]
        remotes_match = re.search(r"-- remotes fired/invoked:\n(.*?)(?:\n\n|\Z)", trace, re.S)
        if remotes_match:
            report.remotes = [line.strip("- ").strip() for line in remotes_match.group(1).splitlines() if line.strip()]

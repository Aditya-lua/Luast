from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .environment import RobloxEnvironment


def validate_luau(path: Path, env_dir: Path, timeout: int = 120) -> dict[str, Any]:
    try:
        runtime = RobloxEnvironment(env_dir, timeout=timeout).prepare()
        binary = runtime / "luau-ast"
        if not binary.exists():
            return {"available": False, "valid": False, "error": "luau-ast is unavailable"}
        result = subprocess.run([str(binary), str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        error = result.stderr.decode("utf-8", "replace")[-2000:]
        return {"available": True, "valid": result.returncode == 0, "error": error or None}
    except subprocess.TimeoutExpired:
        return {"available": True, "valid": False, "error": "luau-ast validation timed out"}
    except (OSError, ValueError) as exc:
        return {"available": False, "valid": False, "error": f"{type(exc).__name__}: {exc}"}

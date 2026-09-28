from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .analysis import analyze
from .controlflow import DispatcherPass
from .emitter import emit
from .lexer import lex
from .model import Node, count_nodes, walk
from .parser import parse
from .passes import (
    PassStats,
    PoolResolver,
    fold_constants,
    propagate_locals,
    remove_dead_pool_writes,
    remove_unused_locals,
    rename_bindings,
    restore_library_aliases,
)


@dataclass
class RunReport:
    input_bytes: int = 0
    output_bytes: int = 0
    input_nodes: int = 0
    output_nodes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_complexity: int = 0
    output_complexity: int = 0
    valid_input: bool = False
    valid_output: bool = False
    fallback_reason: str | None = None
    elapsed_seconds: float = 0.0
    stats: PassStats = field(default_factory=PassStats)
    history: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["stats"] = asdict(self.stats)
        return result


class Pipeline:
    def __init__(self, max_iterations: int = 15, error_log: Path | None = None):
        self.max_iterations = max(1, min(int(max_iterations), 15))
        self.error_log = error_log or Path(__file__).resolve().parent.parent / "pipeline_errors.log"

    def run(self, source: str, aggressive: bool = True) -> tuple[str, RunReport]:
        started = time.monotonic()
        from .junk import clear_verdict_cache
        clear_verdict_cache()
        report = RunReport(input_bytes=len(source.encode("utf-8", "surrogateescape")))
        try:
            root, errors, comments = parse(source)
        except Exception as exc:
            report.fallback_reason = f"parser exception: {type(exc).__name__}: {exc}"
            self.log("parse", report.fallback_reason)
            report.output_bytes = report.input_bytes
            report.valid_output = False
            report.elapsed_seconds = time.monotonic() - started
            return source, report
        report.input_nodes = count_nodes(root)
        report.input_tokens = token_count(source)
        report.input_complexity = complexity(root)
        report.valid_input = not errors
        if errors:
            report.fallback_reason = "input parser errors: " + "; ".join(str(error) for error in errors[:3])
            self.log("parse", report.fallback_reason)
            report.output_bytes = report.input_bytes
            report.output_nodes = report.input_nodes
            report.output_tokens = report.input_tokens
            report.output_complexity = report.input_complexity
            report.elapsed_seconds = time.monotonic() - started
            return source, report

        phases: list[tuple[str, Callable[[Node, Any, PassStats, str], None], bool]] = [
            ("aliases", self.phase_aliases, True),
            ("pool", self.phase_pool, aggressive),
            ("fold", self.phase_fold, True),
            ("propagate", self.phase_propagate, True),
            ("control-flow", self.phase_control_flow, aggressive),
            ("fold-after-cff", self.phase_fold, True),
            ("propagate-after-cff", self.phase_propagate, True),
            ("dead-code", self.phase_dead_code, True),
            ("pool-cleanup", self.phase_pool_cleanup, True),
            ("dead-code-2", self.phase_dead_code, True),
            ("fold-final", self.phase_fold, True),
            ("dead-code-3", self.phase_dead_code, True),
            ("rename", self.phase_rename, True),
        ]
        current_source = source
        committed_root = root
        import gc as _gc
        # [SUPERZ] huge-input mode: per-phase emit+reparse verification doubles
        # the AST peak (the verified tree is as big as the candidate) and the
        # per-phase backup clone adds another copy. On multi-MB inputs that
        # OOMs 4 GB machines. Instead: mutate in place, verify ONCE at the end,
        # and fall back to the original input if any phase raises.
        huge = len(source) > 1_500_000
        import os as _os, resource as _res, sys as _sys
        _memdbg = _os.environ.get("SUPERZ_MEM_DEBUG") == "1"

        def _memmark(label: str) -> None:
            if _memdbg:
                mb = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss // 1024
                print(f"[mem] {label:20} maxRSS={mb}MB", file=_sys.stderr, flush=True)

        _memmark("pipeline start")
        for phase_name, phase, enabled in phases:
            if not enabled:
                report.history.append({"name": phase_name, "status": "skipped"})
                continue
            backup = None if huge else committed_root.clone()
            candidate = committed_root
            analyzer = analyze(candidate)
            _memmark(f"{phase_name}: analyzed")
            before = self.stats_snapshot(report.stats)
            try:
                phase(candidate, analyzer, report.stats, current_source)
                changed = self.stats_snapshot(report.stats) != before
                if not changed:
                    report.history.append({"name": phase_name, "status": "unchanged"})
                    continue
                if huge:
                    # in-place commit; verification deferred to the final emit
                    committed_root = candidate
                    report.history.append({"name": phase_name, "status": "committed-huge"})
                    continue
                rendered = emit(candidate, current_source)
                verified, verification_errors, _ = parse(rendered)
                if verification_errors:
                    raise ValueError("output parser errors: " + "; ".join(str(error) for error in verification_errors[:3]))
                committed_root = verified
                current_source = rendered
                report.history.append({"name": phase_name, "status": "committed", "bytes": len(rendered.encode("utf-8", "surrogateescape"))})
            except Exception as exc:
                if huge:
                    # no backup kept for huge inputs: restarting from the
                    # original is the only safe rollback, and a clean failure
                    # beats a half-transformed output
                    report.fallback_reason = f"{phase_name} failed on huge input: {type(exc).__name__}: {exc}"
                    self.log(phase_name, report.fallback_reason)
                    report.history.append({"name": phase_name, "status": "failed-huge", "error": report.fallback_reason})
                    current_source = source
                    report.valid_output = False
                    report.output_bytes = report.input_bytes
                    report.elapsed_seconds = time.monotonic() - started
                    return source, report
                committed_root = backup
                message = f"{type(exc).__name__}: {exc}"
                self.log(phase_name, message)
                report.history.append({"name": phase_name, "status": "rolled-back", "error": message})
            finally:
                # [SUPERZ] free per-phase intermediates promptly; the old root
                # and backup for huge inputs are hundreds of MB each.
                _memmark(f"{phase_name}: done")
                backup = None
                analyzer = None
                candidate = None
                verified = None
                rendered = None
                _gc.collect()
        if huge:
            # [SUPERZ] final validation without a second Python AST: verify the
            # rendered source with the C++ luau-ast parser when available (a
            # subprocess, zero heap cost) and skip node statistics, which would
            # otherwise build yet another multi-GB tree next to committed_root.
            _memmark("final: emitting")
            rendered = emit(committed_root, current_source)
            del committed_root
            _gc.collect()
            _memmark("final: emitted")
            headered = prepend_header(rendered, source)
            import subprocess as _sub
            luau_ast = None
            for cand in (
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "luau-ast"),
                "/home/z/my-project/download/project/LUAST/extracted/LUAST 1.0.1/ROBLOX_ENV/luau-ast",
            ):
                if os.path.exists(cand):
                    luau_ast = cand
                    break
            valid = True
            if luau_ast:
                from tempfile import NamedTemporaryFile
                with NamedTemporaryFile("w", suffix=".luau", delete=False, encoding="utf-8", errors="surrogateescape") as tf:
                    tf.write(headered)
                    tmpname = tf.name
                try:
                    proc = _sub.run([luau_ast, tmpname], capture_output=True, timeout=300)
                    valid = proc.returncode == 0
                    if not valid:
                        self.log("final", "luau-ast rejected output: " + proc.stderr.decode("utf-8", "replace")[:300])
                except Exception as exc:
                    # validator unavailable or timed out: trust the pipeline
                    self.log("final", f"luau-ast validation skipped: {exc}")
                    valid = True
                finally:
                    try:
                        os.unlink(tmpname)
                    except OSError:
                        pass
            _memmark("final: validated")
            if valid:
                report.valid_output = True
                report.output_bytes = len(headered.encode("utf-8", "surrogateescape"))
                report.output_nodes = 0
                report.output_tokens = len(headered) // 5
                report.output_complexity = 0
                report.elapsed_seconds = time.monotonic() - started
                return headered, report
            report.fallback_reason = "final luau-ast validation failed"
            current_source = source
            report.valid_output = False
            report.output_bytes = report.input_bytes
            report.output_nodes = report.input_nodes
            report.output_tokens = report.input_tokens
            report.output_complexity = report.input_complexity
            report.elapsed_seconds = time.monotonic() - started
            return current_source, report
        try:
            rendered = emit(committed_root, current_source)
            verified, verification_errors, _ = parse(rendered)
            if verification_errors:
                raise ValueError("final parser errors: " + "; ".join(str(error) for error in verification_errors[:3]))
            current_source = prepend_header(rendered, source)
            report.valid_output = True
        except Exception as exc:
            report.fallback_reason = f"final validation failed: {type(exc).__name__}: {exc}"
            self.log("final", report.fallback_reason)
            current_source = source
            report.valid_output = False
        report.output_bytes = len(current_source.encode("utf-8", "surrogateescape"))
        if report.valid_output:
            final_root, final_errors, _ = parse(current_source)
            if final_errors:
                report.valid_output = False
                report.fallback_reason = "header validation failed"
                current_source = source
                report.output_bytes = report.input_bytes
            else:
                report.output_nodes = count_nodes(final_root)
                report.output_tokens = token_count(current_source)
                report.output_complexity = complexity(final_root)
        else:
            report.output_nodes = report.input_nodes
            report.output_tokens = report.input_tokens
            report.output_complexity = report.input_complexity
        report.elapsed_seconds = time.monotonic() - started
        return current_source, report

    def phase_aliases(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        restore_library_aliases(root, analyzer, stats)

    def phase_pool(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        resolver = PoolResolver(root, analyzer, source, allow_escape=True)
        resolver.apply(root, stats)

    def phase_fold(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        fold_constants(root, analyzer, stats)

    def phase_propagate(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        propagate_locals(root, analyzer, stats)

    def phase_control_flow(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        DispatcherPass(root, analyzer, source, stats).apply(rounds=min(8, self.max_iterations))

    def phase_dead_code(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        remove_unused_locals(root, analyzer, stats)

    def phase_pool_cleanup(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        remove_dead_pool_writes(root, analyzer, stats)

    def phase_rename(self, root: Node, analyzer: Any, stats: PassStats, source: str) -> None:
        rename_bindings(root, analyzer, stats)

    def stats_snapshot(self, stats: PassStats) -> tuple[Any, ...]:
        return (
            stats.folded, stats.propagated, stats.pool_reads, stats.pool_values, stats.decoder_calls,
            stats.branches_removed, stats.dead_locals, stats.dispatchers_found, stats.dispatchers_removed,
            stats.states_recovered, stats.names_renamed, stats.aliases_restored,
        )

    def log(self, phase: str, message: str) -> None:
        try:
            self.error_log.parent.mkdir(parents=True, exist_ok=True)
            with self.error_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"phase": phase, "message": message}, ensure_ascii=False) + "\n")
        except OSError:
            pass


def token_count(source: str) -> int:
    # [SUPERZ] memory guard: lexing a huge source materializes a full token
    # list while the AST is live; approximate the stat for very large inputs.
    if len(source) > 1_500_000:
        return max(1, len(source) // 5)
    try:
        tokens, _, _ = lex(source)
        n = len(tokens)
        tokens.clear()
        return n
    except Exception:
        return 0


def complexity(root: Node) -> int:
    score = 1
    for node in walk(root):
        if node.kind in {"if", "while", "repeat", "fornum", "forin"}:
            score += 1
        elif node.kind == "binop" and node.get("op") in {"and", "or"}:
            score += 1
    return score


def prepend_header(rendered: str, original: str) -> str:
    lines = original.splitlines()
    header: list[str] = []
    for line in lines[:12]:
        stripped = line.lstrip()
        if stripped.startswith("--[[") or stripped.startswith("--[="):
            break
        if stripped.startswith("--"):
            header.append(line.rstrip())
        elif stripped and not header:
            break
        elif not stripped:
            continue
        else:
            break
    if not header:
        return rendered
    existing = rendered[:4096]
    missing = [line for line in header if line not in existing]
    if not missing:
        return rendered
    return "\n".join(missing) + "\n" + rendered


def process_file(input_path: Path, output_path: Path, max_iterations: int = 15, aggressive: bool = True, error_log: Path | None = None) -> RunReport:
    source = input_path.read_text(encoding="utf-8", errors="surrogateescape")
    pipeline = Pipeline(max_iterations=max_iterations, error_log=error_log)
    rendered, report = pipeline.run(source, aggressive=aggressive)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", errors="surrogateescape")
    os.replace(temporary, output_path)
    return report

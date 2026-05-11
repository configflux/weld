"""``tree-sitter-cli`` adapter for the public benchmark (ADR 0059).

The ``tree-sitter-cli`` binary is the symbol-only baseline. We invoke
``tree-sitter query`` for definitions / references via subprocess and
parse the output. When the binary is missing the adapter reports
``status="unavailable"`` so the run continues on the remaining
adapters (no crash, no fatal error).

Adapter contract (shared by every adapter in
:mod:`weld.bench.adapters`):

  - returns :class:`AdapterResult`, never raises;
  - bounds every external subprocess by ``_TIMEOUT_S``;
  - emits ``unavailable`` when the binary is missing so the row in
    the public-bench report is rendered as ``unavailable`` and
    excluded from per-family rollups.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from weld.bench._public_runner import AdapterResult, PublicTask
from weld.bench.primitives import count_tokens


_BIN = "tree-sitter"
_TIMEOUT_S = 60


def _which(binary: str) -> str | None:
    """Indirection so tests can mock ``shutil.which`` per adapter."""
    return shutil.which(binary)


def _run_subprocess(
    cmd: list[str], cwd: Path, timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Bounded subprocess wrapper. Separate so tests can mock it."""
    return subprocess.run(  # noqa: S603 -- bounded, args validated above
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _build_cmd(task: PublicTask) -> list[str]:
    """Build the tree-sitter-cli invocation for a task.

    We use ``tree-sitter query`` with a synthesized capture pattern that
    matches the symbol name. Tree-sitter parsers vary by language so
    this is a best-effort heuristic baseline.
    """
    target = task.symbol or task.term
    return [_BIN, "query", "--scope", "source", target]


def _parse_output(stdout: str) -> list[str]:
    """Parse tree-sitter-cli output into a deduplicated file list.

    Tree-sitter prints lines of the form ``path:line:column``. We harvest
    the path portion and dedupe while preserving first-appearance order.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        path = line.split(":", 1)[0].strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def run(task: PublicTask, repo_root: Path) -> AdapterResult:
    """Invoke ``tree-sitter`` for ``task``; tolerate every failure mode."""
    start = time.perf_counter()
    if _which(_BIN) is None:
        return AdapterResult(
            status="unavailable",
            files=[],
            tokens=0,
            duration_ms=0.0,
            error=f"{_BIN} binary not on PATH",
        )
    cmd = _build_cmd(task)
    try:
        result = _run_subprocess(cmd, repo_root, _TIMEOUT_S)
    except subprocess.TimeoutExpired:
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=f"{_BIN} timed out after {_TIMEOUT_S}s",
        )
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=str(exc),
        )

    elapsed = (time.perf_counter() - start) * 1000.0
    if getattr(result, "returncode", 1) != 0:
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=(result.stderr or "").strip()[:200],
        )
    files = _parse_output(result.stdout or "")
    return AdapterResult(
        status="ok",
        files=files,
        tokens=count_tokens(result.stdout or ""),
        duration_ms=elapsed,
        ttft_ms=elapsed,
    )


__all__ = ["run"]

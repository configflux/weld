"""``graphify`` adapter for the public benchmark (ADR 0059).

Wraps the external ``graphify`` CLI (install via ``pipx install
graphifyy``). When the binary is missing the adapter reports
``status="unavailable"`` so ``wd bench --public`` keeps running on the
remaining adapters -- per ADR 0059 we DO NOT fail the bench run when a
competitor's tool is absent.

Adapter contract (shared by every adapter in
:mod:`weld.bench.adapters`):

  - returns :class:`AdapterResult`, never raises;
  - bounds every external subprocess by ``_TIMEOUT_S``;
  - tolerates malformed JSON output (treated as ``degraded``);
  - the binary path is taken from ``WELD_GRAPHIFY_BIN`` if set, else
    ``shutil.which("graphify")``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from weld.bench._public_runner import AdapterResult, PublicTask
from weld.bench.primitives import count_tokens


_BIN_DEFAULT = "graphify"
_BIN_ENV = "WELD_GRAPHIFY_BIN"
_TIMEOUT_S = 120


def _which(binary: str) -> str | None:
    """Resolve the graphify binary, honoring ``WELD_GRAPHIFY_BIN``."""
    env_override = os.environ.get(_BIN_ENV)
    if env_override:
        # Trust the env override if the path exists; otherwise fall through
        # so tests that mock ``_which`` get a deterministic answer.
        if Path(env_override).exists():
            return env_override
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


def _build_cmd(binary: str, task: PublicTask) -> list[str]:
    """Build the graphify invocation for a task.

    graphify's exact subcommand set is out of scope for this adapter;
    we use a documented ``query`` subcommand with a JSON output flag and
    the natural-language prompt -- if graphify's CLI surface changes the
    adapter has a single edit point.
    """
    target = task.symbol or task.term
    return [binary, "query", "--format", "json", target]


def _parse_json_output(stdout: str) -> tuple[list[str], int]:
    """Parse graphify JSON output into ``(files, tokens)``.

    Tolerates missing ``files`` key and malformed JSON (returns empty
    files + 0 tokens, caller treats as degraded).
    """
    if not stdout.strip():
        return [], 0
    payload = json.loads(stdout)
    raw_files = payload.get("files", [])
    if not isinstance(raw_files, list):
        return [], count_tokens(stdout)
    files: list[str] = []
    seen: set[str] = set()
    for item in raw_files:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            files.append(item)
        elif isinstance(item, dict):
            for key in ("file", "path"):
                val = item.get(key)
                if isinstance(val, str) and val and val not in seen:
                    seen.add(val)
                    files.append(val)
                    break
    return files, count_tokens(stdout)


def run(task: PublicTask, repo_root: Path) -> AdapterResult:
    """Invoke ``graphify`` for ``task``; tolerate every failure mode."""
    start = time.perf_counter()
    binary = _which(_BIN_DEFAULT)
    if binary is None:
        # ADR 0059 mandates we do NOT fail the bench when graphify is
        # missing. Report unavailable so the row renders as such.
        return AdapterResult(
            status="unavailable",
            files=[],
            tokens=0,
            duration_ms=0.0,
            cost_usd=0.0,
            error=(
                f"{_BIN_DEFAULT} binary not on PATH "
                f"(install with `pipx install graphifyy` or set "
                f"{_BIN_ENV})"
            ),
        )
    cmd = _build_cmd(binary, task)
    try:
        result = _run_subprocess(cmd, repo_root, _TIMEOUT_S)
    except subprocess.TimeoutExpired:
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=f"{_BIN_DEFAULT} timed out after {_TIMEOUT_S}s",
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
    try:
        files, tokens = _parse_json_output(result.stdout or "")
    except json.JSONDecodeError as exc:
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=f"malformed JSON from {_BIN_DEFAULT}: {exc}",
        )
    return AdapterResult(
        status="ok",
        files=files,
        tokens=tokens,
        duration_ms=elapsed,
        ttft_ms=elapsed,
    )


__all__ = ["run"]

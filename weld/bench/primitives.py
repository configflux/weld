"""Shared primitives for every wd bench harness.

This module factors out the tokenizer, the ``Prompt`` dataclass, and the
``grep_baseline`` helper so multiple bench harnesses can reuse them
without pulling in the full token-cost runner. Keeping these pieces in
a dedicated leaf module lets the comparative harness
(:mod:`weld.bench_tasks.compare`) depend on them without creating a
circular import / Bazel dep cycle with :mod:`weld.bench.runner`.

The original import path (``from weld.bench.runner import ...``) is
preserved via re-exports in :mod:`weld.bench.runner`.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


# -- Tokenizer (tiktoken if present, else bytes/4) --------------------------


def _bytes_fallback(text: str) -> int:
    return int(math.ceil(len(text.encode("utf-8")) / 4))


_TIKTOKEN_ENCODER = None
_TIKTOKEN_LOADED = False


def _get_encoder():
    global _TIKTOKEN_ENCODER, _TIKTOKEN_LOADED
    if _TIKTOKEN_LOADED:
        return _TIKTOKEN_ENCODER
    _TIKTOKEN_LOADED = True
    try:
        import tiktoken  # type: ignore

        _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TIKTOKEN_ENCODER = None
    return _TIKTOKEN_ENCODER


def count_tokens(text: str) -> int:
    """Return token count using tiktoken cl100k_base, or bytes/4 fallback."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is None:
        return _bytes_fallback(text)
    try:
        return len(enc.encode(text))
    except Exception:
        return _bytes_fallback(text)


def tokenizer_name() -> str:
    return (
        "tiktoken/cl100k_base"
        if _get_encoder() is not None
        else "bytes/4 fallback"
    )


# -- Prompt dataclass -------------------------------------------------------


@dataclass(frozen=True)
class Prompt:
    id: str
    prompt: str
    category: str
    term: str
    symbol: str | None = None


# -- grep baseline ----------------------------------------------------------

# Skip noisy directories an unaided agent would typically exclude.
_GREP_EXCLUDES = (
    ".git",
    ".weld",
    ".worktrees",
    ".claude",
    "node_modules",
    "bazel-bin",
    "bazel-out",
    "bazel-testlogs",
    "bazel-project",
    "__pycache__",
    "dist",
    "build",
)
# Cap files read per prompt and lines read per file -- mirrors the heuristic
# in the issue description ("grep -l + Read first 200 lines").
_GREP_FILE_CAP = 25
_GREP_LINE_CAP = 200


def _git_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return [line for line in proc.stdout.splitlines() if line]
    except FileNotFoundError:
        pass
    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(seg in _GREP_EXCLUDES for seg in rel.split("/")):
            continue
        out.append(rel)
    return out


def _is_text(path: Path, sniff: int = 1024) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(sniff)
    except OSError:
        return False
    return b"\x00" not in chunk if chunk else True


def grep_baseline(prompt: Prompt, root: Path) -> str:
    """Return the text a grep-only agent would land in its context."""
    term = prompt.term or prompt.prompt
    if not term:
        return ""
    files = _git_files(root)
    matches: list[Path] = []
    for rel in files:
        if any(seg in _GREP_EXCLUDES for seg in rel.split("/")):
            continue
        p = root / rel
        if not p.is_file() or not _is_text(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if term in text:
            matches.append(p)
            if len(matches) >= _GREP_FILE_CAP:
                break
    chunks: list[str] = []
    for p in matches:
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                head = "".join(
                    line for _, line in zip(range(_GREP_LINE_CAP), f)
                )
        except OSError:
            continue
        chunks.append(
            f"# file: {p.relative_to(root).as_posix()}\n{head}"
        )
    return "\n".join(chunks)


__all__ = [
    "Prompt",
    "count_tokens",
    "grep_baseline",
    "tokenizer_name",
]

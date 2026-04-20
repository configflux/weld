#!/usr/bin/env python3
"""Public-safe leak audit for common secret and attribution patterns."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

SKIP_PREFIXES = (
    ".git/",
    ".weld/",
    "bazel-",
    "public/",
    "third_party/",
    "weld/tests/fixtures/",
    "weld/viz/static/vendor/",
)
TEXT_SUFFIXES = {
    ".bazel",
    ".bzl",
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".proto",
    ".py",
    ".rs",
    ".rst",
    ".sh",
    ".sql",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_BASENAMES = {"BUILD", "BUILD.bazel", "Dockerfile", "MODULE.bazel", "Makefile"}


def _normalize(path: str) -> str:
    return path.strip().removeprefix("./")


def _joined(*parts: str) -> str:
    return "_".join(parts)


def _git_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        return []
    return [
        _normalize(path)
        for path in proc.stdout.decode("utf-8", errors="replace").split("\x00")
        if path
    ]


def _publishignore_patterns(root: Path) -> list[str]:
    path = root / ".publishignore"
    if not path.is_file():
        return []
    return [
        _normalize(line.strip())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _matches_pattern(rel_path: str, pattern: str) -> bool:
    if pattern.endswith("/") and rel_path.startswith(pattern):
        return True
    return (
        rel_path == pattern
        or rel_path.startswith(f"{pattern}/")
        or fnmatch.fnmatch(rel_path, pattern)
    )


def _is_publish_ignored(rel_path: str, patterns: list[str]) -> bool:
    return any(_matches_pattern(rel_path, pattern) for pattern in patterns)


def _should_skip(rel_path: str, publishignore: list[str]) -> bool:
    return (
        any(rel_path.startswith(prefix) for prefix in SKIP_PREFIXES)
        or _is_publish_ignored(rel_path, publishignore)
    )


def _is_text_file(rel_path: str) -> bool:
    path = Path(rel_path)
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_BASENAMES


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    trailer = "-".join(("Co", "Authored", "By:"))
    return [
        (
            "AI co-author trailer",
            re.compile(
                trailer + r".*(Claude|Codex|noreply@(?:anthropic|openai))",
                re.I,
            ),
        ),
        ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
        ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
        ("OpenAI API key variable", re.compile(_joined("OPENAI", "API", "KEY"))),
        ("Anthropic API key variable", re.compile(_joined("ANTHROPIC", "API", "KEY"))),
    ]


def audit_paths(root: Path, rel_paths: list[str]) -> list[str]:
    """Return audit findings for publish-visible text files."""
    findings: list[str] = []
    publishignore = _publishignore_patterns(root)
    patterns = _patterns()
    for rel_path in sorted({_normalize(path) for path in rel_paths}):
        if not rel_path or _should_skip(rel_path, publishignore):
            continue
        if not _is_text_file(rel_path):
            continue
        path = root / rel_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern in patterns:
            if pattern.search(text):
                findings.append(f"{rel_path}: {label}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    findings = audit_paths(root, _git_files(root))
    if findings:
        for finding in findings:
            print(f"[public-surface-audit] {finding}", file=sys.stderr)
        return 1
    print("[public-surface-audit] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

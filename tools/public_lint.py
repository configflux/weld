#!/usr/bin/env python3
"""Small public-safe repository lint for the published Weld repo."""

from __future__ import annotations

import argparse
import py_compile
import subprocess
import sys
from pathlib import Path

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
TAB_INDENT_SUFFIXES = {".bazel", ".bzl", ".py"}
SKIP_PREFIXES = (
    ".claude/",
    ".codex/",
    ".git/",
    ".weld/",
    "bazel-",
    "public/",
    "third_party/",
    "weld/tests/fixtures/",
    "weld/viz/static/vendor/",
)


def _normalize(path: str) -> str:
    return path.strip().removeprefix("./")


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


def _should_skip(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in SKIP_PREFIXES)


def _is_text_file(rel_path: str) -> bool:
    path = Path(rel_path)
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_BASENAMES


def lint_paths(root: Path, rel_paths: list[str]) -> list[str]:
    """Return public lint findings for *rel_paths* under *root*."""
    findings: list[str] = []
    for rel_path in sorted({_normalize(path) for path in rel_paths}):
        if not rel_path or _should_skip(rel_path):
            continue
        path = root / rel_path
        if rel_path.endswith(".py"):
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                findings.append(f"{rel_path}: syntax error: {exc.msg}")
        if not _is_text_file(rel_path) or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if line.endswith((" ", "\t")):
                findings.append(f"{rel_path}:{line_no}: trailing whitespace")
            if Path(rel_path).suffix in TAB_INDENT_SUFFIXES and line.startswith("\t"):
                findings.append(f"{rel_path}:{line_no}: tab indentation")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    findings = lint_paths(root, _git_files(root))
    if findings:
        for finding in findings:
            print(f"[public-lint] {finding}", file=sys.stderr)
        return 1
    print("[public-lint] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Public-safe leak audit for common secret and attribution patterns, plus
argparse help-text ADR citations (bd 8zjr)."""

from __future__ import annotations

import argparse
import ast
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


# Files allowed to reference env-var NAMES (not secrets). The first-run
# enrichment policy publishes a deterministic provider precedence chain
# whose env vars are part of the public API; the strings appear here as
# literal env-var names, not embedded credentials.
ENV_VAR_NAME_ALLOWLIST: dict[str, frozenset[str]] = {
    "OpenAI API key variable": frozenset({
        "weld/_first_run_enrich.py",
        "weld/_first_run_render.py",
    }),
    "Anthropic API key variable": frozenset({
        "weld/_first_run_enrich.py",
        "weld/_first_run_render.py",
    }),
}


# --- argparse help-text ADR-citation leak ----------------------------------
#
# argparse help=/description=/epilog= strings ship straight to PyPI users,
# but the narrative-doc bare-ADR scan (tools/audit_publish_adr_refs.sh) only
# looks at Markdown. A CLI help string citing an internal ADR number dangles
# for a public user exactly like a Markdown link into the .publishignore'd
# docs/adrs/ tree would. The allowance mirrors that script's: a citation is
# fine when the number resolves to a *shipping* public ADR
# (weld/docs/adr/0001-0004 today); anything else is an internal reference
# leaking into user-facing text.
#
# Two shapes carry this text in this codebase:
#   1. Inline literal: parser.add_argument("--x", help="... (ADR 1234).")
#   2. Named-constant indirection: ROOT_HELP = "...(ADR 1234)."; used later
#      as help=ROOT_HELP -- sometimes from a different module entirely
#      (weld/_root_resolver.py exports ROOT_HELP; five CLI modules import it
#      and pass help=ROOT_HELP). A per-file "was this name used as help="
#      check would miss that cross-module case, so instead every module-level
#      string assigned to a name ending in HELP/DESCRIPTION/EPILOG is treated
#      as candidate user-facing text regardless of local usage -- that is the
#      established naming convention here (ROOT_HELP, _JSON_HELP, _HELP,
#      _EXIT_CODE_EPILOG, ...).
#
# Deliberately NOT flagged: a call whose keyword happens to be named
# ``description`` but isn't argparse -- e.g. weld/arch_lint.py's
# ``Rule(description=...)`` lint-rule catalog. Scoped out by requiring the
# call's callee to look like an argparse method/constructor, not just any
# function that accepts a same-named keyword.
_ADR_CITATION_RE = re.compile(r"\bADR[ -](\d{4})\b")
_HELP_CONST_NAME_RE = re.compile(r"(?:^|_)(?:HELP|DESCRIPTION|EPILOG)$")
_ARGPARSE_CALLEE_NAMES = frozenset({
    "ArgumentParser",
    "add_argument",
    "add_parser",
    "add_argument_group",
    "add_mutually_exclusive_group",
    "add_subparsers",
})
_HELP_KWARGS = frozenset({"help", "description", "epilog"})


def _shipping_adr_numbers(rel_paths: list[str], publishignore: list[str]) -> frozenset[str]:
    """Four-digit numbers of ADRs that actually ship: weld/docs/adr/NNNN-*.md
    files present in *rel_paths* that survive the .publishignore filter
    (currently 0001-0004; 0005-0007 are internal-process ADRs excluded by
    name in .publishignore). Mirrors ``_shipping_public_adr_numbers`` in
    tools/audit_publish_adr_refs.sh -- same source of truth, same allowance.
    """
    numbers: set[str] = set()
    for rel_path in rel_paths:
        match = re.match(r"^weld/docs/adr/(\d{4})-[^/]*\.md$", rel_path)
        if not match or _is_publish_ignored(rel_path, publishignore):
            continue
        numbers.add(match.group(1))
    return frozenset(numbers)


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _const_string(value: ast.expr, source: str) -> str:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return ast.get_source_segment(source, value) or ""


def _help_adr_findings(
    rel_path: str, source: str, shipping_numbers: frozenset[str],
) -> list[str]:
    """Flag ADR citations in argparse-visible text that isn't a shipping ADR."""
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []
    findings: list[str] = []

    def record(lineno: int, text: str) -> None:
        for match in _ADR_CITATION_RE.finditer(text):
            if match.group(1) in shipping_numbers:
                continue
            findings.append(
                f"{rel_path}:{lineno}: user-visible help text cites internal "
                f"ADR {match.group(1)} -- strip the parenthetical; move the "
                "rationale to a docstring or comment"
            )

    # Part 1: module-level *_HELP/*_DESCRIPTION/*_EPILOG constants, regardless
    # of local usage -- catches cross-module indirection like ROOT_HELP,
    # whose defining file never itself calls .add_argument(). Restricted to
    # true module level (tree.body, not a full ast.walk) so a same-named
    # local variable buried in an unrelated function can't false-positive;
    # every real constant of this shape in this codebase already lives here.
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        for target in targets:
            if isinstance(target, ast.Name) and _HELP_CONST_NAME_RE.search(target.id):
                record(value.lineno, _const_string(value, source))

    # Part 2: inline literal held directly by an argparse-shaped call, at any
    # nesting depth -- .add_argument() calls live inside builder functions.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _callee_name(node.func) not in _ARGPARSE_CALLEE_NAMES:
            continue
        for kw in node.keywords:
            if kw.arg not in _HELP_KWARGS or isinstance(kw.value, ast.Name):
                continue  # ast.Name case: caught at its definition above (Part 1)
            record(kw.value.lineno, _const_string(kw.value, source))

    return findings


def audit_paths(root: Path, rel_paths: list[str]) -> list[str]:
    """Return audit findings for publish-visible text files."""
    findings: list[str] = []
    publishignore = _publishignore_patterns(root)
    patterns = _patterns()
    normalized_paths = sorted({_normalize(path) for path in rel_paths})
    shipping_adr_numbers = _shipping_adr_numbers(normalized_paths, publishignore)
    for rel_path in normalized_paths:
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
            if not pattern.search(text):
                continue
            if rel_path in ENV_VAR_NAME_ALLOWLIST.get(label, frozenset()):
                continue
            findings.append(f"{rel_path}: {label}")
        if rel_path.endswith(".py"):
            findings.extend(_help_adr_findings(rel_path, text, shipping_adr_numbers))
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

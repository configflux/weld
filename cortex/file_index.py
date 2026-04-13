#!/usr/bin/env python3
"""Keyword-to-file inverted index for the knowledge graph.

Builds an index by walking all source files (.py, .ts, .tsx, .md, .yaml, .yml)
and extracting tokens from: path segments, exported symbol names, class/function
names, import targets, and markdown headings.

Output: .cortex/file-index.json

Usage (via cortex wrapper):
    cortex build-index              # regenerate .cortex/file-index.json
    cortex find <term>              # substring match against the index
"""

from __future__ import annotations

import ast
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from cortex._git import get_git_sha
from cortex.repo_boundary import iter_repo_files

# File extensions to index
INDEXED_EXTENSIONS = frozenset([".py", ".ts", ".tsx", ".md", ".yaml", ".yml"])

def _tokenize_path(rel_path: str) -> list[str]:
    """Extract tokens from path segments and filename (without extension)."""
    parts = Path(rel_path).parts
    tokens: list[str] = []
    for part in parts:
        # Strip extension from the last part (filename)
        stem = Path(part).stem if part == parts[-1] else part
        tokens.append(stem)
    return tokens

def _extract_python_tokens(content: str) -> list[str]:
    """Extract class names, function names, and import targets from Python."""
    tokens: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return tokens

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            tokens.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                tokens.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                tokens.append(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.append(node.module.split(".")[-1])
            for alias in node.names:
                tokens.append(alias.name)

    # Also extract __all__ exports
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                tokens.append(elt.value)

    return tokens

def _extract_markdown_tokens(content: str) -> list[str]:
    """Extract headings from markdown content."""
    tokens: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove leading #s and whitespace
            heading = stripped.lstrip("#").strip()
            if heading:
                # Add the full heading and individual words
                for word in heading.split():
                    # Strip common punctuation
                    clean = word.strip("*`_()[]{}:,;.!?\"'")
                    if clean and len(clean) > 1:
                        tokens.append(clean)
    return tokens

def _extract_yaml_tokens(content: str) -> list[str]:
    """Extract top-level keys from YAML content."""
    tokens: list[str] = []
    for line in content.splitlines():
        # Match top-level keys (no leading whitespace)
        if line and not line[0].isspace() and ":" in line:
            key = line.split(":")[0].strip()
            if key and not key.startswith("#"):
                tokens.append(key)
    return tokens

def _extract_typescript_tokens(content: str) -> list[str]:
    """Extract exported symbol names and import targets from TypeScript."""
    tokens: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        # export function/const/class/interface/type
        m = re.match(
            r"export\s+(?:default\s+)?(?:async\s+)?(?:function|const|let|var|class|interface|type|enum)\s+(\w+)",
            stripped,
        )
        if m:
            tokens.append(m.group(1))
            continue
        # import ... from "module"
        m = re.match(r"import\s+.*from\s+['\"]([^'\"]+)['\"]", stripped)
        if m:
            mod = m.group(1).split("/")[-1]
            tokens.append(mod)
    return tokens

def build_file_index(root: Path) -> dict[str, list[str]]:
    """Walk the repo and build a file-to-tokens mapping.

    Returns a dict mapping relative file paths to their extracted tokens.
    """
    root = root.resolve()
    index: dict[str, list[str]] = {}

    for filepath in iter_repo_files(root):
        suffix = filepath.suffix
        if suffix not in INDEXED_EXTENSIONS:
            continue

        rel_path = str(filepath.relative_to(root))
        tokens = _tokenize_path(rel_path)

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        if suffix == ".py":
            tokens.extend(_extract_python_tokens(content))
        elif suffix in (".ts", ".tsx"):
            tokens.extend(_extract_typescript_tokens(content))
        elif suffix == ".md":
            tokens.extend(_extract_markdown_tokens(content))
        elif suffix in (".yaml", ".yml"):
            tokens.extend(_extract_yaml_tokens(content))

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        if unique:
            index[rel_path] = unique

    return index

def save_file_index(root: Path, index: dict[str, list[str]]) -> Path:
    """Write the file index to .cortex/file-index.json atomically.

    The output envelope is ``{"meta": {...}, "files": {...}}`` so that
    the git SHA captured at index time can be stored alongside the data.
    """
    out_path = root / ".cortex" / "file-index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta: dict = {"version": 1}
    git_sha = get_git_sha(root)
    if git_sha is not None:
        meta["git_sha"] = git_sha

    envelope: dict = {"meta": meta, "files": index}

    fd, tmp = tempfile.mkstemp(
        prefix="file-index.json.tmp.",
        dir=str(out_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, str(out_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return out_path

def load_file_index(root: Path) -> dict[str, list[str]]:
    """Load the file index from .cortex/file-index.json.

    Handles both the legacy flat format (``{path: tokens, ...}``) and
    the new envelope format (``{"meta": {...}, "files": {...}}``).
    """
    idx_path = root / ".cortex" / "file-index.json"
    if not idx_path.exists():
        return {}
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    # New envelope format has a "files" key; legacy is a flat dict of
    # path -> token-list entries (no "files" key at the top level).
    if "files" in data and isinstance(data["files"], dict):
        return data["files"]
    return data

def find_files(index: dict[str, list[str]], term: str) -> dict:
    """Search the index for files matching *term* via substring match.

    Returns ranked results: files where the term appears in more tokens
    are ranked higher.
    """
    term_lower = term.lower()
    results: list[dict] = []

    for path, tokens in index.items():
        matching_tokens = [t for t in tokens if term_lower in t.lower()]
        if matching_tokens:
            results.append({
                "path": path,
                "tokens": matching_tokens,
            })

    # Rank by number of matching tokens (descending), then by path (ascending)
    results.sort(key=lambda r: (-len(r["tokens"]), r["path"]))

    return {"query": term, "files": results}

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for build-index subcommand."""
    parser = argparse.ArgumentParser(
        prog="cortex build-index",
        description="Build the cortex file keyword index",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    index = build_file_index(root)
    out = save_file_index(root, index)
    print(f"Indexed {len(index)} files -> {out}", file=sys.stderr)

if __name__ == "__main__":
    main()

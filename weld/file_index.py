#!/usr/bin/env python3
"""Keyword-to-file inverted index for the connected structure.

Builds an index by walking common source, config, build, and documentation
files. Rich extractors handle Python, TypeScript, Markdown, and YAML; a bounded
generic extractor covers the broader text surfaces that agents often need to
locate by filename or token.

This module owns *which* files are surface, how one file's tokens are
assembled, and the index artifact itself. The raw per-extension token
algorithms live in :mod:`weld._file_index_extractors` (re-exported here so a
caller reads one module) and the read-side matcher lives in
:mod:`weld.file_index_search` (also re-exported); each half stays within the
line cap. :func:`tokens_for_file` -- the per-file assembly step both a full
walk here and an incremental refresh call -- lives in this module rather
than in :mod:`weld._file_index_incremental`: that module is a one-directional
consumer of this one's build/save/load surface, and a primitive shared by
two callers belongs with the module that owns the artifact, not with the one
feature that happened to introduce it (bd 5038-kxx79).

Output: .weld/file-index.json

Usage (via weld wrapper):
    wd build-index              # regenerate .weld/file-index.json
    wd find <term>              # substring match against the index
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Re-exported whole, not selectively: the carve promised that no existing
# ``from weld.file_index import <name>`` stops resolving, and the per-file
# bounds are imported by their own tests under this module's name.
from weld._file_index_extractors import (  # noqa: F401
    _GENERIC_TOKEN_RE,
    _MAX_GENERIC_TOKENS,
    _MAX_PYTHON_CONSTANT_NAME_LEN,
    _MAX_PYTHON_CONSTANTS,
    _PY_CONSTANT_NAME_RE,
    _extract_generic_tokens,
    _extract_markdown_tokens,
    _extract_python_tokens,
    _extract_typescript_tokens,
    _extract_yaml_tokens,
    _is_python_constant_name,
    _module_constant_names,
    _tokenize_path,
)
from weld.repo_boundary import iter_repo_files

# File extensions to index. Discovery coverage is still driven by
# ``.weld/discover.yaml``; this list is the broad file-locator surface for
# ``wd find`` and MCP ``weld_find``.
INDEXED_EXTENSIONS = frozenset({
    ".bzl", ".c", ".cc", ".cpp", ".cs", ".css", ".cxx", ".go", ".h",
    ".hh", ".hpp", ".hxx", ".ipp", ".java", ".js", ".json", ".jsx",
    ".md", ".proto", ".py", ".rs", ".sh", ".sql", ".srv", ".tf", ".toml",
    ".tpp", ".ts", ".tsx", ".xml", ".yaml", ".yml",
})
INDEXED_FILENAMES = frozenset({
    ".bazelrc", "BUILD", "BUILD.bazel", "CMakeLists.txt", "Cargo.toml",
    "Dockerfile", "Makefile", "MODULE.bazel", "go.mod", "go.sum",
    "package.json", "package.xml", "pom.xml", "pyproject.toml",
    "requirements.txt",
})
#: Opening bytes of an interpreter directive. An extensionless file that
#: starts with them is a script; the two allow-lists above cannot express
#: that class, because an extensionless file has no suffix to match and its
#: basename is whatever the project called its entry point.
_SHEBANG = b"#!"


def _opens_with_shebang(filepath: Path) -> bool:
    """Return True if *filepath* begins with an interpreter directive.

    Two bytes decide, so an extensionless binary is rejected on its magic
    number rather than on a decode error and costs the same as a tiny
    script. Offset 0 only: a ``#!`` further down is ordinary content.

    Never raises -- this runs mid-walk, where a path can vanish, be a
    directory, or be unreadable. Each of those means "not text surface",
    not "abort the index build".
    """
    try:
        with filepath.open("rb") as handle:
            return handle.read(2) == _SHEBANG
    except OSError:
        return False


def _is_indexed_file(filepath: Path) -> bool:
    """Return True if *filepath* belongs to the ``wd find`` text surface.

    Rules in ascending cost: a symlink gate answers from an ``lstat``, the
    two allow-lists answer from the path, and the last opens the file, so
    it is reached only for a name neither list claims.

    The third exists because the allow-lists structurally cannot carry a
    repo's extensionless entry points -- ``gradlew``, ``configure``,
    ``mvnw``, git hooks, a project's own top-level task runner. Such a file
    has no suffix to match and a basename that is whatever the project
    chose, so no static list anticipates it, and a repo's most-invoked
    commands stayed invisible to ``wd find`` (bd 0edz).

    Narrow on two deliberate axes. **Extensionless only**: widening to any
    unrecognized extension would head-read every image and archive in the
    tree, for a class already servable by extending
    :data:`INDEXED_EXTENSIONS`. **Non-hidden only**: ``Path(".env").suffix``
    is ``""``, so every dotfile shares the property this keys on -- the
    exclusion keeps secret-bearing dotfiles out of a searchable index
    whatever their first bytes, and an entry point is never hidden anyway.
    Dotfiles that do belong (``.bazelrc``) are admitted above by name.

    **A symlink is never index surface**, and that rule comes first so no
    allow-list can route around it. Git tracks a symlink as an ordinary
    entry, so ``repo/linked.sh -> /outside/outside.sh`` reached the
    extension rule and the index then tokenized a file from outside the
    checkout into a searchable ``.weld/file-index.json`` (bd a2gr). The
    repo boundary is the whole contract this walk is bounded by; a path
    that leaves it is not weld's to read.

    Skipping every symlink rather than only the escaping ones is
    deliberate and matches the two places that already state this posture:
    ``validator_targets._safe_direct_path`` drops symlinks outright and
    ``glob_match.walk_glob`` never follows them. A containment check would
    make the index a third, weaker policy -- one that has to resolve
    correctly through chains, races, and bind mounts to be worth anything,
    where ``is_symlink`` cannot be argued with. What it costs is small and
    recoverable: an in-repo alias like ``docs/latest.md -> docs/v2.md``
    loses its own entry, while the target it points at is tracked and
    indexed on its own name.
    """
    if filepath.is_symlink():
        return False
    if filepath.suffix in INDEXED_EXTENSIONS or filepath.name in INDEXED_FILENAMES:
        return True
    if filepath.suffix or filepath.name.startswith("."):
        return False
    return _opens_with_shebang(filepath)

def tokens_from_content(rel_path: str, content: str) -> list[str]:
    """Canonical sorted token list for *rel_path* given its *content*.

    The pure tokenizer with no I/O, so a caller that already holds the
    bytes (and their hash) can tokenize the exact same bytes -- closing
    the read-twice race where a file edited mid-refresh could be hashed
    and tokenized from different contents
    (:mod:`weld._file_index_incremental`, bd 85tb.2). Dispatches to the
    per-extension extractors re-exported above.
    """
    suffix = Path(rel_path).suffix
    tokens = _tokenize_path(rel_path)
    if suffix == ".py":
        tokens.extend(_extract_python_tokens(content))
    elif suffix in (".ts", ".tsx", ".js", ".jsx"):
        tokens.extend(_extract_typescript_tokens(content))
    elif suffix == ".md":
        tokens.extend(_extract_markdown_tokens(content))
    elif suffix in (".yaml", ".yml"):
        tokens.extend(_extract_yaml_tokens(content))
    else:
        tokens.extend(_extract_generic_tokens(content))
    return sorted(set(tokens))

def tokens_for_file(root: Path, rel_path: str) -> list[str]:
    """Return the canonical sorted token list for one indexed file.

    The single per-file tokenizer shared by this module's own
    :func:`build_file_index` walk and
    :mod:`weld._file_index_incremental`'s patch path, so both emit
    byte-identical per-file token lists (ADR 0012 §3). Returns an empty
    list when the file is unreadable; callers treat an empty result as
    "drop this path", matching the full walk which only stores files with
    a non-empty token set.
    """
    try:
        content = (root / rel_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    return tokens_from_content(rel_path, content)

def build_file_index(root: Path) -> dict[str, list[str]]:
    """Walk the repo and build a file-to-tokens mapping.

    Returns a dict mapping relative file paths to their extracted tokens.
    Per-file token lists are emitted in lexicographic sorted order so the
    in-memory representation is canonical and matches ADR 0012's
    determinism contract: any downstream consumer -- ``save_file_index``,
    the brief builder, CLI ``find``, or MCP tools -- sees the same token
    sequence regardless of AST visit order or dict insertion order. File
    iteration order is already stable via ``iter_repo_files``. Uses
    :func:`tokens_for_file` for the per-file tokenization so this full walk
    and :mod:`weld._file_index_incremental`'s patch path emit
    byte-identical output.
    """
    root = root.resolve()
    index: dict[str, list[str]] = {}

    for filepath in iter_repo_files(root):
        if not _is_indexed_file(filepath):
            continue
        rel_path = str(filepath.relative_to(root))
        unique = tokens_for_file(root, rel_path)
        if unique:
            index[rel_path] = unique

    return index

def save_file_index(root: Path, index: dict[str, list[str]]) -> Path:
    """Write the file index to .weld/file-index.json atomically.

    The output envelope is ``{"meta": {...}, "files": {...}}``. ``meta``
    intentionally carries no ``git_sha``: earlier it recorded ``get_git_sha``
    at write time, but that value names the commit the file is written
    *under*, and the file is then committed *into* the next commit -- so a
    Mode B (``--track-graphs``) repo could never reach a zero-diff steady
    state, restamping on every single no-change ``wd discover`` (bd nwbn). A
    repo-wide search found no reader of the field on either this file or its
    ``file-index-state.json`` companion, so it was dropped rather than moved
    to a sidecar -- the same shape bd lrfu already used for
    ``discovery-state.json``'s unread ``created_at``. Nothing downstream
    needs a migration: this function has always built ``meta`` fresh rather
    than merging onto a prior on-disk value, so a legacy file carrying the
    old key simply stops carrying it the next time anything writes here.

    Serialization follows the determinism contract from ADR 0012 §3:
    every dict emits keys with ``sort_keys=True`` at every level of
    nesting, per-file token lists are sorted lexicographically so list
    contents are stable across runs regardless of AST visit order, the
    output is indented with two spaces, ``ensure_ascii=False`` preserves
    Unicode, and exactly one trailing newline terminates the file. ADR
    0012 targets ``graph.json`` normatively; ``file-index.json`` is a
    sibling artifact consumed by the same audience and rides the same
    contract to keep diffs, caching, and byte-level regression guards
    meaningful -- and, since bd nwbn, commit-independent too.
    """
    out_path = root / ".weld" / "file-index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta: dict = {"version": 1}

    # Sort each file's tokens so list contents are canonical regardless
    # of AST visit / insertion order.
    canonical_files = {path: sorted(tokens) for path, tokens in index.items()}
    envelope: dict = {"meta": meta, "files": canonical_files}

    fd, tmp = tempfile.mkstemp(
        prefix="file-index.json.tmp.",
        dir=str(out_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                envelope,
                f,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
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
    """Load the file index from .weld/file-index.json.

    Handles both the legacy flat format (``{path: tokens, ...}``) and
    the new envelope format (``{"meta": {...}, "files": {...}}``).
    """
    idx_path = root / ".weld" / "file-index.json"
    if not idx_path.exists():
        return {}
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    # New envelope format has a "files" key; legacy is a flat dict of
    # path -> token-list entries (no "files" key at the top level).
    if "files" in data and isinstance(data["files"], dict):
        return data["files"]
    return data

# The read-side matcher behind ``wd find`` / MCP ``weld_find`` lives in
# :mod:`weld.file_index_search`; it is re-exported here so existing
# ``from weld.file_index import find_files`` imports keep working while the
# build half (this module) and the query half stay within the line cap.
from weld.file_index_search import find_files  # noqa: E402,F401

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for build-index subcommand."""
    parser = argparse.ArgumentParser(
        prog="wd build-index",
        description="Build the weld file keyword index",
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

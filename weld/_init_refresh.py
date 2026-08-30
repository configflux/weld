"""Non-destructive refresh of an existing discover.yaml (``wd init --refresh``).

``wd init --force`` regenerates the whole config from a fresh scan, discarding
every hand edit (custom globs, extra strategies, exclusions, comments). Field
eval v0.23.1 Finding 05 made that cost concrete: the only remediation for a
stale config was ``--force``, so a maintainer who had customised the config
either lost those edits or kept an invisible-source graph.

``--refresh`` is the middle path. It treats the *entire existing file text* as
user-owned and appends -- never rewrites -- the source entries for languages
present on disk that no wired strategy claims. The unit of detection is the
language (ADR 0135), reused verbatim from :mod:`weld._unclaimed_sources`, so
this module wires exactly the classes the drift check reports and nothing more.

Merge semantics (append-only, text-preserving):

- **User-owned**: the whole existing ``discover.yaml`` -- every entry, comment,
  exclusion, and custom strategy is kept byte-for-byte and never reordered.
- **Generated**: new ``- glob:`` entries for unclaimed languages only, appended
  under a marked refresh section at the end of the ``sources:`` block.
- **Conflict handling**: an unclaimed language is by definition one *nothing*
  wired claims, so an appended entry cannot collide with an existing wired
  entry. Nothing to reconcile.
- **Version stamp** (Finding 05): the ``# generated-by: weld <version>`` line is
  updated (or inserted) to the current version, so a refreshed config reads as
  current -- the stamp bump is the visible signal a refresh ran.

Security posture: writes only ``discover.yaml`` (atomic replace); never prints
the absolute project root.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from weld._safe_text import sanitize_terminal_line
from weld._unclaimed_sources import (
    UnclaimedClass,
    detect_unclaimed_source_classes,
)

#: Marks the appended block so a human -- and a second ``--refresh`` -- can see
#: exactly which entries the tool added versus what was hand-written.
_REFRESH_MARKER = "# ===== refresh (wd init --refresh) ====="

#: Matches the version-stamp comment ``wd init`` writes (weld/init.py
#: ``_version_stamp``). Rewritten in place so a refresh bumps the stamp without
#: disturbing the surrounding header.
_STAMP_RE = re.compile(r"^# generated-by: weld .*$", re.MULTILINE)


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of a refresh pass, for the caller to report and to test."""

    #: Languages whose entries were appended (empty => no drift wired).
    wired: tuple[UnclaimedClass, ...]
    #: True when the version stamp changed (bumped or inserted).
    stamp_updated: bool
    #: The new full file text (already written to disk by :func:`refresh`).
    new_text: str


def _entries_for_language(language: str) -> list[str]:
    """Return the ``discover.yaml`` source-entry lines for one language.

    Reuses the exact strategy wiring ``wd init`` emits for a fresh config
    (weld/init.py) so a refreshed entry is indistinguishable from a
    freshly-generated one -- the same ``_source_entry`` block and the same
    ``_TREE_SITTER_LANGUAGES`` / test-peer constants. Kept here (not in
    init.py) so init.py stays under its line cap and the refresh-only concern
    lives with the rest of the refresh logic.
    """
    from weld._init_framework_sources import _source_entry
    from weld.init import (
        _TREE_SITTER_EMIT_CALLS,
        _TREE_SITTER_LANGUAGES,
        _TREE_SITTER_TEST_PEER_GLOBS,
    )

    blocks: list[str] = []
    if language == "python":
        blocks.append(_source_entry(
            "src/**/*.py", "file", "python_module",
            comment="Python modules (refresh)"))
        blocks.append(_source_entry(
            "src/**/*.py", "symbol", "python_callgraph",
            comment="Python call graph (refresh)"))
        return blocks

    exts = _TREE_SITTER_LANGUAGES.get(language)
    if not exts:
        return blocks
    extras: dict[str, str] = {"language": language}
    if language in _TREE_SITTER_EMIT_CALLS:
        extras["emit_calls"] = "true"
    label = "C#" if language == "csharp" else language.capitalize()
    for ext in exts:
        blocks.append(_source_entry(
            f"**/*{ext}", "file", "tree_sitter",
            comment=f"{label} sources ({ext}) (refresh)", extra=extras))
    for test_glob in _TREE_SITTER_TEST_PEER_GLOBS.get(language, ()):  # ADR 0046
        blocks.append(_source_entry(
            test_glob, "file", "test_peer",
            comment=f"{label} tests (test_peer; refresh)"))
    return blocks


def _apply_stamp(text: str, version: str | None) -> tuple[str, bool]:
    """Update or insert the ``generated-by`` stamp; return (text, changed).

    An existing stamp is rewritten in place; if the file predates the stamp
    (Finding 05: pre-stamp configs exist), the stamp is inserted just above the
    ``sources:`` line so the refreshed config gains the version signal. When the
    version cannot be resolved the text is returned unchanged (never lies).
    """
    if not version:
        return text, False
    new_stamp = f"# generated-by: weld {version}"
    if _STAMP_RE.search(text):
        updated = _STAMP_RE.sub(new_stamp, text, count=1)
        return updated, updated != text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("sources:"):
            lines.insert(i, f"#\n{new_stamp}\n")
            return "".join(lines), True
    return text, False


def _append_refresh_block(text: str, entries: list[str], version: str | None) -> str:
    """Append the marked refresh block of new entries to the end of the file.

    Entries are appended after all existing content so nothing above moves. The
    marker line names the pass; entries reuse the standard ``- glob:`` shape so
    ``wd discover`` reads them exactly as init-generated ones.
    """
    tag = _REFRESH_MARKER
    if version:
        tag = f"# ===== refresh (wd init --refresh, weld {version}) ====="
    body = "\n".join(entries)
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}\n  {tag}{body}\n"


def refresh(root: Path, output: Path) -> RefreshResult | None:
    """Merge unclaimed-language strategies into an existing ``discover.yaml``.

    Returns ``None`` when ``output`` does not exist (the caller must direct the
    user to ``wd init`` first -- refresh only edits, never creates). Otherwise
    detects unclaimed languages (ADR 0135), appends their entries, updates the
    version stamp, writes atomically, and returns a :class:`RefreshResult`.
    A config with no unclaimed language is a no-op on entries: only the stamp
    is refreshed, so ``wired`` is empty and the caller reports "already
    current".
    """
    from weld._version import weld_version

    if not output.is_file():
        return None

    text = output.read_text(encoding="utf-8")
    unclaimed = detect_unclaimed_source_classes(root)
    version = weld_version() or None

    entries: list[str] = []
    for item in unclaimed:
        entries.extend(_entries_for_language(item.language))

    new_text = text
    if entries:
        new_text = _append_refresh_block(new_text, entries, version)
    new_text, stamp_updated = _apply_stamp(new_text, version)

    if new_text != text:
        _atomic_write(output, new_text)

    return RefreshResult(
        wired=tuple(unclaimed),
        stamp_updated=stamp_updated,
        new_text=new_text,
    )


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + replace (no partial file)."""
    tmp = path.with_suffix(path.suffix + ".refresh-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def run_refresh(root: Path, output: Path) -> None:
    """CLI entry for ``wd init --refresh``: run the merge and report on stderr.

    A missing config is an explicit cannot-answer (ADR 0134): refresh edits, it
    does not create, so it points at ``wd init`` rather than silently writing a
    fresh config. Reporting lives here (not in init.py) to keep that module
    under its line cap.
    """
    result = refresh(root, output)
    if result is None:
        print(
            f"No discover.yaml at {output.name} to refresh: run `wd init` first "
            "to bootstrap a config, then `wd init --refresh` to merge in "
            "newly-detected strategies.",
            file=sys.stderr,
        )
        return
    if not result.wired:
        tail = ("Version stamp refreshed." if result.stamp_updated
                else "Nothing to change.")
        print(
            "discover.yaml is already current: every language present on disk "
            f"has a wired strategy. {tail}",
            file=sys.stderr,
        )
        return
    print(
        f"Merged {len(result.wired)} newly-detected language(s) into "
        f"{output.name}, preserving your existing entries:",
        file=sys.stderr,
    )
    for item in result.wired:
        label = "C#" if item.language == "csharp" else item.language
        print(
            sanitize_terminal_line(f"  wired {label} ({item.file_count} files)"),
            file=sys.stderr,
        )
    print("Run `wd discover` to rebuild the graph with the new sources.",
          file=sys.stderr)

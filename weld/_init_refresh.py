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
  wired claims, so its own entries cannot collide. The whole-repo stacks
  (ROS2, the interface strategies) are keyed on artifacts rather than on a
  language, so those *can* already be wired -- any block whose exact
  ``glob`` + ``strategy`` pair the config already carries is dropped before
  the append.
- **Strategy parity** (field eval v0.24.0 N7): the entries come from
  :func:`weld._init_language_entries.language_source_entries`, the same table
  ``wd init`` generates a fresh config from, so ``--refresh`` wires the C#
  project/MSBuild/ASP.NET/EF-Core stack, the Go and Rust framework entries,
  ``go_package``, ROS2 and the interface strategies -- not just the
  tree-sitter backbone. Before that, following the doctor warning to a clean
  doctor left a further tier silently unwired.
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

from weld._init_language_entries import LanguageWiring, language_source_entries
from weld._safe_text import sanitize_terminal_line
from weld._unclaimed_sources import (
    UnclaimedClass,
    detect_unclaimed_source_classes,
)
from weld._yaml import parse_yaml

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


#: Matches the ``- glob: "..."`` line of a generated entry block, and the
#: ``strategy:`` line under it. Together they key a block against what the
#: existing config already wires. A block neither pattern matches is kept:
#: the guard exists to avoid duplicates, never to silently drop wiring.
_BLOCK_GLOB_RE = re.compile(r'^\s*-\s+glob:\s+"(?P<glob>.*)"\s*$', re.MULTILINE)
_BLOCK_STRATEGY_RE = re.compile(r"^\s*strategy:\s+(?P<strategy>\S+)\s*$", re.MULTILINE)


def _block_key(block: str) -> tuple[str, str] | None:
    """``(glob, strategy)`` for a generated entry block, or None if unreadable."""
    glob = _BLOCK_GLOB_RE.search(block)
    strategy = _BLOCK_STRATEGY_RE.search(block)
    if glob is None or strategy is None:
        return None
    return glob.group("glob"), strategy.group("strategy")


def _wired_block_keys(text: str) -> set[tuple[str, str]]:
    """``(glob, strategy)`` pairs the existing config already wires.

    A ``enabled: false`` entry does not count, matching
    :mod:`weld._unclaimed_sources`: a disabled entry leaves the source just as
    invisible as a missing one, so re-offering it is the right move. An
    unparsable config claims nothing -- refresh then appends its full set
    rather than dropping blocks on a guess.
    """
    try:
        data = parse_yaml(text)
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 -- an unreadable config claims nothing.
        return set()
    keys: set[tuple[str, str]] = set()
    for src in sources:
        if not isinstance(src, dict) or src.get("enabled") is False:
            continue
        glob, strategy = src.get("glob"), src.get("strategy")
        if isinstance(glob, str) and isinstance(strategy, str):
            keys.add((glob, strategy))
    return keys


def _detect_wiring(root: Path, files: list[Path], languages: list[str]) -> LanguageWiring:
    """Run the language-scoped detectors ``wd init`` runs, for *languages*.

    Every field but ``languages`` is a whole-repo detection artifact, computed
    exactly as :func:`weld.init.init` computes it -- narrowing the *selection*
    rather than the *artifacts* is what makes the appended entries a subset of
    a full init's output rather than a different set (see
    :class:`weld._init_language_entries.LanguageWiring`).
    """
    from weld._init_classify import classify_files
    from weld._init_csharp import detect_csharp_artifacts
    from weld._init_interfaces import detect_interfaces, interface_source_entries
    from weld.init_detect import (
        detect_all_from_classified,
        detect_frameworks,
        detect_languages,
        detect_ros2,
    )

    detected = detect_all_from_classified(classify_files(root, files))
    python_globs = (
        detected["python_globs"] if "python" in detect_languages(files) else []
    )
    selected = frozenset(languages)
    return LanguageWiring(
        languages=selected,
        frameworks=tuple(detect_frameworks(root, files)),
        python_globs=tuple(python_globs),
        csharp_flags=(
            detect_csharp_artifacts(files) if "csharp" in selected else None
        ),
        ros2_pkg_roots=tuple(detect_ros2(root, files)),
        interface_sources=tuple(interface_source_entries(
            detect_interfaces(root, files, detected["compose_files"]), python_globs,
        )),
    )


def _entries_for_languages(root: Path, languages: list[str], config_text: str) -> list[str]:
    """Return the entry blocks to append so *languages* are wired like a full init.

    The second file walk (``scan_files``) is deliberate: the unclaimed set comes
    from :func:`weld._unclaimed_sources.detect_unclaimed_source_classes`
    unchanged, so the doctor warning and the remedy can never disagree about
    what is unclaimed. ``wd init --refresh`` is an explicit, infrequent command;
    one extra bounded walk buys that agreement.
    """
    if not languages:
        return []
    from weld.init_detect import scan_files

    code, tests = language_source_entries(_detect_wiring(root, scan_files(root), languages))
    wired = _wired_block_keys(config_text)
    return [block for block in (*code, *tests) if _block_key(block) not in wired]


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

    entries = _entries_for_languages(
        root, [item.language for item in unclaimed], text)

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

"""Non-destructive refresh of an existing discover.yaml (``wd init --refresh``).

``wd init --force`` regenerates the whole config from a fresh scan, discarding
every hand edit (custom globs, extra strategies, exclusions, comments). Field
eval v0.23.1 Finding 05 made that cost concrete: the only remediation for a
stale config was ``--force``, so a maintainer who had customised the config
either lost those edits or kept an invisible-source graph.

``--refresh`` is the middle path. It treats the *entire existing file text* as
user-owned and appends -- never rewrites -- what two comparisons report:

1. **Languages** with files on disk that no wired strategy claims. The unit of
   detection is the language (ADR 0135) and the unit of *claiming* is a matched
   file (ADR 0144), both reused verbatim from :mod:`weld._unclaimed_sources`,
   so this module wires exactly the classes the drift check reports and nothing
   more -- including the dialect case, where a config wiring ``**/*.ts`` gets
   the ``**/*.{ts,tsx}`` family glob appended beside it.
2. **Entries** a language can never stand for: a root config, and a framework
   entry for a language that is already claimed (ADR 0144 § 2026-09-02, bd
   5038-j5o5d). ``tsconfig.json`` is not a language, so no amount of language
   detection could ever deliver it to an existing project; the second
   comparison is keyed on the entry (:mod:`weld._init_entry_offer`) and
   subtracts both what the config carries and what weld recorded writing into
   it (:mod:`weld._init_wired_ledger`), so an entry removed by hand stays out.

Merge semantics (append-only, text-preserving):

- **User-owned**: the whole existing ``discover.yaml`` -- every entry, comment,
  exclusion, and custom strategy is kept byte-for-byte and never reordered.
- **Generated**: the entries the two comparisons report -- ``- glob:`` blocks
  for an unclaimed language's stack and for a detected framework, and a
  ``- files:`` block for detected root configs -- appended together under one
  marked refresh section at the end of the ``sources:`` block.
- **Conflict handling**: an unclaimed language is by definition one *nothing*
  wired claims, so its own entries cannot collide. Everything else can: the
  whole-repo stacks (ROS2, the interface strategies) are keyed on artifacts
  rather than on a language, and the entry pass is keyed on the entry by
  construction. Any block whose ``glob``/``files`` + ``strategy`` key the
  config already carries is dropped before the append -- and so is one the
  *other* pass is appending in the same run, which is the only shape the two
  overlap on (a framework entry for a language that is also unclaimed).
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
- **Wired-entry record**: the ``# wired-entry:`` comment lines are rewritten to
  what weld has now wired, which is what makes the second comparison
  reversible -- delete a line and the next refresh offers that entry again.

Security posture: writes only ``discover.yaml`` (atomic replace); never prints
the absolute project root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from weld._init_entry_offer import (
    EntryKey,
    EntryWiring,
    block_entry_key,
    block_glob_and_strategy,
    entry_blocks,
    entry_keys,
)
from weld._init_language_entries import LanguageWiring, language_source_entries
from weld._init_wired_ledger import apply_ledger, config_entry_keys, ledger_keys
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
    #: ``(strategy, target)`` keys the entry-shaped pass appended, in the order
    #: they were written. Kept apart from ``wired`` because the two answer
    #: different questions -- which languages were invisible, and which
    #: detected entries this config had never been offered.
    entries: tuple[EntryKey, ...] = ()
    #: True when the ``# wired-entry:`` record changed. Reported, because a run
    #: that wires nothing can still write the file -- seeding the record of a
    #: config that predates it -- and saying "nothing to change" there would be
    #: false.
    record_updated: bool = False


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


@dataclass(frozen=True)
class _Detection:
    """One pass of ``wd init``'s detectors, shared by both comparisons.

    The language pass and the entry pass need overlapping halves of the same
    detection (``detect_frameworks`` reads source files, so running it twice is
    the one cost worth avoiding here). Detecting once and selecting twice is
    also what keeps the two passes describing the same repository.
    """

    files: list[Path]
    detected: dict
    frameworks: tuple[tuple[str, str, str], ...]
    languages: frozenset[str]


def _detect(root: Path, files: list[Path]) -> _Detection:
    """Run the shared detectors once over *files*."""
    from weld._init_classify import classify_files
    from weld.init_detect import (
        detect_all_from_classified,
        detect_frameworks,
        detect_languages,
    )

    return _Detection(
        files=files,
        detected=detect_all_from_classified(classify_files(root, files)),
        frameworks=tuple(detect_frameworks(root, files)),
        languages=frozenset(detect_languages(files)),
    )


def _language_wiring(
    root: Path, det: _Detection, languages: list[str],
) -> LanguageWiring:
    """Run the language-scoped detectors ``wd init`` runs, for *languages*.

    Every field but ``languages`` is a whole-repo detection artifact, computed
    exactly as :func:`weld.init.init` computes it -- narrowing the *selection*
    rather than the *artifacts* is what makes the appended entries a subset of
    a full init's output rather than a different set (see
    :class:`weld._init_language_entries.LanguageWiring`).
    """
    from weld._init_csharp import detect_csharp_artifacts
    from weld._init_interfaces import detect_interfaces, interface_source_entries
    from weld.init_detect import detect_ros2

    python_globs = (
        det.detected["python_globs"] if "python" in det.languages else []
    )
    selected = frozenset(languages)
    return LanguageWiring(
        languages=selected,
        frameworks=det.frameworks,
        python_globs=tuple(python_globs),
        csharp_flags=(
            detect_csharp_artifacts(det.files) if "csharp" in selected else None
        ),
        ros2_pkg_roots=tuple(detect_ros2(root, det.files)),
        interface_sources=tuple(interface_source_entries(
            detect_interfaces(root, det.files, det.detected["compose_files"]),
            python_globs,
        )),
    )


def _entry_wiring(det: _Detection) -> EntryWiring:
    """The entry-shaped detectors' view of the same detection pass.

    ``languages`` is the set present on disk rather than the unclaimed subset:
    a framework entry is missing or not independently of whether its language
    is claimed, which is why this comparison exists beside the language one
    (bd 5038-j5o5d).
    """
    return EntryWiring(
        root_configs=tuple(det.detected["root_configs"]),
        frameworks=det.frameworks,
        python_globs=(
            tuple(det.detected["python_globs"])
            if "python" in det.languages else ()
        ),
        languages=det.languages,
    )


def _entries_for_languages(
    root: Path, det: _Detection, languages: list[str], config_text: str,
) -> list[str]:
    """Return the entry blocks to append so *languages* are wired like a full init.

    The unclaimed set comes from
    :func:`weld._unclaimed_sources.detect_unclaimed_source_classes` unchanged,
    so the doctor warning and the remedy can never disagree about what is
    unclaimed.
    """
    if not languages:
        return []
    code, tests = language_source_entries(
        _language_wiring(root, det, languages))
    wired = _wired_block_keys(config_text)
    return [
        block for block in (*code, *tests)
        if block_glob_and_strategy(block) not in wired
    ]


def _pending_keys(blocks: list[str]) -> set[EntryKey]:
    """``(strategy, glob)`` for blocks the language pass is already appending.

    The two passes overlap on exactly one shape: a framework entry for a
    language that is *also* unclaimed. The language pass emits that language's
    whole stack, framework entries included, and the entry pass emits framework
    entries whatever the language's claim status -- which is the gap it exists
    for. Without this the appended section carried the same ``strategy: gin``
    entry twice.
    """
    return {
        key for key in (block_entry_key(block) for block in blocks)
        if key is not None
    }


def _entries_for_config(
    det: _Detection, config_text: str, pending: set[EntryKey],
) -> tuple[list[str], list[EntryKey], set[EntryKey]]:
    """The entry-shaped pass: blocks to append, their keys, and the new record.

    ``known`` is what the config carries, union what weld recorded writing into
    it, union what the language pass is appending in this same run. A detected
    entry therefore drops out of the offer because it is wired now, because it
    is being wired now, or because it was wired once and has been removed
    since -- the last being the distinction an append-only diff against the
    live config cannot make.

    The returned record folds three sets together: what was already recorded,
    the detectable keys this config already accounts for (which is how a config
    written before the record existed seeds itself, once), and what this run
    appended -- through either pass, so a framework entry the language pass
    wrote is as durable against a hand removal as one this pass wrote.
    """
    wiring = _entry_wiring(det)
    carried = config_entry_keys(config_text)
    recorded = ledger_keys(config_text)
    blocks, keys = entry_blocks(
        wiring, frozenset(carried | recorded | pending))
    detectable = set(entry_keys(wiring))
    accounted = detectable & (carried | pending)
    return blocks, keys, recorded | accounted | set(keys)


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
    """Merge newly-detected sources into an existing ``discover.yaml``.

    Returns ``None`` when ``output`` does not exist (the caller must direct the
    user to ``wd init`` first -- refresh only edits, never creates). Otherwise
    runs both comparisons -- unclaimed languages (ADR 0135, ADR 0144) and
    unoffered entries (ADR 0144 § 2026-09-02) -- appends what each reports,
    updates the version stamp and the wired-entry record, writes atomically,
    and returns a :class:`RefreshResult`. A config both comparisons find
    current is a no-op on entries: only the stamp is refreshed, so ``wired``
    and ``entries`` are both empty and the caller reports "already current".

    The one file walk feeds both passes; ``detect_unclaimed_source_classes``
    keeps its own, deliberately, so the doctor warning and the remedy cannot
    disagree about what is unclaimed. ``wd init --refresh`` is an explicit,
    infrequent command; one extra bounded walk buys that agreement.
    """
    from weld._version import weld_version
    from weld.init_detect import scan_files

    if not output.is_file():
        return None

    text = output.read_text(encoding="utf-8")
    unclaimed = detect_unclaimed_source_classes(root)
    version = weld_version() or None
    det = _detect(root, scan_files(root))

    blocks = _entries_for_languages(
        root, det, [item.language for item in unclaimed], text)
    entry_only, wired_keys, record = _entries_for_config(
        det, text, _pending_keys(blocks))
    blocks.extend(entry_only)

    new_text = text
    if blocks:
        new_text = _append_refresh_block(new_text, blocks, version)
    new_text, stamp_updated = _apply_stamp(new_text, version)
    new_text, record_updated = apply_ledger(new_text, record)

    if new_text != text:
        _atomic_write(output, new_text)

    return RefreshResult(
        wired=tuple(unclaimed),
        stamp_updated=stamp_updated,
        new_text=new_text,
        entries=tuple(wired_keys),
        record_updated=record_updated,
    )


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + replace (no partial file)."""
    tmp = path.with_suffix(path.suffix + ".refresh-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

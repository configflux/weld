"""Detect source-language classes no wired strategy claims (ADR 0135).

`.weld/discover.yaml` is generated once by `wd init` and never revisited, so a
checkout initialised before a strategy shipped keeps discovering with the old
config and nothing reports the gap -- a repo can have 100% of its source
invisible to the graph while `wd doctor` reports healthy (field eval v0.23.1
Finding 05).

This module runs the *same* read-only detection pass `wd init` uses
(:func:`weld.init_detect.scan_files` -- extension-only, no file contents read)
and asks, of the files it finds, which ones an enabled source entry actually
claims. A language with files no entry claims is *unclaimed*: that source is
invisible.

**A claim is a matched file, not a strategy name** (ADR 0144). The original
check compared languages on disk against the flat set of ``strategy:`` values
the config mentions, which read one ``tree_sitter`` entry as claiming every
tree-sitter language, and read a glob that matches none of a language's files
as claiming it anyway -- so a config wiring ``**/*.ts`` spoke for the ``.tsx``
files ``EXT_TO_LANG`` counts as the same language, and two-thirds of a Node
repo stayed invisible with every diagnostic reporting it healthy.

Granularity is deliberately the language for *reporting* and the extension for
*claiming* (ADR 0144 noise control): a repo that merely lacks an optional
framework extractor but does wire ``python_module`` is silent, and so is one
whose config scopes a language to a subtree on purpose -- one claimed ``.py``
file settles Python. Only a file class nothing reads is reported.

Security posture: never prints the absolute project root or environment. The
only user-facing values are language names and integer file counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from weld._yaml import parse_yaml

#: Minimum *unclaimed* files of a language before it is reported. The
#: field-eval repro is 8 ``.cs`` files, and any invisible file is a real gap,
#: so the floor is 1 (effectively "present and unread"). Kept as a named knob
#: rather than a literal so the noise threshold has one documented home.
_MIN_FILES = 1

#: The two remedies, in the order a maintainer should reach for them. Named
#: once so ``wd doctor``'s warning and ``wd prime``'s next step cannot drift
#: apart on which one they lead with: ``--force`` regenerates the config from
#: a fresh scan and discards hand edits, ``--refresh`` merges and keeps them,
#: so a diagnostic that names only ``--force`` advises throwing work away.
_REFRESH = "wd init --refresh"
_FORCE = "wd init --force"
_REMEDY = (
    f"{_REFRESH} (keeps your entries) or {_FORCE} (regenerate from scratch)"
)

#: Language -> the strategy names that can *read* that language. An entry
#: claims a file only if its strategy is in the file's language's set (ADR
#: 0135: membership, not id equality -- we report a language nothing speaks
#: for, not a missing optional extractor) *and* its glob matches that file
#: (ADR 0144). ``tree_sitter`` is the common backbone for the tree-sitter
#: languages; the framework/build strategies are additive claimers.
_CLAIMING_STRATEGIES: dict[str, frozenset[str]] = {
    "python": frozenset({"python_module", "python_callgraph"}),
    "csharp": frozenset({
        "tree_sitter", "csharp_solution", "csharp_project",
        "csharp_msbuild_targets", "csharp_test_framework",
        "csharp_aspnet_routes", "csharp_efcore",
    }),
    "go": frozenset({"tree_sitter", "go_package", "gin"}),
    "rust": frozenset({"tree_sitter", "axum"}),
    "typescript": frozenset({"tree_sitter", "express", "next"}),
    # JavaScript was absent from this table until ADR 0142 D1, so a repo whose
    # ``.js`` files nothing wired was not merely unreported -- it was
    # unrefreshable, because ``wd init --refresh`` wires exactly the languages
    # this function returns. The omission was invisible while ``wd init`` had
    # no JavaScript entry to offer; now that it has one, a JavaScript repo can
    # be told about the gap and can close it.
    "javascript": frozenset({"tree_sitter", "express", "next"}),
    "cpp": frozenset({"tree_sitter", "cpp_buildsystem_detector"}),
    "java": frozenset({"tree_sitter"}),
}


@dataclass(frozen=True)
class UnclaimedClass:
    """A language with files on disk that no enabled source entry claims.

    ``file_count`` is the number of *unclaimed* files, which for a language
    nothing wires at all -- the field eval v0.23.1 Finding 05 case -- is every
    file it has.
    """

    language: str
    file_count: int


def _enabled_sources(weld_dir: Path) -> list[dict]:
    """Return the enabled source entries of ``discover.yaml``.

    A source entry with ``enabled: false`` claims nothing -- a disabled entry
    leaves the source just as invisible as a missing one. An unreadable config
    claims nothing either: the caller then reports every language it finds,
    which is the truthful answer for a config weld cannot read.
    """
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return []
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 -- an unreadable config claims nothing.
        return []
    return [
        src
        for src in sources
        if isinstance(src, dict) and src.get("enabled") is not False
    ]


def _claim_entries(sources: list[dict], language: str) -> list[dict]:
    """The entries that could claim *language*, by strategy.

    Brace groups used to be expanded here before the entries were handed on,
    because :func:`weld._staleness_coverage.in_scope_files` did not expand
    them itself -- so an unexpanded ``**/*.{ts,tsx}``, what ``wd init`` writes
    for every TypeScript repo, matched nothing there and the remedy could
    never close what this reports. That resolver now expands as ``walk_glob``
    does (bd 2z5no), which is where the expansion belongs: this asks which
    files an entry claims, and how a ``glob:`` resolves is the resolver's
    business, not its caller's. The dialect probe over the real CLI
    (``weld_unclaimed_dialect_e2e_test``) is unchanged and still green, which
    is what says the pre-expansion had become redundant rather than merely
    unused.
    """
    claimers = _CLAIMING_STRATEGIES.get(language) or frozenset()
    return [src for src in sources if src.get("strategy") in claimers]


#: How many candidate paths :func:`_any_claimed` hands the resolver at a time.
#: Big enough that a claimed class usually settles in one pass, small enough
#: that settling never costs the whole class.
_CLAIM_PROBE_CHUNK = 64


def _any_claimed(entries: list[dict], paths: list[str]) -> bool:
    """True when *entries* resolve at least one of *paths*.

    Asked in chunks because the answer is a boolean while
    :func:`weld._staleness_coverage.in_scope_files` resolves every path it is
    given: on this repo that is 1760 Python files against eight Python entries
    for a question the first matched file settles. Chunking keeps the one
    shared resolution path (its regex translation, its exclude semantics, its
    structural prunes) rather than open-coding a cheaper near-copy.
    """
    from weld._staleness_coverage import in_scope_files

    for start in range(0, len(paths), _CLAIM_PROBE_CHUNK):
        if in_scope_files(entries, paths[start:start + _CLAIM_PROBE_CHUNK]):
            return True
    return False


def _unclaimed_file_counts(
    sources: list[dict], rel_paths: list[str],
) -> dict[str, int]:
    """Per-language count of *rel_paths* no entry in *sources* claims.

    The claim unit is the **extension**, not the file: a language's ``.tsx``
    files are unclaimed when no entry that can read TypeScript matches any of
    them, even though its ``.ts`` files are matched. Per-file accounting would
    report every deliberately-unscoped file (951 of this repo's 1760 Python
    files live under ``examples/`` and ``weld/tests/fixtures/``), and
    per-language accounting lets one matched ``.ts`` speak for an unread
    ``.tsx`` -- ADR 0144 has the measurements.

    Only languages in :data:`_CLAIMING_STRATEGIES` are considered: a language
    weld cannot extract at all is not something re-running ``wd init`` would
    fix, so reporting it would be noise, not signal.
    """
    from weld.init_detect import EXT_TO_LANG

    by_class: dict[tuple[str, str], list[str]] = {}
    for rel in rel_paths:
        ext = PurePosixPath(rel).suffix.lower()
        language = EXT_TO_LANG.get(ext)
        if language is None or language not in _CLAIMING_STRATEGIES:
            continue
        by_class.setdefault((language, ext), []).append(rel)

    counts: dict[str, int] = {}
    entries_by_language: dict[str, list[dict]] = {}
    for (language, _ext), paths in by_class.items():
        if language not in entries_by_language:
            entries_by_language[language] = _claim_entries(sources, language)
        entries = entries_by_language[language]
        if entries and _any_claimed(entries, paths):
            continue
        counts[language] = counts.get(language, 0) + len(paths)
    return counts


def detect_unclaimed_from_sources(
    sources: list[dict], rel_paths: list[str],
) -> list[UnclaimedClass]:
    """Pure comparison of files on disk against what the config claims.

    Split from the disk walk so it is unit-testable without a filesystem and
    reused by both the doctor and prime surfaces. *rel_paths* are
    repo-relative POSIX paths, the vocabulary ``glob:`` matches in.

    Returns unclaimed classes sorted by descending file count then name, so the
    most-invisible language leads.
    """
    unclaimed = [
        UnclaimedClass(language, count)
        for language, count in _unclaimed_file_counts(sources, rel_paths).items()
        if count >= _MIN_FILES
    ]
    unclaimed.sort(key=lambda u: (-u.file_count, u.language))
    return unclaimed


def detect_unclaimed_source_classes(root: Path) -> list[UnclaimedClass]:
    """Return languages under ``root`` with files no source entry claims.

    Runs the ``wd init`` detection pass read-only (the repo-bounded file walk,
    extension-only, no file contents read) and intersects it with what
    ``.weld/discover.yaml``'s enabled entries resolve. One walk, no second
    traversal: the claim is decided by matching the list already in hand.
    Read-only -- touches nothing.
    """
    from weld._rel_path import rel_to_root
    from weld.init_detect import EXT_TO_LANG, scan_files

    weld_dir = root / ".weld"
    if not weld_dir.is_dir():
        return []
    sources = _enabled_sources(weld_dir)
    # Only a file whose extension names a language can be claimed or unclaimed,
    # and relativising a path costs more than the whole claim match does, so
    # the rest are dropped before the conversion. A *superset* of what
    # :func:`_unclaimed_file_counts` keeps -- it still applies its own rule, so
    # this can never become a second, drifting filter.
    rel_paths = [
        rel_to_root(path, root)
        for path in scan_files(root)
        if path.suffix.lower() in EXT_TO_LANG
    ]
    return detect_unclaimed_from_sources(sources, rel_paths)


def _label(language: str) -> str:
    """Human display label for a language (C# / C++ read better than the id)."""
    return {"csharp": "C#", "cpp": "C/C++"}.get(language, language)


def unclaimed_message(item: UnclaimedClass) -> str:
    """One-line advisory for a single unclaimed language.

    Shared by ``wd doctor`` and ``wd prime`` so the two surfaces cannot drift
    on wording. Names the count, the language, and both remedies -- the
    non-destructive one first. ``wd init --force`` regenerates the config from
    a fresh scan and discards hand edits, so naming it alone told a maintainer
    following the advice to throw their own customisation away;
    ``wd init --refresh`` merges the missing entries in and keeps it.

    The count is the *unclaimed* files, so the sentence says "files ... that no
    wired strategy claims" rather than the original "files present but no wired
    strategy claims '<language>'": since ADR 0144 a language can be partly
    claimed -- a wired ``**/*.ts`` beside unread ``.tsx`` files -- and the
    original wording would be false there.
    """
    plural = "file" if item.file_count == 1 else "files"
    return (
        f"{item.file_count} {_label(item.language)} {plural} present that no "
        f"wired strategy claims -> run: {_REMEDY}"
    )


def check_unclaimed_sources(root: Path, result_cls: type) -> list:
    """Return ``warn`` results for every unclaimed language under ``root``.

    ``result_cls`` is ``weld.doctor.CheckResult`` -- passed in to avoid a
    circular import. Each result sits in the ``Config`` section with a stable
    ``note_id`` so ``wd doctor --ack`` can dismiss a language a repo
    deliberately leaves ungraphed. Never raises: a diagnostic must not crash
    the command that runs it, so a detection failure yields no results rather
    than an exception.
    """
    try:
        unclaimed = detect_unclaimed_source_classes(root)
    except Exception:  # noqa: BLE001 -- a diagnostic must never crash doctor.
        return []
    return [
        result_cls(
            "warn",
            unclaimed_message(item),
            "Config",
            note_id=f"unclaimed-source-{item.language}",
        )
        for item in unclaimed
    ]


def prime_unclaimed_lines(root: Path, status) -> tuple[list[str], list[str]]:
    """Return ``(lines, steps)`` for the ``wd prime`` unclaimed-source check.

    ``status`` is ``weld.prime._status`` (``(tag, msg) -> str``). Emits one
    WARN-tagged line per unclaimed language plus a single ``wd init --refresh``
    next step -- a next step is a command to run, so it is the one that keeps
    hand edits; the WARN line itself still names ``--force`` as the
    regenerate-from-scratch alternative. Read-only; never raises -- a
    diagnostic must not break prime.
    """
    try:
        unclaimed = detect_unclaimed_source_classes(root)
    except Exception:  # noqa: BLE001 -- a diagnostic must never crash prime.
        return [], []
    if not unclaimed:
        return [], []
    lines = [status("WARN", unclaimed_message(item)) for item in unclaimed]
    return lines, [_REFRESH]

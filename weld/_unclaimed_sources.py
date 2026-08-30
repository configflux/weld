"""Detect source-language classes no wired strategy claims (ADR 0135).

`.weld/discover.yaml` is generated once by `wd init` and never revisited, so a
checkout initialised before a strategy shipped keeps discovering with the old
config and nothing reports the gap -- a repo can have 100% of its source
invisible to the graph while `wd doctor` reports healthy (field eval v0.23.1
Finding 05).

This module runs the *same* read-only detection pass `wd init` uses
(:func:`weld.init_detect.scan_files` + :func:`~weld.init_detect.detect_languages`
-- extension-only, no file contents read) and compares the languages present on
disk against the strategies the config actually wires. A language present on
disk that no wired strategy claims is *unclaimed*: its source is invisible.

Granularity is deliberately the language, not the framework (ADR 0135 noise
control): a repo that merely lacks an optional framework extractor but does wire
``python_module`` is silent -- only a language nothing claims is reported.

Security posture: never prints the absolute project root or environment. The
only user-facing values are language names and integer file counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml

#: Minimum files of a language before an unclaimed language is reported. The
#: field-eval repro is 8 ``.cs`` files, and any invisible language is a real
#: gap, so the floor is 1 (effectively "present at all"). Kept as a named knob
#: rather than a literal so the noise threshold has one documented home.
_MIN_FILES = 1

#: Language -> set of strategy names that, when wired in ``discover.yaml``,
#: mean that language's source is claimed. A language is claimed when the
#: config wires *any* strategy in its set (ADR 0135: membership, not id
#: equality -- we report a language nothing speaks for, not a missing optional
#: extractor). ``tree_sitter`` is the common backbone for the tree-sitter
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
    "typescript": frozenset({"tree_sitter"}),
    "cpp": frozenset({"tree_sitter", "cpp_buildsystem_detector"}),
    "java": frozenset({"tree_sitter"}),
}


@dataclass(frozen=True)
class UnclaimedClass:
    """A language present on disk that no wired strategy claims."""

    language: str
    file_count: int


def _wired_strategies(weld_dir: Path) -> set[str]:
    """Return the set of strategy names referenced by enabled sources.

    A source entry with ``enabled: false`` does not count as wiring its
    strategy -- a disabled entry leaves the language just as invisible as a
    missing one. Mirrors :func:`weld._doctor_strategies._collect_strategy_usage`
    but returns only the enabled set (the disabled/enabled split is not needed
    here).
    """
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return set()
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 -- an unreadable config claims nothing.
        return set()
    wired: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        if src.get("enabled") is False:
            continue
        strat = src.get("strategy")
        if isinstance(strat, str):
            wired.add(strat)
    return wired


def detect_unclaimed_from_counts(
    language_counts: dict[str, int], wired: set[str],
) -> list[UnclaimedClass]:
    """Pure comparison of detected languages against wired strategies.

    Split from the disk walk so it is unit-testable without a filesystem and
    reused by both the doctor and prime surfaces. Only languages in
    :data:`_CLAIMING_STRATEGIES` are considered -- a language with no known
    claiming set (one weld cannot extract at all) is not something re-running
    ``wd init`` would fix, so reporting it would be noise, not signal.

    Returns unclaimed classes sorted by descending file count then name, so the
    most-invisible language leads.
    """
    unclaimed: list[UnclaimedClass] = []
    for lang, count in language_counts.items():
        if count < _MIN_FILES:
            continue
        claimers = _CLAIMING_STRATEGIES.get(lang)
        if claimers is None:
            continue
        if wired & claimers:
            continue
        unclaimed.append(UnclaimedClass(lang, count))
    unclaimed.sort(key=lambda u: (-u.file_count, u.language))
    return unclaimed


def detect_unclaimed_source_classes(root: Path) -> list[UnclaimedClass]:
    """Return languages present under ``root`` that no wired strategy claims.

    Runs the ``wd init`` detection pass read-only (extension-only language
    counting over the repo-bounded file walk) and compares it against the
    strategies ``.weld/discover.yaml`` wires. Read-only: touches nothing.
    """
    from weld.init_detect import detect_languages, scan_files

    weld_dir = root / ".weld"
    if not weld_dir.is_dir():
        return []
    wired = _wired_strategies(weld_dir)
    files = scan_files(root)
    counts = detect_languages(files)
    return detect_unclaimed_from_counts(counts, wired)


def _label(language: str) -> str:
    """Human display label for a language (C# / C++ read better than the id)."""
    return {"csharp": "C#", "cpp": "C/C++"}.get(language, language)


def unclaimed_message(item: UnclaimedClass) -> str:
    """One-line advisory for a single unclaimed language.

    Shared by ``wd doctor`` and ``wd prime`` so the two surfaces cannot drift
    on wording. Names the count, the language, and the fix (``wd init
    --force``), matching the field-report's expected output.
    """
    plural = "file" if item.file_count == 1 else "files"
    return (
        f"{item.file_count} {_label(item.language)} {plural} present but no "
        f"wired strategy claims '{item.language}' -> run: wd init --force"
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
    WARN-tagged line per unclaimed language plus a single ``wd init --force``
    next step. Read-only; never raises -- a diagnostic must not break prime.
    """
    try:
        unclaimed = detect_unclaimed_source_classes(root)
    except Exception:  # noqa: BLE001 -- a diagnostic must never crash prime.
        return [], []
    if not unclaimed:
        return [], []
    lines = [status("WARN", unclaimed_message(item)) for item in unclaimed]
    return lines, ["wd init --force"]

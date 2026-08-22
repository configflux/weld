"""Strategy-related diagnostic checks for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under the
400-line cap. These helpers read ``.weld/discover.yaml`` and classify
referenced strategies into enabled vs disabled, then check whether each
enabled strategy resolves to a bundled or project-local plugin.

Security posture: this module never prints the absolute project root or
environment variables. Strategy identifiers are taken only from
``discover.yaml`` and echoed verbatim; ``check_failed_files`` echoes
repo-relative paths already recorded in ``discovery-state.json`` by a
completed discovery run -- the same paths a user already sees via ``git
status`` or their editor, never an absolute path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml
from weld.discovery_state import load_state


@dataclass(frozen=True)
class _Result:
    level: str
    message: str
    section: str = "Strategies"


def _collect_strategy_usage(weld_dir: Path) -> tuple[set[str], set[str]]:
    """Split strategies in discover.yaml into (enabled, disabled).

    A source entry with ``enabled: false`` contributes to the disabled set.
    Anything else contributes to enabled. If the same strategy is both
    enabled and disabled across different sources, enabled wins.
    """
    path = weld_dir / "discover.yaml"
    enabled: set[str] = set()
    disabled: set[str] = set()
    if not path.is_file():
        return enabled, disabled
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        return enabled, disabled
    for src in sources:
        if not isinstance(src, dict):
            continue
        strat = src.get("strategy")
        if not isinstance(strat, str):
            continue
        if src.get("enabled") is False:
            disabled.add(strat)
        else:
            enabled.add(strat)
    disabled.difference_update(enabled)
    return enabled, disabled


def _resolve_strategy(name: str, root: Path, bundled_dir: Path) -> bool:
    """Return True if *name* resolves to a project-local or bundled plugin."""
    if name == "external_json":
        return True
    project_local = root / ".weld" / "strategies" / f"{name}.py"
    bundled = bundled_dir / f"{name}.py"
    return project_local.is_file() or bundled.is_file()


def check_strategies(
    weld_dir: Path, root: Path, bundled_dir: Path, result_cls: type
) -> list:
    """Return a list of strategy-related check results.

    ``result_cls`` is ``weld.doctor.CheckResult`` -- passed in to avoid a
    circular import.
    """
    enabled, disabled = _collect_strategy_usage(weld_dir)
    if not enabled and not disabled:
        return []

    missing: list[str] = []
    for strat in sorted(enabled):
        if not _resolve_strategy(strat, root, bundled_dir):
            missing.append(strat)

    results: list = []
    if missing:
        for name in missing:
            results.append(
                result_cls(
                    "fail",
                    f"strategy '{name}' referenced but not found",
                    "Strategies",
                )
            )
    else:
        count = len(enabled)
        suffix = "strategies" if count != 1 else "strategy"
        results.append(
            result_cls(
                "ok",
                f"all {count} referenced {suffix} resolved",
                "Strategies",
            )
        )

    if enabled:
        names = ", ".join(sorted(enabled))
        results.append(
            result_cls(
                "ok",
                f"enabled strategies ({len(enabled)}): {names}",
                "Strategies",
            )
        )
    if disabled:
        names = ", ".join(sorted(disabled))
        results.append(
            result_cls(
                "warn",
                f"disabled strategies ({len(disabled)}): {names}",
                "Strategies",
            )
        )
    return results


def check_trust_boundaries(weld_dir: Path, result_cls: type) -> list:
    """Warn when discovery will load repo-owned code or commands."""
    results: list = []

    strategies_dir = weld_dir / "strategies"
    local_strategies = (
        sorted(path.name for path in strategies_dir.glob("*.py"))
        if strategies_dir.is_dir()
        else []
    )
    if local_strategies:
        sample = ", ".join(local_strategies[:3])
        extra = (
            ""
            if len(local_strategies) <= 3
            else f", +{len(local_strategies) - 3} more"
        )
        results.append(
            result_cls(
                "warn",
                "project-local strategies present "
                f"({sample}{extra}) -- run wd discover only on trusted repos",
                "Strategies",
            )
        )

    config_path = weld_dir / "discover.yaml"
    if not config_path.is_file():
        return results
    try:
        data = parse_yaml(config_path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        return results

    if any(
        isinstance(src, dict) and src.get("strategy") == "external_json"
        for src in sources
    ):
        results.append(
            result_cls(
                "warn",
                "external_json adapters execute configured commands with "
                "the repository root as cwd -- use only with trusted repos",
                "Strategies",
            )
        )
    return results


def check_failed_sources(root: Path, result_cls: type) -> list:
    """Report source entries ``discovery-state.json`` recorded as failed.

    Bd um00: a source entry with no ``glob``/``path``/``files`` key (a
    command-only ``external_json`` adapter) has no file to carry a failure
    signal, so the per-file repair and every freshness check are structurally
    blind to it -- the only trace was a stderr line on the run that hit it.
    ``DiscoveryState.sources_with_failed_strategy`` is the record; this is
    the report.

    Deliberately not fed into ``coverage_stale`` / ``wd stale`` (bd 0jck's
    reasoning for the file-keyed sibling, applied here directly): a
    permanently failing command would then earn a refresh on every read,
    forever, for no benefit. Read-only and off the hot read path -- this
    parses the already-written state file, at most once per ``wd doctor``
    invocation, same as :func:`check_strategies` reads ``discover.yaml``.
    """
    state = load_state(root)
    if state is None or not state.sources_with_failed_strategy:
        return []
    results: list = []
    for info in state.sources_with_failed_strategy.values():
        kind = info.get("kind", "unknown") if isinstance(info, dict) else "unknown"
        reason = info.get("reason", "") if isinstance(info, dict) else ""
        message = f"source entry failed ({kind})"
        if reason:
            message += f": {reason}"
        results.append(result_cls("warn", message, "Strategies"))
    return results


#: Cap on inline file paths shown for a ``files_with_failed_strategy``
#: report. Unlike its entry-keyed sibling above -- bounded implicitly by the
#: number of ``discover.yaml`` source entries, typically a handful -- one
#: missing optional dependency or one disallowed strategy under ``--safe``
#: can fail every file of a language, so this field has no such ceiling.
#: Bounded here instead (ADR 0082 discipline: bound the list, but always
#: report the true count so a large failure set is never undercounted).
_MAX_FAILED_FILES_SHOWN = 10


def check_failed_files(root: Path, result_cls: type) -> list:
    """Report files ``discovery-state.json`` recorded as strategy failures.

    Bd hch4: a file no strategy could speak for this run -- refused by
    ``--safe``, a strategy that would not load, or a file a strategy could
    not parse -- is recorded in ``DiscoveryState.files_with_failed_strategy``
    so the ADR 0008 per-file repair keeps retrying it while the vouching
    audit stays exempt. Until bd 0jck (this function), the only trace was a
    stderr line on the run that hit it.

    File-keyed sibling of :func:`check_failed_sources` (bd um00,
    entry-keyed), reached the same way: read-only and off the hot read
    path, one parse of the already-written state file per ``wd doctor``
    invocation. Deliberately not fed into ``coverage_stale`` / ``wd
    stale`` -- a permanently failing file would then earn a refresh, which
    would fail the same way, on every read, forever.

    Unlike the entry-keyed sibling, no per-file reason is recorded here
    (only the path), so the message cannot name a strategy the way
    ``check_failed_sources`` names a ``kind`` -- it points instead at
    where that detail actually lives: stderr on the next ``wd discover``.
    """
    state = load_state(root)
    if state is None or not state.files_with_failed_strategy:
        return []
    files = sorted(state.files_with_failed_strategy)
    count = len(files)
    shown = files[:_MAX_FAILED_FILES_SHOWN]
    remaining = count - len(shown)
    suffix = "files" if count != 1 else "file"
    message = (
        f"{count} {suffix} could not be processed by their strategy: "
        + ", ".join(shown)
    )
    if remaining > 0:
        message += f", +{remaining} more"
    message += " -- rerun `wd discover` to see the strategy and reason in stderr"
    return [result_cls("warn", message, "Strategies")]

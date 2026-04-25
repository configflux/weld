"""Strategy-related diagnostic checks for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under the
400-line cap. These helpers read ``.weld/discover.yaml`` and classify
referenced strategies into enabled vs disabled, then check whether each
enabled strategy resolves to a bundled or project-local plugin.

Security posture: this module never prints filesystem paths. Strategy
identifiers are taken only from ``discover.yaml`` and echoed verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml


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

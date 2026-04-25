"""Trust-posture engine shared by ``wd doctor --security`` and ``wd security``.

ADR 0025 mandates a single source of truth for the operator-facing question
"is it safe for me to run weld in this workspace?". This module collects the
relevant signals (project-local strategies, ``external_json`` adapters,
enrichment provider configuration, MCP importability, safe-mode availability,
``.mcp.json`` posture), rolls them up into a 3-level risk verdict, and emits
either grouped human text or stable JSON.

Security posture: this module never reads API keys or ``.env`` files, never
prints absolute paths, and never instantiates an enrichment provider. It only
inspects ``.weld/`` files, the standard environment, and an in-process
``importlib.import_module`` of ``weld.mcp_server``.

The ``signals`` ordering and the ``id`` vocabulary are part of the contract
pinned by ADR 0025 -- adding ids is allowed; renaming or removing them is a
breaking change.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from weld._security_mcp import (
    check_mcp_config,
    check_mcp_graph_ready,
    check_mcp_importable,
)
from weld._yaml import parse_yaml
from weld.providers import NETWORK_PROVIDERS

# Section vocabulary -- mirrors wd doctor's section concept so output stays
# scannable when the engine is rendered alongside other doctor output.
_SECTION_TRUST = "Trust"
_SECTION_NETWORK = "Network"
_SECTION_MCP = "MCP"
_SECTION_SAFE = "SafeMode"


@dataclass(frozen=True)
class Signal:
    """One trust-posture finding. ``id`` is a stable snake_case identifier;
    ``level`` is ``ok``/``warn``/``high``; ``details`` carries structured
    context for JSON consumers.
    """

    id: str
    level: str
    section: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """Trust-posture assessment. ``risk`` is ``high`` if any signal is high,
    ``medium`` if any is warn (no high), else ``low``.
    """

    risk: str
    signals: tuple[Signal, ...]
    recommendations: tuple[str, ...]


# ── individual checks ────────────────────────────────────────────────


def _check_safe_mode_available() -> Signal:
    """Report that ``--safe`` is wired on ``wd discover`` and ``wd enrich``
    (ADR 0024) and surface the optional ``WELD_SAFE_MODE`` env hint. The
    operator's actual per-invocation opt-in is not observable from
    workspace state.
    """
    env_hint = os.environ.get("WELD_SAFE_MODE", "").strip()
    if env_hint:
        return Signal(
            id="safe_mode_available",
            level="ok",
            section=_SECTION_SAFE,
            message=(
                "safe mode available -- WELD_SAFE_MODE env hint set "
                f"({env_hint!r}); pass --safe to wd discover / wd enrich "
                "for enforcement"
            ),
            details={"env_hint": env_hint},
        )
    return Signal(
        id="safe_mode_available",
        level="ok",
        section=_SECTION_SAFE,
        message=(
            "safe mode available -- pass --safe to wd discover / wd enrich "
            "in untrusted workspaces"
        ),
    )


def _check_project_local_strategies(weld_dir: Path) -> Signal | None:
    strategies_dir = weld_dir / "strategies"
    if not strategies_dir.is_dir():
        return None
    names = sorted(p.name for p in strategies_dir.glob("*.py"))
    if not names:
        return None
    sample = ", ".join(names[:3])
    extra = "" if len(names) <= 3 else f", +{len(names) - 3} more"
    return Signal(
        id="project_local_strategies",
        level="high",
        section=_SECTION_TRUST,
        message=(
            f"project-local strategies present ({sample}{extra}) -- "
            "wd discover (without --safe) will load and execute this code"
        ),
        details={"files": names},
    )


def _read_discover_sources(weld_dir: Path) -> list[dict]:
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return []
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [s for s in sources if isinstance(s, dict)]


def _check_external_json_adapters(weld_dir: Path) -> Signal | None:
    sources = _read_discover_sources(weld_dir)
    adapters = [s for s in sources if s.get("strategy") == "external_json"]
    if not adapters:
        return None
    # Echo only the first executable token (basename) so we never paste an
    # argument list -- ADR 0025 secrets-hygiene rule.
    safe_commands: list[str] = []
    for src in adapters:
        cmd = src.get("command")
        if isinstance(cmd, str) and cmd.strip():
            head = cmd.strip().split()[0]
            safe_commands.append(Path(head).name or head)
    return Signal(
        id="external_json_adapters",
        level="high",
        section=_SECTION_TRUST,
        message=(
            f"external_json adapters configured ({len(adapters)}) -- "
            "wd discover (without --safe) will spawn subprocesses with the "
            "repo root as cwd"
        ),
        details={"count": len(adapters), "commands": safe_commands},
    )


def _check_enrichment_env() -> Signal:
    name = os.environ.get("WELD_ENRICH_PROVIDER", "").strip().lower()
    network = sorted(NETWORK_PROVIDERS)
    if not name:
        return Signal(
            id="enrichment_provider_env",
            level="ok",
            section=_SECTION_NETWORK,
            message=(
                "no WELD_ENRICH_PROVIDER set -- wd enrich will not run "
                "without an explicit --provider argument"
            ),
            details={"network_providers": network},
        )
    if name in NETWORK_PROVIDERS:
        return Signal(
            id="enrichment_provider_env",
            level="warn",
            section=_SECTION_NETWORK,
            message=(
                f"WELD_ENRICH_PROVIDER={name!r} -- wd enrich will issue "
                "outbound network calls; pass --safe to refuse"
            ),
            details={"provider": name, "network_providers": network},
        )
    return Signal(
        id="enrichment_provider_env",
        level="ok",
        section=_SECTION_NETWORK,
        message=(
            f"WELD_ENRICH_PROVIDER={name!r} -- not registered as "
            "network-bound"
        ),
        details={"provider": name, "network_providers": network},
    )


# MCP checks are delegated to ``weld._security_mcp`` -- see that module.


# ── roll-up + recommendations ────────────────────────────────────────


def _roll_up(signals: list[Signal]) -> str:
    if any(s.level == "high" for s in signals):
        return "high"
    if any(s.level == "warn" for s in signals):
        return "medium"
    return "low"


def _recommendations(signals: list[Signal]) -> list[str]:
    recs: list[str] = []
    by_id = {s.id: s for s in signals}

    def level(sig_id: str) -> str:
        return by_id[sig_id].level if sig_id in by_id else "ok"

    if "project_local_strategies" in by_id or "external_json_adapters" in by_id:
        recs.append(
            "Run `wd discover --safe` instead of `wd discover` until you "
            "have audited .weld/strategies/ and any external_json commands."
        )
    if level("enrichment_provider_env") == "warn":
        recs.append(
            "Pass `--safe` to `wd enrich` (or unset WELD_ENRICH_PROVIDER) "
            "when working with untrusted graphs to refuse network providers."
        )
    if level("mcp_importable") == "warn":
        recs.append(
            "Reinstall or repair the weld package -- MCP clients cannot "
            "import weld.mcp_server."
        )
    if level("mcp_graph_present") == "warn":
        recs.append(
            "Run `wd discover` to materialize .weld/graph.json so MCP "
            "graph-backed tools return data."
        )
    if level("mcp_config_servers") == "warn":
        recs.append(
            "Audit external MCP servers in `.mcp.json` -- their `command` "
            "entries run as the operator."
        )
    return recs


# ── public API ───────────────────────────────────────────────────────


def assess(root: Path) -> Report:
    """Run all trust-posture checks under *root* and roll them up.

    *root* may or may not contain a ``.weld/`` directory. When the directory
    is missing, the engine still emits the safe-mode-available, MCP, and
    enrichment-env signals -- there is simply nothing to report under the
    Trust section.
    """
    root = Path(root)
    weld_dir = root / ".weld"

    signals: list[Signal] = []
    signals.append(_check_safe_mode_available())

    if weld_dir.is_dir():
        sig = _check_project_local_strategies(weld_dir)
        if sig is not None:
            signals.append(sig)
        sig = _check_external_json_adapters(weld_dir)
        if sig is not None:
            signals.append(sig)

    signals.append(_check_enrichment_env())
    signals.append(check_mcp_importable(Signal))
    signals.append(check_mcp_graph_ready(root, Signal))

    sig = check_mcp_config(root, Signal)
    if sig is not None:
        signals.append(sig)

    risk = _roll_up(signals)
    recs = _recommendations(signals)
    return Report(risk=risk, signals=tuple(signals), recommendations=tuple(recs))


def to_json(report: Report) -> dict[str, Any]:
    """Serialize *report* into the ADR-0025 JSON shape."""
    return {
        "risk": report.risk,
        "signals": [asdict(s) for s in report.signals],
        "recommendations": list(report.recommendations),
    }


def format_human(report: Report) -> str:
    """Render *report* as the grouped human-readable summary."""
    by_section: dict[str, list[Signal]] = {}
    for s in report.signals:
        by_section.setdefault(s.section, []).append(s)

    section_order = (_SECTION_TRUST, _SECTION_NETWORK, _SECTION_MCP, _SECTION_SAFE)
    sections = sorted(
        by_section.keys(),
        key=lambda name: (
            section_order.index(name) if name in section_order else len(section_order),
            name,
        ),
    )

    lines: list[str] = []
    for section in sections:
        lines.append(f"[{section}]")
        for s in by_section[section]:
            lines.append(f"  [{s.level:4s}] {s.message}")

    if lines:
        lines.append("")
    lines.append(f"Risk: {report.risk}")

    if report.recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
    return "\n".join(lines)


def has_high(report: Report) -> bool:
    """Return True when the report contains any ``high`` signal."""
    return any(s.level == "high" for s in report.signals)

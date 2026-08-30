"""Active-strategy detection for the runtime capability matrix.

Split from :mod:`weld.capabilities` (which sits at the 400-line cap) so the
per-language attribution added for Finding 03 has room. This module answers
"which strategies are wired *and* known here, and what languages were wired
per source entry" -- the inputs :func:`weld.capabilities.compute_capabilities`
crosses with graph contents.

Public helper :func:`list_disk_strategies` is re-exported by
:mod:`weld.capabilities` so the enforcement test's import site is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from weld._capabilities_local import load_local_capabilities
from weld._capabilities_registry import (
    STRATEGY_CAPABILITIES,
    StrategyCapability,
)

# Strategy-directory modules that are imported by other strategies but
# are NOT themselves registered in ``.weld/discover.yaml`` (and so do
# not need a registry entry). The naming convention is loose -- some
# helpers carry an ``_`` prefix, this set captures the rest. Adding a
# new shared helper without a leading underscore requires extending
# this list and is intentional friction.
_STRATEGY_DIR_HELPERS: frozenset[str] = frozenset({"events_shared"})


def list_disk_strategies(repo_root: Path) -> frozenset[str]:
    """Return the set of public strategy module stems on disk.

    Mirrors the discovery loader: files under ``weld/strategies/`` whose
    name does not start with ``_``, ends in ``.py``, is not
    ``__init__``, and is not a known shared helper
    (:data:`_STRATEGY_DIR_HELPERS`). Used by the enforcement test to
    compare against :data:`EXPECTED_STRATEGIES`.
    """
    strategies_dir = repo_root / "weld" / "strategies"
    if not strategies_dir.is_dir():
        return frozenset()
    out: set[str] = set()
    for path in strategies_dir.glob("*.py"):
        stem = path.stem
        if stem.startswith("_") or stem == "__init__":
            continue
        if stem in _STRATEGY_DIR_HELPERS:
            continue
        out.add(stem)
    return frozenset(out)


def read_yaml_wiring(repo_root: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Return ``(strategies, strategy -> per-entry languages)`` from config.

    Best-effort: tolerates a missing or malformed file by returning empty
    containers so consumers do not crash on a fresh checkout. Uses the
    bundled ``weld._yaml`` reader to avoid a hard PyYAML dep.

    The second element captures the per-entry ``language:`` key that
    multi-language strategies without a fixed registry language rely on --
    notably ``tree_sitter``, whose language is wired per source entry rather
    than declared in :data:`STRATEGY_CAPABILITIES`. Finding 03: the registry
    credits ``tree_sitter`` nothing because it declares no languages and this
    key was never read.
    """
    cfg_path = repo_root / ".weld" / "discover.yaml"
    if not cfg_path.is_file():
        return set(), {}
    try:
        from weld._yaml import parse_yaml
    except Exception:
        return set(), {}
    try:
        data = parse_yaml(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set(), {}
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return set(), {}
    out: set[str] = set()
    languages: dict[str, set[str]] = {}
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        name = entry.get("strategy")
        if not (isinstance(name, str) and name):
            continue
        out.add(name)
        lang = entry.get("language")
        if isinstance(lang, str) and lang:
            languages.setdefault(name, set()).add(lang)
    return out, languages


def known_capabilities(repo_root: Path) -> dict[str, StrategyCapability]:
    """Bundled registry merged with project-local declared capabilities.

    Bundled entries win: a project-local manifest (ADR 0087) can add a
    *new* stem's capability -- closing the bundled-vs-local asymmetry --
    but never overrides an in-tree registry entry, so bundled strategy
    behavior is unchanged. Reading the manifest imports no project code
    (it is pure YAML data), so this stays correct under ``--safe``.
    """
    merged: dict[str, StrategyCapability] = dict(STRATEGY_CAPABILITIES)
    try:
        local = load_local_capabilities(repo_root)
    except Exception:
        local = {}
    for stem, cap in local.items():
        merged.setdefault(stem, cap)
    return merged


def active_strategies(
    wired: set[str], known: dict[str, StrategyCapability],
) -> set[str]:
    """Strategies wired in ``discover.yaml`` (*wired*) AND *known*.

    *known* is the bundled registry merged with project-local declared
    capabilities (:func:`known_capabilities`). The intersection ensures we
    never report capability for a strategy name that exists in config but has
    been retired, and never report capability for an unwired strategy. An
    empty config (no ``.weld/discover.yaml``) falls back to the full known set
    so consumers in fresh checkouts get the maximum honest answer.
    """
    if not wired:
        return set(known.keys())
    return wired & set(known.keys())

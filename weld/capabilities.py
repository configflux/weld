"""Runtime capability matrix for ``wd impact`` (ADR 0043 Layer B).

Surfaces, for the loaded graph and active strategies, what evidence weld
actually has per language and per framework. The contract is **runtime-
derived**: a flag is true iff (a) at least one wired strategy claims that
evidence kind, AND (b) the graph contains at least one node attributable
to that strategy. Aspirational support -- a strategy that is wired but
matched no files -- surfaces with all flags ``False`` so consumers see
the gap explicitly.

The registry lives in :mod:`weld._capabilities_registry` (split for the
400-line cap). Public surface here:

- :func:`compute_capabilities` -- deterministic dict ready for the
  impact envelope.
- :func:`detect_missing` -- frameworks present-on-disk but emitting empty
  edge support (powers ``wd capabilities --missing``).
- :data:`STRATEGY_CAPABILITIES`, :data:`EXPECTED_STRATEGIES`,
  :data:`MISSING_FRAMEWORK_PATTERNS` -- re-exported for tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from weld._capabilities_registry import (
    EXPECTED_STRATEGIES,
    FRAMEWORK_EVIDENCE,
    LANGUAGE_EVIDENCE,
    MISSING_FRAMEWORK_PATTERNS,
    MULTI_FRAMEWORK_FILES,
    STRATEGY_CAPABILITIES,
    StrategyCapability,
)

__all__ = [
    "EXPECTED_STRATEGIES",
    "FRAMEWORK_EVIDENCE",
    "LANGUAGE_EVIDENCE",
    "MISSING_FRAMEWORK_PATTERNS",
    "STRATEGY_CAPABILITIES",
    "StrategyCapability",
    "compute_capabilities",
    "compute_capabilities_for_graph",
    "detect_missing",
    "list_disk_strategies",
]


def compute_capabilities_for_graph(graph: object) -> dict:
    """Derive the capability matrix for an in-memory ``Graph``-like object.

    Reads ``graph._data`` and resolves the repo root from
    ``graph._path``. Failures are swallowed and yield an empty matrix
    so a malformed config never breaks ``wd impact`` itself. This is
    the shim called from :func:`weld.impact_core.impact`; the standalone
    :func:`compute_capabilities` accepts the raw dict instead.
    """
    try:
        repo_root = graph._path.parent.parent  # type: ignore[attr-defined]
        return compute_capabilities(graph._data, repo_root)  # type: ignore[attr-defined]
    except Exception:
        return {"languages": {}, "frameworks": {}}


# ---------------------------------------------------------------------------
# Active-strategy detection
# ---------------------------------------------------------------------------

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


def _read_yaml_strategies(repo_root: Path) -> set[str]:
    """Return strategy names referenced in ``.weld/discover.yaml``.

    Best-effort: tolerates a missing or malformed file by returning an
    empty set so consumers do not crash on a fresh checkout. Uses the
    bundled ``weld._yaml`` reader to avoid a hard PyYAML dep.
    """
    cfg_path = repo_root / ".weld" / "discover.yaml"
    if not cfg_path.is_file():
        return set()
    try:
        from weld._yaml import parse_yaml
    except Exception:
        return set()
    try:
        data = parse_yaml(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return set()
    out: set[str] = set()
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        name = entry.get("strategy")
        if isinstance(name, str) and name:
            out.add(name)
    return out


def _active_strategies(repo_root: Path) -> set[str]:
    """Strategies wired in this repo's ``discover.yaml`` AND in the registry.

    The intersection ensures we never report capability for a strategy
    name that exists in config but has been retired from the registry,
    and never report capability for an unwired strategy. An empty
    config (no ``.weld/discover.yaml``) falls back to the full registry
    so consumers in fresh checkouts get the maximum honest answer.
    """
    wired = _read_yaml_strategies(repo_root)
    if not wired:
        return set(STRATEGY_CAPABILITIES.keys())
    return wired & set(STRATEGY_CAPABILITIES.keys())


# ---------------------------------------------------------------------------
# Graph attribution
# ---------------------------------------------------------------------------

def _node_files_from_graph(graph_data: dict) -> list[str]:
    nodes = graph_data.get("nodes") or {}
    files: set[str] = set()
    for nid, node in nodes.items():
        props = node.get("props") or {}
        path = str(props.get("file") or "")
        if path:
            files.add(path)
        elif isinstance(nid, str) and nid.startswith("file:"):
            files.add(nid[len("file:") :])
    return sorted(files)


def _matches_capability(path: str, cap: StrategyCapability) -> bool:
    """Return True if *path* matches any extension/basename declared by *cap*.

    A strategy with no extensions and no basenames declared is treated
    as a structural strategy and is never matched here; structural
    presence is handled separately in
    :func:`_strategy_has_evidence_in_graph`.
    """
    if not path:
        return False
    p = Path(path)
    name = p.name
    if cap.file_basenames and name in cap.file_basenames:
        return True
    if cap.file_extensions:
        suffix = p.suffix
        if suffix and suffix in cap.file_extensions:
            return True
        if len(p.suffixes) >= 2:
            joined = "".join(p.suffixes)
            if joined in cap.file_extensions:
                return True
    return False


def _strategy_has_evidence_in_graph(
    cap: StrategyCapability, graph_files: list[str],
) -> bool:
    """True iff at least one graph file path matches *cap*'s patterns.

    Strategies without file-level signatures (e.g.,
    ``boundary_entrypoint``) are conservatively assumed present when
    they are wired -- the active-strategy filter in the caller has
    already ensured wiring.
    """
    if not cap.file_extensions and not cap.file_basenames:
        return True
    return any(_matches_capability(path, cap) for path in graph_files)


def _framework_has_evidence_in_graph(
    stem: str,
    cap: StrategyCapability,
    framework: str,
    graph_files: list[str],
) -> bool:
    """True iff the graph has evidence for *framework* under *stem*.

    For single-framework strategies this collapses to the same logic as
    :func:`_strategy_has_evidence_in_graph`. For multi-framework
    strategies (``manifest`` -> ``npm``+``make``), this consults
    :data:`MULTI_FRAMEWORK_FILES` to attribute basenames to the
    correct framework so a Makefile-only repo never flips ``npm``
    flags true.

    An explicit ``((), ())`` entry in the split means "this framework
    is part of the strategy's declared set for registry-completeness
    but has no file-level signature the registry can match" -- e.g.
    ``deploy_surface`` declares ``k8s`` because it processes k8s
    manifests structurally (by content), but those filenames are not
    enumerated in the registry. Such entries report ``False`` so a
    Chart.yaml-only repo never flips ``k8s`` flags true.
    """
    split = MULTI_FRAMEWORK_FILES.get(stem)
    if split is None or framework not in split:
        return _strategy_has_evidence_in_graph(cap, graph_files)
    exts, basenames = split[framework]
    if not exts and not basenames:
        return False
    ext_set = frozenset(exts)
    base_set = frozenset(basenames)
    sub_cap = StrategyCapability(
        file_extensions=ext_set,
        file_basenames=base_set,
    )
    return any(_matches_capability(path, sub_cap) for path in graph_files)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_capabilities(graph_data: dict, repo_root: Path) -> dict:
    """Return the runtime capability matrix for the loaded graph.

    Output shape (deterministic; ``json.dumps(..., sort_keys=True)`` is
    byte-identical across calls)::

        {
          "languages": {<lang>: {"file": bool, "module": bool, ...}},
          "frameworks": {<framework>: {"nodes_emitted": bool, ...}}
        }

    A flag is True iff at least one *active* strategy (wired in
    ``discover.yaml``) registered for that language or framework lists
    the corresponding evidence AND the graph contains a file matching
    that strategy's signature. Untouched languages/frameworks still
    appear -- with every flag ``False`` -- so consumers see the gap
    explicitly.
    """
    active = _active_strategies(repo_root)
    graph_files = _node_files_from_graph(graph_data)

    languages: dict[str, dict[str, bool]] = {}
    frameworks: dict[str, dict[str, bool]] = {}

    for stem, cap in STRATEGY_CAPABILITIES.items():
        if stem not in active:
            continue
        present_in_graph = _strategy_has_evidence_in_graph(cap, graph_files)
        for lang in cap.languages_set():
            row = languages.setdefault(
                lang, {k: False for k in sorted(LANGUAGE_EVIDENCE)},
            )
            if present_in_graph:
                for tag in cap.evidence & LANGUAGE_EVIDENCE:
                    row[tag] = True
        for fw in cap.frameworks_set():
            row = frameworks.setdefault(
                fw,
                {k: False for k in sorted(FRAMEWORK_EVIDENCE)},
            )
            # Per-framework graph attribution: a multi-framework strategy
            # (e.g. ``manifest`` -> ``npm``+``make``) must only flip the
            # row for the framework whose own basenames/extensions match
            # the graph. Otherwise a Makefile-only repo would falsely
            # claim ``npm: nodes_emitted=true``.
            fw_present = _framework_has_evidence_in_graph(
                stem, cap, fw, graph_files,
            )
            if fw_present:
                for tag in cap.evidence & FRAMEWORK_EVIDENCE:
                    row[tag] = True

    # Registry-completeness: any registered language/framework that no
    # active strategy populated still appears with all-False flags so
    # consumers see the gap explicitly rather than silently missing.
    for cap in STRATEGY_CAPABILITIES.values():
        for lang in cap.languages_set():
            if lang not in languages:
                languages[lang] = {
                    k: False for k in sorted(LANGUAGE_EVIDENCE)
                }
        for fw in cap.frameworks_set():
            if fw not in frameworks:
                frameworks[fw] = {
                    k: False for k in sorted(FRAMEWORK_EVIDENCE)
                }

    return {
        "languages": {
            k: dict(sorted(languages[k].items())) for k in sorted(languages)
        },
        "frameworks": {
            k: dict(sorted(frameworks[k].items())) for k in sorted(frameworks)
        },
    }


def _walk_repo(root: Path, skip_dirs: set[str]):
    """Iterate ``(dirpath, dirnames, filenames)`` skipping noise dirs.

    Mirrors :func:`os.walk` semantics but prunes ``skip_dirs`` in-place
    on ``dirnames`` so we never descend into them. Yields filenames
    sorted for determinism.
    """
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        yield dirpath, dirnames, sorted(filenames)


def detect_missing(repo_root: Path) -> list[str]:
    """Return frameworks present-on-disk but not emitting useful edges.

    Globs the repo for the patterns in
    :data:`MISSING_FRAMEWORK_PATTERNS`. Skips ``.git``, ``node_modules``,
    ``.weld``, and similar high-noise directories. The returned list is
    sorted, deduplicated, and a strict subset of the
    ``MISSING_FRAMEWORK_PATTERNS`` keys.
    """
    if not repo_root.is_dir():
        return []
    skip_dirs = {
        ".git", ".weld", "node_modules", "__pycache__", ".venv",
        "venv", "dist", "build", ".bazel", ".cache",
    }
    found: set[str] = set()
    target = len(MISSING_FRAMEWORK_PATTERNS)
    for _dirpath, _dirnames, filenames in _walk_repo(repo_root, skip_dirs):
        for name in filenames:
            for fw, (exts, basenames) in MISSING_FRAMEWORK_PATTERNS.items():
                if fw in found:
                    continue
                if name in basenames:
                    found.add(fw)
                    continue
                if exts and any(name.endswith(ext) for ext in exts):
                    found.add(fw)
        if len(found) == target:
            break
    return sorted(found)

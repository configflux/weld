"""Strategy-pair-consistency rule (ADR 0041, Layer 3, rule 3).

Lives alongside ``weld._graph_closure_invariants`` so that file's
canonical-id-uniqueness and file-anchor-symmetry helpers stay under the
400-line cap. Reads ``.weld/discover.yaml`` to discover declared
strategy pairs and the optional ``pair_asymmetry_allowlist``, then
asserts each pair's members visit the same file set on the current
tree.

The rule catches the structural drift class ADR 0041 calls out: paired
strategies (``python_module`` + ``python_callgraph``, ROS2 packages,
gRPC bindings) that make independent decisions about which files to
skip and therefore emit children rooted at file anchors the partner
never sees. Empty allow-lists are the steady-state expectation; new
entries require code-reviewed ``reason`` strings.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Mapping, Sequence

if TYPE_CHECKING:
    from weld.arch_lint import Violation


def _make_violation(
    rule: str, node_id: str, message: str, severity: str = "error"
) -> "Violation":
    """Build a :class:`weld.arch_lint.Violation` (late import breaks cycle)."""
    from weld.arch_lint import Violation

    return Violation(
        rule=rule,
        node_id=node_id,
        message=message,
        severity=severity,
    )


def _load_yaml(root: Path) -> dict:
    """Load ``<root>/.weld/discover.yaml``; return ``{}`` on miss."""
    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    from weld._yaml import parse_yaml
    config = parse_yaml(text)
    return config if isinstance(config, dict) else {}


def _strategy_file_sets(
    root: Path, sources: Sequence[Mapping]
) -> dict[str, set[str]]:
    """Return ``{strategy_name: {rel_posix_path, ...}}`` for each strategy.

    Walks each ``glob`` source entry under *root* using the same
    prune-aware walker the strategies themselves use, then applies each
    entry's ``exclude`` list. The result is the file set each strategy
    *would* visit on the current tree, before any per-strategy
    ``should_skip`` logic the strategy applies internally. Sources
    without a ``glob`` (e.g. ``files:`` or ``path:`` entries) are not
    relevant to the strategy-pair drift class and are ignored here.
    """
    from weld.glob_match import walk_glob

    by_strategy: dict[str, set[str]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        strategy = source.get("strategy")
        glob_pattern = source.get("glob")
        if not strategy or not glob_pattern:
            continue
        excludes = source.get("exclude") or []
        if not isinstance(excludes, list):
            excludes = []
        try:
            matched = walk_glob(root, str(glob_pattern), excludes=excludes)
        except (OSError, ValueError):
            matched = []
        bucket = by_strategy.setdefault(str(strategy), set())
        for path in matched:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            bucket.add(rel)
    return by_strategy


def _path_in_pair_allowlist(
    path: str,
    entries: Sequence[Mapping] | None,
    member: str | None = None,
) -> bool:
    """Return True when *path* is exempt under the pair-asymmetry allow-list.

    Allow-list entries shape: ``{path: <glob>, member_skipping: <name>,
    reason: <str>}``. When *member* is given, the entry's
    ``member_skipping`` must match (or be unset, treated as "any
    member"). The ``reason`` field is required by repo policy but not
    re-checked here.
    """
    if not entries:
        return False
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        pattern = entry.get("path")
        if not pattern:
            continue
        if not (
            fnmatch.fnmatchcase(path, str(pattern)) or path == str(pattern)
        ):
            continue
        skipping = entry.get("member_skipping")
        if member is not None and skipping and str(skipping) != member:
            continue
        return True
    return False


def check_strategy_pair_consistency(root: Path) -> Iterator["Violation"]:
    """Flag declared strategy pairs whose members visit divergent file sets.

    Reads ``<root>/.weld/discover.yaml`` and pulls ``strategy_pairs``
    (list of ``{name, members: [strategy_name, ...]}``) and the optional
    ``pair_asymmetry_allowlist`` (mapping of pair name to list of
    ``{path, member_skipping, reason}`` entries).

    For each declared pair, the rule resolves each member's source
    entries, walks the file system using the same prune-aware walker
    the strategies use, and computes the union of file sets each
    strategy would visit. Files that are visible to a proper subset of
    pair members are violations unless explicitly listed in the
    allow-list.

    The pair canonical name is the sorted-tuple of member strategy
    names joined by ``+`` -- the ``name:`` field on the YAML entry is
    for documentation only and is not used to look up allow-list keys.
    """
    config = _load_yaml(root)
    pairs = config.get("strategy_pairs") or []
    if not isinstance(pairs, list) or not pairs:
        return
    sources = config.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    allowlist_map = config.get("pair_asymmetry_allowlist") or {}
    if not isinstance(allowlist_map, Mapping):
        allowlist_map = {}

    by_strategy = _strategy_file_sets(root, sources)

    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        members = pair.get("members") or []
        if not isinstance(members, list) or len(members) < 2:
            continue
        member_names = [str(m) for m in members]
        pair_key = "+".join(sorted(member_names))

        member_sets = {m: by_strategy.get(m, set()) for m in member_names}
        union: set[str] = set().union(*member_sets.values())
        if not union:
            continue

        pair_allowlist = allowlist_map.get(pair_key) or []
        if not isinstance(pair_allowlist, list):
            pair_allowlist = []

        for path in sorted(union):
            missing_from = [
                m for m in member_names if path not in member_sets[m]
            ]
            if not missing_from:
                continue
            unallowed_skips = [
                m for m in missing_from
                if not _path_in_pair_allowlist(
                    path, pair_allowlist, member=m,
                )
            ]
            if not unallowed_skips:
                continue
            yield _make_violation(
                rule="strategy-pair-consistency",
                node_id=path,
                message=(
                    f"strategy pair {pair_key!r} drift on {path!r}: "
                    f"missing from {sorted(unallowed_skips)}; add the "
                    f"missing strategy or list it in "
                    f"pair_asymmetry_allowlist with a reason"
                ),
            )


__all__ = ["check_strategy_pair_consistency"]

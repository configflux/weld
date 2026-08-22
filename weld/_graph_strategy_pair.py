"""Strategy-pair-consistency rule (ADR 0041, Layer 3, rule 3).

Lives alongside ``weld._graph_closure_invariants`` so that file's
canonical-id-uniqueness and file-anchor-symmetry helpers stay under the
400-line cap. Reads ``.weld/discover.yaml`` to discover declared
strategy pairs and the optional ``pair_asymmetry_allowlist``, then
asserts two things about each pair.

**Declared half.** Each pair's members must resolve the same file set
from config on the current tree. This catches the structural drift
class ADR 0041 calls out: paired strategies (``python_module`` +
``python_callgraph``, ROS2 packages, gRPC bindings) that make
independent *declared* decisions about which files to skip and
therefore emit children rooted at file anchors the partner never sees.
Empty allow-lists are the steady-state expectation; new entries require
code-reviewed ``reason`` strings.

**Emitted half** (ADR 0041 Rule 3 amendment; bd sf36). Each member's
emitted file anchors must stay inside the file set that member
declared. The declared half alone re-derives both members' sets from
the same config, so a pair whose members declare identical ``glob`` +
``exclude`` -- which the Python trio requires and a separate test pins
-- compares clean no matter what either strategy did at runtime. That
is how bd 3abf stayed green: ``python_callgraph`` resolved its glob
with no excludes at all and emitted ~10.4k symbol nodes from excluded
trees while this rule reported nothing. Reading provenance off the
graph (``props.source_strategy`` + ``props.file``) makes the rule
observe behaviour rather than intent.

The emitted half asserts containment, not equality: a symbol-emitting
member legitimately produces nothing for a file with no definitions,
so its anchor set is a strict subset of its partner's. Only the
over-reach direction -- an anchor the member's own config excludes --
is a violation, and that direction has no legitimate case.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping, Sequence

from weld._arch_lint_types import Violation

# The glob-walking logic behind the per-strategy file-set resolver lives in
# weld._discover_source_globs, shared with weld._graph_edge_provenance_lint,
# which needs the finer per-*entry* granularity that module also exposes
# (see its docstring for why "merged by strategy name" is not enough for
# that rule). Re-imported under the original private name so nothing else
# in this module has to change.
from weld._discover_source_globs import strategy_file_sets as _strategy_file_sets


def _make_violation(
    rule: str, node_id: str, message: str, severity: str = "error"
) -> Violation:
    """Build a :class:`weld._arch_lint_types.Violation`."""
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


def _declared_file_sets(
    sources: Sequence[Mapping],
    glob_sets: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    """Return ``{strategy: declared_set}`` for glob-declaring strategies.

    Starts from the glob-resolved sets in *glob_sets* and folds in each
    of those strategies' literal ``path:`` / ``files:`` entries, so a
    strategy that mixes resolution modes is judged against everything it
    was configured to read. Existence is not checked: a declared path
    that is absent only makes the containment test more permissive.

    Strategies with no ``glob`` entry at all are deliberately absent
    from the result and are therefore exempt from the emission check. A
    ``path:``-declared strategy reads one manifest and may legitimately
    emit nodes about entirely different files --
    :mod:`weld.strategies.concept_from_bd` reads a JSON-lines issue
    store and points at the repo files those issues cite. A ``glob:``
    entry is the declaration of *which files this strategy walks*,
    which is the thing ``exclude:`` constrains, so glob-declaring
    members are the principled scope for containment.
    """
    from weld.glob_match import matches_exclude

    declared = {name: set(paths) for name, paths in glob_sets.items()}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        strategy = source.get("strategy")
        if not strategy or source.get("glob"):
            continue
        bucket = declared.get(str(strategy))
        if bucket is None:
            continue
        excludes = source.get("exclude") or []
        if not isinstance(excludes, list):
            excludes = []
        excludes = [str(p) for p in excludes if p]
        literals: list[str] = []
        path_entry = source.get("path")
        if path_entry:
            literals.append(str(path_entry))
        files_entry = source.get("files")
        if isinstance(files_entry, list):
            literals.extend(str(f) for f in files_entry if f)
        for literal in literals:
            rel = PurePosixPath(literal).as_posix()
            if excludes and matches_exclude(rel, excludes):
                continue
            bucket.add(rel)
    return declared


def _emitted_file_anchors(
    nodes: Mapping[str, object], root: Path
) -> dict[str, set[str]]:
    """Return ``{strategy: {rel_posix_anchor, ...}}`` from node provenance.

    Reads the ``source_strategy`` / ``file`` prop pair that discovery
    strategies stamp on every file-anchored node they emit. Nodes with
    no provenance, no anchor, or an anchor that cannot be expressed
    relative to *root* contribute nothing: an unresolvable anchor is not
    evidence that a strategy ignored its excludes. A strategy that omits
    ``props.source_strategy`` is invisible here -- the graph contract
    (:mod:`weld._contract_validators`) already asks for it.

    Separators are normalised to ``/`` before comparison. Most
    strategies build ``props.file`` as ``str(path.relative_to(root))``
    (``python_callgraph`` among them), which is backslash-separated on
    Windows, while the declared sets are always ``as_posix()`` -- left
    unnormalised, every anchor on that platform would read as
    out-of-declared and the rule would be a false-positive storm rather
    than a gate. The cost is a POSIX filename containing a literal
    backslash, which this would misread; that trade is worth it.

    Unconditional by decision, not by inheritance: bd 3x85 retired the
    identical hand-rolled replace in :mod:`weld.impact_surfaces` and
    deliberately left this one, because a wrong path there is one line
    of a search result while here it fails a gate. See
    :mod:`weld._rel_path` for the full argument, and
    ``test_windows_style_anchor_compares_against_posix_declaration``
    for the contract.
    """
    by_strategy: dict[str, set[str]] = {}
    for node in nodes.values():
        if not isinstance(node, Mapping):
            continue
        props = node.get("props")
        if not isinstance(props, Mapping):
            continue
        strategy = props.get("source_strategy")
        anchor = props.get("file")
        if not strategy or not isinstance(anchor, str) or not anchor:
            continue
        candidate = Path(anchor.replace("\\", "/"))
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(root)
            except ValueError:
                continue
        by_strategy.setdefault(str(strategy), set()).add(candidate.as_posix())
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


def _emission_violations(
    member_names: Sequence[str],
    pair_key: str,
    declared: Mapping[str, set[str]],
    emitted: Mapping[str, set[str]],
    reported: set[tuple[str, str]],
) -> Iterator[Violation]:
    """Yield one violation per member anchor outside its declared set.

    *reported* carries ``(member, path)`` pairs already yielded so a
    strategy listed in more than one pair reports each offending anchor
    once. Anchors are sorted so the envelope stays deterministic.
    """
    for member in member_names:
        member_declared = declared.get(member)
        if member_declared is None:
            continue
        for path in sorted(emitted.get(member, set()) - member_declared):
            if (member, path) in reported:
                continue
            reported.add((member, path))
            yield _make_violation(
                rule="strategy-pair-consistency",
                node_id=path,
                message=(
                    f"strategy {member!r} (pair {pair_key!r}) emitted a "
                    f"node anchored at {path!r}, which its own "
                    f"discover.yaml glob/exclude does not cover: the "
                    f"strategy is not honouring its declared excludes, "
                    f"or the graph carries a stale node -- re-run "
                    f"discovery to rule that out"
                ),
            )


def check_strategy_pair_consistency(
    root: Path, nodes: Mapping[str, object] | None = None
) -> Iterator[Violation]:
    """Flag declared strategy pairs that drift, as declared or as emitted.

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

    When *nodes* is supplied -- the graph's ``nodes`` mapping, which the
    ``weld.arch_lint`` adapter always passes -- the rule additionally
    asserts that each glob-declaring member's *emitted* file anchors
    stay inside that member's declared set. This is the half that
    observes runtime behaviour; without it a pair whose members declare
    identical config compares clean by construction (bd sf36). Omitting
    *nodes* keeps the declared-only behaviour for direct callers.

    The allow-list governs the declared half only. Its entries record an
    intentional *skip* by one member; emitting a node for a file that
    member declared it would skip is the opposite direction and does not
    inherit the exemption.

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
    declared = _declared_file_sets(sources, by_strategy)
    emitted = _emitted_file_anchors(nodes or {}, root)
    reported: set[tuple[str, str]] = set()

    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        members = pair.get("members") or []
        if not isinstance(members, list) or len(members) < 2:
            continue
        member_names = [str(m) for m in members]
        pair_key = "+".join(sorted(member_names))

        yield from _emission_violations(
            member_names, pair_key, declared, emitted, reported,
        )

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

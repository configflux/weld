"""Coverage staleness (ADR 0101).

Split out of :mod:`weld._staleness` so that module stays under its
line-count cap, mirroring the existing :mod:`weld._staleness_worktree`
precedent: a freshness signal's own helpers live in their own file, and
:func:`weld._staleness.compute_stale_info` remains the sole composer of all
four signals -- splitting the *helpers* across modules does not let a
caller consult this one in isolation and mistake it for the freshness
verdict, which is the thing the parent module's docstring warns against.

ADR 0017's two signals both ask whether a file the graph *already knows
about* changed. Neither can see a file that is in discovery scope at the
current commit but was never ingested: with the recorded SHA already at
HEAD there is no commit range to diff, a committed-and-clean file raises no
git-status entry, and ``meta.discovered_from`` lists what the graph did
read, so a never-read file is absent from it by construction. That gap is
self-perpetuating -- every later refresh re-stamps the same SHA -- so the
graph reports fresh indefinitely while reads answer "no such symbol" for
shipped code.

The probe runs on the read path, so scope is decided by matching the ADR
0020 repo-boundary snapshot (one ``git ls-files``) against
``.weld/discover.yaml`` in memory, reusing the regex translation and
exclude semantics ``walk_glob`` applies. A real glob walk costs ~730 ms on
this repo; this costs ~45 ms.

Membership is deliberately one-directional. Under-reporting scope costs a
missed detection; over-reporting marks a file the state can never cover and
refreshes on every read forever. ``in_scope_files`` is therefore specified
as a subset of what a walk resolves, and pinned that way by
``weld_coverage_scope_match_test``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _ancestor_dirs(rel: str) -> list[str]:
    """Repo-relative ancestor directories of *rel*, outermost first."""
    parts = rel.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _excluded(rel: str, excludes: list[str]) -> bool:
    """True when *rel* is excluded, directly or via an ancestor directory.

    ``walk_glob`` prunes excluded *directories* before descending and tests
    surviving files individually. Matching a path list has no descent to
    prune, so the ancestor check is what reproduces the pruning half: a bare
    directory pattern (``node_modules``) matches the directory name, never
    ``node_modules/pkg/index.js``.
    """
    from weld.glob_match import matches_exclude

    if not excludes:
        return False
    if matches_exclude(rel, excludes):
        return True
    return any(matches_exclude(d, excludes) for d in _ancestor_dirs(rel))


def _structurally_hidden(rel: str) -> bool:
    """True for paths ``walk_glob`` never yields regardless of config.

    Mirrors its two unconditional prunes: ``EXCLUDED_DIR_NAMES`` and nested
    repo copies. The boundary snapshot already drops git-hidden files, so
    these are the only structural filters left to apply.
    """
    from weld.repo_boundary import is_excluded_dir_name, is_nested_repo_copy

    parts = tuple(rel.split("/")[:-1])
    if any(is_excluded_dir_name(p) for p in parts):
        return True
    return is_nested_repo_copy(parts)


def in_scope_files(sources: list[dict], candidates: Iterable[str]) -> set[str]:
    """Return the subset of *candidates* that any *sources* entry resolves.

    Mirrors :func:`weld._source_resolve.resolve_source_files` over a known
    path list: ``glob`` via the shared pattern-to-regex translation (whose
    ``*`` never spans ``/``, so single-directory patterns stay
    single-directory), plus the ``path`` and ``files`` keys. Each entry's own
    ``exclude`` list applies to that entry only.
    """
    from weld.glob_match import _glob_pattern_to_regex

    candidate_list = [c for c in candidates if not _structurally_hidden(c)]
    if not candidate_list:
        return set()

    out: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        excludes = [p for p in (source.get("exclude") or []) if p]
        pattern = source.get("glob")
        regex = _glob_pattern_to_regex(pattern) if pattern else None
        path_entry = source.get("path")
        explicit = {f for f in (source.get("files") or ()) if f}
        if regex is None and not path_entry and not explicit:
            continue
        for rel in candidate_list:
            if rel in out:
                continue
            matched = (
                (regex is not None and regex.match(rel) is not None)
                or (bool(path_entry) and rel == path_entry)
                or rel in explicit
            )
            if not matched or _excluded(rel, excludes):
                continue
            out.add(rel)
    return out


def _load_sources(root: Path) -> list[dict]:
    """Parse ``.weld/discover.yaml`` sources; ``[]`` when absent or unusable."""
    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return []
    try:
        from weld._yaml import parse_yaml
        config = parse_yaml(config_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- advisory probe; never break a read.
        return []
    sources = config.get("sources") if isinstance(config, dict) else None
    return sources if isinstance(sources, list) else []


def files_missing_from_inventory(root: Path) -> set[str]:
    """In-scope files at *root* absent from the discovery state's inventory.

    Empty when the answer cannot be established cheaply and soundly -- no
    discovery state (a first run has nothing to be stale against), no
    configured sources, or a non-git root (whose only candidate source would
    be the tree walk this check exists to avoid).
    """
    from weld.discovery_state import load_state
    from weld.repo_boundary import get_repo_boundary

    state = load_state(root)
    if state is None or not state.files:
        return set()
    sources = _load_sources(root)
    if not sources:
        return set()
    try:
        boundary = get_repo_boundary(root)
    except Exception:  # noqa: BLE001 -- advisory probe; never break a read.
        return set()
    if not boundary.uses_git or not boundary.visible_files:
        return set()

    covered = set(state.files)
    # Narrow before matching: a converged graph covers nearly every visible
    # file, so this is the difference between matching ~1k paths and ~2k.
    candidates = [
        rel for rel in boundary.visible_files
        if rel not in covered and rel not in state.files_with_no_nodes
    ]
    if not candidates:
        return set()
    # The boundary snapshot reads ``git ls-files --cached``, which still lists
    # a file deleted from the working tree but not yet staged as removed. A
    # glob walk cannot resolve such a path, so no discovery run could ever
    # cover it -- reporting it here would mean staleness on every read
    # forever. Stat only the handful that matched scope.
    return {
        rel for rel in in_scope_files(sources, candidates)
        if (root / rel).is_file()
    }


def inventory_vouches_for_graph(root: Path) -> bool:
    """True when the discovery state describes the graph a reader will load.

    ``discovery-state.json`` lists the files a discovery run *resolved*. That
    is evidence about ``.weld/graph.json`` only when the same run also wrote
    it *and* the body still on disk is the one it wrote:
    ``finalize_single_repo`` persists the state on every path but writes
    the graph only when asked to, so a run whose graph goes to ``--output``
    elsewhere, is returned to a library caller, or is interrupted before the
    graph lands leaves an inventory covering files no reader can see. The
    second half is the bd wq9i case: a body swapped in under a state that
    already vouched for it (ADR 0096 gate 5 keeps a state file already
    present at the destination while landing a foreign graph beside it).

    That divergence is the one shape the rest of freshness is blind to, and
    it is self-perpetuating: the file is absent from ``meta.discovered_from``
    by construction (so neither ADR 0017 signal applies),
    :func:`files_missing_from_inventory` finds it in ``files`` and calls it
    covered, so ``stale`` is False, so auto-refresh never runs, so the ADR 0008
    per-file repair (:func:`weld._discover_state_check.files_missing_from_graph`)
    -- which closes the hole in a single pass -- is never scheduled. Four
    weeks of reads answered "no such module" for shipped code that way
    (bd esww / hfm6).

    Reporting the doubt is safe in the ADR 0101 section 4 sense: it can only
    over-report a state whose graph was never published, and the refresh that
    follows publishes graph and inventory together and re-stamps the flag, so
    it converges after one pass rather than looping.

    Degraded inputs stay silent (ADR 0101 section 5): no state, an empty
    inventory, or no ``graph.json`` at all (the missing-graph guard owns a
    first run) all vouch trivially.
    """
    from weld._discover_state_check import state_vouches_for_graph
    from weld.discovery_state import load_state

    graph_path = root / ".weld" / "graph.json"
    if not graph_path.is_file():
        return True
    state = load_state(root)
    if state is None or not state.files:
        return True
    return state_vouches_for_graph(state, graph_path)


def coverage_stale(root: Path) -> bool:
    """True when the inventory has an uncovered file, or cannot vouch for the graph.

    Two conditions, both read as "the graph may have a hole in it" rather
    than "the graph is behind HEAD": an in-scope file the inventory never
    recorded (:func:`files_missing_from_inventory`), or an inventory that
    cannot vouch for the graph on disk at all
    (:func:`inventory_vouches_for_graph`).

    Only the second inspects the graph body. The first inspects the
    inventory alone, and stands in for "absent from the graph" only because
    :func:`inventory_vouches_for_graph` already guards that link: every
    path that earns a state its vouching token first confirms, one way or
    another, that the inventory's node-bearing files are anchored in the
    graph body it names -- see that function, and
    :func:`weld._discover_state_check.inventory_describes_graph` (bd qmbp)
    for the explicit form of the check. A file this check reports uncovered
    was therefore never *inventoried*, and reads as never *ingested* only
    on the strength of that guarantee.
    """
    if not inventory_vouches_for_graph(root):
        return True
    return bool(files_missing_from_inventory(root))


def coverage_stale_detail(root: Path) -> list[dict]:
    """Enumerate the in-scope files :func:`coverage_stale` found uncovered.

    The full-enumeration companion to the boolean gate: called only after
    :func:`coverage_stale` has already returned ``True``, so the cost of
    calling :func:`files_missing_from_inventory` a second time is paid on
    the already-stale path only, never on a converged graph.

    Returns ``[]`` when the doubt instead comes from
    :func:`inventory_vouches_for_graph` alone -- the graph body may not
    match the inventory that vouches for it, with no single uncovered file
    to blame. That condition has no file-level detail to add; it stays
    covered by the ``coverage_stale`` boolean itself (see
    :data:`weld._stale_reasons` module docstring).
    """
    from weld._stale_reasons import NEVER_INGESTED

    return [
        {"path": p, "reason": NEVER_INGESTED}
        for p in sorted(files_missing_from_inventory(root))
    ]

"""Strategy: surface multi-language test files as discoverable file nodes.

The bundled language strategies (``python_module``, ``typescript_exports``,
``java``, ``csharp``, ``cpp_resolver``, ``tree_sitter``) intentionally do
not crawl test directories, so without this strategy a query for a
domain term like ``telemetry test`` returned an empty result even though
the on-disk test files clearly exist.

This strategy walks the configured glob, emits one ``file`` node per
matched test file with ``roles: ["test"]`` and a stable canonical id
(``file:<rel_path_no_ext>`` per ADR 0041 § Layer 1), and adds a
``tests`` edge to the production peer when one can be located on disk.
The strategy never reads file contents and applies the shared
exclusion policy via :mod:`weld.strategies._helpers`, so the cost is
proportional to the number of matched test files.

Per ADR 0046 (multi-language test-peer edges) the strategy dispatches
by file extension to per-language resolvers:

- ``.py`` -> :mod:`weld.strategies._test_peer_python`
- ``.go`` -> :mod:`weld.strategies._test_peer_go`
- ``.ts`` / ``.tsx`` / ``.js`` / ``.jsx`` -> :mod:`weld.strategies._test_peer_ts`
- ``.java`` -> :mod:`weld.strategies._test_peer_java`
- ``.cs`` -> :mod:`weld.strategies._test_peer_csharp`
- ``.rs`` -> :mod:`weld.strategies._test_peer_rust`

Each resolver implements ``is_test_file`` and ``resolve_peer``; the
emission shape (file node + ``tests`` edge with ``confidence=inferred``)
is identical across languages so the impact engine sees a uniform
graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies import (
    _test_peer_csharp,
    _test_peer_go,
    _test_peer_java,
    _test_peer_python,
    _test_peer_rust,
    _test_peer_ts,
)
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

# Re-exported for backward compatibility with the pre-multi-language
# unit tests. New code should prefer the per-language modules directly.
_TEST_SUFFIX = _test_peer_python._TEST_SUFFIX

#: Resolver protocol: each per-language helper exposes ``is_test_file``
#: and ``resolve_peer``. ``resolve_peer`` returns ``(peer_id, peer_rel)``
#: when the peer file exists on disk, ``None`` otherwise.
_PeerResolver = Callable[[Path, Path], "tuple[str, str] | None"]
_TestPredicate = Callable[[Path], bool]

#: Dispatch table keyed by suffix. The Python entry handles ``.py``;
#: the TS/JS entry handles all four web extensions through the shared
#: TS helper. Order in this dict matters only for documentation;
#: lookup is by suffix.
_RESOLVERS_BY_SUFFIX: dict[str, tuple[_TestPredicate, _PeerResolver]] = {
    ".py": (_test_peer_python.is_test_file, _test_peer_python.resolve_peer),
    ".go": (_test_peer_go.is_test_file, _test_peer_go.resolve_peer),
    ".ts": (_test_peer_ts.is_test_file, _test_peer_ts.resolve_peer),
    ".tsx": (_test_peer_ts.is_test_file, _test_peer_ts.resolve_peer),
    ".js": (_test_peer_ts.is_test_file, _test_peer_ts.resolve_peer),
    ".jsx": (_test_peer_ts.is_test_file, _test_peer_ts.resolve_peer),
    ".java": (_test_peer_java.is_test_file, _test_peer_java.resolve_peer),
    ".cs": (_test_peer_csharp.is_test_file, _test_peer_csharp.resolve_peer),
    ".rs": (_test_peer_rust.is_test_file, _test_peer_rust.resolve_peer),
}


def _legacy_test_node_id(rel_path: Path) -> str:
    """Pre-ADR-0041 file-id shape for Python tests; recorded under aliases.

    Only emitted for ``*_test.py`` modules to preserve compatibility
    with sidecar caches and MCP transcripts that captured the legacy
    ``file:tests/<stem>`` shape. Other languages were never indexed
    under that prefix and therefore do not need an alias.
    """
    return f"file:tests/{rel_path.stem}"


def _test_node_id(rel_path: Path) -> str:
    """Return the canonical node id for a discovered test module.

    Per ADR 0041 § Layer 1, the id is the full repo-relative POSIX
    path without extension routed through :func:`weld._node_ids.file_id`.
    """
    return _canonical_file_id(rel_path.as_posix())


def _peer_node_id(rel_path: Path) -> str | None:
    """Return the *first* candidate peer node id for a Python ``*_test.py``.

    Provenance-only helper retained for backward compatibility with the
    pre-multi-language unit tests in
    ``weld_test_peer_strategy_test.py``. New callers should use the
    per-language ``resolve_peer`` helpers, which require a real on-disk
    match before returning a peer id.
    """
    return _test_peer_python.first_candidate_peer_id(rel_path)


def _resolve_glob(root: Path, pattern: str, excludes: list[str]) -> list[Path]:
    """Resolve *pattern* under *root* using the shared walker.

    Mirrors the resolution path used by ``python_module`` so excluded
    subtrees (``.cache``, ``node_modules``, nested-repo copies, plus any
    user-supplied excludes) are pruned during descent rather than
    after-the-fact.
    """
    from weld.glob_match import walk_glob

    matched: list[Path] = []
    if "**" in pattern:
        for path in walk_glob(root, pattern, excludes=excludes):
            matched.append(path)
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return []
        for path in walk_glob(root, pattern, excludes=excludes):
            matched.append(path)
    return filter_glob_results(root, matched, excludes=excludes)


def _resolver_for(rel: Path) -> tuple[_TestPredicate, _PeerResolver] | None:
    """Pick the per-language resolver for *rel* by file suffix.

    Returns ``None`` when the suffix is not in :data:`_RESOLVERS_BY_SUFFIX`,
    which causes the caller to skip the file. This is the deterministic
    way the strategy declines files that match the configured glob but
    are not test files in any supported language (e.g. a stray
    ``foo.txt``).
    """
    return _RESOLVERS_BY_SUFFIX.get(rel.suffix)


def _build_node(rel: Path) -> tuple[str, dict]:
    """Build the ``(node_id, node_dict)`` pair for a discovered test file.

    Python test modules carry the legacy ``file:tests/<stem>`` alias
    for one minor version (ADR 0041 migration). Other languages were
    never indexed under that prefix.
    """
    nid = _test_node_id(rel)
    node_props: dict = {
        "file": rel.as_posix(),
        "kind": "test",
        "roles": ["test"],
        "source_strategy": "test_peer",
        "authority": "derived",
        "confidence": "definite",
    }
    if rel.suffix == ".py":
        legacy_nid = _legacy_test_node_id(rel)
        aliases = sorted({legacy_nid} - {nid})
        if aliases:
            node_props["aliases"] = aliases
    return nid, {
        "type": "file",
        "label": rel.stem,
        "props": node_props,
    }


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit a ``file`` node per matched test module + ``tests`` peer edges.

    The per-language resolver picks the test-file predicate and peer
    resolution by file suffix. Files that match the glob but do not
    look like test files in any supported language are silently
    skipped; missing peers yield no edge so
    :func:`weld._discover_postprocess._clean_and_dedup_edges` has
    nothing to prune.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", []) or []

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched = _resolve_glob(root, pattern, excludes)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    for path in sorted(matched):
        if should_skip(path, excludes, root=root):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        resolver = _resolver_for(rel)
        if resolver is None:
            continue
        is_test, resolve_peer = resolver
        if not is_test(rel):
            continue

        nid, node = _build_node(rel)
        nodes[nid] = node
        discovered_from.append(rel.parent.as_posix() + "/")

        resolved = resolve_peer(root, rel)
        if resolved is not None:
            peer_id, _peer_path = resolved
            edges.append(
                {
                    "from": nid,
                    "to": peer_id,
                    "type": "tests",
                    "props": {
                        "source_strategy": "test_peer",
                        "confidence": "inferred",
                    },
                }
            )

    # Deduplicate discovered_from while preserving insertion order; the
    # discovery layer expects a list of unique directory hints.
    seen: set[str] = set()
    deduped: list[str] = []
    for d in discovered_from:
        if d not in seen:
            seen.add(d)
            deduped.append(d)

    return StrategyResult(nodes, edges, deduped)

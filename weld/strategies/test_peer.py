"""Strategy: surface ``weld/tests/*_test.py`` as discoverable file nodes.

The default ``python_module`` strategy intentionally skips modules whose
names lack top-level public exports, and it is not configured for the
``weld/tests/`` directory anyway. As a result, querying the connected
structure for a domain term like ``telemetry test`` returned an empty
result even though the on-disk test files clearly exist.

This strategy walks the configured glob (typically
``weld/tests/*_test.py``), emits one ``file`` node per test module with
``roles: ["test"]`` and a stable canonical id (``file:<rel_path_no_ext>``
per ADR 0041 § Layer 1, e.g. ``file:weld/tests/<stem>``), and adds a
``tests`` edge to the production peer when one can be located on disk.
The strategy never reads file contents and applies the shared
exclusion policy via :mod:`weld.strategies._helpers`, so the cost is
proportional to the number of matched test files.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

# ``_test.py`` is the canonical Bazel/pytest naming convention used
# throughout this repository. Helper modules drop the suffix so they are
# never mistaken for runnable tests.
_TEST_SUFFIX = "_test"

# Many modules in this repository follow ``<area>_test.py`` while their
# production peer lives at ``<area>.py``. A smaller subset uses the
# ``weld_<area>_test.py`` shape against ``<area>.py`` (or
# ``_<area>.py`` when the production module is private). We try these
# transforms in order and stop at the first hit so the resolved peer
# id is stable and predictable.
_PEER_PREFIX_CANDIDATES: tuple[str, ...] = ("", "weld_")
_PEER_FILENAME_PREFIXES: tuple[str, ...] = ("", "_")


def _legacy_test_node_id(rel_path: Path) -> str:
    """Pre-ADR-0041 file-id shape; recorded under ``aliases`` for compat."""
    return f"file:tests/{rel_path.stem}"


def _test_node_id(rel_path: Path) -> str:
    """Return the canonical node id for a discovered test module.

    Per ADR 0041 § Layer 1, the id is the full repo-relative POSIX
    path without extension routed through :func:`weld._node_ids.file_id`.
    The trailing ``_test`` suffix on the stem is preserved naturally so
    downstream consumers retain the semantic distinction between a test
    module and its production peer (lowest-blast-radius default per
    PR 4c).
    """
    return _canonical_file_id(rel_path.as_posix())


def _candidate_peer_stems(test_stem: str) -> list[str]:
    """Yield candidate production-module stems for a ``*_test.py`` stem.

    Order matches ``_PEER_PREFIX_CANDIDATES``: first the literal
    ``stem_without_suffix``, then variants with leading repo-style
    prefixes stripped. Returns an empty list when the stem does not
    look like a test module.
    """
    if not test_stem.endswith(_TEST_SUFFIX) or test_stem == _TEST_SUFFIX:
        return []
    base = test_stem[: -len(_TEST_SUFFIX)]
    if not base:
        return []
    candidates: list[str] = [base]
    for prefix in _PEER_PREFIX_CANDIDATES:
        if prefix and base.startswith(prefix):
            stripped = base[len(prefix):]
            if stripped and stripped not in candidates:
                candidates.append(stripped)
    return candidates


def _resolve_peer(
    root: Path,
    rel_path: Path,
) -> tuple[str, str] | None:
    """Resolve *rel_path* to ``(peer_id, peer_rel_posix)`` when possible.

    Walks each candidate stem and each filename-prefix variant
    (``foo.py`` then ``_foo.py``) under the test file's grandparent
    directory. Only the first existing file is returned; missing peers
    yield ``None`` so the caller skips edge emission instead of writing
    a dangling edge.

    The peer id is the canonical ADR-0041 file-id for the resolved peer
    file, matching the form emitted by ``python_module`` after the
    PR 1 migration so the ``tests`` edge points at a real anchor.
    """
    parent = rel_path.parent.parent
    for stem_guess in _candidate_peer_stems(rel_path.stem):
        for fn_prefix in _PEER_FILENAME_PREFIXES:
            filename = f"{fn_prefix}{stem_guess}.py"
            candidate = root / parent / filename
            if candidate.is_file():
                peer_rel = (parent / filename).as_posix()
                peer_id = _canonical_file_id(peer_rel)
                return peer_id, peer_rel
    return None


def _peer_node_id(rel_path: Path) -> str | None:
    """Return the *first* candidate peer node id for a ``*_test.py`` module.

    This helper is provenance-only: it never inspects the filesystem and
    therefore only returns the leading candidate. The actual edge is
    emitted by :func:`extract` after :func:`_resolve_peer` confirms the
    file exists.

    Per ADR 0041 § Layer 1, the candidate id is the canonical
    ``file:<rel_path_without_ext>`` form. Because this helper never
    touches the disk it cannot disambiguate the production directory;
    callers that need a guaranteed match should call
    :func:`_resolve_peer` instead.
    """
    candidates = _candidate_peer_stems(rel_path.stem)
    if not candidates:
        return None
    parent = rel_path.parent.parent.as_posix()
    if parent and parent != ".":
        return _canonical_file_id(f"{parent}/{candidates[0]}")
    return _canonical_file_id(candidates[0])


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


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit a ``file`` node per matched test module + ``tests`` peer edges."""
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
        # Only files whose stem ends with the canonical test suffix are
        # surfaced. This keeps test helpers (``telemetry_test_helpers.py``)
        # and conftest-style modules out of the result.
        if not rel.stem.endswith(_TEST_SUFFIX) or rel.stem == _TEST_SUFFIX:
            continue

        nid = _test_node_id(rel)
        legacy_nid = _legacy_test_node_id(rel)
        aliases = sorted({legacy_nid} - {nid})
        rel_posix = rel.as_posix()
        node_props: dict = {
            "file": rel_posix,
            "kind": "test",
            "roles": ["test"],
            "source_strategy": "test_peer",
            "authority": "derived",
            "confidence": "definite",
        }
        if aliases:
            node_props["aliases"] = aliases
        nodes[nid] = {
            "type": "file",
            "label": rel.stem,
            "props": node_props,
        }
        discovered_from.append(rel.parent.as_posix() + "/")

        resolved = _resolve_peer(root, rel)
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

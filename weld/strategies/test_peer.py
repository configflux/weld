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
The node shape itself never required reading file contents, and applies
the shared exclusion policy via :mod:`weld.strategies._helpers`, so the
cost was proportional to the number of matched test files; the mock-patch
harvester below and (bd ikof) the summary reader are the two reads that
now happen per language, gated the same way.

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
graph -- the edge is minted here, once, for all six.

Per ADR 0074 (second amendment) every ``tests`` edge also carries
``props.provenance.file``: the **test** file this strategy walked, which
is the file that produced the edge. That direction is load-bearing for
incremental correctness, not decoration. The incremental purge
(:func:`weld._incremental_purge.purge_edges_by_provenance`) retains an
edge across a node purge only when it can attribute the edge to a file;
an unattributed edge falls back to endpoint membership and is dropped
whenever *either* endpoint is purged. So without this stamp, editing a
production module purged its ``file:`` node and took the inbound
``tests`` edge with it -- and because the test file itself was clean,
this strategy's glob held no dirty file, never re-ran, and never
re-minted the edge (bd heum). Stamping the *peer* instead would be
exactly as broken: the peer is stale precisely in the case that fails.

Test modules that name a mock target as a *string literal* get a second,
finer edge kind on top of the file-level ``tests`` peer: ``depends_on`` to
what the string names. Such a target is a real dependency no import records,
so without it "who touches this symbol" omits every mock-based test (bd ymso).
Two languages have the shape, dispatched through
:data:`_MOCK_HARVESTERS_BY_SUFFIX`:

- ``.py`` -> :mod:`weld.strategies._mock_patch_python`, resolving
  ``unittest.mock.patch("dotted.string")`` against the project root.
- ``.ts`` / ``.tsx`` / ``.js`` / ``.jsx`` ->
  :mod:`weld.strategies._mock_module_ts`, resolving
  ``jest.mock("./module")`` / ``vi.mock("../lib/thing")`` against the test
  file's own directory (bd gyve).

Reading the file is what this costs: the strategy used to resolve peers from
filenames alone. The read is bounded by the configured test glob, one scan per
test module, and the per-harvester cache keeps each *mocked* module to one
parse (Python) or one stat (TS/JS) per run.

Every emitted node also carries ``props.summary`` (bd ikof, widened by bd
cw4f), populated via :data:`_SUMMARY_RESOLVERS_BY_SUFFIX` for ``.py``
(module docstring), ``.go``/``.rs``/``.ts``/``.tsx``/``.js``/``.jsx``/
``.java`` (the test file's own leading comment, read by
:mod:`weld.strategies._ts_file_doc_comments`) and left ``""`` for ``.cs``
today -- the same "always present, empty when absent" shape ADR 0114
established for every other ``props.summary`` writer, so a consumer never
has to branch on whether the key exists. Before bd ikof, a test file's own
docstring -- often the single most precise statement of the invariant it
proves -- was invisible to the query index entirely: ``wd query
"incremental discovery equivalence full"`` matched zero of the six
``incremental_*_equivalence_test.py`` files despite their opening lines
stating exactly that ("Incremental refresh is byte-equivalent to a full
discover"), because only their filename tokens were ever queryable. See
:mod:`weld._test_paths` for the ranking half: giving test nodes a summary
was necessary but not sufficient, since ``test_noise_demotion`` sorted
every test node behind every non-test node regardless of match strength.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies import (
    _mock_module_ts,
    _mock_patch_python,
    _test_peer_csharp,
    _test_peer_go,
    _test_peer_java,
    _test_peer_python,
    _test_peer_rust,
    _test_peer_ts,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

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

#: Mock-target harvesters, keyed by suffix: ``(edge builder, cache factory)``.
#: Both entries emit the same ``depends_on`` edge tagged
#: ``props.resolution = "mock_patch"``; only the resolution rule differs
#: (dotted-absolute for Python, module-relative for TS/JS).
#:
#: This was an explicit ``if rel.suffix == ".py"`` branch, carrying the note
#: that "an explicit branch beats a per-language hook with one filled slot;
#: the day the second language arrives is the day the hook is worth its cost".
#: bd gyve is that day. Java/C#/Go/Rust stay absent rather than mapping to a
#: no-op: they name a mock subject with a type or interface reference the
#: import graph already records, so there is nothing for them to harvest, and
#: an empty entry would imply a gap where there is none.
_MOCK_HARVESTERS_BY_SUFFIX: dict[str, tuple[Callable, Callable]] = {
    ".py": (_mock_patch_python.patch_target_edges, _mock_patch_python.new_cache),
    ".ts": (_mock_module_ts.mock_target_edges, _mock_module_ts.new_cache),
    ".tsx": (_mock_module_ts.mock_target_edges, _mock_module_ts.new_cache),
    ".js": (_mock_module_ts.mock_target_edges, _mock_module_ts.new_cache),
    ".jsx": (_mock_module_ts.mock_target_edges, _mock_module_ts.new_cache),
}

#: Summary readers, keyed by suffix (bd ikof, widened by bd cw4f): each
#: returns the opening paragraph of the test file's own leading comment
#: (docstring for Python, package/module doc comment for the tree-sitter
#: languages), ``""`` when there is none or the language has no reader yet.
#: ``.cs`` stays without a reader: unlike the other five, a C# XML doc
#: comment's own content is structured markup (``/// <summary>...``), which
#: is exactly the "different shape of work" ADR 0124 already deferred for
#: C#'s *symbol*-level reader and which reading at the file level does not
#: sidestep (see the bd comment on 7ui6). Every node still gets an explicit
#: ``summary: ""`` regardless of suffix (see :func:`_build_node`), so a
#: consumer never has to branch on whether the key exists.
_SUMMARY_RESOLVERS_BY_SUFFIX: dict[str, Callable[[Path, Path], str]] = {
    ".py": _test_peer_python.module_summary_for_test,
    ".go": _test_peer_go.file_summary_for_test,
    ".rs": _test_peer_rust.file_summary_for_test,
    ".ts": _test_peer_ts.file_summary_for_test,
    ".tsx": _test_peer_ts.file_summary_for_test,
    ".js": _test_peer_ts.file_summary_for_test,
    ".jsx": _test_peer_ts.file_summary_for_test,
    ".java": _test_peer_java.file_summary_for_test,
}


def _legacy_test_node_id(rel_path: Path) -> str:
    """Pre-ADR-0041 file-id shape for Python tests; recorded under aliases.

    Emitted for every Python test module (``*_test.py`` and pytest's
    ``test_*.py``) to preserve compatibility with sidecar caches and
    MCP transcripts that captured the legacy ``file:tests/<stem>``
    shape. Other languages were never indexed under that prefix and
    therefore do not need an alias.
    """
    return f"file:tests/{rel_path.stem}"


def _test_node_id(rel_path: Path) -> str:
    """Return the canonical node id for a discovered test module.

    Per ADR 0041 § Layer 1, the id is the full repo-relative POSIX
    path without extension routed through :func:`weld._node_ids.file_id`.
    """
    return _canonical_file_id(rel_path.as_posix())


def _peer_node_id(rel_path: Path) -> str | None:
    """Return the *first* candidate peer node id for a Python test file.

    Provenance-only helper retained for backward compatibility with the
    pre-multi-language unit tests in
    ``weld_test_peer_strategy_test.py``. Recognises both ``*_test.py``
    (Bazel / Go-style) and ``test_*.py`` (pytest default) inputs via
    :func:`_test_peer_python.first_candidate_peer_id`. New callers
    should use the per-language ``resolve_peer`` helpers, which
    require a real on-disk match before returning a peer id.
    """
    return _test_peer_python.first_candidate_peer_id(rel_path)


def _resolver_for(rel: Path) -> tuple[_TestPredicate, _PeerResolver] | None:
    """Pick the per-language resolver for *rel* by file suffix.

    Returns ``None`` when the suffix is not in :data:`_RESOLVERS_BY_SUFFIX`,
    which causes the caller to skip the file. This is the deterministic
    way the strategy declines files that match the configured glob but
    are not test files in any supported language (e.g. a stray
    ``foo.txt``).
    """
    return _RESOLVERS_BY_SUFFIX.get(rel.suffix)


def _build_node(rel: Path, summary: str = "") -> tuple[str, dict]:
    """Build the ``(node_id, node_dict)`` pair for a discovered test file.

    Python test modules carry the legacy ``file:tests/<stem>`` alias
    for one minor version (ADR 0041 migration). Other languages were
    never indexed under that prefix.

    Per ADR 0042, every emitted file node carries ``props.origin``.
    The strategy only ever sees files that matched a configured test
    glob under the project root (excludes prune ``node_modules`` /
    nested-repo / cache trees during the walk), so every match is
    unambiguously project code -- the strategy never has the signals
    needed to mint a stdlib or external test file node.

    *summary* (bd ikof) is always written, defaulting to ``""`` for a
    language with no reader in :data:`_SUMMARY_RESOLVERS_BY_SUFFIX` yet or a
    file with no docstring -- the same "key always present" shape ADR 0114
    established, so a consumer never has to check whether the prop exists.
    """
    nid = _test_node_id(rel)
    node_props: dict = {
        "file": rel.as_posix(),
        "kind": "test",
        "roles": ["test"],
        "source_strategy": "test_peer",
        "authority": "derived",
        "confidence": "definite",
        "origin": "project",
        "summary": summary,
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
    nothing to prune. Mock-patch targets follow the same rule: an
    unresolvable target yields no edge rather than a dangling one.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    # One cache per harvester for the whole call, keyed by the harvester
    # itself, so a module mocked by fifty test files is parsed (Python) or
    # stat-ed (TS/JS) once rather than fifty times. Built lazily: a run whose
    # glob matches no TS test never allocates the TS cache.
    mock_caches: dict[object, dict] = {}

    pattern = source.get("glob", "")
    excludes = source.get("exclude", []) or []

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched = resolve_glob(root, pattern, excludes)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    for path in sorted(matched):
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

        # Provenance is the test file itself, recorded before it is read --
        # never ``rel.parent``. A parent directory is broader than the ADR
        # 0017 source-*file* model even when it is harmless, and for a
        # repo-root test glob (``*_test.py``, which small Python projects
        # really do configure) the parent is ``.``, so the entry is ``"./"``
        # -- the marker :func:`weld._git._path_is_tracked` reads as "every
        # path in this repository is tracked source". Such a repo is then
        # permanently ``source_stale``. The peer this strategy resolves is
        # deliberately *not* provenance; see the module docstring.
        discovered_from.append(rel.as_posix())

        summary_resolver = _SUMMARY_RESOLVERS_BY_SUFFIX.get(rel.suffix)
        summary = summary_resolver(root, rel) if summary_resolver else ""
        nid, node = _build_node(rel, summary)
        nodes[nid] = node

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
                        # ADR 0074: the file whose scan produced this edge,
                        # which is the *test* file -- never the peer. See
                        # the module docstring for why that direction is
                        # load-bearing for incremental correctness.
                        "provenance": {"file": rel.as_posix()},
                    },
                }
            )

        # Mock targets named by string literal, per language. See
        # :data:`_MOCK_HARVESTERS_BY_SUFFIX` for why this is a table now.
        harvester = _MOCK_HARVESTERS_BY_SUFFIX.get(rel.suffix)
        if harvester is not None:
            build_edges, make_cache = harvester
            cache = mock_caches.get(build_edges)
            if cache is None:
                cache = mock_caches[build_edges] = make_cache()
            edges.extend(build_edges(root, rel, nid, cache=cache))

    # Deduplicate discovered_from while preserving insertion order; the
    # discovery layer expects a list of unique directory hints.
    seen: set[str] = set()
    deduped: list[str] = []
    for d in discovered_from:
        if d not in seen:
            seen.add(d)
            deduped.append(d)

    return StrategyResult(nodes, edges, deduped)

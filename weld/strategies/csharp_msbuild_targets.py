"""Strategy: C# MSBuild target extraction (ADR 0056 Wave 3).

Parses ``<Target Name="...">`` and child ``<ItemGroup>`` declarations
in ``.csproj``, ``.props``, and ``.targets`` files. Each declared
target becomes one ``build-target://<csproj-stem>:<target>`` node;
``BeforeTargets`` and ``AfterTargets`` attributes become ``depends_on``
edges that materialise the MSBuild ordering relation in the graph:

- ``BeforeTargets="Build"`` on ``MyPre`` -> ``MyPre`` runs before
  ``Build``, so the upstream-to-downstream edge points
  ``MyPre -[depends_on]-> Build`` (ordering="before").
- ``AfterTargets="Restore"`` on ``MyPost`` -> ``MyPost`` runs after
  ``Restore``, so the edge is ``MyPost -[depends_on]-> Restore``
  (ordering="after").

Both directions share the ``depends_on`` edge type per ADR 0056 ("use
existing edge types"). The ``ordering`` prop on the edge preserves the
distinction so downstream consumers can keep them apart without
introducing a new edge vocabulary.

Item-group declarations *inside* a ``<Target>`` are summed into two
props on the target node:

- ``itemgroup_count`` -- how many ``<ItemGroup>`` blocks the target
  contains.
- ``item_count`` -- the total number of items across those blocks.

This is a deliberately small surface: the goal is "this target
contributes N declarations" rather than full item-level extraction
(which would require modelling MSBuild item types, out of scope per
ADR 0056 Wave 3).

Out of scope (ADR 0056):

- ``Condition=`` attribute resolution on targets or properties.
- Cross-csproj target inheritance via ``<Import Project="...">``.
- ``DependsOnTargets`` attribute (MSBuild's third ordering attribute) --
  follow-up if needed; the Wave 3 deliverable is BeforeTargets /
  AfterTargets.

Per ADR 0050 every emitted edge ships with
``confidence="definite"``: the XML is deterministic, so target names
and ordering relations are precise. The XML may use the legacy
``http://schemas.microsoft.com/developer/msbuild/2003`` namespace --
local-name handling below strips it transparently.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)
from weld.strategies._msbuild_targets_parser import (
    iter_target_records,
    parse_msbuild_xml,
    split_msbuild_list as _split_targets_attribute,
)

#: Default glob covers the three MSBuild file kinds that may declare a
#: ``<Target>``. Discovery callers can override via the ``glob`` source
#: entry; the strategy still works with a narrower glob (e.g. only
#: ``**/*.csproj``).
_DEFAULT_GLOBS: tuple[str, ...] = (
    "**/*.csproj",
    "**/*.props",
    "**/*.targets",
)

_STRATEGY_NAME = "csharp_msbuild_targets"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract MSBuild target nodes and ordering edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    excludes = source.get("exclude", [])

    matched = _matched_files(root, pattern)
    matched = filter_glob_results(root, matched)

    for project_file in matched:
        if not project_file.is_file():
            continue
        if should_skip(project_file, excludes):
            continue

        rel_path = project_file.relative_to(root).as_posix()
        root_elem = parse_msbuild_xml(project_file)
        if root_elem is None:
            # Malformed XML is tolerated: discovery does not crash, but
            # the file contributes no targets. Track it so the canary
            # in ``test_malformed_xml_does_not_crash`` stays honest.
            continue

        owner = project_file.stem
        target_records = list(iter_target_records(root_elem))
        if not target_records:
            continue

        discovered_from.append(rel_path)

        for record in target_records:
            target_node_id = _msbuild_target_id(owner, record.name)
            nodes[target_node_id] = _target_node(
                owner=owner,
                target_name=record.name,
                file=rel_path,
                itemgroup_count=record.itemgroup_count,
                item_count=record.item_count,
                depends_on_targets=record.depends_on_targets,
            )

            for before in sorted(record.before_targets):
                edges.append(_ordering_edge(
                    target_node_id, owner, before, "before",
                ))
            for after in sorted(record.after_targets):
                edges.append(_ordering_edge(
                    target_node_id, owner, after, "after",
                ))

    return StrategyResult(nodes, edges, discovered_from)


def _matched_files(root: Path, pattern: str | None) -> list[Path]:
    """Return MSBuild project / props / targets files under *root*.

    When *pattern* is provided the caller's glob is honoured verbatim;
    the special MSBuild brace-glob (``**/*.{csproj,props,targets}``) is
    expanded manually because :meth:`Path.glob` does not support brace
    expansion. When *pattern* is missing the defaults union is used.
    """
    if pattern:
        return _expand_brace_glob(root, pattern)
    matched: list[Path] = []
    for default in _DEFAULT_GLOBS:
        matched.extend(sorted(root.glob(default)))
    # De-duplicate while preserving sort order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in sorted(matched):
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _expand_brace_glob(root: Path, pattern: str) -> list[Path]:
    """Expand a brace alternation like ``**/*.{csproj,props,targets}``.

    Most patterns do not need brace handling; we keep ``Path.glob``'s
    behaviour for everything else.
    """
    if "{" not in pattern or "}" not in pattern:
        return sorted(root.glob(pattern))
    brace_start = pattern.index("{")
    brace_end = pattern.index("}", brace_start)
    prefix = pattern[:brace_start]
    suffix = pattern[brace_end + 1:]
    options = pattern[brace_start + 1:brace_end].split(",")
    matched: list[Path] = []
    for option in options:
        option = option.strip()
        if not option:
            continue
        matched.extend(root.glob(f"{prefix}{option}{suffix}"))
    return sorted(set(matched))


def _msbuild_target_id(owner: str, target_name: str) -> str:
    """Return the canonical ``build-target://<owner>:<target>`` id.

    *owner* is the originating file's stem (``Sample.Web`` for
    ``Sample.Web.csproj``). The combination is unique within the repo
    because MSBuild target names are unique within a single project
    file. Cross-project inheritance via ``<Import>`` is out of scope
    per ADR 0056.
    """
    return f"build-target://{owner}:{target_name}"


def _target_node(
    *,
    owner: str,
    target_name: str,
    file: str,
    itemgroup_count: int,
    item_count: int,
    depends_on_targets: list[str],
) -> dict:
    """Build the node payload for a single ``<Target>``.

    The ``label`` is ``<owner>:<target>`` so a graph viewer renders the
    same identifier the node uses, without the URL prefix. The ``kind``
    prop is ``msbuild_target`` so consumers can filter MSBuild-flavoured
    build targets from solution / project flavoured ones (which also
    use the ``build-target`` node type).
    """
    props: dict = {
        "file": file,
        "owner": owner,
        "target_name": target_name,
        "kind": "msbuild_target",
        "itemgroup_count": itemgroup_count,
        "item_count": item_count,
        "source_strategy": _STRATEGY_NAME,
        "authority": "canonical",
        "confidence": "definite",
        "roles": ["build"],
        "language": "csharp",
    }
    if depends_on_targets:
        # Non-edge ``DependsOnTargets`` mirror: kept as a prop for now
        # so consumers can see the list without scanning edges. Per ADR
        # 0056 Wave 3 the explicit ordering edges are limited to
        # Before/AfterTargets; DependsOnTargets is informational here.
        props["depends_on_targets"] = sorted(depends_on_targets)
    return {
        "type": "build-target",
        "label": f"{owner}:{target_name}",
        "props": props,
    }


def _ordering_edge(
    src_id: str, owner: str, target_name: str, ordering: str,
) -> dict:
    """Build a ``depends_on`` ordering edge."""
    dst_id = _msbuild_target_id(owner, target_name)
    return {
        "from": src_id,
        "to": dst_id,
        "type": "depends_on",
        "props": {
            "source_strategy": _STRATEGY_NAME,
            "confidence": "definite",
            "ordering": ordering,
        },
    }


#: Re-exported for the strategy's unit test. The canonical
#: implementation lives in :mod:`weld.strategies._msbuild_targets_parser`
#: so this strategy and the parser cannot disagree on the rule.
_split_targets_attribute = _split_targets_attribute


__all__ = [
    "extract",
    "_msbuild_target_id",
    "_split_targets_attribute",
]

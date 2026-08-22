"""Cross-source-edge-provenance rule (ADR 0074, sixth amendment).

Four reactive repro-and-amendment cycles on this ADR (``python_callgraph``
the original decision, ``test_peer`` bd heum, ``bazel`` bd cpkp,
``concept_from_bd``/``tool_script``/``yaml_meta``/``gh_workflow`` bd 57lra)
share one shape: a strategy emits an edge whose endpoints are minted by
*different* discover.yaml source entries, without ``props.provenance.file``.
When the target's own entry is later dirtied while the producing entry's own
source stays clean, ``weld._incremental_purge.purge_edges_by_provenance`` has
nothing to attribute the edge to, applies the conservative
endpoint-membership floor, and the edge is silently lost until the next
``--full`` discover -- a loss no freshness signal reports.

This module is the authoring-time detector the amendment history kept
re-deriving by hand: run it over a freshly-discovered graph (this repo's own,
via the ``cross-source-edge-provenance`` ``wd lint`` rule, or any other) and
it flags the next strategy that regresses this the moment its output lands
in the graph.

Formulation
-----------
A strategy edge is *cross-source* -- and therefore required to carry
``props.provenance.file`` -- when its endpoints are not provably minted by
the same single discover.yaml source entry. "Provably" is answered per edge,
cheaply, from the graph plus the config alone (no strategy internals, no
synthetic fixture):

1. The edge already carries a non-empty ``props.provenance.file`` --
   compliant. Extra stamping beyond what is required is always fine, so
   nothing past this point needs to run.
2. The ``from`` node's ``props.roles`` contains ``"package"`` -- exempt.
   Directory/namespace-rooted parent nodes (``python_package``,
   ``csharp_package``) are governed by the *documented alternate*
   self-repair mechanism (purge-on-zero-``contains``-out-edges,
   ``docs/extending-discovery.md`` "Node and edge shape"), not by ADR 0074
   provenance, and by construction do not anchor via ``props.file``.
3. The ``to`` node has no resolvable ``props.file`` -- exempt.
   ``purge_stale_nodes`` only ever purges a node by matching a *stale file*
   against ``props.file``; an endpoint with no file anchor can never be
   purged by content-dirtying, so the edge is never at ADR 0074 risk
   regardless of provenance (an external-dependency sentinel, for example).
4. The ``from`` node has no resolvable ``props.file`` -- **violation**. This
   is the conservative default: nothing below can prove the edge safe
   another way, and every currently-compliant strategy's producer node
   *does* carry ``props.file``, so this branch should never legitimately
   fire on real strategy output.
5. ``from_file == to_file`` -- exempt. An intra-file edge's producer and
   target dirty together by construction; re-parsing the file is what
   re-emits the edge in the same pass.
6. Cross-file: resolve the literal discover.yaml source *entry* (not
   merged by strategy name -- see :mod:`weld._discover_source_globs`) whose
   glob-resolved file set contains ``from_file``. If the edge's strategy is
   not one of the two ADR-authorized dirty-subset carve-outs
   (:data:`NARROWS_OWN_DIRTY_SCOPE`) and that same entry's file set also
   contains ``to_file`` -- exempt. This is the bd 41vw shape
   (``incremental_markdown_provenance_purge_test.py``): dirtying *either*
   file forces the *whole* entry to re-run (every strategy except the two
   carve-outs reprocesses its entire matched set whenever any one member is
   dirty -- ADR 0008's default), which re-mints every edge that entry owns,
   so no window for loss exists. Entry granularity, not strategy-name
   granularity, is what makes this sound: a strategy registered on several
   disjoint globs (``python_callgraph`` has four) is one strategy but
   several independent dirty-scopes, and collapsing them would silently
   re-open the original defect this ADR exists to close (see
   :data:`NARROWS_OWN_DIRTY_SCOPE`'s docstring for the sharper version of
   this failure mode, which entry granularity alone does not fix).
7. Otherwise -- **violation**.

Only edges whose ``props.source_strategy`` names a strategy actually
declared in ``.weld/discover.yaml`` are considered at all
(:func:`weld._discover_source_globs.declared_strategies`). Post-processing
synthesis (``graph_closure``'s ``source_strategy: "graph_closure"``, the
node-merge driver's ``"topology"``) never appears in ``sources:`` and
reruns unconditionally and in full on every discovery pass -- it is not
merged from a purged prior graph the way strategy output is, so it is never
at ADR 0074 risk and evaluating it would only manufacture false positives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Mapping, Sequence

from weld._arch_lint_types import Violation
from weld._discover_source_globs import declared_strategies, source_entry_file_sets

RULE_ID = "cross-source-edge-provenance"

#: Strategies ADR 0074 (``python_callgraph``) and ADR 0084 (``python_module``)
#: authorize to narrow their own re-parse to a per-file dirty subset within
#: one source entry (``weld/strategies/_incremental_hint.py``). For these
#: two, sharing a source entry with the target does NOT prove safety: dirty
#: file B's entry-level rerun does not guarantee producer file A gets
#: re-parsed in the same pass, because the strategy itself narrows further
#: than "did this entry's dirty-intersect fire" -- this is exactly the
#: original cjij.2 defect (a `calls` edge between two symbols in the same
#: glob, lost when only the callee's file was dirty). Both strategies
#: already stamp every edge they emit unconditionally, so this set exists to
#: keep the entry-membership exemption (item 6 above) from silently
#: re-opening that defect for either of them, or for a hypothetical third
#: strategy that later takes the same carve-out without updating this set.
NARROWS_OWN_DIRTY_SCOPE = frozenset({"python_callgraph", "python_module"})


def _make_violation(node_id: str, message: str) -> Violation:
    """Build a :class:`weld._arch_lint_types.Violation`."""
    return Violation(rule=RULE_ID, node_id=node_id, message=message)


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


def _node_file(node: object) -> str:
    """Return *node*'s ``props.file``, or ``""`` if absent/unresolvable."""
    if not isinstance(node, Mapping):
        return ""
    props = node.get("props")
    if not isinstance(props, Mapping):
        return ""
    file_prop = props.get("file")
    return file_prop if isinstance(file_prop, str) and file_prop else ""


def _node_has_package_role(node: object) -> bool:
    if not isinstance(node, Mapping):
        return False
    props = node.get("props")
    if not isinstance(props, Mapping):
        return False
    roles = props.get("roles")
    return isinstance(roles, list) and "package" in roles


def _edge_has_provenance(edge: Mapping) -> bool:
    props = edge.get("props")
    if not isinstance(props, Mapping):
        return False
    prov = props.get("provenance")
    if not isinstance(prov, Mapping):
        return False
    file_prop = prov.get("file")
    return isinstance(file_prop, str) and bool(file_prop)


def _same_source_entry(
    file_sets: Sequence[set[str]], from_file: str, to_file: str
) -> bool:
    """True iff some one entry's resolved file set holds both files."""
    return any(
        from_file in file_set and to_file in file_set
        for file_set in file_sets
    )


def check_cross_source_edge_provenance(
    root: Path,
    nodes: Mapping[str, object],
    edges: Sequence[Mapping],
) -> Iterator[Violation]:
    """Flag strategy edges crossing a source-entry boundary with no provenance.

    *nodes* / *edges* are a freshly-discovered graph's own mappings (the
    ``weld.arch_lint`` adapter passes ``graph.dump()``'s halves). *root* is
    the project root ``.weld/discover.yaml`` is read from. See the module
    docstring for the full rule.
    """
    config = _load_yaml(root)
    sources = config.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    declared = declared_strategies(sources)
    if not declared:
        return

    entries_by_strategy: dict[str, list[set[str]]] = {}
    for strategy, file_set in source_entry_file_sets(root, sources):
        entries_by_strategy.setdefault(strategy, []).append(file_set)

    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        props = edge.get("props")
        strategy = props.get("source_strategy") if isinstance(props, Mapping) else None
        if strategy not in declared:
            continue
        if _edge_has_provenance(edge):
            continue

        from_id = edge.get("from")
        to_id = edge.get("to")
        from_node = nodes.get(from_id) if isinstance(from_id, str) else None
        to_node = nodes.get(to_id) if isinstance(to_id, str) else None

        if _node_has_package_role(from_node):
            continue
        to_file = _node_file(to_node)
        if not to_file:
            continue
        from_file = _node_file(from_node)
        if from_file:
            if from_file == to_file:
                continue
            if strategy not in NARROWS_OWN_DIRTY_SCOPE and _same_source_entry(
                entries_by_strategy.get(strategy, []), from_file, to_file,
            ):
                continue

        edge_type = edge.get("type", "?")
        yield _make_violation(
            node_id=f"{from_id} --{edge_type}--> {to_id}",
            message=(
                f"strategy {strategy!r} emitted a {edge_type!r} edge from "
                f"{from_id!r} to {to_id!r} across a discover.yaml source "
                f"boundary with no props.provenance.file -- ADR 0074: an "
                f"incremental refresh that dirties only the target, while "
                f"the {strategy!r} source itself stays clean, can silently "
                f"drop this edge and never re-mint it. Stamp props."
                f"provenance.file with the file {strategy!r} read to "
                f"produce the edge (the producer, never the target)."
            ),
        )


__all__ = ["NARROWS_OWN_DIRTY_SCOPE", "RULE_ID", "check_cross_source_edge_provenance"]

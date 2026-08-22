"""Node/edge contract-conformance check over a full graph (bd 5038-rhuc).

Root cause this closes: nothing asserted repo-wide that what discovery
*emits* is what ``weld.contract`` *accepts*. ``python_package`` stamped
``roles: ["package"]`` while ``"package"`` was absent from ``ROLE_VALUES``
-- a graph the product's own ``wd validate`` rejected, undetected until
someone ran it by hand (bd rgru). rgru's fix added one
``test_emitted_nodes_satisfy_the_contract`` method to each of
``python_package`` and ``csharp_package``'s own strategy test files. This
module is the generalization: one reusable check, run automatically over
every node and edge a strategy actually emits, so the next strategy that
regresses this needs no one to remember to wire it in by hand.

Formulation
-----------
For every node, run :func:`weld.contract.validate_node`. For every edge,
run :func:`weld.contract.validate_edge`. Each
:class:`weld.contract.ValidationError` becomes one
:class:`ContractViolation`, carrying the emitting ``props.source_strategy``
read directly off the offending node or edge -- never re-derived from the
error's own string ``path``, which stays free to change shape without
breaking attribution here.

Not a ``wd lint`` rule
-----------------------
ADR 0074's cross-source-edge-provenance check (bd whnwb,
:mod:`weld._graph_edge_provenance_lint`) shipped as *both* a ``wd lint``
rule and a real-repo zero-violations test, because no other surface
already caught that invariant. Node/edge contract conformance is
different: ``wd validate`` (``wd graph validate``, documented in
``README.md``) already runs :func:`weld.contract.validate_graph` --
``validate_node`` over every node, ``validate_edge`` over every edge --
against any repo's graph, and is the standing "give users this check on
their own repos" surface. A second ``wd lint`` rule running the identical
check under a different verb would duplicate that command, not extend the
product. The gap bd rhuc closes is narrower and purely internal: nothing
gated this repo's *own* fresh discovery output against the contract in the
test suite, so a strategy could regress it and nothing would go red until
a human happened to run ``wd validate`` by hand. A repo test closes exactly
that gap; see ``weld_node_edge_contract_repo_test.py``.

Relationship to weld_normalized_metadata_test.py
--------------------------------------------------
``_assert_valid_metadata_values`` there hand-picks a fixture per bundled
strategy and checks only three optional props' vocabulary (authority,
confidence, roles) *if present*. It was never wired to ``python_package``
or ``csharp_package`` -- the exact omission bd rgru found. This module's
check is strictly broader per node/edge it sees (every required field,
every vocabulary-constrained prop, span shape, edge referential integrity
-- the whole contract, not three props), and needs no per-strategy wiring:
run over a real discovery, it automatically covers every strategy that
fires on the tree being discovered.

Run over *this* repo's own tree (see the repo test), that set is the 14
strategies ``.weld/discover.yaml`` declares here. Cross-referenced against
normalized-metadata's own 14-strategy curated list, the two sets are
exactly 7/7/7:

* 7 overlap (``config_file``, ``firstline_md``, ``frontmatter_md``,
  ``markdown``, ``python_module``, ``tool_script``, ``yaml_meta``) --
  normalized-metadata's synthetic vocab check for these becomes redundant
  but harmless.
* 7 fire on this repo but were never in normalized-metadata's curated list
  at all (``bazel``, ``concept_from_bd``, ``python_callgraph``,
  ``python_package``, ``test_peer``, ``validator_targets``,
  ``viz_frontend``) -- net-new coverage from this module, ``python_package``
  (the rgru strategy itself) included.
* 7 match nothing in this repo's own source tree (``compose``,
  ``dockerfile``, ``fastapi``, ``pydantic``, ``sqlalchemy``,
  ``typescript_exports``, ``worker_stage``) -- only normalized-metadata's
  synthetic fixtures exercise these at all; a real-repo check can only see
  what the repo actually contains, so this module is a complement here,
  not a replacement.

(``weld_normalized_metadata_edge_test.py``'s ``ContractValidationIntegrationTest``
predates rgru with the same per-strategy ``validate_node`` shape for
``sqlalchemy`` specifically, plus a synthetic hand-authored ``topology:``
graph unrelated to strategy output -- neither overlaps this module's scope
either, for the same reason: no ``sqlalchemy`` source in this repo's own
tree.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

from weld.contract import validate_edge, validate_node


@dataclass(frozen=True)
class ContractViolation:
    """One ``validate_node``/``validate_edge`` failure, attributed to a strategy."""

    source_strategy: str
    kind: str  # "node" or "edge"
    subject_id: str  # node id, or "from -> to (type)" for an edge
    field: str
    message: str

    def __str__(self) -> str:
        return (
            f"[{self.source_strategy}] {self.kind} {self.subject_id!r} "
            f"field {self.field!r}: {self.message}"
        )


def _source_strategy(props: object) -> str:
    """Return ``props.source_strategy`` if it is a non-empty string."""
    if not isinstance(props, Mapping):
        return "<unknown>"
    strategy = props.get("source_strategy")
    return strategy if isinstance(strategy, str) and strategy else "<unknown>"


def check_node_edge_contract(
    nodes: Mapping[str, object],
    edges: Sequence[Mapping],
) -> Iterator[ContractViolation]:
    """Run the node/edge contract over every node and edge in a graph.

    *nodes* / *edges* are a graph document's own mappings (``graph["nodes"]``
    / ``graph["edges"]``, or ``Graph.dump()``'s halves -- the same shape
    :func:`weld.contract.validate_graph` consumes). Yields one
    :class:`ContractViolation` per :class:`weld.contract.ValidationError`
    either validator reports, so a caller sees exactly which strategy
    emitted the offending node or edge and which field failed.
    """
    for node_id, node in nodes.items():
        if not isinstance(node, Mapping):
            # validate_node assumes dict-like access; a garbage value (None,
            # an int) would raise instead of reporting. A checker whose job
            # is to catch every malformed emission must not itself crash on
            # the most malformed case -- report it as its own violation.
            yield ContractViolation(
                source_strategy="<unknown>",
                kind="node",
                subject_id=node_id,
                field="<node>",
                message=f"node value must be a mapping (got {type(node).__name__})",
            )
            continue
        strategy = _source_strategy(node.get("props"))
        for err in validate_node(node_id, node):
            yield ContractViolation(
                source_strategy=strategy,
                kind="node",
                subject_id=node_id,
                field=err.field,
                message=err.message,
            )

    node_ids = set(nodes.keys())
    for edge in edges:
        if not isinstance(edge, Mapping):
            yield ContractViolation(
                source_strategy="<unknown>",
                kind="edge",
                subject_id="<edge>",
                field="<edge>",
                message=f"edge value must be a mapping (got {type(edge).__name__})",
            )
            continue
        strategy = _source_strategy(edge.get("props"))
        from_id = edge.get("from", "?")
        to_id = edge.get("to", "?")
        edge_type = edge.get("type", "?")
        for err in validate_edge(edge, node_ids):
            yield ContractViolation(
                source_strategy=strategy,
                kind="edge",
                subject_id=f"{from_id} -> {to_id} ({edge_type})",
                field=err.field,
                message=err.message,
            )


__all__ = ["ContractViolation", "check_node_edge_contract"]

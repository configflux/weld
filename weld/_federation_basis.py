"""The freshness basis a federated root discover leaves behind (ADR 0141 D1).

A federation root reads exactly one thing: its own
:file:`.weld/workspaces.yaml`. :func:`weld.federation_root.build_root_meta_graph`
says so in the graph it writes -- ``meta.discovered_from`` is that single entry
-- and every freshness signal reads that manifest to decide *which* paths could
possibly be drift. What no federated run recorded until now is the file's
*content*, and that omission is field-eval finding M1.

The shape it produced: wire ``cross_repo_strategies: [package_graph]``, which
is the first thing this feature invites a user to do, and the edit leaves
``workspaces.yaml`` uncommitted-dirty. ``compute_stale_info`` hands the dirty
path to :func:`weld._staleness_worktree.dirty_sources_diverge`, which asks ADR
0008's inventory whether the graph read this content -- and at a federation
root there was no inventory to ask, so it returned its conservative ``True``
on every read while ``dirty_sources_diverge_detail`` returned ``[]`` for the
same undecidable input. ``wd stale`` answered ``stale: yes`` with an empty
``stale_sources`` forever, ``wd discover`` (the remedy the message names)
could not clear it because discovering wrote no inventory either, and
``wd impact`` refused without ``--allow-stale``. Committing the config and
rediscovering cleared it; nothing said so.

So: everything a discovery pass reads joins the freshness basis. This module
is the "and discovery feeds it" half of ADR 0141 D1, and it is deliberately
thin -- three delegations and no path logic of its own:

* **What to record** comes from the graph's own ``meta.discovered_from``, via
  :func:`weld._discover_inputs.graph_input_hashes`. Deriving it means the
  recorded basis *is* the declared basis; a second literal here would be a
  second place for the two to disagree, and would silently stop covering a
  manifest that grows a second entry (which is exactly what a resolver reading
  child manifests would do).
* **How to record it** is :func:`weld._discover_state_check.save_state_for_graph`,
  the single-repo path's own writer, so graph-anchoring, the
  ``files_with_no_nodes`` split and the published-graph token behave here
  exactly as they do there rather than as a second implementation of the same
  contract.
* **Whether to record it** is :func:`record_root_basis`'s own test on the path
  that was written: only the canonical ``.weld/graph.json`` is a body a reader
  will load.

The two halves come as one call. :func:`publish_root_graph` writes the body
*and* records the basis, and it is what the three sites that land a federated
root graph now use, because M1 is precisely those two halves having come
apart: a graph was published for years with nothing recorded beside it, and
nothing in the code said they belonged together. A fourth publisher gets both
by construction or neither.

What is deliberately *not* recorded is the child manifests a wired resolver
scanned, ADR 0141 D1's parenthetical. The root's ``discovered_from`` is the
registry alone, so :func:`weld._git.working_tree_dirty_sources` at the root can
never surface a child manifest and the working-tree check would never consult
one; the only signal that would read them is
:mod:`weld._staleness_reverted`. And that fact already reaches the root by a
better route: a child whose manifest was edited is stale *as a child*, and ADR
0066's aggregation puts it in the root's own verdict by name
(``libs-order-schema: stale, source_changed``). A second channel for one fact
buys no coverage, costs a hash per manifest per read, and gives root and child
a way to disagree. Because the basis is derived rather than spelled, widening
``meta.discovered_from`` is all it would take to change that decision.
"""

from __future__ import annotations

from pathlib import Path


def canonical_graph_path(root: Path) -> Path:
    """*root*'s own ``.weld/graph.json`` -- the body its readers load."""
    return root / ".weld" / "graph.json"


def publish_root_graph(
    root: Path, graph: dict, target: Path | None = None,
) -> None:
    """Land the federated root *graph*, and the basis it was built from.

    The ADR 0065 paired write (``graph.json`` with volatile meta stripped,
    plus its ``graph-meta.json`` sidecar) followed by
    :func:`record_root_basis`. One call, because M1 is the two halves having
    come apart: the body was published and nothing was recorded beside it,
    and no signature said the pair belonged together.

    *target* defaults to :func:`canonical_graph_path`; pass one only for the
    ``--output`` shape, which lands the body elsewhere and therefore records
    no basis -- see :func:`record_root_basis`.
    """
    from weld._graph_meta_sidecar import write_graph_with_meta

    dest = canonical_graph_path(root) if target is None else target
    write_graph_with_meta(dest, graph)
    record_root_basis(root, graph, dest)


def record_root_basis(root: Path, graph: dict, written: Path) -> None:
    """Record the freshness basis of a federated root graph just published.

    *graph* is the meta-graph :func:`weld.federation_root.build_root_meta_graph`
    built (after any cross-repo merge), and its ``meta.discovered_from`` is
    what this reads to decide which content to vouch for. *written* is where
    the body actually landed.

    Declines unless *written* is *root*'s canonical graph. A federated run
    whose graph goes to ``--output /tmp/x.json``, or only to stdout, leaves
    the body a reader loads untouched, so it has nothing to vouch for;
    stamping an inventory anyway would mark the root stale (or fresh)
    because a run no reader can see happened. Same line
    :func:`weld._discover_state_check.mark_state_published` draws on the
    single-repo path, and drawn here rather than at the call sites so a
    fourth publisher cannot get it subtly different.

    Silent no-op when the manifest names nothing readable under *root*:
    ``discovered_from`` is graph-authored data and ``graph_input_hashes``
    treats it as untrusted (a ``..`` escape, a symlink out of the tree, or a
    path that is not a regular file is dropped). An empty basis vouches for
    nothing, and recording an empty inventory would be worse than recording
    none -- ``dirty_sources_diverge`` reads ``not state.files`` as exactly the
    undecidable state this module exists to remove.
    """
    from weld._discover_inputs import graph_input_hashes
    from weld._discover_state_check import save_state_for_graph

    try:
        if written.resolve() != canonical_graph_path(root).resolve():
            return
    except OSError:
        return
    hashes = graph_input_hashes(root, graph, {})
    if not hashes:
        return
    save_state_for_graph(root, hashes, graph, graph_published=True)

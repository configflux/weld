"""On-disk federated workspaces for the ADR 0137 endpoint-contract tests.

Three suites -- the id index, ``wd graph validate`` at a workspace root, and
``wd doctor`` -- all need the same thing: a root that registers children in
each of the four lifecycle states, a root meta-graph that mints ``repo:``
nodes for the present ones only, and cross-repo edges pointed wherever the
test wants them. Building that three times over invites three subtly
different workspaces and a suite that agrees with itself rather than with the
product, so it is built once here.

Children are given a bare ``.git`` directory rather than a real repository:
:func:`weld.federation_child_loader.maybe_sentinel` classifies a child as
``missing`` on ``.git`` not existing, and nothing in the id path reads git
history. That keeps every suite here subprocess-free.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld import _sqlite_writer
from weld.contract import SCHEMA_VERSION
from weld.serializer import dumps_graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

#: The four ``children_status()`` classifications, spelled as this module's
#: ``state`` argument.
PRESENT = "present"
MISSING = "missing"
UNINITIALIZED = "uninitialized"
CORRUPT = "corrupt"


def node(node_id: str) -> dict:
    """A minimal contract-valid node body."""
    return {"type": "file", "label": node_id, "props": {}}


def repo_node(name: str) -> dict:
    """A root-minted ``repo:<name>`` node body (``federation_root`` shape)."""
    return {"type": "repo", "label": name, "props": {"path": name}}


def cross_repo_edge(from_id: str, to_id: str, **props: object) -> dict:
    """One cross-repo edge in the on-wire shape the serializer writes."""
    return {
        "from": from_id,
        "to": to_id,
        "type": "cross_repo:depends_on",
        "props": dict(props),
    }


def write_graph(repo_root: Path, payload: dict) -> Path:
    """Write *payload* to ``<repo_root>/.weld/graph.json`` and return the path."""
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    path = weld_dir / "graph.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def write_child(
    root: Path,
    name: str,
    *,
    state: str = PRESENT,
    node_ids: tuple[str, ...] = ("n1",),
    sidecar: bool = False,
) -> Path:
    """Materialise child *name* under *root* in one lifecycle *state*.

    With *sidecar*, the child also gets a fresh ``graph.db`` (ADR 0058), which
    is what makes the federation loader hand back a
    :class:`~weld._sqlite_reader.SqliteBackedGraph` instead of a
    :class:`~weld.graph.Graph` -- a different id-reading path, and one whose
    failure mode would be to call every endpoint in a healthy child dangling.
    """
    child = root / name
    if state == MISSING:
        return child  # never created: no directory, no .git
    child.mkdir(parents=True, exist_ok=True)
    (child / ".git").mkdir(exist_ok=True)
    if state == UNINITIALIZED:
        return child  # a checkout with no graph yet
    if state == CORRUPT:
        weld_dir = child / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        (weld_dir / "graph.json").write_text("{not json", encoding="utf-8")
        return child
    payload = {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": {nid: node(nid) for nid in node_ids},
        "edges": [],
    }
    if not sidecar:
        write_graph(child, payload)
        return child
    # The sidecar's freshness is a hash of the graph.json bytes, so both have
    # to come from the one canonical serialization.
    weld_dir = child / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    body = dumps_graph(payload).encode("utf-8")
    (weld_dir / "graph.json").write_bytes(body)
    _sqlite_writer.build_sidecar_for_bytes(
        payload, body, weld_dir / "graph.db", generated_at="t",
    )
    return child


def write_workspace_root(
    root: Path,
    *,
    registered: tuple[str, ...],
    repo_nodes: tuple[str, ...] = (),
    edges: tuple[dict, ...] = (),
    strategies: tuple[str, ...] = (),
) -> None:
    """Write ``workspaces.yaml`` plus the root meta-graph.

    *registered* is what ``workspaces.yaml`` names; *repo_nodes* is what the
    root graph actually mints. They are separate arguments on purpose: the
    root mints a ``repo:`` node for present children only, so a registered
    child that is absent has no node -- the case ADR 0137 ss3 rules
    ``dangling`` rather than ``unverifiable``.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(
        WorkspaceConfig(
            children=[ChildEntry(name=name, path=name) for name in registered],
            cross_repo_strategies=list(strategies),
        ),
        weld_dir / "workspaces.yaml",
    )
    write_graph(
        root,
        {
            "meta": {"version": SCHEMA_VERSION, "schema_version": 2},
            "nodes": {f"repo:{name}": repo_node(name) for name in repo_nodes},
            "edges": list(edges),
        },
    )

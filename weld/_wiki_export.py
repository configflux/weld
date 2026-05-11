"""Wiki / agent-readable markdown export (ADR 0053).

Writes a directory tree of markdown wikilinks derived from the graph:

    <output-dir>/
      index.md
      by-type/<type>.md
      by-community/<community-id>.md
      nodes/<safe-id>.md
      .id-map.json

The module exposes a :class:`WikiExporter` that implements the
``MultiFileExporter`` protocol declared in :mod:`weld.export`. The
implementation is deterministic: file order is alphabetical, edge order
is sorted by ``(edge_type, target_id)``, and a second export over an
unchanged graph produces byte-identical output. Re-export is incremental:
only files whose source node or edges changed are rewritten.

Renderer helpers live in :mod:`weld._wiki_renderers` to keep both modules
under the 400-line cap.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from weld._wiki_renderers import (
    render_by_community_page,
    render_by_type_page,
    render_index_page,
    render_node_page,
)
from weld.graph import Graph
from weld.graph_communities import build_graph_communities


# ---------------------------------------------------------------------------
# Safe-id mapping
# ---------------------------------------------------------------------------


_ILLEGAL_FS_CHARS = re.compile(r"[^A-Za-z0-9_.]")


def wiki_safe_id(node_id: str) -> str:
    """Convert *node_id* to a filesystem-safe, deterministic identifier.

    The output is ``<sha1-8>-<slug>``: an 8-char SHA1 prefix carries the
    collision-resistance signal, and the slug carries human readability.
    Filesystem-illegal characters are mapped to ``_``; the result is
    ASCII-printable and stable across runs.
    """
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:8]
    slug = _ILLEGAL_FS_CHARS.sub("_", node_id)
    # Collapse runs of underscores. ``a__b`` -> ``a_b`` keeps the slug
    # readable while preserving the SHA1 prefix as the source of truth
    # for distinguishing inputs.
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"{digest}-{slug}" if slug else digest


# ---------------------------------------------------------------------------
# Graph -> normalized export model
# ---------------------------------------------------------------------------


def _build_id_map(node_ids: list[str]) -> dict[str, str]:
    """Map raw node ids to safe-ids deterministically.

    Iteration order is alphabetical so two runs over the same node set
    produce the same map.
    """
    return {nid: wiki_safe_id(nid) for nid in sorted(node_ids)}


def _node_signature(
    node_id: str,
    node: Mapping[str, Any],
    outgoing: list[Mapping[str, Any]],
    incoming: list[Mapping[str, Any]],
) -> str:
    """Compute a stable signature for the rendered page of *node_id*.

    The signature changes if and only if the page content would change:
    the node's own data, its outgoing edges, or its incoming edges. We
    use this in the incremental rebuild path to skip files whose source
    has not moved.
    """
    payload = {
        "node": _canonical(node),
        "outgoing": sorted(
            (_canonical(e) for e in outgoing),
            key=lambda e: (e.get("type", ""), e.get("to", "")),
        ),
        "incoming": sorted(
            (_canonical(e) for e in incoming),
            key=lambda e: (e.get("type", ""), e.get("from", "")),
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    """Return a JSON-safe canonical view of *value*.

    Used by :func:`_node_signature` so the signature is stable across
    Python dict-iteration orderings.
    """
    if isinstance(value, Mapping):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# WikiExporter
# ---------------------------------------------------------------------------


class WikiExporter:
    """Multi-file wiki exporter (ADR 0053).

    Instantiate with the graph object, then call :meth:`write` with the
    target directory.
    """

    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    # -- public entry points ------------------------------------------------

    def write(self, output: Path) -> None:
        """Render the full wiki tree to *output*.

        Creates the directory if it does not exist. Idempotent across
        runs against the same graph (byte-identical output).
        """
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        data = self._graph.dump()
        nodes: Mapping[str, dict] = data.get("nodes") or {}
        edges: list[dict] = list(data.get("edges") or [])

        id_map = _build_id_map(list(nodes.keys()))
        outgoing, incoming = _index_edges_by_node(edges)
        communities = self._compute_communities(data)

        existing_signatures = self._read_signatures(output)
        new_signatures: dict[str, str] = {}

        self._write_node_pages(
            output=output,
            nodes=nodes,
            outgoing=outgoing,
            incoming=incoming,
            id_map=id_map,
            communities=communities,
            old_signatures=existing_signatures,
            new_signatures=new_signatures,
        )
        self._write_by_type_pages(
            output=output, nodes=nodes, id_map=id_map,
        )
        self._write_by_community_pages(
            output=output,
            nodes=nodes,
            communities=communities,
            id_map=id_map,
        )
        self._write_index(
            output=output, nodes=nodes, communities=communities,
        )
        self._write_id_map(output=output, id_map=id_map, signatures=new_signatures)

    # -- internals ----------------------------------------------------------

    def _compute_communities(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Run community detection (ADR 0039) and return its payload.

        On an empty graph or detection failure we fall back to an empty
        assignments map; this keeps the export usable on edge-case inputs
        without raising.
        """
        try:
            return build_graph_communities(data, top=12)
        except Exception:
            return {"assignments": {}, "communities": [], "hubs": []}

    def _write_node_pages(
        self,
        *,
        output: Path,
        nodes: Mapping[str, dict],
        outgoing: Mapping[str, list[dict]],
        incoming: Mapping[str, list[dict]],
        id_map: Mapping[str, str],
        communities: Mapping[str, Any],
        old_signatures: Mapping[str, str],
        new_signatures: dict[str, str],
    ) -> None:
        nodes_dir = output / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        assignments: Mapping[str, str] = communities.get("assignments") or {}
        for node_id in sorted(nodes):
            node = nodes[node_id]
            out_edges = outgoing.get(node_id, [])
            in_edges = incoming.get(node_id, [])
            signature = _node_signature(node_id, node, out_edges, in_edges)
            new_signatures[node_id] = signature
            safe = id_map[node_id]
            page_path = nodes_dir / f"{safe}.md"
            if (
                old_signatures.get(node_id) == signature
                and page_path.exists()
            ):
                continue  # unchanged -- skip the write to preserve mtime.
            body = render_node_page(
                node_id=node_id,
                node=node,
                outgoing=out_edges,
                incoming=in_edges,
                id_map=id_map,
                community=assignments.get(node_id),
            )
            _atomic_write_text(page_path, body)

    def _write_by_type_pages(
        self,
        *,
        output: Path,
        nodes: Mapping[str, dict],
        id_map: Mapping[str, str],
    ) -> None:
        by_type_dir = output / "by-type"
        by_type_dir.mkdir(parents=True, exist_ok=True)
        type_groups: dict[str, list[str]] = {}
        for node_id, node in nodes.items():
            t = str(node.get("type", "unknown")) or "unknown"
            type_groups.setdefault(t, []).append(node_id)
        for t in sorted(type_groups):
            body = render_by_type_page(
                node_type=t,
                node_ids=sorted(type_groups[t]),
                id_map=id_map,
            )
            _atomic_write_text(by_type_dir / f"{_safe_filename(t)}.md", body)

    def _write_by_community_pages(
        self,
        *,
        output: Path,
        nodes: Mapping[str, dict],
        communities: Mapping[str, Any],
        id_map: Mapping[str, str],
    ) -> None:
        by_comm_dir = output / "by-community"
        by_comm_dir.mkdir(parents=True, exist_ok=True)
        assignments: Mapping[str, str] = communities.get("assignments") or {}
        members: dict[str, list[str]] = {}
        for node_id, cid in assignments.items():
            if node_id in nodes:
                members.setdefault(str(cid), []).append(node_id)
        # Fall back: every node lands in community "c000" if the
        # community payload was empty.
        if not members and nodes:
            members["c000"] = list(nodes.keys())
        comm_summaries = {
            c.get("id"): c for c in (communities.get("communities") or [])
            if c.get("id")
        }
        for cid in sorted(members):
            summary = comm_summaries.get(cid) or {"id": cid}
            body = render_by_community_page(
                community_id=cid,
                summary=summary,
                node_ids=sorted(members[cid]),
                id_map=id_map,
            )
            _atomic_write_text(by_comm_dir / f"{_safe_filename(cid)}.md", body)

    def _write_index(
        self,
        *,
        output: Path,
        nodes: Mapping[str, dict],
        communities: Mapping[str, Any],
    ) -> None:
        body = render_index_page(nodes=nodes, communities=communities)
        _atomic_write_text(output / "index.md", body)

    def _write_id_map(
        self,
        *,
        output: Path,
        id_map: Mapping[str, str],
        signatures: Mapping[str, str],
    ) -> None:
        payload = {
            "version": 1,
            "ids": {k: id_map[k] for k in sorted(id_map)},
            "signatures": {k: signatures[k] for k in sorted(signatures)},
        }
        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        _atomic_write_text(output / ".id-map.json", text)

    def _read_signatures(self, output: Path) -> dict[str, str]:
        id_map_path = output / ".id-map.json"
        if not id_map_path.is_file():
            return {}
        try:
            payload = json.loads(id_map_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sigs = payload.get("signatures")
        if not isinstance(sigs, dict):
            return {}
        return {str(k): str(v) for k, v in sigs.items()}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _index_edges_by_node(
    edges: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if isinstance(src, str):
            outgoing.setdefault(src, []).append(edge)
        if isinstance(dst, str):
            incoming.setdefault(dst, []).append(edge)
    return outgoing, incoming


def _safe_filename(name: str) -> str:
    """Render a filename-safe slug from a free-form string.

    Used for type-page and community-page filenames where the input is
    already short and ascii-clean in practice; we still scrub to be
    defensive against weird type names emitted by future strategies.
    """
    cleaned = _ILLEGAL_FS_CHARS.sub("_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* via a same-directory tempfile + rename.

    The atomic-rename pattern matches what :func:`weld.workspace_state.atomic_write_text`
    does for graph IO; we keep a local copy here so the wiki exporter
    has no surprise dependency on graph-write internals.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


__all__ = ["WikiExporter", "wiki_safe_id"]

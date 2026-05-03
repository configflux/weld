#!/usr/bin/env python3
"""Connected structure engine: storage, CRUD, query, context, path, staleness,
import/export, validate. Run ``wd --help`` for details."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from weld._alias_index import build_alias_index as _build_alias_index
from weld._git import get_git_sha, is_git_repo
from weld._staleness import compute_stale_info as _compute_stale_info
from weld._graph_schema import (
    CHILD_SCHEMA_VERSION,
    ROOT_FEDERATED_SCHEMA_VERSION,
    SchemaVersionError,
    load_graph_file,
    schema_version_for as _graph_schema_version_for,
)
from weld.contract import SCHEMA_VERSION
from weld.graph_context import compute_neighborhood as _compute_neighborhood
from weld.graph_context import context_with_fallback as _context_with_fallback
from weld.graph_context import simple_exact_context as _simple_exact_context
from weld.graph_query import query_graph as _query_graph
from weld.query_state import build_query_state as _build_query_state
from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace_state import atomic_write_text

# Re-export schema symbols for backward compatibility -- many test files
# import these directly from weld.graph.
__all__ = [
    "CHILD_SCHEMA_VERSION",
    "ROOT_FEDERATED_SCHEMA_VERSION",
    "SchemaVersionError",
    "Graph",
    "load_graph_file",
    "main",
]

# Backward-compat alias kept private; used only by internal callers.
_has_repo_nodes = __import__("weld._graph_schema", fromlist=["has_repo_nodes"]).has_repo_nodes


def _schema_version_for(nodes: dict[str, dict]) -> int:
    """Return the schema version for internal graph writers.

    ``Graph.save`` and the discovery post-processing writer intentionally
    share this private compatibility wrapper so both stamp
    ``meta.schema_version`` through the same schema helper without exposing
    it as part of ``weld.graph``'s public API.
    """
    return _graph_schema_version_for(nodes)


def _now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Graph:
    """In-memory graph backed by a single JSON file."""

    def __init__(self, root: Path) -> None:
        self._path = root / ".weld" / "graph.json"
        self._data: dict = {"meta": {}, "nodes": {}, "edges": []}
        self._inverted_index: dict[str, set[str]] = {}
        self._bm25 = None
        self._structural_scores: dict[str, float] = {}
        self._embedding_cache = None
        self._query_state_counts = (0, 0)
        self._alias_index: dict[str, str] = {}  # ADR 0041 lookup

    def load(self) -> None:
        if self._path.exists():
            self._data = load_graph_file(self._path)
            self._load_query_state_with_sidecar()
        else:
            self._data = {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": _now(),
                    "schema_version": CHILD_SCHEMA_VERSION,
                },
                "nodes": {},
                "edges": [],
            }
            self._build_inverted_index()

    def _load_query_state_with_sidecar(self) -> None:
        """Read the sidecar or rebuild + rewrite (ADR 0031). Helper in :mod:`weld._query_sidecar`."""
        from weld._query_sidecar import load_query_state_for_graph

        load_query_state_for_graph(self)

    def save(self, *, touch_git_sha: bool = False) -> None:
        """Atomically persist the graph (ADR 0011 ss8, ADR 0012 ss3).

        Stamps ``meta.schema_version`` from the node set. When
        *touch_git_sha* is True and the root is a git working tree,
        also stamp ``meta.git_sha=HEAD`` before writing (ADR 0017).
        Silent no-op outside a git repo.
        """
        self._data["meta"]["updated_at"] = _now()
        self._data["meta"]["schema_version"] = _schema_version_for(
            self._data.get("nodes", {})
        )
        if touch_git_sha and is_git_repo(self._path.parent.parent):
            sha = get_git_sha(self._path.parent.parent)
            if sha is not None:
                self._data["meta"]["git_sha"] = sha
        atomic_write_text(self._path, _dumps_graph(self._data))

    def add_node(self, node_id: str, node_type: str, label: str, props: dict) -> dict:
        entry = {"type": node_type, "label": label, "props": props}
        self._data["nodes"][node_id] = entry
        self._build_inverted_index()
        return {"id": node_id, **entry}

    def add_edge(self, from_id: str, to_id: str, edge_type: str, props: dict) -> dict:
        edge = {"from": from_id, "to": to_id, "type": edge_type, "props": props}
        if edge not in self._data["edges"]:  # avoid exact duplicates
            self._data["edges"].append(edge)
            self._build_inverted_index()
        return edge

    def rm_node(self, node_id: str) -> bool:
        removed = node_id in self._data["nodes"]
        self._data["nodes"].pop(node_id, None)
        before_edges = len(self._data["edges"])
        self._data["edges"] = [
            e for e in self._data["edges"]
            if e["from"] != node_id and e["to"] != node_id
        ]
        if removed or before_edges != len(self._data["edges"]):
            self._build_inverted_index()
        return removed

    def rm_edge(self, from_id: str, to_id: str, edge_type: str | None) -> int:
        before = len(self._data["edges"])
        self._data["edges"] = [
            e for e in self._data["edges"]
            if not (
                e["from"] == from_id
                and e["to"] == to_id
                and (edge_type is None or e["type"] == edge_type)
            )
        ]
        if before != len(self._data["edges"]):
            self._build_inverted_index()
        return before - len(self._data["edges"])

    def merge_import(self, data: dict) -> dict:
        added_nodes = 0
        added_edges = 0
        incoming_nodes = data.get("nodes", {})
        for nid, node in incoming_nodes.items():
            if nid not in self._data["nodes"]:
                added_nodes += 1
            self._data["nodes"][nid] = node
        for edge in data.get("edges", []):
            if edge not in self._data["edges"]:
                self._data["edges"].append(edge)
                added_edges += 1
        if incoming_nodes or added_edges:
            self._build_inverted_index()
        return {"added_nodes": added_nodes, "added_edges": added_edges}

    def _build_inverted_index(self) -> None:
        state = _build_query_state(self._data["nodes"], self._data["edges"])
        self._inverted_index = state.inverted_index
        self._bm25 = state.bm25
        self._structural_scores = state.structural_scores
        self._embedding_cache = state.embedding_cache
        self._query_state_counts = (len(self._data["nodes"]), len(self._data["edges"]))
        self._alias_index = _build_alias_index(self._data["nodes"])  # ADR 0041

    def _ensure_query_state(self) -> None:
        counts = (len(self._data["nodes"]), len(self._data["edges"]))
        if counts != self._query_state_counts:
            self._build_inverted_index()

    # -- queries --

    def get_node(self, node_id: str) -> dict | None:
        n = self._data["nodes"].get(node_id)
        if n is None:
            return None
        return {"id": node_id, **n}

    def list_nodes(self, type_filter: str | None = None) -> list[dict]:
        result = []
        for nid, n in sorted(self._data["nodes"].items()):
            if type_filter and n["type"] != type_filter:
                continue
            result.append({"id": nid, **n})
        return result

    def query(self, term: str, limit: int = 20) -> dict:
        """Synonym-expanded tokenized search across id, label, file, exports, description."""
        return _query_graph(self, term, limit)

    @staticmethod
    def _match_tokens(tokens: list[str], nid: str, node: dict) -> int:
        """Count matched tokens; returns 0 if any token misses all fields."""
        return Graph._match_token_groups([[t] for t in tokens], nid, node)

    @staticmethod
    def _match_token_groups(token_groups: list[list[str]], nid: str, node: dict) -> int:
        """Match synonym-expanded token groups; 0 if any group misses."""
        nid_l, label_l = nid.lower(), node.get("label", "").lower()
        props = node.get("props") or {}
        file_l = (props.get("file") or "").lower()
        exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
        # ``constants`` carries module-level Python constants
        # (``UPPER_CASE`` / ``_UPPER_CASE``) emitted by the
        # ``python_module`` strategy. Lowercased here so substring match
        # against query tokens works the same way as for ``exports``.
        constants_l = [c.lower() for c in props.get("constants", []) if isinstance(c, str)]
        desc_l = (props.get("description") or "").lower()
        hits = 0
        for group in token_groups:
            if any(
                t in nid_l or t in label_l or t in file_l or t in desc_l
                or any(t in e for e in exports_l)
                or any(t in c for c in constants_l)
                for t in group
            ):
                hits += 1
            else:
                return 0
        return hits

    def _resolve_symbol_name(self, symbol_name: str) -> list[dict]:
        """Resolve a bare symbol *name* to matching graph nodes.

        Matches symbol nodes on ``props.qualname`` (with a trailing
        ``.<name>`` suffix check) and additionally includes the
        ``symbol:unresolved:<name>`` sentinel. Shared by :meth:`callers`
        and :meth:`references` so both use one bare-name rule.
        """
        matches: list[dict] = []
        for nid, n in self._data["nodes"].items():
            if n.get("type") != "symbol":
                continue
            qual = (n.get("props") or {}).get("qualname") or n.get("label", "")
            if qual == symbol_name or qual.endswith("." + symbol_name):
                matches.append({"id": nid, **n})
            elif nid == f"symbol:unresolved:{symbol_name}":
                matches.append({"id": nid, **n})
        return matches

    def callers(self, symbol_id: str, depth: int = 1) -> dict:
        """Return the set of symbols that call *symbol_id*, up to *depth*.

        Walks ``calls`` edges in reverse. ``symbol_id`` accepts either a
        fully-qualified node id (e.g.
        ``symbol:py:weld.discover:_load_strategy``) or a bare name
        (e.g. ``_load_strategy``); bare names use the same resolution
        rule as :meth:`references`, with callers aggregated and
        deduplicated across matches. An unknown name still surfaces an
        ``error`` so callers can distinguish "no match" from "no callers".
        """
        if depth < 1:
            depth = 1
        if symbol_id in self._data["nodes"]:
            seeds = [symbol_id]
        else:
            matches = self._resolve_symbol_name(symbol_id)
            if not matches:
                return {
                    "symbol": symbol_id, "depth": depth,
                    "callers": [], "edges": [],
                    "error": f"node not found: {symbol_id}",
                }
            seeds = [m["id"] for m in matches]
        # Build reverse adjacency for calls edges only.
        rev: dict[str, list[dict]] = {}
        for e in self._data["edges"]:
            if e.get("type") == "calls":
                rev.setdefault(e["to"], []).append(e)
        seen: set[str] = set(seeds)
        frontier: list[str] = list(seeds)
        out_callers: list[dict] = []
        out_edges: list[dict] = []
        for _ in range(depth):
            next_frontier: list[str] = []
            for node_id in frontier:
                for edge in rev.get(node_id, []):
                    src = edge["from"]
                    out_edges.append(edge)
                    if src in seen:
                        continue
                    seen.add(src)
                    n = self.get_node(src)
                    if n is not None:
                        out_callers.append(n)
                    next_frontier.append(src)
            frontier = next_frontier
            if not frontier:
                break
        return {
            "symbol": symbol_id,
            "depth": depth,
            "callers": out_callers,
            "edges": out_edges,
        }

    def references(self, symbol_name: str) -> dict:
        """Return callers + textual references for a symbol *name*.

        ``symbol_name`` is the bare identifier (e.g. ``_load_strategy``)
        rather than a full id. The result combines resolved callers and
        file-index textual occurrences.
        """
        matches = self._resolve_symbol_name(symbol_name)
        all_callers: dict[str, dict] = {}
        all_edges: list[dict] = []
        for m in matches:
            res = self.callers(m["id"], depth=1)
            for c in res["callers"]:
                all_callers.setdefault(c["id"], c)
            all_edges.extend(res["edges"])
        return {
            "symbol": symbol_name,
            "matches": matches,
            "callers": list(all_callers.values()),
            "edges": all_edges,
        }

    def context(self, node_id: str, *, fallback: bool = True) -> dict:
        """Node + 1-hop neighborhood; alias-aware per ADR 0041."""
        eid = node_id if node_id in self._data["nodes"] else self._alias_index.get(node_id, node_id)
        return _context_with_fallback(
            raw_node_id=eid, error_node_id=node_id, fallback=fallback,
            exact_fn=lambda: _simple_exact_context(
                self.get_node, self._neighborhood, eid),
            query_fn=self.query,
            recurse_fn=lambda nid: self.context(nid, fallback=False),
            match_tokens_fn=Graph._match_tokens,
        )

    def path(self, from_id: str, to_id: str) -> dict:
        # ADR 0041 alias-aware: rewrite legacy IDs to canonical first.
        nodes = self._data["nodes"]
        from_id = from_id if from_id in nodes else self._alias_index.get(from_id, from_id)
        to_id = to_id if to_id in nodes else self._alias_index.get(to_id, to_id)
        if from_id not in nodes or to_id not in nodes:
            return {"path": None, "reason": "node not found"}
        adj: dict[str, list[tuple[str, dict]]] = {}
        for e in self._data["edges"]:
            adj.setdefault(e["from"], []).append((e["to"], e))
            adj.setdefault(e["to"], []).append((e["from"], e))
        visited = {from_id}
        queue: deque[list[str]] = deque([[from_id]])
        while queue:
            current_path = queue.popleft()
            current = current_path[-1]
            if current == to_id:
                nodes = [self.get_node(nid) for nid in current_path]
                edges = []
                for i in range(len(current_path) - 1):
                    a, b = current_path[i], current_path[i + 1]
                    for e in self._data["edges"]:
                        if (e["from"] == a and e["to"] == b) or (e["from"] == b and e["to"] == a):
                            edges.append(e)
                            break
                return {"path": nodes, "edges": edges}
            for neighbor, _ in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(current_path + [neighbor])
        return {"path": None, "reason": "no path found"}

    def stats(self, *, top: int | None = None) -> dict:
        from weld._graph_stats import compute_stats as _compute_stats
        return _compute_stats(self._data, top=top)

    def stale(self) -> dict:
        """Report graph freshness (ADR 0017); primary = source drift."""
        return _compute_stale_info(self._path, self._data.get("meta", {}))

    def dump(self) -> dict:
        return self._data

    # -- internal --

    def _neighborhood(self, node_ids: set[str]) -> tuple[list[dict], list[dict]]:
        return _compute_neighborhood(self._data["nodes"], self._data["edges"], node_ids)


def main(argv: list[str] | None = None, *, prog: str = "wd") -> None:
    """CLI entry point -- delegates to :mod:`weld._graph_cli`."""
    from weld._graph_cli import main as _cli_main
    _cli_main(argv, prog=prog)


if __name__ == "__main__":
    main()

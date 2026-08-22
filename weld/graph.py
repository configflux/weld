#!/usr/bin/env python3
"""Connected structure engine: storage, CRUD, query, context, path, staleness,
import/export, validate. Run ``wd --help`` for details."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from weld._alias_index import build_alias_index as _build_alias_index
from weld._git import get_git_sha, is_git_repo
from weld._graph_match import match_token_groups, match_tokens, resolve_symbol_name
from weld._graph_meta_sidecar import merge_sidecar_meta, write_graph_with_meta
from weld._staleness import compute_stale_info as _compute_stale_info
from weld._graph_schema import (
    CHILD_SCHEMA_VERSION,
    GraphShapeError,
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
from weld import graph_referrers as _referrers

# Re-export schema symbols for backward compatibility -- some tests import directly from weld.graph.
__all__ = ["CHILD_SCHEMA_VERSION", "ROOT_FEDERATED_SCHEMA_VERSION", "GraphShapeError", "SchemaVersionError", "Graph", "load_graph_file", "main"]

# Backward-compat alias kept private; used only by internal callers.
_has_repo_nodes = __import__("weld._graph_schema", fromlist=["has_repo_nodes"]).has_repo_nodes


def _schema_version_for(nodes: dict[str, dict]) -> int:
    """Internal alias: ``Graph.save`` and discover share the schema helper without re-exporting it."""
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
            # ADR 0065: overlay the volatile-meta sidecar (legacy fallback).
            self._data["meta"] = merge_sidecar_meta(self._data.get("meta", {}), self._path)
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

    @classmethod
    def open(cls, root: Path):  # type: ignore[no-untyped-def]
        """Return a sqlite-backed view if fresh, else a JSON-backed Graph (ADR 0058)."""
        from weld._sqlite_reader import open_sidecar_if_fresh
        backed = open_sidecar_if_fresh(Path(root) / ".weld" / "graph.json")
        if backed is not None:
            return backed
        instance = cls(Path(root))
        instance.load()
        return instance

    def save(self, *, touch_git_sha: bool = False) -> None:
        """Atomically persist the graph (ADR 0011 ss8, ADR 0012 ss3).

        Stamps ``meta.schema_version`` from the node set. When
        *touch_git_sha* is True and the root is a git working tree,
        also stamp ``meta.git_sha=HEAD`` before writing (ADR 0017).
        Silent no-op outside a git repo. Volatile meta (``updated_at`` /
        ``git_sha``) is split to the ``graph-meta.json`` sidecar by
        ``write_graph_with_meta`` (ADR 0065); ``self._data`` keeps it all.
        """
        self._data["meta"]["updated_at"] = _now()
        self._data["meta"]["schema_version"] = _schema_version_for(
            self._data.get("nodes", {})
        )
        if touch_git_sha and is_git_repo(self._path.parent.parent):
            sha = get_git_sha(self._path.parent.parent)
            if sha is not None:
                self._data["meta"]["git_sha"] = sha
        write_graph_with_meta(self._path, self._data)

    def add_node(self, node_id: str, node_type: str, label: str, props: dict) -> dict:
        entry = {"type": node_type, "label": label, "props": props}
        self._data["nodes"][node_id] = entry
        self._build_inverted_index()
        return {"id": node_id, **entry}

    def add_edge(self, from_id: str, to_id: str, edge_type: str, props: dict) -> dict:
        edge = {"from": from_id, "to": to_id, "type": edge_type, "props": props}
        if edge not in self._data["edges"]:  # avoid exact duplicates
            # ADR 0050: warn (during the one-minor migration window) on
            # missing/invalid confidence at the first append site only.
            from weld._confidence_warn import warn_edge_confidence
            warn_edge_confidence(edge)
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

    # bd jkir: the three matching primitives live in weld._graph_match, which
    # is where they went when this module hit the 400-line cap. These stay as
    # delegates because graph_query, federation and four test modules address
    # them through the class.

    @staticmethod
    def _match_tokens(tokens: list[str], nid: str, node: dict) -> int:
        """Count matched tokens; returns 0 if any token misses all fields."""
        return match_tokens(tokens, nid, node)

    @staticmethod
    def _match_token_groups(token_groups: list[list[str]], nid: str, node: dict) -> int:
        """Match synonym-expanded token groups; 0 if any group misses."""
        return match_token_groups(token_groups, nid, node)

    def _resolve_symbol_name(self, symbol_name: str) -> list[dict]:
        """Resolve a bare symbol *name* to matching graph nodes."""
        return resolve_symbol_name(self._data["nodes"], symbol_name)

    def callers(self, symbol_id: str, depth: int = 1) -> dict:
        """Symbols that call *symbol_id*, up to *depth* (weld.graph_referrers)."""
        return _referrers.callers(self, symbol_id, depth)

    def references(self, symbol_name: str) -> dict:
        """What points at *symbol_name*; accepts a bare name or a node id.

        See :func:`weld.graph_referrers.references` for the node-id rule and
        the read-path disagreement it closes (bd nywd).
        """
        return _referrers.references(self, symbol_name)

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

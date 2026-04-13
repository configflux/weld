#!/usr/bin/env python3
"""Knowledge graph engine: storage, CRUD, query, context, path, staleness,
import/export, validate. Run ``cortex --help`` for details."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from cortex._git import commits_behind as _commits_behind
from cortex._git import get_git_sha, is_git_repo
from cortex.contract import SCHEMA_VERSION, VALID_EDGE_TYPES, VALID_NODE_TYPES
from cortex.query_index import build_index as _build_index
from cortex.query_index import deindex_node as _deindex_node
from cortex.query_index import index_node as _index_node
from cortex.ranking import query_rank_key
from cortex.synonyms import candidate_nodes_grouped as _candidate_nodes_grouped
from cortex.synonyms import expand_token_groups as _expand_token_groups
class Graph:
    """In-memory graph backed by a single JSON file."""

    def __init__(self, root: Path) -> None:
        self._path = root / ".cortex" / "graph.json"
        self._data: dict = {"meta": {}, "nodes": {}, "edges": []}
        self._inverted_index: dict[str, set[str]] = {}

    def load(self) -> None:
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        else:
            self._data = {"meta": {"version": SCHEMA_VERSION, "updated_at": _now()}, "nodes": {}, "edges": []}
        self._build_inverted_index()

    def save(self) -> None:
        self._data["meta"]["updated_at"] = _now()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="graph.json.tmp.", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, str(self._path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add_node(self, node_id: str, node_type: str, label: str, props: dict) -> dict:
        entry = {"type": node_type, "label": label, "props": props}
        if node_id in self._data["nodes"]:
            _deindex_node(self._inverted_index, node_id)
        self._data["nodes"][node_id] = entry
        _index_node(self._inverted_index, node_id, entry)
        return {"id": node_id, **entry}

    def add_edge(self, from_id: str, to_id: str, edge_type: str, props: dict) -> dict:
        edge = {"from": from_id, "to": to_id, "type": edge_type, "props": props}
        if edge not in self._data["edges"]:  # avoid exact duplicates
            self._data["edges"].append(edge)
        return edge

    def rm_node(self, node_id: str) -> bool:
        removed = node_id in self._data["nodes"]
        self._data["nodes"].pop(node_id, None)
        if removed:
            _deindex_node(self._inverted_index, node_id)
        self._data["edges"] = [
            e for e in self._data["edges"]
            if e["from"] != node_id and e["to"] != node_id
        ]
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
        return before - len(self._data["edges"])

    def merge_import(self, data: dict) -> dict:
        added_nodes = 0
        added_edges = 0
        for nid, node in data.get("nodes", {}).items():
            if nid not in self._data["nodes"]:
                added_nodes += 1
            else:
                _deindex_node(self._inverted_index, nid)
            self._data["nodes"][nid] = node
            _index_node(self._inverted_index, nid, node)
        for edge in data.get("edges", []):
            if edge not in self._data["edges"]:
                self._data["edges"].append(edge)
                added_edges += 1
        return {"added_nodes": added_nodes, "added_edges": added_edges}

    def _build_inverted_index(self) -> None:
        self._inverted_index = _build_index(self._data["nodes"])

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
        tokens = term.lower().split()
        if not tokens:
            return {"query": term, "matches": [], "neighbors": [], "edges": []}
        token_groups = _expand_token_groups(tokens)
        candidates = _candidate_nodes_grouped(self._inverted_index, token_groups)
        if candidates is not None and not candidates:
            return {"query": term, "matches": [], "neighbors": [], "edges": []}
        if candidates is None:
            candidate_iter = self._data["nodes"].items()
        else:
            candidate_iter = (
                (nid, self._data["nodes"][nid]) for nid in candidates if nid in self._data["nodes"]
            )
        scored: list[tuple[int, str, dict]] = []
        for nid, n in candidate_iter:
            hits = self._match_token_groups(token_groups, nid, n)
            if hits:
                scored.append((hits, nid, n))
        scored.sort(key=lambda t: query_rank_key(t[0], {"id": t[1], **(t[2])}))
        matches = [{"id": nid, **n} for _, nid, n in scored[:limit]]
        match_ids = {m["id"] for m in matches}
        neighbors, edges = self._neighborhood(match_ids)
        return {"query": term, "matches": matches, "neighbors": neighbors, "edges": edges}

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
        desc_l = (props.get("description") or "").lower()
        hits = 0
        for group in token_groups:
            if any(
                t in nid_l or t in label_l or t in file_l or t in desc_l or any(t in e for e in exports_l)
                for t in group
            ):
                hits += 1
            else:
                return 0
        return hits

    def callers(self, symbol_id: str, depth: int = 1) -> dict:
        """Return the set of symbols that call *symbol_id*, up to *depth*.

        Walks ``calls`` edges in reverse from *symbol_id*. ``depth=1``
        returns direct callers; higher values return transitive callers
        as a flattened, deduplicated set with the depth at which each
        was first reached. Self-loops and revisits are skipped.

        Returns ``{"symbol": <id>, "depth": <int>, "callers": [...],
        "edges": [...]}``. ``callers`` entries are full node dicts;
        ``edges`` are the raw call edges traversed.
        """
        if depth < 1:
            depth = 1
        if symbol_id not in self._data["nodes"]:
            return {
                "symbol": symbol_id,
                "depth": depth,
                "callers": [],
                "edges": [],
                "error": f"node not found: {symbol_id}",
            }
        # Build reverse adjacency for calls edges only.
        rev: dict[str, list[dict]] = {}
        for e in self._data["edges"]:
            if e.get("type") == "calls":
                rev.setdefault(e["to"], []).append(e)
        seen: set[str] = {symbol_id}
        frontier: list[str] = [symbol_id]
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
        rather than a full id. The result combines:

          * ``callers``: every symbol node whose outgoing ``calls`` edge
            ends at any symbol whose qualname matches *symbol_name*
            (resolved hits) or whose unresolved sentinel matches it.
          * ``files``: a passthrough of ``cortex.file_index.find_files`` so
            agents can locate textual occurrences in non-symbol surfaces
            (docs, configs, comments).
        """
        # Find all symbol nodes whose qualname matches.
        matches: list[dict] = []
        for nid, n in self._data["nodes"].items():
            if n.get("type") != "symbol":
                continue
            qual = (n.get("props") or {}).get("qualname") or n.get("label", "")
            if qual == symbol_name or qual.endswith("." + symbol_name):
                matches.append({"id": nid, **n})
            elif nid == f"symbol:unresolved:{symbol_name}":
                matches.append({"id": nid, **n})
        # Aggregate callers across every match.
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

    def context(self, node_id: str) -> dict:
        node = self.get_node(node_id)
        if node is None:
            return {"error": f"node not found: {node_id}"}
        neighbors, edges = self._neighborhood({node_id})
        return {"node": node, "neighbors": neighbors, "edges": edges}

    def path(self, from_id: str, to_id: str) -> dict:
        if from_id not in self._data["nodes"] or to_id not in self._data["nodes"]:
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

    def stats(self) -> dict:
        nc: dict[str, int] = {}
        dc: dict[str, int] = {}  # described count per type
        for n in self._data["nodes"].values():
            t = n["type"]
            nc[t] = nc.get(t, 0) + 1
            desc = (n.get("props") or {}).get("description")
            if desc and isinstance(desc, str) and desc.strip():
                dc[t] = dc.get(t, 0) + 1
        ec: dict[str, int] = {}
        for e in self._data["edges"]:
            ec[e["type"]] = ec.get(e["type"], 0) + 1
        total = len(self._data["nodes"])
        desc_total = sum(dc.values())
        cov_by_type = {
            t: {"total": nc[t], "with_description": dc.get(t, 0),
                "coverage_pct": round(dc.get(t, 0) / nc[t] * 100, 2)}
            for t in nc
        }
        return {
            "total_nodes": total, "total_edges": len(self._data["edges"]),
            "nodes_by_type": nc, "edges_by_type": ec,
            "nodes_with_description": desc_total,
            "description_coverage_pct": round(desc_total / total * 100, 2) if total else 0.0,
            "description_coverage_by_type": cov_by_type,
        }

    def stale(self) -> dict:
        root = self._path.parent.parent  # .cortex/ -> project root
        if not is_git_repo(root):
            return {"stale": False, "reason": "not a git repo"}
        cur = get_git_sha(root)
        gsha = self._data.get("meta", {}).get("git_sha")
        if gsha is None:
            stale, behind = True, -1
        elif gsha == cur:
            stale, behind = False, 0
        else:
            stale, behind = True, _commits_behind(root, gsha, cur)
        return {"stale": stale, "graph_sha": gsha, "current_sha": cur, "commits_behind": behind}

    def dump(self) -> dict:
        return self._data

    # -- internal --

    def _neighborhood(self, node_ids: set[str]) -> tuple[list[dict], list[dict]]:
        edges = []
        neighbor_ids: set[str] = set()
        for e in self._data["edges"]:
            if e["from"] in node_ids or e["to"] in node_ids:
                edges.append(e)
                neighbor_ids.add(e["from"])
                neighbor_ids.add(e["to"])
        neighbor_ids -= node_ids
        neighbors = []
        for nid in sorted(neighbor_ids):
            n = self._data["nodes"].get(nid)
            if n:
                neighbors.append({"id": nid, **n})
        return neighbors, edges

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def _now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _out(data: object) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cortex", description="Knowledge graph CLI")
    parser.add_argument("--root", type=Path, default=Path("."), help="Project root directory")
    sub = parser.add_subparsers(dest="command")
    p_query = sub.add_parser("query", help="Tokenized search (fields: id, label, props.file, props.exports, props.description)")
    p_query.add_argument("term", help="Search term (multi-word is tokenized)")
    p_query.add_argument("--limit", type=int, default=20)
    p_ctx = sub.add_parser("context", help="Node + neighborhood")
    p_ctx.add_argument("node_id", help="Node ID")
    p_path = sub.add_parser("path", help="Shortest path between nodes")
    p_path.add_argument("from_id", help="Start node ID")
    p_path.add_argument("to_id", help="End node ID")
    p_an = sub.add_parser("add-node", help="Add or update a node")
    p_an.add_argument("id", help="Node ID (e.g. entity:Store)")
    p_an.add_argument("--type", required=True, dest="node_type",
                       choices=sorted(VALID_NODE_TYPES), help="Node type")
    p_an.add_argument("--label", default="", help="Human-readable label")
    p_an.add_argument("--props", default="{}", help="JSON properties")
    p_an.add_argument("--merge", action="store_true", help="Deep-merge props into existing node")
    p_ae = sub.add_parser("add-edge", help="Add an edge")
    p_ae.add_argument("from_id", help="Source node ID")
    p_ae.add_argument("to_id", help="Target node ID")
    p_ae.add_argument("--type", required=True, dest="edge_type",
                       choices=sorted(VALID_EDGE_TYPES), help="Edge type")
    p_ae.add_argument("--props", default="{}", help="JSON properties")
    p_rn = sub.add_parser("rm-node", help="Remove a node and its edges")
    p_rn.add_argument("id", help="Node ID")
    p_re = sub.add_parser("rm-edge", help="Remove edge(s)")
    p_re.add_argument("from_id", help="Source node ID")
    p_re.add_argument("to_id", help="Target node ID")
    p_re.add_argument("--type", dest="edge_type", choices=sorted(VALID_EDGE_TYPES),
                       default=None, help="Edge type filter")
    p_list = sub.add_parser("list", help="List nodes")
    p_list.add_argument("--type", dest="type_filter", choices=sorted(VALID_NODE_TYPES),
                         default=None, help="Filter by type")
    p_find = sub.add_parser("find", help="Search file index by keyword")
    p_find.add_argument("term", help="Search term (substring match)")
    p_callers = sub.add_parser(
        "callers", help="Direct (and optionally transitive) callers of a symbol"
    )
    p_callers.add_argument("symbol", help="Full symbol id, e.g. symbol:py:cortex.discover:_load_strategy")
    p_callers.add_argument("--depth", type=int, default=1, help="Caller traversal depth (default 1)")
    p_refs = sub.add_parser(
        "references",
        help="Callers + textual file-index references for a symbol name",
    )
    p_refs.add_argument("name", help="Bare symbol name, e.g. _load_strategy")
    sub.add_parser("stale", help="Check if graph is stale vs current HEAD")
    sub.add_parser("dump", help="Full graph JSON")
    sub.add_parser("stats", help="Summary counts")
    p_imp = sub.add_parser("import", help="Import/merge from file")
    p_imp.add_argument("file", type=Path, help="JSON file to import")
    sub.add_parser("validate", help="Validate graph against the metadata contract")
    p_vf = sub.add_parser("validate-fragment", help="Validate a JSON fragment")
    p_vf.add_argument("file", type=Path, help="JSON fragment file")
    p_vf.add_argument("--source-label", default="fragment", help="Diagnostic label")
    p_vf.add_argument("--allow-dangling", action="store_true", help="Skip ref checks")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    g = Graph(args.root)
    g.load()
    cmd = args.command
    mutates = False
    if cmd == "find":
        from cortex.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        _out(find_files(index, args.term))
    elif cmd == "query":
        _out(g.query(args.term, args.limit))
    elif cmd == "context":
        _out(g.context(args.node_id))
    elif cmd == "callers":
        _out(g.callers(args.symbol, depth=args.depth))
    elif cmd == "references":
        from cortex.file_index import find_files, load_file_index
        index = load_file_index(args.root)
        refs = g.references(args.name)
        refs["files"] = find_files(index, args.name).get("files", [])
        _out(refs)
    elif cmd == "path":
        _out(g.path(args.from_id, args.to_id))
    elif cmd == "add-node":
        props = json.loads(args.props)
        if args.merge:
            existing = g.get_node(args.id)
            if existing:
                merged = _deep_merge(existing.get("props", {}), props)
                label = args.label or existing.get("label", args.id)
                _out(g.add_node(args.id, args.node_type, label, merged))
            else:
                _out(g.add_node(args.id, args.node_type, args.label or args.id, props))
        else:
            _out(g.add_node(args.id, args.node_type, args.label or args.id, props))
        mutates = True
    elif cmd == "add-edge":
        props = json.loads(args.props)
        _out(g.add_edge(args.from_id, args.to_id, args.edge_type, props))
        mutates = True
    elif cmd == "rm-node":
        removed = g.rm_node(args.id)
        _out({"removed": removed, "id": args.id})
        mutates = True
    elif cmd == "rm-edge":
        count = g.rm_edge(args.from_id, args.to_id, args.edge_type)
        _out({"removed_count": count})
        mutates = True
    elif cmd == "list":
        _out(g.list_nodes(args.type_filter))
    elif cmd == "stale":
        _out(g.stale())
    elif cmd == "dump":
        _out(g.dump())
    elif cmd == "stats":
        _out(g.stats())
    elif cmd == "import":
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        result = g.merge_import(data)
        _out(result)
        mutates = True
    elif cmd == "validate":
        from cortex.contract import validate_graph
        errs = validate_graph(g.dump())
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            sys.exit(1)
    elif cmd == "validate-fragment":
        from cortex.contract import validate_fragment
        raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
        data = json.loads(raw)
        errs = validate_fragment(
            data,
            source_label=args.source_label,
            allow_dangling_edges=args.allow_dangling,
        )
        _out({"valid": not errs, "errors": [str(e) for e in errs]})
        if errs:
            sys.exit(1)
    if mutates:
        g.save()

if __name__ == "__main__":
    main()

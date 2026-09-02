"""Hand-built TypeScript call graphs for the closure's import-binding pass.

Shaped like the graph a ``wd discover`` hands :func:`weld.graph_closure.close_graph`
and no larger: file nodes, definite symbols, and ``calls`` edges carrying the
import hint the tree-sitter pass writes (bd lrnx1.3, ADR 0142 D2). Written by
hand rather than parsed, because the pass under test never sees source -- it
reads a merged graph, and building one directly is the only way to state a
case the fixture corpus does not happen to contain (two packages exporting one
name, an entry file that defines something else, an endpoint a previous round
already moved).

The caller is the synthetic ``<file>`` symbol in every case. Which node an edge
*starts* at is the call-graph pass's business and is pinned by its own suite;
what matters here is what the edge points at, and using one caller shape keeps
each case about that.
"""

from __future__ import annotations

from weld.strategies._ts_call_sites import TS_IMPORT_PROP

def module_of(rel_path: str) -> str:
    """The module segment a symbol id for *rel_path* is minted under.

    Mirrors ``_ts_call_graph.ts_module_from_path`` for the flat paths used
    here rather than importing it: what these fixtures need is an id that
    *looks* like a real one, and borrowing the production derivation would
    make a case silently change meaning if that derivation ever moved.
    """
    stem, _, _ext = rel_path.rpartition(".")
    return (stem or rel_path).replace("/", ".")


def file_id(rel_path: str) -> str:
    return f"file:{rel_path.rpartition('.')[0] or rel_path}"


def symbol_id(rel_path: str, name: str, language: str = "typescript") -> str:
    return f"symbol:{language}:{module_of(rel_path)}:{name}"


def caller_id(rel_path: str) -> str:
    return symbol_id(rel_path, "<file>")


def file_node(rel_path: str) -> dict:
    return {
        "type": "file",
        "label": rel_path.rpartition("/")[2],
        "props": {
            "file": rel_path,
            "language": "typescript",
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def symbol_node(
    rel_path: str,
    name: str,
    *,
    language: str = "typescript",
    confidence: str = "definite",
) -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {
            "file": rel_path,
            "module": module_of(rel_path),
            "qualname": name,
            "language": language,
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": confidence,
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def caller_node(rel_path: str) -> dict:
    node = symbol_node(rel_path, "<file>", confidence="inferred")
    node["props"]["kind"] = "file"
    node["props"]["scope"] = "module"
    return node


def call_edge(
    rel_path: str,
    local: str,
    *,
    name: str = "",
    specifier: str = "",
    target: str = "",
    hinted: bool = True,
) -> dict:
    """One ``calls`` edge as the strategy writes it, sentinel included.

    *hinted* off yields the shape every un-imported callee has -- a member
    call, a global -- which the closure must leave exactly where it found it.
    """
    props: dict = {
        "source_strategy": "tree_sitter",
        "confidence": "speculative",
        "resolved": False,
        "raw": local,
        "resolution": "unresolved",
        "provenance": {"file": rel_path, "line": 1},
    }
    if hinted:
        props[TS_IMPORT_PROP] = {
            "local": local,
            "name": name or local,
            "from": specifier,
            "target": target,
        }
    return {
        "from": caller_id(rel_path),
        "to": f"symbol:unresolved:{local}",
        "type": "calls",
        "props": props,
    }


def sentinel_node(local: str) -> dict:
    return {
        "type": "symbol",
        "label": local,
        "props": {
            "qualname": local,
            "language": "typescript",
            "kind": "unresolved",
            "resolved": False,
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "unresolved",
        },
    }


def graph(
    *,
    files: list[str],
    symbols: list[tuple[str, str]],
    calls: list[dict],
    foreign: list[tuple[str, str, str]] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """A merged graph holding *files*, *symbols* and *calls*.

    *foreign* is ``(file, name, language)`` for a definition in some *other*
    language -- the case that proves the definition index is filtered by
    language rather than by name alone.
    """
    nodes: dict[str, dict] = {}
    for rel_path in files:
        nodes[file_id(rel_path)] = file_node(rel_path)
        nodes[caller_id(rel_path)] = caller_node(rel_path)
    for rel_path, name in symbols:
        nodes[symbol_id(rel_path, name)] = symbol_node(rel_path, name)
    for rel_path, name, language in foreign or ():
        nodes[symbol_id(rel_path, name, language)] = symbol_node(
            rel_path, name, language=language,
        )
    for edge in calls:
        nodes.setdefault(str(edge["to"]), sentinel_node(str(edge["props"]["raw"])))
    return nodes, list(calls)


def endpoint(edges: list[dict], local: str) -> str:
    """Where the (single) edge whose call was written ``local`` now points."""
    hits = [
        str(edge["to"])
        for edge in edges
        if edge.get("type") == "calls"
        and (edge.get("props") or {}).get("raw") == local
    ]
    if len(hits) != 1:
        raise AssertionError(f"expected one calls edge for {local}, got {hits}")
    return hits[0]


def edge_props(edges: list[dict], local: str) -> dict:
    for edge in edges:
        if edge.get("type") == "calls" and (edge.get("props") or {}).get("raw") == local:
            return dict(edge["props"])
    raise AssertionError(f"no calls edge for {local}")

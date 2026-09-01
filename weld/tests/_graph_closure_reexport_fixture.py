"""Shared fixture for the closure's re-export-facade suites.

Two suites read this: ``weld_graph_closure_reexport_test`` (which facade
shapes retarget) and ``weld_graph_closure_reexport_guards_test`` (what the
retarget must refuse to do, and why re-running it is a no-op). Same split, and
the same reason, as the ``_graph_closure_python_fixture`` pair next door: the
rule is an inference plus its bounds, and both halves need one tiny graph
builder.

The builder is deliberately not the N4 one. That fixture only ever needs
``file`` nodes -- it is about which spelling of an import resolves -- while
every shape here needs the three-node cast the bug is made of: a definer that
holds the real symbol, a facade whose file node re-exports it, and a caller
whose ``calls`` edge landed on the facade's speculative stub.
"""

from __future__ import annotations

from weld.graph_closure import close_graph

PY = "python"


def file_node(path: str, imports: list[str] | None = None) -> dict:
    """A ``file`` node for *path*, optionally declaring ``imports_from``."""
    props: dict = {"file": path, "language": PY}
    if imports is not None:
        props["imports_from"] = imports
    return {"type": "file", "label": path.rsplit("/", 1)[-1], "props": props}


def symbol_node(module: str, qualname: str, path: str) -> dict:
    """A walked, ``definite`` symbol node -- the shape a definer gets."""
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": PY,
            "file": path,
            "kind": "function",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def stub_node(module: str, qualname: str, origin: str = "project") -> dict:
    """The exact payload ``make_resolved_target_node`` stamps.

    Written out rather than called so a drift in that minter shows up here as
    a failing assertion instead of silently reshaping every fixture at once.
    """
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": PY,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": origin,
        },
    }


def call_edge(src: str, dst: str, edge_type: str = "calls") -> dict:
    """A strategy-minted interaction edge, provenance stamped as the real ones are."""
    return {
        "from": src,
        "to": dst,
        "type": edge_type,
        "props": {
            "source_strategy": "python_callgraph",
            "confidence": "definite",
            "provenance": {"file": "pkg/caller.py", "line": 7},
        },
    }


def facade_graph(
    *,
    facade_imports: list[str] | None = None,
    extra_nodes: dict[str, dict] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """The three-file cast: ``pkg.definer`` defines it, ``pkg.facade`` re-exports it.

    ``pkg/caller.py`` writes ``from pkg.facade import widget`` and calls it, so
    ``python_callgraph`` resolved the call against its own import table and
    minted ``symbol:py:pkg.facade:widget`` -- a module that defines no such
    name. That stub, and the edge that points at it, are the bug.
    """
    nodes: dict[str, dict] = {
        "file:pkg/definer": file_node("pkg/definer.py"),
        "file:pkg/facade": file_node(
            "pkg/facade.py",
            ["pkg.definer"] if facade_imports is None else facade_imports,
        ),
        "file:pkg/caller": file_node("pkg/caller.py", ["pkg.facade"]),
        "symbol:py:pkg.definer:widget": symbol_node(
            "pkg.definer", "widget", "pkg/definer.py"
        ),
        "symbol:py:pkg.caller:run": symbol_node(
            "pkg.caller", "run", "pkg/caller.py"
        ),
        "symbol:py:pkg.facade:widget": stub_node("pkg.facade", "widget"),
    }
    nodes.update(extra_nodes or {})
    edges = [call_edge("symbol:py:pkg.caller:run", "symbol:py:pkg.facade:widget")]
    return nodes, edges


def close(
    nodes: dict[str, dict], edges: list[dict],
) -> tuple[dict[str, dict], list[dict]]:
    """Run the closure over *nodes*/*edges* and return them, mutated."""
    close_graph(nodes, edges)
    return nodes, edges


def interaction_edges(edges: list[dict], edge_type: str = "calls") -> list[dict]:
    """Every edge of *edge_type*, in list order."""
    return [e for e in edges if e["type"] == edge_type]


def targets(edges: list[dict], src: str, edge_type: str = "calls") -> list[str]:
    """Sorted targets of every *edge_type* edge leaving *src*."""
    return sorted(
        str(e["to"]) for e in edges if e["type"] == edge_type and e["from"] == src
    )

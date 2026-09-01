"""Shared fixture for the closure's deferred-attribute-call suites.

Two suites read this: ``weld_graph_closure_import_attr_test`` (which readings
resolve, and which the rule refuses) and
``weld_graph_closure_import_attr_undo_test`` (that a move made in an earlier
round is restored and re-derived rather than inherited). Same split, and the
same reason, as the ``_graph_closure_reexport_fixture`` pair next door: the
rule is an inference plus its bounds, and its idempotence is a separate
mechanism with its own failure mode.

The cast is the one the bug is made of: ``tools/go.py`` writes
``from lib import inner`` and calls ``inner.work()``. ``python_callgraph``
walked it under the ``tools`` glob, which owns no ``lib`` module of any kind,
so it emitted the ``symbol:unresolved:work`` sentinel and recorded what the
reading turns on. ``lib/inner.py`` was walked by a different glob, and only the
merged graph the closure sees holds both.
"""

from __future__ import annotations

from weld.graph_closure import close_graph
from weld.strategies._python_import_attr import IMPORT_ATTR_PROP

PY = "python"

CALLER = "symbol:py:tools.go:go"
SENTINEL = "symbol:unresolved:work"
RESOLVED = "symbol:py:lib.inner:work"

#: The class-base cast, same two globs: ``tools/go.py`` writes
#: ``from lib.tables import Corpus`` and calls ``Corpus.build()``.
#: ``lib.tables.Corpus`` is no module, so the submodule rule declines and the
#: answer is the method's own symbol -- a node the walk of ``lib/tables.py``
#: already emitted, which is why the rule names it rather than minting it.
CLASS = "symbol:py:lib.tables:Corpus"
METHOD = "symbol:py:lib.tables:Corpus.build"
CLASS_SENTINEL = "symbol:unresolved:build"


def file_node(path: str, imports: list[str] | None = None) -> dict:
    """A ``file`` node for *path*, optionally declaring ``imports_from``."""
    props: dict = {"file": path, "language": PY}
    if imports is not None:
        props["imports_from"] = imports
    return {"type": "file", "label": path.rsplit("/", 1)[-1], "props": props}


def symbol_node(
    module: str,
    qualname: str,
    path: str,
    kind: str = "function",
    confidence: str = "definite",
) -> dict:
    """A walked symbol node, ``definite`` and ``kind=function`` by default.

    ``kind`` and ``confidence`` are parameters because the class-base rule
    reads exactly those two props to decide whether a base was *walked as a
    class* rather than merely claimed by some id -- so a fixture has to be able
    to produce each half of that proof independently.
    """
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": PY,
            "file": path,
            "kind": kind,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": confidence,
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def sentinel_node(name: str, resolution: str = "unresolved") -> dict:
    """The exact payload ``make_sentinel_node`` stamps.

    Written out rather than called so a drift in that minter shows up here as
    a failing assertion instead of silently reshaping every fixture at once --
    the closure re-mints this node itself when it restores an endpoint, and
    "re-mints what the strategy would have" is the whole claim.
    """
    return {
        "type": "symbol",
        "label": name,
        "props": {
            "module": "",
            "qualname": name,
            "language": PY,
            "resolved": False,
            "resolution": resolution,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "stdlib" if resolution == "builtin" else "unresolved",
        },
    }


def deferred_edge(
    *,
    src: str = CALLER,
    dst: str = SENTINEL,
    module: str = "lib",
    base: str = "inner",
    attr: str = "work",
    side: str = "to",
    edge_type: str = "calls",
    resolved: bool = False,
    line: int = 5,
) -> dict:
    """One strategy-emitted edge carrying a deferred-resolution hint."""
    return {
        "from": src,
        "to": dst,
        "type": edge_type,
        "props": {
            "source_strategy": "python_callgraph",
            "confidence": "definite" if resolved else "speculative",
            "resolved": resolved,
            "raw": attr,
            "resolution": "import" if resolved else "unresolved",
            "provenance": {"file": "tools/go.py", "line": line},
            IMPORT_ATTR_PROP: {
                "module": module, "base": base, "attr": attr, "side": side,
            },
        },
    }


def cross_glob_graph(
    *, definer: bool = True, extra_nodes: dict[str, dict] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """The two-glob cast. ``definer=False`` drops ``lib/inner.py`` entirely."""
    nodes: dict[str, dict] = {
        "file:tools/go": file_node("tools/go.py", ["lib", "lib.inner"]),
        CALLER: symbol_node("tools.go", "go", "tools/go.py"),
        SENTINEL: sentinel_node("work"),
    }
    if definer:
        nodes["file:lib/inner"] = file_node("lib/inner.py", [])
        nodes[RESOLVED] = symbol_node("lib.inner", "work", "lib/inner.py")
    nodes.update(extra_nodes or {})
    return nodes, [deferred_edge()]


def class_base_graph(
    *,
    method: bool = True,
    base_kind: str | None = "class",
    base_confidence: str = "definite",
    method_confidence: str = "definite",
) -> tuple[dict[str, dict], list[dict]]:
    """The class-base cast. Each keyword removes one half of the proof.

    ``method=False`` drops ``Corpus.build``; ``base_kind`` makes the base
    something other than a walked class (``None`` drops the prop entirely, the
    shape a speculative stub has); the two ``*_confidence`` knobs downgrade a
    node that still carries the right ``kind``.
    """
    base = symbol_node(
        "lib.tables", "Corpus", "lib/tables.py",
        kind=base_kind or "function", confidence=base_confidence,
    )
    if base_kind is None:
        del base["props"]["kind"]
    nodes: dict[str, dict] = {
        "file:tools/go": file_node("tools/go.py", ["lib.tables"]),
        CALLER: symbol_node("tools.go", "go", "tools/go.py"),
        CLASS_SENTINEL: sentinel_node("build"),
        "file:lib/tables": file_node("lib/tables.py", []),
        CLASS: base,
    }
    if method:
        nodes[METHOD] = symbol_node(
            "lib.tables", "Corpus.build", "lib/tables.py", kind="method",
            confidence=method_confidence,
        )
    edges = [
        deferred_edge(
            dst=CLASS_SENTINEL, module="lib.tables", base="Corpus", attr="build",
        )
    ]
    return nodes, edges


def close(
    nodes: dict[str, dict], edges: list[dict],
) -> tuple[dict[str, dict], list[dict]]:
    """Run the closure over *nodes*/*edges* and return them, mutated."""
    close_graph(nodes, edges)
    return nodes, edges


def one(edges: list[dict], src: str, edge_type: str = "calls") -> dict:
    """The single *edge_type* edge leaving *src*."""
    matches = [e for e in edges if e["type"] == edge_type and e["from"] == src]
    assert len(matches) == 1, f"expected exactly one edge, got {matches}"
    return matches[0]


def targets(edges: list[dict], src: str, edge_type: str = "calls") -> list[str]:
    """Sorted targets of every *edge_type* edge leaving *src*."""
    return sorted(
        str(e["to"]) for e in edges if e["type"] == edge_type and e["from"] == src
    )

"""Shared fixture for the closure's Python import-resolution suites.

Two suites read this: ``weld_graph_closure_python_modules_test`` (which
spellings of an import resolve, and in what order) and
``weld_graph_closure_python_guards_test`` (what resolution must refuse to
do). They are split because the file they cover is a single rule with two
halves -- an inference and its bounds -- and both halves need the same tiny
graph builder.
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


def close(nodes: dict[str, dict]) -> tuple[dict[str, dict], list[dict]]:
    """Run the closure over *nodes* and return the mutated nodes and edges."""
    edges: list[dict] = []
    close_graph(nodes, edges)
    return nodes, edges


def depends_on(edges: list[dict], src: str) -> dict[str, dict]:
    """Map ``import_name`` -> edge for every ``depends_on`` leaving *src*."""
    return {
        str(e["props"]["import_name"]): e
        for e in edges
        if e["type"] == "depends_on" and e["from"] == src
    }


def package_ids(nodes: dict[str, dict]) -> list[str]:
    """Every synthesised Python package node id, sorted."""
    return sorted(n for n in nodes if n.startswith("package:python:"))

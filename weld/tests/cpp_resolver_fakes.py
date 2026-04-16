"""Fake tree-sitter hooks for C++ include resolver tests."""

from __future__ import annotations

from pathlib import Path


def fake_parse(file_path, language, queries):  # noqa: ANN001, ARG001
    """Return hand-rolled symbol/import lists keyed by filename."""
    name = Path(file_path).name
    if name == "foo.h":
        return {
            "exports": ["Foo", "bar", "baz", "inline_double", "free_add", "identity"],
            "classes": ["Foo"],
            "imports": [],
        }
    if name == "app.h":
        return {
            "exports": ["ItemStore", "add", "items", "Bar", "baz"],
            "classes": ["ItemStore", "Bar", "Item"],
            "imports": [],
        }
    if name == "foo.cpp":
        return {
            "exports": ["Foo::bar", "Foo::baz", "free_add"],
            "classes": [],
            "imports": ['"foo.h"'],
        }
    if name == "main.cpp":
        return {
            "exports": ["main"],
            "classes": [],
            "imports": ['"app.h"', '"foo.h"'],
        }
    if name == "app.cpp":
        return {
            "exports": ["ItemStore::add", "ItemStore::items"],
            "classes": [],
            "imports": ['"app.h"'],
        }
    if name == "test_main.cpp":
        return {
            "exports": ["test_main"],
            "classes": [],
            "imports": ['"app.h"', '"foo.h"'],
        }
    return {"exports": [], "classes": [], "imports": []}


def fake_call_edges(file_path, rel_path, language, queries):  # noqa: ANN001, ARG001
    """Emit unresolved call sentinels that exercise layer-2 resolution."""
    from weld.strategies.tree_sitter import _ts_module_from_path

    module = _ts_module_from_path(rel_path)
    file_caller = f"symbol:{language}:{module}:<file>"
    nodes: dict = {
        file_caller: {
            "type": "symbol",
            "label": module,
            "props": {
                "file": rel_path,
                "module": module,
                "qualname": "<file>",
                "language": language,
                "scope": "module",
                "source_strategy": "tree_sitter",
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
            },
        }
    }
    edges: list = []

    name = Path(file_path).name
    callees: list[str] = []
    if name == "main.cpp":
        callees = [
            "Foo::bar",
            "Bar::baz",
            "free_add",
            "inline_double",
            "add",
            "items",
            "external_unresolvable_call",
        ]
    elif name == "foo.cpp":
        for q in ("Foo::bar", "Foo::baz", "free_add"):
            sid = f"symbol:{language}:{module}:{q}"
            nodes[sid] = {
                "type": "symbol",
                "label": q,
                "props": {
                    "file": rel_path,
                    "module": module,
                    "qualname": q,
                    "language": language,
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            }
        callees = ["Foo::baz", "identity"]
    elif name == "app.cpp":
        for q in ("ItemStore::add", "ItemStore::items"):
            sid = f"symbol:{language}:{module}:{q}"
            nodes[sid] = {
                "type": "symbol",
                "label": q,
                "props": {
                    "file": rel_path,
                    "module": module,
                    "qualname": q,
                    "language": language,
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            }
    elif name == "test_main.cpp":
        callees = ["add", "items"]

    for callee in callees:
        target = f"symbol:unresolved:{callee}"
        nodes.setdefault(
            target,
            {
                "type": "symbol",
                "label": callee,
                "props": {
                    "qualname": callee,
                    "language": language,
                    "resolved": False,
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "speculative",
                    "roles": ["implementation"],
                },
            },
        )
        edges.append(
            {
                "from": file_caller,
                "to": target,
                "type": "calls",
                "props": {
                    "source_strategy": "tree_sitter",
                    "confidence": "speculative",
                    "resolved": False,
                },
            }
        )
    return nodes, edges

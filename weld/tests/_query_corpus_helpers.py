"""Node-shape builders shared by the query-quality eval corpus fixtures.

Split out of :mod:`weld.tests._query_corpus_nodes` when the subject/concept
population and the noise population (:mod:`weld.tests._query_corpus_noise`)
both needed these three builders and neither should import the other --
keeping them here avoids a two-file cycle. See
:mod:`weld.tests._query_corpus_nodes` for why this fixture exists at all.
"""

from __future__ import annotations


def _symbol(label: str, module: str, file_path: str, **props) -> dict:
    """A python_callgraph-shaped symbol node.

    ``roles: ["implementation"]`` on everything is not a simplification -- it is
    what discovery really stamps, including on symbols defined inside a test
    module, which is why only the file path separates test material from the
    code it covers (bd to8x).
    """
    return {
        "type": "symbol",
        "label": label,
        "props": {
            "authority": "derived",
            "confidence": "definite",
            "file": file_path,
            "kind": "function",
            "language": "python",
            "module": module,
            "origin": "project",
            "qualname": label,
            "roles": ["implementation"],
            "source_strategy": "python_callgraph",
            **props,
        },
    }


def _file(label: str, file_path: str, **props) -> dict:
    return {
        "type": "file",
        "label": label,
        "props": {
            "authority": "derived",
            "confidence": "definite",
            "file": file_path,
            "language": "python",
            "roles": ["implementation"],
            "source_strategy": "python_module",
            **props,
        },
    }


def _concept(label: str, description: str, bd_short_id: str) -> dict:
    """A ``concept_from_bd`` node, shaped exactly as discovery emits one.

    ``source_strategy: concept_from_bd`` is the whole signal ADR 0113 keys on,
    so it is the one field here that must not drift from the real strategy.
    """
    return {
        "type": "concept",
        "label": label,
        "props": {
            "authority": "derived",
            "bd_short_id": bd_short_id,
            "confidence": "inferred",
            "description": description,
            "priority": 3,
            "roles": ["doc"],
            "source_strategy": "concept_from_bd",
            "status": "open",
        },
    }


__all__ = ["_concept", "_file", "_symbol"]

"""Declared-channel extraction from Python call sites (project-xoq.6.1).

Code half of the ``events`` strategy. Walks Python files for calls
shaped ``<Root>.<verb>("literal", ...)`` where ``<Root>`` is a known
async client identifier and ``<verb>`` is a known publish verb. As
with :mod:`cortex.strategies.http_client`, resolving assigned instances or
attribute chains is out of scope; per ADR 0018, omission is preferred
over guesswork.

The config half lives in :mod:`cortex.strategies.events_config`; the
facade in :mod:`cortex.strategies.events` dispatches between the two.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cortex.strategies._helpers import filter_glob_results
from cortex.strategies.events_shared import (
    channel_id,
    channel_node,
    contains_edge,
)

_TRANSPORT_KAFKA = "kafka"
_TRANSPORT_TCP = "tcp"

# ---------------------------------------------------------------------------
# Call-site vocabulary.
#
# A rule fires when the receiver Name is in ``roots`` and the attribute
# being called is in ``verbs``. Everything else -- assigned instances,
# deep attribute chains -- is left alone.
# ---------------------------------------------------------------------------
_PY_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (
        frozenset(["KafkaProducer", "kafka"]),
        frozenset(["send", "produce", "send_and_wait"]),
        _TRANSPORT_KAFKA,
    ),
    (
        frozenset(["redis"]),
        frozenset(["publish"]),
        _TRANSPORT_TCP,
    ),
)

#: Library root names that indicate an async client import. Cheap
#: pre-filter so we only AST-walk files that could possibly match.
_PY_IMPORT_ROOTS: frozenset[str] = frozenset(["kafka", "redis", "aiokafka"])

def _literal_first_arg(call: ast.Call) -> str | None:
    """Return the literal string first positional arg, or None.

    Accepts plain constants and literal-only f-strings. A FormattedValue
    part in the f-string (runtime substitution) disqualifies the arg.
    """
    if not call.args:
        return None
    node = call.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None

def _classify_call(call: ast.Call) -> str | None:
    """Return the transport for a matching call, or None."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    root = func.value.id
    verb = func.attr
    for roots, verbs, transport in _PY_RULES:
        if root in roots and verb in verbs:
            return transport
    return None

def _file_has_async_import(tree: ast.Module) -> bool:
    """Cheap pre-filter: only walk files that import a known async lib."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _PY_IMPORT_ROOTS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _PY_IMPORT_ROOTS:
                return True
    return False

def _collect_calls(tree: ast.Module) -> list[tuple[str, str]]:
    """Walk *tree* and return ``(transport, name)`` for static call sites."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        transport = _classify_call(node)
        if transport is None:
            continue
        name = _literal_first_arg(node)
        if name is None or name == "":
            continue
        found.append((transport, name))
    return found

def _iter_sources(root: Path, pattern: str) -> list[Path]:
    matches = sorted(root.glob(pattern))
    return filter_glob_results(root, matches)

def extract_py_callsite(
    root: Path, pattern: str
) -> tuple[dict[str, dict], list[dict], list[str]]:
    """Extract declared channels from Python publish/produce call sites."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    for py in _iter_sources(root, pattern):
        if not py.is_file() or py.suffix != ".py":
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        if not _file_has_async_import(tree):
            continue

        rel_path = str(py.relative_to(root))
        calls = _collect_calls(tree)
        if not calls:
            continue

        discovered_from.append(rel_path)
        file_id = f"file:{rel_path}"

        for transport, name in calls:
            nid = channel_id(transport, name)
            nodes[nid] = channel_node(
                transport=transport, name=name, rel_path=rel_path
            )
            edges.append(contains_edge(file_id, nid))

    return nodes, edges, discovered_from

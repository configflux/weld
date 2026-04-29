"""Strategy: channel producer/consumer/payload linking (tracked project).

Links Python producer and consumer call sites back to the
``channel:<transport>:<name>`` nodes emitted by the ``events`` strategy
(tracked project). Per ADR 0018's static-truth policy, detection is
purely structural: no runtime, no data-flow, no instance tracking.

Supported shapes:

- Producer binding: ``<Root>.<verb>("literal", ...)`` where ``<Root>``
  is a known async client identifier (``KafkaProducer``, ``kafka``,
  ``redis``) and ``<verb>`` is a known publish verb (``send``,
  ``produce``, ``send_and_wait``, ``publish``). Emits a ``produces``
  edge from ``file:<rel-path>`` to the channel node id.

- Consumer binding: ``<Root>.subscribe(["literal", ...])`` or
  ``<Root>.subscribe("literal")`` where ``<Root>`` is a known async
  consumer (``KafkaConsumer``, ``kafka``, ``redis``). Emits a
  ``consumes`` edge from ``file:<rel-path>`` to each channel node id.

- Payload contract: when the enclosing function of a producer call has
  a typed parameter whose annotation is an uppercase non-primitive name,
  an ``implements`` edge is emitted from the channel node to
  ``contract:<AnnotationName>``. This is ``confidence="inferred"``
  because BaseModel inheritance cannot be verified from one file.

All edges carry ``source_strategy="events_bindings"`` and
``confidence="inferred"``. Edges intentionally dangle against channel
node ids; discovery's dangling-edge sweep resolves or drops them
depending on whether the ``events`` fragment is present in the graph.

Out of scope (ADR 0018): following assigned instances (``p = Producer();
p.send(...)``), decorator-based bindings (``@celery.task``), and any
shape requiring data-flow analysis.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results
from weld.strategies.events_shared import channel_id

# ---------------------------------------------------------------------------
# Call-site vocabulary — mirrors events_callsite but adds consumer verbs.
# ---------------------------------------------------------------------------

_TRANSPORT_KAFKA = "kafka"
_TRANSPORT_TCP = "tcp"

#: (root_names, publish_verbs, transport)
_PRODUCER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
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

#: (root_names, subscribe_verbs, transport)
_CONSUMER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (
        frozenset(["KafkaConsumer", "kafka"]),
        frozenset(["subscribe"]),
        _TRANSPORT_KAFKA,
    ),
    (
        frozenset(["redis"]),
        frozenset(["subscribe"]),
        _TRANSPORT_TCP,
    ),
)

#: Library root names for the cheap import pre-filter.
_IMPORT_ROOTS: frozenset[str] = frozenset(
    ["kafka", "redis", "aiokafka"]
)

#: Annotation names treated as plain values (not contracts).
_PRIMITIVE_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "int", "str", "float", "bool", "bytes", "None", "Any",
        "dict", "list", "tuple", "set", "frozenset",
        "Dict", "List", "Tuple", "Set", "FrozenSet",
        "Sequence", "Iterable", "Optional", "Union",
        "Annotated", "Literal",
    }
)

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _file_has_async_import(tree: ast.Module) -> bool:
    """Cheap pre-filter: only walk files that import a known async lib."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _IMPORT_ROOTS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _IMPORT_ROOTS:
                return True
    return False

def _literal_first_arg(call: ast.Call) -> str | None:
    """Return the literal string first positional arg, or None."""
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

def _literal_list_arg(call: ast.Call) -> list[str] | None:
    """Return literal strings from a list first arg, or None.

    Handles ``subscribe(["topic1", "topic2"])``. Non-literal elements
    cause the entire list to be dropped (conservative).
    """
    if not call.args:
        return None
    node = call.args[0]
    # Single string arg (``subscribe("topic")``)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value] if node.value else None
    # List arg (``subscribe(["topic1", "topic2"])``)
    if not isinstance(node, ast.List):
        return None
    result: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            if elt.value:
                result.append(elt.value)
        else:
            return None  # Any non-literal element kills the list.
    return result or None

def _annotation_name(anno: ast.expr | None) -> str | None:
    """Return the trailing identifier of an annotation, or None."""
    if anno is None:
        return None
    if isinstance(anno, ast.Name):
        return anno.id
    if isinstance(anno, ast.Attribute):
        return anno.attr
    if isinstance(anno, ast.Subscript):
        slice_node = anno.slice
        if isinstance(slice_node, ast.Tuple) and slice_node.elts:
            slice_node = slice_node.elts[0]
        inner = _annotation_name(slice_node)
        if inner is not None:
            return inner
        return _annotation_name(anno.value)
    return None

def _enclosing_function_contracts(
    tree: ast.Module, target_lineno: int
) -> list[str]:
    """Find contract-like annotations on the function enclosing *target_lineno*.

    Returns uppercase, non-primitive annotation names. Returns empty if
    the call is at module level or no contract annotations are found.
    """
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= target_lineno <= end:
            enclosing = node
    if enclosing is None:
        return []
    found: list[str] = []
    params: list[ast.arg] = []
    params.extend(enclosing.args.posonlyargs)
    params.extend(enclosing.args.args)
    params.extend(enclosing.args.kwonlyargs)
    for param in params:
        if param.arg in ("self", "cls"):
            continue
        name = _annotation_name(param.annotation)
        if not name or name in _PRIMITIVE_ANNOTATIONS:
            continue
        if not name[:1].isupper():
            continue
        if name not in found:
            found.append(name)
    return found

# ---------------------------------------------------------------------------
# Edge helpers
# ---------------------------------------------------------------------------

def _edge(src: str, dst: str, etype: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {
            "source_strategy": "events_bindings",
            "confidence": "inferred",
        },
    }

# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def _classify_producer(call: ast.Call) -> str | None:
    """Return the transport when *call* matches a producer verb."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    root = func.value.id
    verb = func.attr
    for roots, verbs, transport in _PRODUCER_RULES:
        if root in roots and verb in verbs:
            return transport
    return None

def _classify_consumer(call: ast.Call) -> str | None:
    """Return the transport when *call* matches a consumer verb."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    root = func.value.id
    verb = func.attr
    for roots, verbs, transport in _CONSUMER_RULES:
        if root in roots and verb in verbs:
            return transport
    return None

def _process_file(
    tree: ast.Module,
    rel_path: str,
    edges: list[dict],
) -> bool:
    """Process one parsed Python module. Returns True if any edge emitted."""
    file_id = f"file:{rel_path}"
    emitted = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # --- Producer ---
        transport = _classify_producer(node)
        if transport is not None:
            topic = _literal_first_arg(node)
            if topic and topic != "":
                cid = channel_id(transport, topic)
                edges.append(_edge(file_id, cid, "produces"))
                emitted = True
                # Payload contract linking
                lineno = getattr(node, "lineno", 0)
                contracts = _enclosing_function_contracts(tree, lineno)
                for contract_name in contracts:
                    edges.append(
                        _edge(cid, f"contract:{contract_name}", "implements")
                    )
            continue

        # --- Consumer ---
        transport = _classify_consumer(node)
        if transport is not None:
            topics = _literal_list_arg(node)
            if topics:
                for topic in topics:
                    cid = channel_id(transport, topic)
                    edges.append(_edge(file_id, cid, "consumes"))
                    emitted = True
            continue

    return emitted

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Link Python producer/consumer call sites to declared channel surfaces."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for py in filter_glob_results(root, sorted(root.glob(pattern))):
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
        if _process_file(tree, rel_path, edges):
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)

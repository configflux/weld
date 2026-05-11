"""Partial-class accumulation + merge pass (ADR 0056 Wave 3).

Extracted from :mod:`weld.strategies._csharp_tree_sitter` to keep that
module under the 400-line cap. Two responsibilities:

1. :func:`record_partial_classes` -- runs per-file inside the C#
   enricher. Adds every ``partial class`` declaration in the source
   text to a shared state dict keyed by ``(namespace, class_name)``.
2. :func:`finalise` -- runs once after every file has been visited.
   Emits one ``symbol:csharp:<namespace>.<class>`` node per recorded
   key plus one ``contains`` edge per contributing file. Each edge
   ships with ``confidence="definite"`` per ADR 0050.

Generic-parameter syntax (``Foo<T>``) is preserved verbatim from the
first non-empty declaration so the merged node label retains the
type identity. The node id strips the generic list because the id has
to be stable across declarations with different generic-arg names
(``T`` vs ``U``) -- consumers join on the namespace-qualified name.
"""

from __future__ import annotations

import re

from weld.strategies._csharp_syntax import namespace_at, namespace_spans

#: Matches a ``partial class`` declaration capturing the class name and
#: optional generic-parameter list. ``partial`` is *required* and may
#: appear before or after other modifiers (C# allows
#: ``sealed partial class``, ``partial sealed class``, etc.). Groups:
#:   1. class name (``[A-Za-z_][A-Za-z0-9_]*``)
#:   2. generic-parameter list including angle brackets, or empty
#:      string when the declaration is non-generic.
#:
#: The lookbehind window is ``(?:<modifier>\s+)*`` *before* ``partial``
#: and ``(?:<modifier>\s+)*`` *after* ``partial`` and before
#: ``class``; the cumulative effect is "``partial`` somewhere in the
#: modifier sequence". Required, not optional.
_MODIFIER_GROUP = (
    r"(?:public|internal|protected|private|static|sealed|abstract)"
)
_PARTIAL_CLASS_RE = re.compile(
    rf"(?:{_MODIFIER_GROUP}[\t ]+)*"
    r"partial[\t ]+"
    rf"(?:{_MODIFIER_GROUP}[\t ]+)*"
    r"class[\t ]+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"(\s*<[^>]*>)?",
)

#: Matches a line-comment tail (``// ...`` to end-of-line) and a
#: block-comment span (``/* ... */``, possibly multi-line). Comments
#: are erased -- replaced with spaces, preserving newlines so line
#: offsets used by :mod:`weld.strategies._csharp_syntax` stay stable
#: -- before the partial-class regex scans the buffer. Without this,
#: the literal phrase "partial class" inside a doc comment would
#: synthesise a phantom symbol node.
_COMMENT_RE = re.compile(
    r"//[^\n]*|/\*.*?\*/",
    re.DOTALL,
)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out.

    Replaces each comment span with a same-length run of spaces (and
    preserves newlines inside block comments). This keeps every
    offset in the buffer stable so :func:`namespace_at` continues to
    return the right namespace for a regex match position.
    """
    def _blank(match: re.Match) -> str:
        return "".join(
            "\n" if ch == "\n" else " "
            for ch in match.group(0)
        )
    return _COMMENT_RE.sub(_blank, source_text)


def record_partial_classes(
    state: dict,
    *,
    file_node_id: str,
    rel_path: str,
    source_text: str,
) -> None:
    """Record every ``partial class`` declaration in *source_text*.

    The accumulator key is ``(namespace, class_name)``; the value is a
    dict tracking the contributing files plus the first non-empty
    generic-parameter signature seen (e.g. ``"<T>"``). Subsequent
    declarations that disagree on generic-parameter shape are
    tolerated -- the first non-empty value wins so the merged label
    stays deterministic.
    """
    scan_text = _strip_comments(source_text)
    spans = namespace_spans(scan_text)
    for match in _PARTIAL_CLASS_RE.finditer(scan_text):
        class_name = match.group(1)
        generic_params = (match.group(2) or "").strip()
        namespace = namespace_at(match.start(), spans)
        key = (namespace, class_name)
        record = state.setdefault(
            key,
            {
                "namespace": namespace,
                "class_name": class_name,
                "generic_parameters": "",
                "files": [],
                "file_node_ids": [],
            },
        )
        if rel_path and rel_path not in record["files"]:
            record["files"].append(rel_path)
        if file_node_id and file_node_id not in record["file_node_ids"]:
            record["file_node_ids"].append(file_node_id)
        if generic_params and not record["generic_parameters"]:
            record["generic_parameters"] = generic_params


def finalise(
    nodes: dict[str, dict],
    edges: list[dict],
    enricher_caches: dict | None,
    source_strategy: str,
) -> None:
    """Emit merged symbol nodes for every tracked partial-class set.

    Walks the ``partial_class_state`` accumulator produced by
    :func:`record_partial_classes`. Each ``(namespace, class)`` key
    yields one ``symbol:csharp:<ns>.<class>`` node plus one
    ``contains`` edge per contributing file node (``symbol --
    contains --> file:...``). All edges ship with
    ``confidence="definite"`` per ADR 0050.

    A ``None`` *enricher_caches* (non-C# language path) is a no-op so
    the dispatcher in :mod:`weld.strategies.tree_sitter` can invoke
    this unconditionally.
    """
    if not enricher_caches:
        return
    state = enricher_caches.get("partial_class_state")
    if not state:
        return
    for (namespace, class_name), record in sorted(state.items()):
        symbol_id = partial_class_symbol_id(namespace, class_name)
        generic_params = record["generic_parameters"]
        label = (
            f"{class_name}{generic_params}"
            if generic_params else class_name
        )
        files = sorted(set(record["files"]))
        file_node_ids = sorted(set(record["file_node_ids"]))

        nodes[symbol_id] = {
            "type": "symbol",
            "label": label,
            "props": {
                "name": class_name,
                "namespace": namespace,
                "kind": "partial_class",
                "language": "csharp",
                "files": files,
                "partial_count": len(files),
                "generic_parameters": generic_params,
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "definite",
                "roles": ["implementation"],
            },
        }

        for file_node_id in file_node_ids:
            edges.append({
                "from": symbol_id,
                "to": file_node_id,
                "type": "contains",
                "props": {
                    "source_strategy": source_strategy,
                    "confidence": "definite",
                    "kind": "partial_class",
                },
            })


def partial_class_symbol_id(namespace: str, class_name: str) -> str:
    """Return the canonical ``symbol:csharp:<ns>.<class>`` id.

    Mirrors :func:`weld.strategies._csharp_routes_helpers.symbol_id` so
    the partial-class merger writes to the same id shape that Wave 2
    framework-aware strategies (EF Core, controllers) use. An empty
    namespace collapses to a bare ``symbol:csharp:<class>`` id.
    """
    qualified = (
        f"{namespace}.{class_name}" if namespace else class_name
    )
    return f"symbol:csharp:{qualified}"


__all__ = [
    "finalise",
    "partial_class_symbol_id",
    "record_partial_classes",
]

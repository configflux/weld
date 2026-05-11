"""Strategy: detect C# unit-test framework markers (ADR 0056 Wave 2).

Three frameworks are recognised by attribute marker:

- **xUnit**: ``[Fact]`` or ``[Theory]`` on a test method, or any class
  that hosts at least one such method.
- **NUnit**: ``[Test]`` on a test method, ``[TestFixture]`` on a class.
- **MSTest**: ``[TestMethod]`` on a method, ``[TestClass]`` on a class.

For every test class detected, the strategy emits a ``test-suite:``
node carrying:

- ``test_framework`` -- one of ``xunit | nunit | mstest``.
- ``methods`` -- the sorted, deduplicated list of test-method names
  detected for that class.
- ``namespace`` and ``class_name`` -- raw lookups for downstream agents.

It also emits a ``test-suite -> contains -> file:`` edge to the test
file. This is the ADR 0046 coordination seam: the ``test_peer``
strategy emits ``file:<test> -[tests]-> file:<source>``, and the
test-suite node sits one edge away with framework provenance, so
consumers walking ``test-suite -[contains]-> file:test`` can pivot
into the test-peer subgraph and tag tests with their framework.

The detection contract is *attribute presence at the source-line
level*. Regex is sufficient because attributes always appear in
``[Name(...)]`` form before the method/class declaration -- on a
preceding line or inlined on the same line. A C# parser is
intentionally not required (ADR 0056 posture: tree-sitter floor, no
Roslyn).

Every emitted edge ships with ``confidence="definite"`` per ADR 0050;
the detection is deterministic (attribute-name match), not a
heuristic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._csharp_syntax import (
    CLASS_RE,
    attribute_window_start,
    class_body_range,
    namespace_at,
    namespace_spans,
)
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

#: Map: method-level attribute name -> framework label. The match is
#: case-sensitive (matching .NET attribute conventions). ``Theory`` is
#: the xUnit parametric form; ``[InlineData]`` accompanies it but is
#: framework-implicit and not used as a primary marker.
_METHOD_ATTRIBUTES: dict[str, str] = {
    "Fact": "xunit",
    "Theory": "xunit",
    "Test": "nunit",
    "TestMethod": "mstest",
}

#: Map: class-level attribute name -> framework label.
_CLASS_ATTRIBUTES: dict[str, str] = {
    "TestFixture": "nunit",
    "TestClass": "mstest",
}

#: Recognised method-level marker names (used to short-circuit body
#: scans).
_METHOD_MARKERS: frozenset[str] = frozenset(_METHOD_ATTRIBUTES.keys())

#: Matches a method declaration of the form
#: ``<modifiers> <return-type> <Name>(`` on a single line.
#: ``async``/``override``/``virtual`` etc. are tolerated; the return
#: type is captured greedily as everything up to the method name.
_METHOD_RE = re.compile(
    r"(?:(?:public|internal|protected|private|static|async|override|"
    r"virtual|sealed|abstract|new|extern|unsafe)[\t ]+)+"
    r"[A-Za-z_][A-Za-z0-9_<>?,\[\] .]*[\t ]+"  # return type
    r"([A-Za-z_][A-Za-z0-9_]*)[\t ]*\(",
)

#: Matches each individual attribute inside an attribute block. C#
#: allows multiple attributes per block (``[Fact, Trait("a", "b")]``)
#: and multiple blocks per declaration (``[Fact][InlineData(1)]``); the
#: scanner accumulates names from every block in the attribute window.
_ATTRIBUTE_NAME_RE = re.compile(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\b")


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract test-suite nodes for every detected test class."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "**/*.cs")
    excludes = source.get("exclude", [])

    matched = _resolve_glob(root, pattern)
    matched = filter_glob_results(root, matched, excludes=excludes)

    for cs_file in matched:
        if not cs_file.is_file():
            continue
        if should_skip(cs_file, excludes, root=root):
            continue
        try:
            source_text = cs_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = cs_file.relative_to(root).as_posix()
        any_suite_emitted = False
        for class_name, namespace, framework, methods in _scan_test_suites(
            source_text,
        ):
            suite_id = _test_suite_id(namespace, class_name)
            label = (
                f"{namespace}.{class_name}" if namespace else class_name
            )
            nodes[suite_id] = {
                "type": "test-suite",
                "label": label,
                "props": {
                    "file": rel_path,
                    "class_name": class_name,
                    "namespace": namespace,
                    "test_framework": framework,
                    "methods": sorted(set(methods)),
                    "source_strategy": "csharp_test_framework",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["test"],
                    "language": "csharp",
                },
            }
            # ``file_id`` strips the suffix automatically; pass the
            # POSIX path verbatim so this id matches the one
            # ``test_peer`` emits for the same file (ADR 0046 join).
            file_nid = _canonical_file_id(rel_path)
            edges.append(
                {
                    "from": suite_id,
                    "to": file_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "csharp_test_framework",
                        "confidence": "definite",
                        "test_framework": framework,
                    },
                }
            )
            any_suite_emitted = True

        if any_suite_emitted:
            discovered_from.append(
                cs_file.parent.relative_to(root).as_posix() + "/"
            )

    seen: set[str] = set()
    deduped: list[str] = []
    for d in discovered_from:
        if d not in seen:
            seen.add(d)
            deduped.append(d)

    return StrategyResult(nodes, edges, deduped)


def _resolve_glob(root: Path, pattern: str) -> list[Path]:
    """Expand *pattern* against *root* deterministically (sorted)."""
    if "**" in pattern:
        return sorted(root.glob(pattern))
    parent = (root / pattern).parent
    if not parent.is_dir():
        return []
    return sorted(parent.glob(Path(pattern).name))


def _test_suite_id(namespace: str, class_name: str) -> str:
    """Return the canonical ``test-suite:<namespace>.<class>`` id.

    When *namespace* is empty, the prefix collapses to the bare class
    name so the id remains unique within the repository.
    """
    qualified = f"{namespace}.{class_name}" if namespace else class_name
    return f"test-suite:{qualified}"


def _scan_test_suites(
    source_text: str,
) -> Iterator[tuple[str, str, str, list[str]]]:
    """Yield ``(class_name, namespace, framework, methods)`` per test class.

    The parser walks class declarations in source order, locating the
    body of each class via brace balancing, and within that body scans
    methods whose attribute window contains a recognised marker. A
    class qualifies as a test class when (a) its own attributes carry
    ``[TestFixture]``/``[TestClass]``, or (b) at least one of its
    methods carries ``[Fact]``/``[Theory]``/``[Test]``/``[TestMethod]``.
    Mixed-framework classes adopt the *first* marker seen as the
    dominant framework label.
    """
    namespaces = namespace_spans(source_text)

    for class_match in CLASS_RE.finditer(source_text):
        class_name = class_match.group(1)
        class_decl_start = class_match.start()
        namespace = namespace_at(class_decl_start, namespaces)

        attrs_window_start = attribute_window_start(
            source_text, class_decl_start,
        )
        class_attrs = _attribute_names(
            source_text[attrs_window_start:class_decl_start]
        )

        body_start, body_end = class_body_range(
            source_text, class_match.end(),
        )
        if body_start is None or body_end is None:
            continue

        body = source_text[body_start:body_end]
        methods, method_framework = _scan_methods(body)

        class_framework = _detect_class_framework(class_attrs)
        framework = class_framework or method_framework
        if framework is None:
            continue
        if methods or class_framework is not None:
            yield (class_name, namespace, framework, methods)


def _scan_methods(body: str) -> tuple[list[str], str | None]:
    """Find method-level test markers within a class *body*.

    Returns ``(method_names, dominant_framework)``. The method-name list
    is emitted in source order; the dominant framework is the first
    recognised marker seen (xUnit ``[Fact]`` beats nothing, etc.).
    """
    methods: list[str] = []
    dominant: str | None = None
    for method_match in _METHOD_RE.finditer(body):
        method_name = method_match.group(1)
        if method_name in {"if", "for", "while", "switch", "return"}:
            continue
        attr_window_start = attribute_window_start(body, method_match.start())
        method_attrs = _attribute_names(body[attr_window_start:method_match.start()])
        for attr in method_attrs:
            if attr in _METHOD_MARKERS:
                methods.append(method_name)
                if dominant is None:
                    dominant = _METHOD_ATTRIBUTES[attr]
                break
    return methods, dominant


def _attribute_names(window: str) -> list[str]:
    """Return every attribute name inside the *window* text.

    ``[Fact, Trait("a", "b")]`` returns ``["Fact", "Trait"]`` because
    the regex matches each leading-bracket token. The window may span
    multiple lines; whitespace is irrelevant.
    """
    return _ATTRIBUTE_NAME_RE.findall(window)


def _detect_class_framework(attrs: list[str]) -> str | None:
    """Return the framework label implied by class *attrs*, or ``None``."""
    for attr in attrs:
        if attr in _CLASS_ATTRIBUTES:
            return _CLASS_ATTRIBUTES[attr]
    return None


def _detect_method_framework(attrs: list[str]) -> str | None:
    """Return the framework label implied by method *attrs*, or ``None``."""
    for attr in attrs:
        if attr in _METHOD_ATTRIBUTES:
            return _METHOD_ATTRIBUTES[attr]
    return None


__all__ = ["extract"]

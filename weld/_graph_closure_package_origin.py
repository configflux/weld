"""Origin classification for closure-synthesised package nodes (ADR 0042).

``weld.graph_closure._ensure_package_node`` mints a ``package:<lang>:<name>``
node every time ``_link_imports`` cannot resolve an ``imports_from`` entry
to a local file or module. Per ADR 0042 every emitted node must carry
``props.origin``; this module provides the single helper the closure
calls to pick the right tag.

The closure layer sees only the import name + language -- it has no
project metadata (``go.mod`` / ``pom.xml`` / ``Cargo.toml``) and no
filesystem context. That bounds the answer to **stdlib** (the name is
unambiguously a language standard-library / built-in member) vs
**external** (everything else): no project membership signal exists at
this layer. Richer per-language tagging continues to live in the
strategy-side helpers (e.g. :mod:`weld.strategies._python_origin`,
:mod:`weld.strategies._java_origin`), which run during discovery
before the closure pass.

Routing through this single helper keeps the closure-side stdlib
detection consistent with the strategy-side modules: the Python and Go
checks delegate to the canonical static lists those modules already
own, and the JDK / .NET / Rust roots use the same first-segment rule
their respective ``classify_*`` functions apply.
"""

from __future__ import annotations

from weld.strategies._go_origin import is_go_stdlib
from weld.strategies._python_origin import is_builtin_name, is_stdlib_module
from weld.strategies._rust_origin import RUST_STDLIB_CRATES

#: First-segment prefixes that always classify as JDK stdlib (mirrors the
#: same set used by :mod:`weld.strategies._java_origin`'s
#: ``_JAVA_STDLIB_ROOTS``). Re-declared here rather than imported so the
#: closure helper stays self-contained and does not depend on a Java
#: module's private name.
_JAVA_STDLIB_ROOTS: frozenset[str] = frozenset({"java", "javax", "jdk"})

#: First-segment namespace roots that always classify as .NET stdlib
#: (mirrors :mod:`weld.strategies._csharp_origin`'s ``_CSHARP_STDLIB_ROOTS``).
#: Matched case-insensitively because C# namespaces are case-insensitive
#: and :func:`weld._node_ids.package_id` lowercases the canonical slug.
_CSHARP_STDLIB_ROOTS: frozenset[str] = frozenset({"system", "microsoft"})


def origin_for_synthesised_package(name: str, language: str) -> str:
    """Return the ADR 0042 origin for a closure-synthesised package node.

    *language* is the closure's already-base-language string
    (``python`` / ``go`` / ``rust`` / ``java`` / ``csharp`` /
    ``typescript`` / ``cpp`` / ...; ``python_ros2`` and ``cpp_ros2``
    have already been folded into their base language by
    :func:`weld.graph_closure._base_language` before this helper is
    called).

    Returns ``"stdlib"`` only when the language has a static
    standard-library list and *name* is unambiguously a member; every
    other case returns ``"external"``. This matches the closure's
    pre-existing ``authority="external"`` semantics for unresolved
    imports while making the stdlib subset visible to consumers that
    rely on ``props.origin`` (the typed contract from ADR 0042) rather
    than on the legacy authority field.

    Note: the closure never emits ``unresolved`` or ``project`` package
    nodes by construction -- a project-local module would have resolved
    via :func:`weld.graph_closure._resolve_import` and a fully unknown
    name still gets a deterministic package id, so the answer is always
    one of ``{stdlib, external}``.
    """
    if not name:
        return "external"

    if language == "python":
        # Python rule: top-level name is either a built-in (e.g.
        # ``builtins``, ``print``) or its first dotted segment lives in
        # ``sys.stdlib_module_names`` (covers ``collections.abc``,
        # ``importlib.util``, ``__future__``, etc.).
        if is_stdlib_module(name) or is_builtin_name(name):
            return "stdlib"
        return "external"

    if language == "go":
        # Go rule: exact match against the static Go stdlib package set.
        # ``net/http`` and ``encoding/json`` are stdlib; anything else
        # (``github.com/foo/bar``, ``go.uber.org/zap``) is external.
        return "stdlib" if is_go_stdlib(name) else "external"

    if language == "rust":
        # Rust rule: leading crate segment in
        # ``{std, core, alloc, proc_macro}`` classifies as stdlib. The
        # closure already sees the use-path with ``crate::`` / ``self::``
        # prefixes stripped because those are project-local and would
        # have resolved before reaching ``_ensure_package_node``.
        head = name.split("::", 1)[0].split(".", 1)[0]
        return "stdlib" if head in RUST_STDLIB_CRATES else "external"

    if language == "java":
        # Java rule: first dotted segment is the JDK stdlib root marker
        # (``java`` / ``javax`` / ``jdk``). After
        # :func:`weld.graph_closure._external_package_name` has stripped
        # the trailing class name, ``java.util.List`` arrives here as
        # ``java.util`` and classifies as stdlib.
        head = name.split(".", 1)[0]
        return "stdlib" if head in _JAVA_STDLIB_ROOTS else "external"

    if language == "csharp":
        # C# rule: first dotted segment in ``{System, Microsoft}``,
        # case-insensitive. The closure passes the raw input name (the
        # canonical-slug lowercase happens at the node-id layer, not on
        # the props ``name`` field), so we cast to lower for the test.
        head = name.split(".", 1)[0].lower()
        return "stdlib" if head in _CSHARP_STDLIB_ROOTS else "external"

    # TypeScript/JavaScript, C++, and any future language without a
    # closure-side stdlib list fall through to ``external``. TS/JS
    # built-in globals live behind the tree-sitter call-graph layer
    # (``weld.strategies._language_origin.JS_BUILTIN_GLOBALS``), not in
    # import paths; C++ stdlib detection needs the cpp_resolver's
    # system-include-root context which is not available here.
    return "external"


__all__ = ["origin_for_synthesised_package"]

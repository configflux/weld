"""Origin classification helpers for non-Python, non-C++ language strategies.

ADR 0042 (graph-node-origin) defines a four-way taxonomy
(``project`` / ``stdlib`` / ``external`` / ``unresolved``) that every
``symbol`` / ``file`` / ``module`` / ``package`` node must declare in
``props.origin``. Python, C++, Go, Rust, and C# have dedicated helper
modules (:mod:`weld.strategies._python_origin`,
:mod:`weld.strategies._cpp_origin`,
:mod:`weld.strategies._go_origin`,
:mod:`weld.strategies._rust_origin`, and
:mod:`weld.strategies._csharp_origin`); this module covers
TypeScript / JavaScript and Java at the call-graph sentinel layer
plus the shared Rust-qualified-callee predicate that the per-language
helper consumes.

Per ADR 0042 §"Per-language detection rules":

  * **TS / JS** — stdlib = built-in globals (``Array``, ``Math``,
    ``console``, ``Object``, ``Promise``, ``JSON``, ...). External and
    project resolution requires ``node_modules`` + ``package.json``
    walking which the bundled tree-sitter strategy does not perform
    yet, so unresolved sentinels that are not built-in globals fall
    back to ``unresolved``.
  * **Rust** — at the *call-graph sentinel* layer, stdlib = qualified
    callees that start with ``std::``, ``core::``, or ``alloc::`` (see
    :func:`is_rust_std_callee`). Bare-name fallbacks (``Vec``,
    ``println``) are intentionally not classified stdlib here. At the
    *import* layer the dedicated :mod:`weld.strategies._rust_origin`
    helper classifies a use-path against the project's ``Cargo.toml``
    package name and dependency tables, matching the Go split.
  * **Go** — at the *import* layer the dedicated
    :mod:`weld.strategies._go_origin` helper classifies an import path
    against the project's ``go.mod`` module path and the static Go
    standard-library set. At the *call-graph sentinel* layer the
    tree-sitter capture is still the bare leaf identifier
    (``Println``, not ``fmt.Println``), so sentinel dispatch here
    intentionally returns ``unresolved`` — the richer signal lives at
    the import layer, not the call layer.
  * **Java / C#** — the call-graph capture only yields the member-call
    leaf identifier (``println``, ``WriteLine``, ``ToString``) so the
    strategy cannot distinguish stdlib from project from external at
    this layer. Sentinels default to ``unresolved``. Richer
    package-layer classification ships in
    :mod:`weld.strategies._java_origin` (``java.*``/``javax.*``/``jdk.*``
    + Maven ``pom.xml``) and :mod:`weld.strategies._csharp_origin`
    (``System.*``/``Microsoft.*`` + ``.csproj`` ``PackageReference``).

The helpers are pure: no I/O, no module imports beyond the standard
library, and no logging.
"""

from __future__ import annotations

from typing import Any

#: Common JavaScript / TypeScript built-in globals that resolve via the
#: implicit lexical environment (no ``import`` required). Sourced from
#: the ECMAScript-2023 global object plus the runtime globals every
#: browser and Node.js process exposes (``console``, ``Promise``,
#: ``setTimeout``...). The list is deliberately conservative -- if a
#: name might be ambiguous we omit it so the classifier never
#: false-claims ``stdlib`` for a project-local variable.
JS_BUILTIN_GLOBALS: frozenset[str] = frozenset(
    {
        # Core constructors / value types
        "Array",
        "ArrayBuffer",
        "BigInt",
        "Boolean",
        "DataView",
        "Date",
        "Error",
        "EvalError",
        "Float32Array",
        "Float64Array",
        "Function",
        "Int8Array",
        "Int16Array",
        "Int32Array",
        "Map",
        "Number",
        "Object",
        "Promise",
        "Proxy",
        "RangeError",
        "ReferenceError",
        "RegExp",
        "Set",
        "String",
        "Symbol",
        "SyntaxError",
        "TypeError",
        "URIError",
        "Uint8Array",
        "Uint8ClampedArray",
        "Uint16Array",
        "Uint32Array",
        "WeakMap",
        "WeakSet",
        # Namespaces / namespace-like globals
        "JSON",
        "Math",
        "Reflect",
        "Intl",
        "Atomics",
        # Common runtime / host globals (browser + node)
        "console",
        "globalThis",
        "queueMicrotask",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "setImmediate",
        "clearImmediate",
        "process",
        # Common functions
        "isNaN",
        "isFinite",
        "parseFloat",
        "parseInt",
        "encodeURI",
        "encodeURIComponent",
        "decodeURI",
        "decodeURIComponent",
    }
)

#: Rust standard-library / core / alloc qualifier prefixes. A callee
#: captured by the tree-sitter Rust ``calls`` query as ``std::println``
#: or ``core::mem::swap`` is unambiguously stdlib. Bare identifiers
#: (``println!``, ``Vec``) are not enough on their own — they could
#: easily be project-local — so the helper looks for the prefix.
RUST_STDLIB_PREFIXES: tuple[str, ...] = (
    "std::",
    "core::",
    "alloc::",
)


def is_js_builtin(name: str) -> bool:
    """Return True if *name* matches a JS/TS built-in global.

    The match is exact (no dotted-path traversal). Member access like
    ``Math.max`` arrives at this helper as the bare ``max`` capture
    rather than the receiver, so this predicate intentionally only
    catches calls whose function expression is the bare global itself
    (``Array(...)``, ``parseInt(x)``).
    """
    if not name:
        return False
    return name in JS_BUILTIN_GLOBALS


def is_rust_std_callee(name: str) -> bool:
    """Return True if a Rust callee is qualified to ``std``/``core``/``alloc``.

    Accepts both the bare ``std::foo`` form and the absolute-path form
    ``::std::foo`` (Rust allows leading ``::`` to anchor at the crate
    root).
    """
    if not name:
        return False
    if name.startswith("::"):
        name = name[2:]
    return any(name.startswith(prefix) for prefix in RUST_STDLIB_PREFIXES)


def origin_for_callgraph_sentinel(language: str, callee: str) -> str:
    """Return the origin tag for a tree-sitter call-graph sentinel.

    Args:
        language: The strategy language (``"typescript"``, ``"javascript"``,
            ``"go"``, ``"rust"``, ``"java"``, ``"csharp"``, ...).
        callee: The captured callee identifier (already decoded to text).

    Returns:
        ``"stdlib"`` when the callee is unambiguously a language built-in
        per ADR 0042; ``"unresolved"`` otherwise. The function is total:
        unknown languages and empty callees both yield ``"unresolved"``.
    """
    if not callee:
        return "unresolved"
    if language in ("typescript", "javascript", "tsx", "jsx"):
        return "stdlib" if is_js_builtin(callee) else "unresolved"
    if language == "rust":
        return "stdlib" if is_rust_std_callee(callee) else "unresolved"
    # go / java / csharp / others: not enough signal at the call-graph
    # layer (the capture is the leaf method name only). Default to
    # unresolved; richer per-language detection is a follow-up.
    return "unresolved"


def project_origin_props(props: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``origin="project"`` on *props* and return it.

    Convenience used by call sites that emit project-glob nodes
    (definitions, file-caller synthetics, file-type nodes); writing
    through the helper keeps the call sites readable and centralises
    the constant.
    """
    props["origin"] = "project"
    return props


__all__ = [
    "JS_BUILTIN_GLOBALS",
    "RUST_STDLIB_PREFIXES",
    "is_js_builtin",
    "is_rust_std_callee",
    "origin_for_callgraph_sentinel",
    "project_origin_props",
]

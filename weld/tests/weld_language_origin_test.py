"""Unit tests for ADR 0042 language-origin helpers.

Covers the pure helpers in :mod:`weld.strategies._language_origin` --
the JS built-in classifier, the Rust ``std::``/``core::``/``alloc::``
classifier, and the per-language sentinel dispatch. Strategy-level
integration (call-graph extraction, file-node origin, package nodes)
lives in ``weld_language_origin_integration_test``.

The helpers under test are pure: no I/O, no module imports beyond the
standard library, so the unit tests do not need any mocking.
"""

from __future__ import annotations

import unittest


from weld.strategies._language_origin import (  # noqa: E402
    JS_BUILTIN_GLOBALS,
    RUST_STDLIB_PREFIXES,
    is_js_builtin,
    is_rust_std_callee,
    origin_for_callgraph_sentinel,
    project_origin_props,
)


# ---------------------------------------------------------------------------
# JS / TS helpers
# ---------------------------------------------------------------------------


class IsJsBuiltinTest(unittest.TestCase):
    """``is_js_builtin`` covers the documented ECMAScript/runtime globals."""

    def test_constructor_globals(self) -> None:
        for name in ("Array", "Object", "String", "Number", "Boolean", "Promise"):
            self.assertTrue(
                is_js_builtin(name), f"expected {name!r} to be classified stdlib"
            )

    def test_namespace_globals(self) -> None:
        for name in ("Math", "JSON", "Reflect", "Intl", "Atomics"):
            self.assertTrue(is_js_builtin(name))

    def test_runtime_globals(self) -> None:
        for name in ("console", "setTimeout", "process", "globalThis"):
            self.assertTrue(is_js_builtin(name))

    def test_function_globals(self) -> None:
        for name in ("parseInt", "isNaN", "encodeURIComponent"):
            self.assertTrue(is_js_builtin(name))

    def test_non_builtin(self) -> None:
        for name in ("myHelper", "render", "useEffect", ""):
            self.assertFalse(
                is_js_builtin(name), f"did not expect {name!r} to be stdlib"
            )

    def test_member_property_not_classified(self) -> None:
        # Member accesses arrive as the leaf identifier (``max``), not
        # the dotted form. ``max`` alone is *not* a global, so the
        # helper must say no.
        self.assertFalse(is_js_builtin("max"))
        self.assertFalse(is_js_builtin("Math.max"))


# ---------------------------------------------------------------------------
# Rust helpers
# ---------------------------------------------------------------------------


class IsRustStdCalleeTest(unittest.TestCase):
    """``is_rust_std_callee`` matches qualified ``std/core/alloc`` paths."""

    def test_std_prefix(self) -> None:
        self.assertTrue(is_rust_std_callee("std::println"))
        self.assertTrue(is_rust_std_callee("std::vec::Vec::new"))

    def test_core_prefix(self) -> None:
        self.assertTrue(is_rust_std_callee("core::mem::swap"))

    def test_alloc_prefix(self) -> None:
        self.assertTrue(is_rust_std_callee("alloc::boxed::Box::new"))

    def test_absolute_path_anchor(self) -> None:
        self.assertTrue(is_rust_std_callee("::std::println"))
        self.assertTrue(is_rust_std_callee("::core::mem::swap"))

    def test_bare_identifier_rejected(self) -> None:
        # Bare identifiers cannot be classified without crate-root
        # resolution. ``Vec`` is part of the prelude but the strategy
        # cannot prove that from the call site.
        for name in ("println", "Vec", "Box", "Result", "Option", ""):
            self.assertFalse(is_rust_std_callee(name))

    def test_other_namespace_rejected(self) -> None:
        # A custom crate or a third-party namespace must not collide
        # with the std/core/alloc prefix list.
        self.assertFalse(is_rust_std_callee("serde::Deserialize"))
        self.assertFalse(is_rust_std_callee("tokio::spawn"))


# ---------------------------------------------------------------------------
# Sentinel dispatch
# ---------------------------------------------------------------------------


class OriginForCallgraphSentinelTest(unittest.TestCase):
    """The dispatch helper returns the right origin per language."""

    def test_typescript_builtin(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("typescript", "Array"), "stdlib",
        )

    def test_typescript_unknown(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("typescript", "myHelper"), "unresolved",
        )

    def test_javascript_builtin(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("javascript", "Math"), "stdlib",
        )

    def test_tsx_builtin(self) -> None:
        # ``tsx`` is the variant key the typescript strategy uses for
        # JSX files; it must follow the same rule set.
        self.assertEqual(
            origin_for_callgraph_sentinel("tsx", "console"), "stdlib",
        )

    def test_rust_qualified(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("rust", "std::println"), "stdlib",
        )

    def test_rust_bare(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("rust", "println"), "unresolved",
        )

    def test_go_unresolved(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("go", "Println"), "unresolved",
        )

    def test_java_unresolved(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("java", "println"), "unresolved",
        )

    def test_csharp_unresolved(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("csharp", "WriteLine"), "unresolved",
        )

    def test_empty_callee(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("typescript", ""), "unresolved",
        )

    def test_unknown_language(self) -> None:
        self.assertEqual(
            origin_for_callgraph_sentinel("kotlin", "anything"), "unresolved",
        )


# ---------------------------------------------------------------------------
# Project-origin convenience
# ---------------------------------------------------------------------------


class ProjectOriginPropsTest(unittest.TestCase):
    def test_stamps_origin(self) -> None:
        props: dict = {"file": "foo.ts"}
        self.assertIs(project_origin_props(props), props)
        self.assertEqual(props["origin"], "project")

    def test_overwrites_existing(self) -> None:
        props: dict = {"origin": "unresolved"}
        project_origin_props(props)
        self.assertEqual(props["origin"], "project")


# ---------------------------------------------------------------------------
# Module-level exports stay in sync with the public surface
# ---------------------------------------------------------------------------


class ModuleSurfaceTest(unittest.TestCase):
    def test_js_builtins_is_frozenset(self) -> None:
        self.assertIsInstance(JS_BUILTIN_GLOBALS, frozenset)
        self.assertGreater(len(JS_BUILTIN_GLOBALS), 20)

    def test_rust_prefixes_complete(self) -> None:
        self.assertEqual(
            set(RUST_STDLIB_PREFIXES), {"std::", "core::", "alloc::"},
        )


if __name__ == "__main__":
    unittest.main()

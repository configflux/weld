"""Tests for the ``cpp_cmake`` general-purpose CMake strategy (ADR 0057).

Covers the lexer, the variable-scope helper, the per-call handlers,
the ROS2 dispatch rule, and the full integration with the
``cpp_cmake_project`` fixture.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path


from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies import cpp_cmake  # noqa: E402
from weld.strategies._cmake_lexer import (  # noqa: E402
    iter_calls,
    strip_comments,
    tokenize_args,
)
from weld.strategies._cmake_vars import (  # noqa: E402
    apply_set,
    contains_unresolved,
    expand,
    is_resolvable,
)


# ---------------------------------------------------------------------------
# Lexer unit tests
# ---------------------------------------------------------------------------


class TokenizeArgsTest(unittest.TestCase):
    def test_basic_split(self) -> None:
        self.assertEqual(tokenize_args("a b c"), ["a", "b", "c"])

    def test_quoted_token_preserves_spaces(self) -> None:
        self.assertEqual(
            tokenize_args('a "b c" d'), ["a", "b c", "d"]
        )

    def test_variable_reference_kept_intact(self) -> None:
        self.assertEqual(
            tokenize_args('${VAR} "v 2"'), ["${VAR}", "v 2"]
        )

    def test_empty_body(self) -> None:
        self.assertEqual(tokenize_args(""), [])

    def test_quote_escape(self) -> None:
        self.assertEqual(
            tokenize_args('"a \\"b\\" c"'), ['a "b" c']
        )

    def test_quoted_concatenates_with_unquoted_prefix(self) -> None:
        # ``target_compile_definitions`` shapes like ``FOO="x"`` must
        # produce a single token, not split into ``FOO=`` and ``x``.
        self.assertEqual(
            tokenize_args('FOO="bar"'), ['FOO=bar']
        )
        self.assertEqual(
            tokenize_args('FOO="bar" BAZ=qux'), ['FOO=bar', 'BAZ=qux']
        )


class IterCallsTest(unittest.TestCase):
    def test_emits_command_and_args(self) -> None:
        text = "add_executable(app src/main.cpp)\n"
        calls = list(iter_calls(text))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].command, "add_executable")
        self.assertEqual(calls[0].args, ["app", "src/main.cpp"])

    def test_handles_nested_parens(self) -> None:
        text = "add_test(NAME foo COMMAND foo $<TARGET_FILE:bar>)\n"
        calls = list(iter_calls(text))
        self.assertEqual(len(calls), 1)
        self.assertIn("$<TARGET_FILE:bar>", calls[0].args)

    def test_multiline_call(self) -> None:
        text = "add_library(\n  alpha STATIC\n  a.cpp\n  b.cpp\n)\n"
        calls = list(iter_calls(text))
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0].args, ["alpha", "STATIC", "a.cpp", "b.cpp"]
        )

    def test_strip_comments_removes_line_comments(self) -> None:
        text = "find_package(fmt) # comment\n# stray\n"
        cleaned = strip_comments(text)
        self.assertNotIn("comment", cleaned)
        self.assertNotIn("stray", cleaned)


# ---------------------------------------------------------------------------
# Variable-scope helper
# ---------------------------------------------------------------------------


class CMakeVarScopeTest(unittest.TestCase):
    def test_set_and_expand(self) -> None:
        scope: dict[str, str] = {}
        apply_set(["VAR", "hello"], scope)
        self.assertEqual(expand("${VAR}", scope), "hello")

    def test_set_multivalue(self) -> None:
        scope: dict[str, str] = {}
        apply_set(["FILES", "a.cpp", "b.cpp"], scope)
        self.assertEqual(expand("${FILES}", scope), "a.cpp;b.cpp")

    def test_set_clears_when_empty(self) -> None:
        scope: dict[str, str] = {"VAR": "value"}
        apply_set(["VAR"], scope)
        self.assertNotIn("VAR", scope)

    def test_unknown_variable_left_unchanged(self) -> None:
        self.assertEqual(expand("${MISSING}", {}), "${MISSING}")
        self.assertTrue(contains_unresolved("${MISSING}", {}))

    def test_self_reference_does_not_loop(self) -> None:
        scope: dict[str, str] = {"A": "${A}"}
        # Should terminate via the fixed-point iteration cap.
        out = expand("${A}", scope)
        self.assertEqual(out, "${A}")

    def test_genex_marked_unresolvable(self) -> None:
        self.assertFalse(is_resolvable("$<TARGET_FILE:foo>"))


# ---------------------------------------------------------------------------
# Integration: extract() on the reference fixture
# ---------------------------------------------------------------------------


_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "cpp_cmake_project"
)


class CmakeExtractIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = cpp_cmake.extract(
            _FIXTURE.parent, {"glob": "cpp_cmake_project/CMakeLists.txt"}, {},
        )

    def test_project_node_minted(self) -> None:
        self.assertIn("package:cpp:example_app", self.result.nodes)
        project = self.result.nodes["package:cpp:example_app"]
        self.assertEqual(project["props"]["build_system"], "cmake")

    def test_build_target_nodes_emitted(self) -> None:
        for target in ("alpha", "beta", "example", "diag"):
            nid = f"build-target:cmake:example_app:{target}"
            self.assertIn(nid, self.result.nodes, f"missing {nid}")
            node = self.result.nodes[nid]
            self.assertEqual(node["type"], "build-target")
            self.assertEqual(node["props"]["target_name"], target)

    def test_contains_edges_emitted_for_sources(self) -> None:
        rels = {
            (e["from"], e["to"])
            for e in self.result.edges
            if e["type"] == "contains"
        }
        # ``alpha`` library should contain its sources.
        self.assertIn(
            (
                "build-target:cmake:example_app:alpha",
                "file:cpp_cmake_project/lib_alpha/alpha",
            ),
            rels,
        )
        # ``example`` exe should resolve the ${APP_SOURCES} variable.
        self.assertIn(
            (
                "build-target:cmake:example_app:example",
                "file:cpp_cmake_project/src/main",
            ),
            rels,
        )
        self.assertIn(
            (
                "build-target:cmake:example_app:example",
                "file:cpp_cmake_project/src/runner",
            ),
            rels,
        )

    def test_internal_depends_on_emitted(self) -> None:
        # beta depends on alpha (target_link_libraries).
        rels = {
            (e["from"], e["to"], e["props"].get("kind"))
            for e in self.result.edges
            if e["type"] == "depends_on"
        }
        self.assertIn(
            (
                "build-target:cmake:example_app:beta",
                "build-target:cmake:example_app:alpha",
                "target_link_libraries",
            ),
            rels,
        )

    def test_external_depends_on_emitted(self) -> None:
        # example -> fmt (external lib).
        external = [
            e for e in self.result.edges
            if e["from"] == "build-target:cmake:example_app:example"
            and e["type"] == "depends_on"
            and e["to"] == "package:cpp:fmt"
        ]
        self.assertEqual(len(external), 1)

    def test_find_package_emits_definite_edge(self) -> None:
        finds = [
            e for e in self.result.edges
            if e["from"] == "package:cpp:example_app"
            and e["type"] == "depends_on"
            and e["props"].get("kind") == "find_package"
        ]
        targets = {e["to"] for e in finds}
        self.assertIn("package:cpp:fmt", targets)
        self.assertIn("package:cpp:boost", targets)

    def test_find_package_components_emit_inferred_edges(self) -> None:
        components = [
            e for e in self.result.edges
            if e["props"].get("kind") == "find_package_component"
        ]
        self.assertGreater(len(components), 0)
        for edge in components:
            self.assertEqual(edge["props"]["confidence"], "inferred")
        component_names = {e["props"]["component"] for e in components}
        self.assertEqual(component_names, {"system", "thread"})

    def test_unresolved_labels_captured(self) -> None:
        diag = self.result.nodes["build-target:cmake:example_app:diag"]
        unresolved = diag["props"].get("unresolved_labels", [])
        # The generator expression in diag's sources must surface here.
        self.assertTrue(
            any("$<" in label for label in unresolved),
            f"expected genex in unresolved_labels, got {unresolved!r}",
        )

    def test_include_dirs_recorded_as_prop(self) -> None:
        example = self.result.nodes["build-target:cmake:example_app:example"]
        self.assertIn(
            "include_directories", example["props"],
        )

    def test_compile_definitions_recorded(self) -> None:
        example = self.result.nodes["build-target:cmake:example_app:example"]
        defs = example["props"].get("compile_definitions", [])
        # The fixture defines ``EXAMPLE_VERSION="0.1.0"``; tokenisation
        # must keep that as one token rather than splitting on the quote
        # boundary (regression guard for the CMake-attached-quote lexer
        # fix).
        self.assertIn("EXAMPLE_VERSION=0.1.0", defs)

    def test_every_edge_has_confidence(self) -> None:
        for edge in self.result.edges:
            confidence = edge["props"].get("confidence")
            self.assertIn(
                confidence, CONFIDENCE_VALUES,
                f"edge {edge['from']} -> {edge['to']} ({edge['type']}) "
                f"missing valid confidence: {confidence!r}",
            )

    def test_every_edge_has_source_strategy(self) -> None:
        for edge in self.result.edges:
            self.assertEqual(
                edge["props"].get("source_strategy"), "cpp_cmake",
            )


# ---------------------------------------------------------------------------
# Dispatch: cpp_cmake defers to ros2_cmake when ROS2 markers are present
# ---------------------------------------------------------------------------


class CmakeRos2DispatchTest(unittest.TestCase):
    def test_skips_ros2_cmakelists(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        pkg = tmp / "pkg_ros"
        pkg.mkdir()
        (pkg / "CMakeLists.txt").write_text(
            textwrap.dedent(
                """\
                cmake_minimum_required(VERSION 3.8)
                project(ros_pkg)
                find_package(ament_cmake REQUIRED)
                find_package(rclcpp REQUIRED)
                add_executable(ros_app src/app.cpp)
                """,
            ),
            encoding="utf-8",
        )
        result = cpp_cmake.extract(
            tmp, {"glob": "**/CMakeLists.txt"}, {},
        )
        # The ROS2 CMakeLists must not appear in discovered_from.
        self.assertEqual(result.discovered_from, [])
        # And no nodes for ros_pkg should be minted.
        self.assertNotIn("package:cpp:ros_pkg", result.nodes)

    def test_commented_ament_cmake_does_not_trigger_ros2_dispatch(self) -> None:
        # A non-ROS project that mentions ament_cmake in a comment must
        # still be processed by cpp_cmake -- the dispatch rule is content,
        # not basename.
        tmp = Path(tempfile.mkdtemp())
        proj = tmp / "comment_only"
        proj.mkdir()
        (proj / "CMakeLists.txt").write_text(
            textwrap.dedent(
                """\
                project(comment_only)
                # Note: this project does NOT use find_package(ament_cmake REQUIRED)
                add_executable(commentapp src/app.cpp)
                """,
            ),
            encoding="utf-8",
        )
        result = cpp_cmake.extract(
            tmp, {"glob": "**/CMakeLists.txt"}, {},
        )
        self.assertIn("package:cpp:comment_only", result.nodes)
        self.assertIn(
            "build-target:cmake:comment_only:commentapp", result.nodes,
        )

    def test_processes_non_ros_cmakelists_alongside(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        ros_dir = tmp / "ros_app"
        plain_dir = tmp / "plain_app"
        ros_dir.mkdir()
        plain_dir.mkdir()
        (ros_dir / "CMakeLists.txt").write_text(
            textwrap.dedent(
                """\
                project(ros_app)
                find_package(ament_cmake REQUIRED)
                add_executable(rosx src/main.cpp)
                """,
            ),
            encoding="utf-8",
        )
        (plain_dir / "CMakeLists.txt").write_text(
            textwrap.dedent(
                """\
                project(plain_app)
                add_executable(plainx src/main.cpp)
                """,
            ),
            encoding="utf-8",
        )
        result = cpp_cmake.extract(
            tmp, {"glob": "**/CMakeLists.txt"}, {},
        )
        self.assertIn("package:cpp:plain_app", result.nodes)
        self.assertNotIn("package:cpp:ros_app", result.nodes)


if __name__ == "__main__":
    unittest.main()

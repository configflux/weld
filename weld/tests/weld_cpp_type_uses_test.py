"""ADR 0061 contract tests for the C++ ``type_uses`` query.

Three layers of coverage:

1. ``CppTypeUsesQueryShapeTest`` -- the cpp.yaml query string contains
   the promised tree-sitter node names. Pure string assertion; no
   grammar required.
2. ``CppTypeUsesStrategyStampTest`` -- the tree_sitter strategy stamps
   ``props.type_uses`` (sorted, deduped, omitted when empty) when the
   parser returns USE-site captures. Mocks the parser.
3. ``CppTypeUsesRealParseTest`` -- the investigation "no churn in
   cpp.yaml without a failing-test repro first" guardrail expressed as
   a concrete real-grammar test. Its Bazel target declares both wheels
   from ``requirements_lock.txt``, so under Bazel the grammars are
   present by construction and their absence is a dropped dep rather
   than an environment fact: the gate fails there instead of skipping.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock



# Read the umbrella's availability through weld.strategies rather than
# probing ``import tree_sitter`` directly, so this file decides on the same
# constant the strategy itself branches on -- including when a harness has
# blocked the import out from under both.
from weld.strategies.tree_sitter import TREE_SITTER_AVAILABLE  # noqa: E402

# The skip-or-fail-on-missing-grammar branch policy itself lives in
# tier_check_grammar_gate (bd 9txq consolidated three identical
# copies there; see that module's docstring for the placement rationale).
# This file keeps its own availability probe below -- it differs from the
# shared module's (bare ``import tree_sitter_cpp`` vs. going through
# weld.strategies._ts_parse) and that difference is deliberately out of
# this consolidation's scope.
import tier_check_grammar_gate as grammar_gate  # noqa: E402

_DECLARED_IN = "weld/tests/treesitter_tests.bzl"
_REQUIRED_LABELS = ("@pypi//tree_sitter", "@pypi//tree_sitter_cpp")


def _grammars_available() -> bool:
    if not TREE_SITTER_AVAILABLE:
        return False
    try:
        import tree_sitter_cpp  # noqa: F401
    except Exception:
        return False
    return True


def _skip_or_fail_without_grammars(case: unittest.TestCase) -> None:
    """Skip only where absence is legitimate; otherwise fail loudly.

    This target names ``@pypi//tree_sitter`` and ``@pypi//tree_sitter_cpp``
    in its deps, so under Bazel the wheels are in the runfiles or the build
    fails -- meaning a missing grammar there is wiring that regressed, not a
    host that lacks an optional extra. Skipping on that is how this guardrail
    sat inert on every public CI run since it was added (bd c42b), so under
    Bazel it fails instead. Outside Bazel, and where the caller declares a
    tree-sitter blocker of its own, absence is expected and skipping is the
    correct answer. The branching that decides which of those applies is
    ``tier_check_grammar_gate.skip_or_fail_without_grammar``; *case*
    is accepted for call-site stability but the shared helper raises
    directly rather than through it (see that function's docstring).
    """
    if _grammars_available():
        return
    grammar_gate.skip_or_fail_without_grammar(
        reason="tree-sitter or tree-sitter-cpp is not importable",
        labels=_REQUIRED_LABELS,
        declared_in=_DECLARED_IN,
    )


# Synthetic header that mirrors the nlohmann/json.hpp <-> json_pointer
# pattern: ``json_pointer`` is USED in parameter, return, friend,
# base-class, and template-arg positions but not DEFINED here. It is
# the smallest fixture that reproduces the real-world gap from
# include/nlohmann/json.hpp.
_FIXTURE_HEADER = textwrap.dedent(
    """\
    #pragma once
    #include "json_pointer.hpp"

    namespace ns {

    class basic_json {
    public:
      // return type via template_type
      json_pointer<basic_json> get_pointer();
      // parameter via template_type
      void apply(const json_pointer<basic_json>& p);
      // friend declaration of a template instantiation
      friend class json_pointer<basic_json>;
      // qualified-identifier parameter
      void multi(const Mixin& m, ns::OtherType o);
    };

    // base-class clause uses two USE sites
    class basic_json_ex : public json_pointer<basic_json>, public Mixin {};

    // free declaration whose return type uses a type the file does
    // not define -- the existing exports query catches `make_counter`
    // but type_uses must catch `Counter`.
    Counter make_counter(const Mixin& m);

    }
    """
)


class CppTypeUsesQueryShapeTest(unittest.TestCase):
    """The bundled cpp.yaml must declare the type_uses query and its
    string must reference each promised tree-sitter node name."""

    def test_type_uses_query_present(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("cpp")
        self.assertIn(
            "type_uses",
            queries,
            "cpp.yaml must declare a type_uses query (ADR 0061)",
        )

    def test_type_uses_query_targets_use_sites(self) -> None:
        """ADR 0061: type_uses must cover the USE-site shapes the task
        promises so headers that consume a type rank for it."""
        from weld.strategies.tree_sitter import load_language_queries

        type_uses = load_language_queries("cpp")["type_uses"]
        # template_type head (the X in X<...>)
        self.assertIn("template_type", type_uses)
        # parameter / return / free declaration types
        self.assertIn("parameter_declaration", type_uses)
        self.assertIn("field_declaration", type_uses)
        self.assertIn("declaration", type_uses)
        # base-class clause + friend declarations
        self.assertIn("base_class_clause", type_uses)
        self.assertIn("friend_declaration", type_uses)
        # qualified_identifier so ns::Foo stays joined
        self.assertIn("qualified_identifier", type_uses)


class CppTypeUsesStrategyStampTest(unittest.TestCase):
    """ADR 0061: tree_sitter strategy must stamp props.type_uses
    sorted+deduped, and omit it when empty."""

    def _make_root(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "x.cpp").write_text("// dummy\n")
        return root

    def test_extract_stamps_type_uses_sorted_dedup(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_root(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["basic_json"],
                         "classes": ["basic_json"],
                         "imports": [],
                         "type_uses": [
                             "json_pointer", "Mixin", "json_pointer",
                             "Counter", "ns::OtherType", "Mixin",
                         ],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cpp", "language": "cpp"},
                    context={},
                )
            node = next(
                n for n in result.nodes.values() if n["type"] == "file"
            )
            self.assertIn("type_uses", node["props"])
            self.assertEqual(
                node["props"]["type_uses"],
                ["Counter", "Mixin", "json_pointer", "ns::OtherType"],
            )

    def test_extract_omits_type_uses_when_empty(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_root(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Foo"],
                         "classes": [],
                         "imports": [],
                         "type_uses": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cpp", "language": "cpp"},
                    context={},
                )
            node = next(
                n for n in result.nodes.values() if n["type"] == "file"
            )
            self.assertNotIn("type_uses", node["props"])


class CppTypeUsesRealParseTest(unittest.TestCase):
    """Real-grammar repro for ADR 0061. This is the investigation
    "no churn in cpp.yaml without a failing-test repro first"
    guardrail expressed as a concrete test."""

    def setUp(self) -> None:
        _skip_or_fail_without_grammars(self)
        from weld.strategies._ts_parse import parse_file_symbols
        from weld.strategies.tree_sitter import load_language_queries

        self._queries = load_language_queries("cpp")
        self._parse = parse_file_symbols

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._fixture = Path(self._tmp.name) / "consumer.hpp"
        self._fixture.write_text(_FIXTURE_HEADER, encoding="utf-8")

    def test_type_uses_captures_all_use_site_shapes(self) -> None:
        """Parsing the synthetic header must surface every USE-only
        type the fixture mentions (json_pointer, Mixin, ns::OtherType,
        Counter)."""
        symbols = self._parse(self._fixture, "cpp", self._queries)
        type_uses = set(symbols.get("type_uses", []))

        # The headline gap: a header that only USES json_pointer must
        # surface it.
        self.assertIn(
            "json_pointer",
            type_uses,
            "json_pointer is used in parameter / return / friend / "
            "base-class / template-arg positions but not surfaced "
            "by the type_uses query (raw text count: "
            f"{_FIXTURE_HEADER.count('json_pointer')})",
        )
        # Plain type_identifier in a parameter position
        self.assertIn("Mixin", type_uses)
        # qualified_identifier parameter (ns::OtherType o)
        self.assertIn("ns::OtherType", type_uses)
        # Free-function return type
        self.assertIn("Counter", type_uses)

    def test_existing_exports_unchanged_by_type_uses_addition(self) -> None:
        """Adding the type_uses query must not remove any export the
        existing query already captured."""
        symbols = self._parse(self._fixture, "cpp", self._queries)
        exports = set(symbols.get("exports", []))
        # The fixture defines basic_json, basic_json_ex, ns; it also
        # declares get_pointer / apply / multi / make_counter.
        for expected in (
            "basic_json",
            "basic_json_ex",
            "ns",
            "get_pointer",
            "apply",
            "make_counter",
        ):
            self.assertIn(
                expected, exports, f"exports lost {expected!r} after adding type_uses"
            )

    def test_existing_classes_unchanged_by_type_uses_addition(self) -> None:
        symbols = self._parse(self._fixture, "cpp", self._queries)
        classes = set(symbols.get("classes", []))
        # basic_json and basic_json_ex are defined here; json_pointer
        # is NOT defined and must not appear in classes.
        self.assertIn("basic_json", classes)
        self.assertIn("basic_json_ex", classes)
        self.assertNotIn(
            "json_pointer",
            classes,
            "json_pointer is not defined here -- classes must not list it",
        )


class CppTypeUsesGrammarGateTest(unittest.TestCase):
    """Pin every branch of the skip-or-fail gate on every host.

    The branch that matters only fires where the grammars are missing --
    which, now that the target declares them, is the one host that cannot
    also prove the diagnostic still works. So drive the branches directly
    instead of waiting for such a host to appear.
    """

    def _run_gate(self, *, available: bool, env: dict[str, str]) -> None:
        with mock.patch(
            __name__ + "._grammars_available", return_value=available
        ), mock.patch.dict(os.environ, env, clear=True):
            _skip_or_fail_without_grammars(self)

    def test_returns_when_grammars_are_importable(self) -> None:
        # Not assertRaises-free by accident: a stray SkipTest here would
        # report this case as skipped, i.e. green, which is the very
        # outcome under test. Convert it to a failure.
        try:
            self._run_gate(available=True, env={})
        except unittest.SkipTest as skipped:
            raise AssertionError(
                f"gate skipped with grammars present: {skipped}"
            ) from skipped

    def test_skips_when_a_blocker_is_deliberately_active(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            self._run_gate(
                available=False,
                env={
                    "WELD_HERMETIC_BLOCK_TREE_SITTER": "1",
                    "TEST_SRCDIR": "/somewhere",
                },
            )

    def test_skips_when_not_running_under_bazel(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            self._run_gate(available=False, env={})

    def test_fails_under_bazel_because_the_deps_promise_the_grammars(
        self,
    ) -> None:
        try:
            self._run_gate(available=False, env={"TEST_SRCDIR": "/somewhere"})
        except unittest.SkipTest as skipped:
            raise AssertionError(
                f"gate skipped under Bazel instead of failing: {skipped}"
            ) from skipped
        except AssertionError as failure:
            message = str(failure)
        else:
            self.fail("gate neither skipped nor failed with grammars missing")
        self.assertIn("treesitter_tests.bzl", message)
        self.assertIn(sys.executable, message)


if __name__ == "__main__":
    unittest.main()

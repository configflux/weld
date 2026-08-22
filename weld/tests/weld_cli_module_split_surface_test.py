"""Import-surface contract for the split CLI dispatcher modules.

``weld/_graph_cli.py`` and ``weld/arch_lint.py`` are both split across
sibling modules to stay under the 400-line cap. Splitting a module is
mechanical, but it has one silent failure mode that no behavioural test
catches: a name that other modules import from the original module stops
resolving there, or the two dispatchers drift onto separate copies of the
terminal-sanitisation chokepoint.

This module pins that surface directly:

* the cross-module names ``weld._graph_cli`` and ``weld.arch_lint`` owe
  their importers (``brief`` / ``trace`` / ``impact_cli`` / ``diff`` /
  ``enrich`` / ``cli`` and the test suite),
* the injected-writer contract ``run_federated_cli`` is called under,
* the single-home invariant for the emit writers -- both the single-repo
  and the federated dispatcher must route through the *same* function
  objects, so sanitising once sanitises everywhere.

Importing the split modules under a ``//weld:runtime`` dep also doubles as
the Bazel wiring check: a module missing from ``weld/runtime_srcs.bzl``
never reaches the target and fails this import inside the sandbox.
"""

from __future__ import annotations

import inspect
import io
import pathlib
import unittest
from contextlib import redirect_stdout


class GraphCliImportSurfaceTest(unittest.TestCase):
    """Names other modules import from ``weld._graph_cli``."""

    def test_cross_module_names_resolve_on_graph_cli(self) -> None:
        """brief/trace/impact_cli/diff/enrich import these by this path."""
        import weld._graph_cli as graph_cli

        for name in (
            "main",
            "ensure_graph_exists",
            "missing_graph_message",
            "_build_retry_hint",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(graph_cli, name, None)),
                    f"weld._graph_cli.{name} must stay importable",
                )

    def test_retry_hint_builder_keeps_its_shape(self) -> None:
        """The quote/flag pattern five call sites depend on."""
        from weld._graph_cli import _build_retry_hint

        self.assertEqual(_build_retry_hint("diff"), "wd diff")
        self.assertEqual(
            _build_retry_hint("path", "a:b", "c:d"), 'wd path "a:b" "c:d"'
        )
        self.assertEqual(
            _build_retry_hint("enrich", node_id="entity:Store"),
            'wd enrich --node-id "entity:Store"',
        )

    def test_missing_graph_message_keeps_its_wording(self) -> None:
        """Onboarding docs and the MCP guard match these substrings."""
        from weld._graph_cli import missing_graph_message

        message = missing_graph_message('wd query "x"')
        self.assertIn("No Weld graph found.", message)
        self.assertIn("wd init", message)
        self.assertIn("wd discover", message)
        self.assertIn('wd query "x"', message)


class EmitWriterHomeTest(unittest.TestCase):
    """The emit writers are one chokepoint, not one copy per dispatcher."""

    def test_split_modules_import(self) -> None:
        """Also the Bazel check -- unlisted srcs never reach //weld:runtime."""
        import weld._graph_cli_emit  # noqa: F401
        import weld._graph_cli_single  # noqa: F401
        import weld.arch_lint_cli  # noqa: F401

    def test_graph_cli_routes_through_the_emit_module(self) -> None:
        import weld._graph_cli as graph_cli
        import weld._graph_cli_emit as emit_mod

        self.assertIs(graph_cli._emit, emit_mod._emit)
        self.assertIs(graph_cli._emit_node_lookup, emit_mod._emit_node_lookup)

    def test_single_repo_dispatcher_shares_the_same_writers(self) -> None:
        """One sanitisation chokepoint across both dispatch paths."""
        import weld._graph_cli_emit as emit_mod
        import weld._graph_cli_single as single

        self.assertIs(single._emit, emit_mod._emit)
        self.assertIs(single._emit_node_lookup, emit_mod._emit_node_lookup)
        self.assertIs(single._out, emit_mod._out)

    def test_emit_sanitises_the_text_path_but_not_json(self) -> None:
        """The behaviour that makes a shared chokepoint worth having."""
        from weld._graph_cli_emit import _emit

        class _Args:
            def __init__(self, as_json: bool) -> None:
                self.as_json = as_json

        text_buffer = io.StringIO()
        with redirect_stdout(text_buffer):
            _emit(_Args(False), {"x": 1}, lambda data: "before\x1b[31mafter\n")
        self.assertNotIn("\x1b", text_buffer.getvalue())

        json_buffer = io.StringIO()
        with redirect_stdout(json_buffer):
            _emit(_Args(True), {"x": 1}, lambda data: "unused")
        self.assertIn('"x": 1', json_buffer.getvalue())
        self.assertNotIn("unused", json_buffer.getvalue())


class FederatedInjectionContractTest(unittest.TestCase):
    """``run_federated_cli`` takes its writers injected, never back-imported."""

    def test_writers_are_required_keyword_only_params(self) -> None:
        from weld._graph_cli_federated import run_federated_cli

        params = inspect.signature(run_federated_cli).parameters
        for name in ("emit", "emit_node_lookup"):
            with self.subTest(name=name):
                self.assertIn(name, params)
                self.assertIs(params[name].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(params[name].default, inspect.Parameter.empty)

    def test_federated_module_does_not_back_import_the_dispatcher(self) -> None:
        """The whole point of the injection -- keep the edge one-way."""
        import weld._graph_cli_federated as federated

        source = pathlib.Path(federated.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from weld._graph_cli import", source)
        self.assertNotIn("import weld._graph_cli\n", source)


class ArchLintImportSurfaceTest(unittest.TestCase):
    """``weld.arch_lint`` stays the anchor its importers expect."""

    def test_public_surface_resolves(self) -> None:
        import weld.arch_lint as arch_lint

        for name in (
            "ARCH_LINT_VERSION",
            "Rule",
            "Violation",
            "available_rule_ids",
            "format_text",
            "lint",
            "main",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(arch_lint, name))
                self.assertIn(name, arch_lint.__all__)

    def test_violation_stays_importable_from_arch_lint(self) -> None:
        """Re-exported from weld._arch_lint_types; external callers rely on
        importing it from here (bd 5038-mx8sd, ADR 0130 disposition #6)."""
        from weld.arch_lint import Violation

        violation = Violation(rule="r", node_id="n", message="m")
        self.assertEqual(
            violation.to_dict(),
            {"rule": "r", "node_id": "n", "message": "m", "severity": "error"},
        )

    def test_main_delegates_to_the_cli_module_with_registry_injected(
        self,
    ) -> None:
        """``weld.arch_lint.main`` is the composition root (ADR 0130
        disposition #7, bd 5038-efr7z): it wraps ``arch_lint_cli.main``,
        injecting the live ``lint``/``available_rule_ids`` functions at call
        time, rather than being the same function object -- a bare re-export
        would require ``arch_lint_cli`` to import the registry back from
        here, recreating the very cycle this broke."""
        import weld.arch_lint as arch_lint
        import weld.arch_lint_cli as arch_lint_cli

        self.assertIsNot(arch_lint.main, arch_lint_cli.main)

        captured: dict = {}

        def fake_cli_main(argv, *, lint_fn, available_rule_ids_fn):
            captured["argv"] = argv
            captured["lint_fn"] = lint_fn
            captured["available_rule_ids_fn"] = available_rule_ids_fn
            return 0

        original = arch_lint_cli.main
        arch_lint_cli.main = fake_cli_main
        try:
            code = arch_lint.main(["--json"])
        finally:
            arch_lint_cli.main = original

        # Proves the wrapper resolves ``arch_lint_cli.main`` fresh at call
        # time (module-attribute lookup), not a name frozen at def time --
        # otherwise this monkeypatch would never be observed.
        self.assertEqual(code, 0)
        self.assertEqual(captured["argv"], ["--json"])
        self.assertIs(captured["lint_fn"], arch_lint.lint)
        self.assertIs(
            captured["available_rule_ids_fn"], arch_lint.available_rule_ids
        )


class ArchLintInjectionContractTest(unittest.TestCase):
    """``arch_lint_cli.main`` takes the registry injected, never imported
    back (ADR 0130 disposition #7, bd 5038-efr7z) -- the same required-
    keyword-only shape ``FederatedInjectionContractTest`` above pins for
    ``run_federated_cli``'s ``emit``/``emit_node_lookup``."""

    def test_registry_params_are_required_keyword_only(self) -> None:
        from weld.arch_lint_cli import main

        params = inspect.signature(main).parameters
        for name in ("lint_fn", "available_rule_ids_fn"):
            with self.subTest(name=name):
                self.assertIn(name, params)
                self.assertIs(params[name].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(params[name].default, inspect.Parameter.empty)

    def test_arch_lint_cli_does_not_back_import_arch_lint(self) -> None:
        """The whole point of the injection -- keep the edge one-way."""
        import weld.arch_lint_cli as arch_lint_cli

        source = pathlib.Path(arch_lint_cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from weld.arch_lint import", source)
        self.assertNotIn("from weld import arch_lint\n", source)
        self.assertNotIn("import weld.arch_lint\n", source)


class LineCountHeadroomTest(unittest.TestCase):
    """The reason for the split: keep real headroom, not a passing lint."""

    #: ``tools/lint_line_counts.py`` caps sources at 400 and both of these
    #: files reached exactly 400 -- lint-clean with zero headroom, so the
    #: next one-line edit forced an unrelated refactor onto whoever made
    #: it. Lint alone cannot catch that, because 400 passes.
    #:
    #: This is deliberately a danger-zone floor, not a freeze on today's
    #: sizes: these modules are meant to keep growing normally, and a
    #: threshold pinned just above their current line counts would fire on
    #: perfectly ordinary work. It only trips once a file is close enough
    #: to the cap to be back in the state this split undid.
    DANGER_ZONE = 385

    def test_split_files_keep_headroom(self) -> None:
        import weld._graph_cli as graph_cli
        import weld._graph_cli_emit as emit_mod
        import weld._graph_cli_single as single
        import weld.arch_lint as arch_lint
        import weld.arch_lint_cli as arch_lint_cli

        for module in (
            graph_cli,
            emit_mod,
            single,
            arch_lint,
            arch_lint_cli,
        ):
            with self.subTest(module=module.__name__):
                lines = len(
                    pathlib.Path(module.__file__).read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
                self.assertLessEqual(
                    lines,
                    self.DANGER_ZONE,
                    f"{module.__name__} is {lines} lines -- back inside "
                    f"the 400-line cap's danger zone. Split a cohesive "
                    f"seam off it rather than landing at the cap and "
                    f"leaving the next edit to pay for it.",
                )


if __name__ == "__main__":
    unittest.main()

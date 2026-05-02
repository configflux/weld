"""Tests for the canonical ``wd graph`` namespace and unnamespaced aliases.

The CLI exposes every entry in the ``--help`` "Graph commands" section as
both ``wd graph X`` (canonical) and ``wd X`` (alias). These tests assert
that parity, the help-text alias rule statement, and that per-command
``--help`` output is unchanged across the two forms.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402

# Every command listed in the ``--help`` "Graph commands" section. The
# canonical form is ``wd graph <name>`` and ``wd <name>`` is the alias.
GRAPH_COMMANDS = (
    "stats",
    "communities",
    "validate",
    "validate-fragment",
    "list",
    "stale",
    "dump",
    "diff",
    "lint",
    "add-node",
    "add-edge",
    "rm-node",
    "rm-edge",
    "import",
)


def _write_graph(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "schema_version": 1,
                    "updated_at": "2026-04-25T00:00:00+00:00",
                },
                "nodes": {
                    "file:src/auth.py": {
                        "type": "file",
                        "label": "auth.py",
                        "props": {"file": "src/auth.py"},
                    }
                },
                "edges": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_cli(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(args)
    if rc not in (None, 0):
        raise AssertionError(f"unexpected return code {rc} for {args!r}")
    return buf.getvalue()


def _capture_help(args: list[str]) -> str:
    """Run ``args + ['--help']`` and capture stdout.

    Argparse calls :func:`sys.exit` after printing help, so we tolerate
    SystemExit(0) and only fail on a non-zero exit.
    """
    buf = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(err):
            cli_main(args + ["--help"])
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise AssertionError(
                f"--help for {args!r} exited with {exc.code}; "
                f"stderr={err.getvalue()!r}"
            ) from exc
    return buf.getvalue()


class GraphNamespaceTest(unittest.TestCase):
    def test_graph_stats_matches_stats_alias(self) -> None:
        # Per ADR 0040 the CLI defaults to human text; this test compares
        # the structured envelope, so both invocations pass --json.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            canonical = json.loads(
                _run_cli(["graph", "--root", str(root), "stats", "--json"])
            )
            alias = json.loads(
                _run_cli(["--root", str(root), "stats", "--json"])
            )

            self.assertEqual(canonical["total_nodes"], 1)
            self.assertEqual(canonical, alias)

    def test_graph_validate_matches_validate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            canonical = json.loads(
                _run_cli(["graph", "--root", str(root), "validate"])
            )
            alias = json.loads(_run_cli(["--root", str(root), "validate"]))

            self.assertEqual(canonical, {"valid": True, "errors": []})
            self.assertEqual(alias, canonical)


class GraphHelpTextTest(unittest.TestCase):
    def test_top_level_help_states_alias_rule(self) -> None:
        text = _run_cli(["--help"])

        self.assertIn(
            "`wd graph X` is an alias for `wd X` for graph operations",
            text,
        )

    def test_top_level_help_lists_each_graph_command_once(self) -> None:
        text = _run_cli(["--help"])
        graph_section = text.split("Graph commands", 1)[1]
        # Match a help line "  graph <name> " (trailing space) so that
        # ``graph validate`` does not also match ``graph validate-fragment``.
        # Hyphenated names are anchored on a trailing space the same way.
        for name in GRAPH_COMMANDS:
            token = f"  graph {name} "
            occurrences = graph_section.count(token)
            self.assertEqual(
                occurrences,
                1,
                f"expected exactly one help line starting {token!r}, "
                f"got {occurrences}",
            )

    def test_top_level_help_does_not_list_alias_for_lines(self) -> None:
        # Each Graph command is listed exactly once in canonical form;
        # the bare-name "Alias for" lines are gone (rule is stated once
        # at the top of the section).
        text = _run_cli(["--help"])
        graph_section = text.split("Graph commands", 1)[1]
        self.assertNotIn("Alias for `wd graph", graph_section)

    def test_top_level_help_keeps_graph_in_core(self) -> None:
        text = _run_cli(["--help"])
        # ``graph`` itself stays in Core commands as the namespace introducer.
        self.assertIn("graph          Canonical graph namespace", text)


class GraphCommandParityTest(unittest.TestCase):
    """Every Graph command must resolve under both forms with --help."""

    def _assert_help_runs(self, args: list[str]) -> None:
        text = _capture_help(args)
        self.assertTrue(
            text.strip(),
            f"--help for {args!r} produced no output",
        )

    def test_namespaced_help_runs_for_every_graph_command(self) -> None:
        for name in GRAPH_COMMANDS:
            with self.subTest(form="namespaced", command=name):
                self._assert_help_runs(["graph", name])

    def test_unnamespaced_help_runs_for_every_graph_command(self) -> None:
        for name in GRAPH_COMMANDS:
            with self.subTest(form="unnamespaced", command=name):
                self._assert_help_runs([name])

    def test_per_command_help_unchanged_across_forms(self) -> None:
        # ``wd <cmd> --help`` and ``wd graph <cmd> --help`` should describe
        # the same flags. The usage line legitimately differs because the
        # prog name is ``wd graph stats`` vs ``wd stats``; argparse also
        # wraps continuation lines based on prog-name length, so we
        # collapse runs of whitespace before comparing the rest.
        import re

        def _normalize(body: str) -> str:
            return re.sub(r"[ \t]+", " ", body).strip()

        for name in (
            "stats",
            "communities",
            "validate",
            "list",
            "stale",
            "dump",
        ):
            with self.subTest(command=name):
                ns_text = _capture_help(["graph", name])
                bare_text = _capture_help([name])
                ns_body = "\n".join(ns_text.splitlines()[1:])
                bare_body = "\n".join(bare_text.splitlines()[1:])
                self.assertEqual(
                    _normalize(ns_body),
                    _normalize(bare_body),
                    f"per-command help for {name!r} differs between "
                    f"namespaced and unnamespaced forms",
                )


class GraphDiffLintTest(unittest.TestCase):
    """``wd graph diff`` and ``wd graph lint`` resolve to their dispatchers."""

    def test_graph_diff_help_resolves(self) -> None:
        text = _capture_help(["graph", "diff"])
        self.assertTrue(text.strip())

    def test_graph_lint_help_resolves(self) -> None:
        text = _capture_help(["graph", "lint"])
        self.assertTrue(text.strip())

    @staticmethod
    def _normalize_body(text: str) -> str:
        import re

        body = "\n".join(text.splitlines()[1:])
        return re.sub(r"[ \t]+", " ", body).strip()

    def test_diff_help_unchanged_under_graph_form(self) -> None:
        bare = _capture_help(["diff"])
        namespaced = _capture_help(["graph", "diff"])
        # Drop the usage line (prog name differs) and normalize wrapping.
        self.assertEqual(
            self._normalize_body(bare),
            self._normalize_body(namespaced),
        )

    def test_lint_help_unchanged_under_graph_form(self) -> None:
        bare = _capture_help(["lint"])
        namespaced = _capture_help(["graph", "lint"])
        self.assertEqual(
            self._normalize_body(bare),
            self._normalize_body(namespaced),
        )


if __name__ == "__main__":
    unittest.main()

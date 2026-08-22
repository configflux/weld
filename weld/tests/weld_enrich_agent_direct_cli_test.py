"""CLI-mode tests for ``wd enrich --agent-direct`` (ADR 0098).

The plan's *content* is held down by
``weld_enrich_agent_direct_test.py``; this file covers the mode as a
command: that it succeeds with no provider configured, never resolves
one even when the environment names it, is permitted under ``--safe``,
leaves the graph untouched, and refuses contradictory flag combinations
instead of silently ignoring them.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from weld.enrich import main as enrich_main
from weld.tests._enrich_agent_direct_test_helpers import nodes, write_graph


class CliModeTest(unittest.TestCase):
    """``wd enrich --agent-direct`` never touches a provider."""

    def _run(self, argv: list[str], env: dict | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env or {}, clear=False), \
                redirect_stdout(out), redirect_stderr(err):
            rc = enrich_main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_succeeds_with_no_provider_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, out, _ = self._run(
                ["--root", str(root), "--agent-direct"],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            self.assertEqual(rc, 0)
            self.assertIn("wd add-node", out)
            self.assertIn("entity:Store", out)

    def test_ignores_ambient_provider_env_without_resolving_it(self) -> None:
        # A provider name in the environment must not send us down the
        # provider path: resolve_provider would raise for a missing extra.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            with mock.patch(
                "weld.providers.resolve_provider",
                side_effect=AssertionError("provider must not be resolved"),
            ):
                rc, out, _ = self._run(
                    ["--root", str(root), "--agent-direct"],
                    {"WELD_ENRICH_PROVIDER": "anthropic"},
                )

            self.assertEqual(rc, 0)
            self.assertIn("wd add-node", out)

    def test_safe_mode_permits_agent_direct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, out, err = self._run(
                ["--root", str(root), "--agent-direct", "--safe"],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            self.assertEqual(rc, 0)
            self.assertIn("wd add-node", out)
            self.assertNotIn("refused", err)

    def test_json_output_parses_and_carries_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, out, _ = self._run(
                ["--root", str(root), "--agent-direct", "--json"],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            payload = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(payload["mode"], "agent-direct")
            self.assertEqual(len(payload["pending"]), 4)

    def test_limit_and_type_flags_reach_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, out, _ = self._run(
                [
                    "--root", str(root), "--agent-direct", "--json",
                    "--type", "entity", "--limit", "1",
                ],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            payload = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(payload["counts"]["pending_total"], 2)
            self.assertEqual(payload["counts"]["returned"], 1)
            self.assertEqual(payload["counts"]["remaining"], 1)

    def test_graph_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())
            before = (root / ".weld" / "graph.json").read_bytes()

            self._run(
                ["--root", str(root), "--agent-direct"],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            self.assertEqual((root / ".weld" / "graph.json").read_bytes(), before)

    def test_unknown_node_id_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, _, err = self._run(
                ["--root", str(root), "--agent-direct", "--node", "entity:Nope"],
                {"WELD_ENRICH_PROVIDER": ""},
            )

            self.assertEqual(rc, 1)
            self.assertIn("entity:Nope", err)


class IncompatibleFlagTest(unittest.TestCase):
    """Contradictory intent is refused, never silently ignored."""

    def _rc_err(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = enrich_main(argv)
        return rc, err.getvalue()

    def test_provider_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_graph(root, nodes())

            rc, err = self._rc_err(
                ["--root", str(root), "--agent-direct", "--provider", "anthropic"],
            )

            self.assertEqual(rc, 1)
            self.assertIn("--provider", err)
            self.assertIn("--agent-direct", err)

    def test_provider_loop_budget_flags_are_rejected(self) -> None:
        for flag, value in (
            ("--model", "gpt-4"),
            ("--max-tokens", "100"),
            ("--max-cost", "1.5"),
        ):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_graph(root, nodes())

                rc, err = self._rc_err(
                    ["--root", str(root), "--agent-direct", flag, value],
                )

                self.assertEqual(rc, 1)
                self.assertIn(flag, err)

    def test_agent_direct_only_flags_rejected_without_the_mode(self) -> None:
        for argv_tail in (["--type", "entity"], ["--limit", "3"]):
            with self.subTest(flag=argv_tail[0]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_graph(root, nodes())

                rc, err = self._rc_err(["--root", str(root), *argv_tail])

                self.assertEqual(rc, 1)
                self.assertIn(argv_tail[0], err)
                self.assertIn("--agent-direct", err)


if __name__ == "__main__":
    unittest.main()

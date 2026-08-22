"""Tests for ``weld._mcp_cli`` -- the ``wd mcp`` subcommand namespace.

Two subcommands that have to agree with each other: ``config`` renders the
per-client JSON snippet, and the invocation that snippet names is ``serve``.
Drift between them is invisible at the renderer -- the config keeps being
generated, correctly shaped -- and shows up only as a server that never
starts in someone's client. So the contract is asserted from the dispatcher
side here, against what the renderer actually emits.

Dispatch only. What ``wd mcp serve`` must *not* execute from the directory
it is launched in cannot be observed in-process; that is
``weld_mcp_serve_launch_shadow_test``, which launches it for real.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from weld import _mcp_stdio as stdio_mod
from weld import cli as cli_mod
from weld import mcp_config


class McpSubcommandDispatchTests(unittest.TestCase):
    """``wd mcp`` is a namespace: ``config`` renders, ``serve`` runs."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="weld_mcp_serve_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_mod.main(["mcp", *args])
        return rc, out.getvalue(), err.getvalue()

    def _record_root(self, sink: list[object]):
        """Stand in for ``run_stdio``, recording the root it was handed.

        Accepts (and ignores) ``tools_provider``: ``wd mcp serve`` now routes
        through ``weld.mcp_server.main``, which always forwards its own
        ``build_tools`` (ADR 0130 disposition #7) -- irrelevant to what this
        fake is pinning, but it must not choke on the keyword.
        """

        def _fake(root: object, *, tools_provider: object = None) -> int:
            sink.append(root)
            return 0

        return _fake

    def test_namespace_help_lists_both_subcommands(self) -> None:
        rc, stdout, _ = self._run("--help")
        self.assertEqual(rc, 0)
        for name in ("config", "serve"):
            self.assertIn(name, stdout)

    def test_rendered_entry_names_a_subcommand_the_dispatcher_routes(
        self,
    ) -> None:
        # Derived from the renderer rather than hard-coded, so this fails if
        # either half moves without the other. The bug it pins is silent: the
        # generated config would keep rendering, and only fail in a client.
        entry = json.loads(mcp_config.render("claude"))["mcpServers"]["weld"]
        self.assertEqual(entry["args"][0], "mcp")

        rc, stdout, _ = self._run(entry["args"][1], "--help")

        self.assertEqual(rc, 0)
        self.assertIn("Usage: wd mcp serve", stdout)

    def test_serve_help_reports_the_console_script_spelling(self) -> None:
        # The banner is a launch instruction; printing the `python -m`
        # spelling here would tell the reader to use the form carrying the
        # launch-directory residual this subcommand exists to avoid.
        rc, stdout, _ = self._run("serve", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("Usage: wd mcp serve [ROOT]", stdout)
        self.assertNotIn("Usage: python -m weld.mcp_server", stdout)

    def test_serve_passes_an_explicit_root_through(self) -> None:
        seen: list[object] = []
        with mock.patch.object(
            stdio_mod, "run_stdio", side_effect=self._record_root(seen)
        ):
            rc, _, _ = self._run("serve", self._tmp)

        self.assertEqual(rc, 0)
        self.assertEqual(len(seen), 1)
        # resolve() on both sides: a temp dir reached through a symlinked
        # /tmp would otherwise compare unequal to its own realpath.
        self.assertEqual(
            Path(str(seen[0])).resolve(), Path(self._tmp).resolve()
        )

    def test_serve_without_a_root_resolves_one(self) -> None:
        # No argument must not mean "serve the empty string": the resolver is
        # what turns a client's arbitrary working directory into a checkout.
        seen: list[object] = []
        with mock.patch.object(
            stdio_mod, "run_stdio", side_effect=self._record_root(seen)
        ):
            rc, _, _ = self._run("serve")

        self.assertEqual(rc, 0)
        self.assertEqual(len(seen), 1)
        self.assertTrue(Path(str(seen[0])).is_absolute(), seen)

    def test_serve_propagates_the_exit_code(self) -> None:
        # A missing MCP SDK exits 2, and a client needs to see that rather
        # than a success the dispatcher invented.
        with mock.patch.object(stdio_mod, "run_stdio", return_value=2):
            rc, _, _ = self._run("serve")

        self.assertEqual(rc, 2)

    def test_unknown_subcommand_lists_the_supported_ones(self) -> None:
        rc, _, stderr = self._run("srv")
        self.assertEqual(rc, 2)
        for name in ("config", "serve"):
            self.assertIn(name, stderr)


if __name__ == "__main__":
    unittest.main()

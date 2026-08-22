"""A per-request ``root`` on the MCP read surface (ADR 0096 §4).

An MCP server is long-lived and launched once, so before this change every
tool call it served was pinned to the directory the process started in --
an agent working in a linked worktree got the *launch* checkout's answers,
silently and from the wrong branch. The CLI already had the fix (``wd
--root``); ADR 0083 says MCP may re-expose exactly that and nothing more.

What the accepted argument does:

* the promise -- a request naming a sibling checkout is answered from that
  checkout's graph, and omitting the argument still answers from the
  server's own root;
* the plumbing -- the argument reaches a handler that already declares
  ``root``, so it must be taken out of the request first;
* the choke point -- dispatch seeds the resolved root, the same bootstrap
  ``ensure_graph_exists`` gives the CLI, so a fresh checkout served over
  MCP is not the one surface that still reports it as un-built;
* the ledger -- telemetry is recorded against the root that answered.

What it may *not* do is :mod:`weld_mcp_root_bound_test`. Fixtures are the
real thing (``git worktree add`` / ``git clone`` of a real ``--track-graphs``
repo, shared with the seeding suites) because "which checkout answered"
cannot be observed on a mock.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from weld import mcp_server
from weld._errors import ROOT_OUT_OF_BOUNDS
from weld._mcp_read import clear_graph_cache
from weld.tests._mode_b_fixture import SIDECAR, ModeBFixture, git, graph_commit, sidecar
from weld.tests._request_root_fixture import FrozenRefreshFixture, match_ids


class PerRequestRootTests(FrozenRefreshFixture):
    """The promise: the named checkout answers, not the launch directory."""

    def test_root_argument_answers_from_the_named_worktree(self) -> None:
        worktree = self.branch_worktree()

        served = mcp_server.dispatch(
            "weld_query", {"term": "gamma", "root": str(worktree)},
            root=str(self.origin),
        )

        self.assertTrue(
            any("gamma" in node_id for node_id in match_ids(served)),
            "a request naming the worktree must be answered from its graph; "
            f"got {sorted(match_ids(served))}",
        )

    def test_omitting_root_still_answers_from_the_server_root(self) -> None:
        """The default is unchanged: no argument means the launch checkout."""
        self.branch_worktree()

        served = mcp_server.dispatch(
            "weld_query", {"term": "gamma"}, root=str(self.origin),
        )

        self.assertFalse(
            any("gamma" in node_id for node_id in match_ids(served)),
            "the server root's graph does not know gamma, so omitting root "
            "must not surface it",
        )

    def test_root_is_honoured_on_a_tool_with_no_other_arguments(self) -> None:
        """``root`` is the only argument ``weld_stale`` takes, so this is also
        the narrowest possible duplicate-kwarg regression (see below).

        The two checkouts sit on different commits after the branch commit,
        so the reported ``current_sha`` names which one was consulted.
        """
        worktree = self.branch_worktree()

        served = mcp_server.dispatch(
            "weld_stale", {"root": str(worktree)}, root=str(self.origin),
        )

        self.assertEqual(
            served.get("current_sha"), git(worktree, "rev-parse", "HEAD"),
        )
        self.assertNotEqual(
            served.get("current_sha"), git(self.origin, "rev-parse", "HEAD"),
        )


class DuplicateKwargRegressionTests(FrozenRefreshFixture):
    """``root`` arrives in *arguments* but reaches the handler as a kwarg.

    Every handler already declares ``root=``, so forwarding the request
    arguments unfiltered is a ``TypeError: got multiple values for keyword
    argument 'root'`` -- the failure mode that makes a new schema property
    look like a broken tool. Pinned per-shape because the argument-passing
    style differs across handlers (positional term, keyword-only, none).
    """

    def test_root_in_arguments_never_duplicates_the_kwarg(self) -> None:
        worktree = self.branch_worktree()
        calls = (
            ("weld_query", {"term": "gamma"}),
            ("weld_find", {"term": "gamma"}),
            ("weld_stale", {}),
            ("weld_context", {"node_id": "file:gamma"}),
        )

        for tool, args in calls:
            with self.subTest(tool=tool):
                try:
                    result = mcp_server.dispatch(
                        tool, {**args, "root": str(worktree)},
                        root=str(self.origin),
                    )
                except TypeError as exc:  # pragma: no cover - the regression
                    self.fail(f"{tool} raised a duplicate-kwarg TypeError: {exc}")
                self.assertIsInstance(result, dict)


class DispatchSeedingTests(ModeBFixture):
    """Dispatch is the MCP mirror of the CLI's ``ensure_graph_exists``.

    A tracked graph arrives without the gitignored sidecar that dates it, so
    the CLI seeds one at its read choke point. Until dispatch did the same,
    the same checkout read fresh over ``wd`` and un-dated over MCP -- one
    repository, two answers, which is exactly what ADR 0083 forbids.
    """

    def setUp(self) -> None:
        super().setUp()
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def test_dispatch_seeds_the_resolved_root(self) -> None:
        worktree = self.worktree()
        self.assertFalse(
            (worktree / ".weld" / SIDECAR).exists(), "precondition: no sidecar",
        )

        with mock.patch(
            "weld.discover._discover_single_repo",
            side_effect=AssertionError("discovery ran on an undrifted worktree"),
        ) as discover:
            served = mcp_server.dispatch(
                "weld_stale", {"root": str(worktree)}, root=str(self.origin),
            )

        self.assertEqual(discover.call_count, 0)
        self.assertEqual(
            sidecar(worktree).get("git_sha"), graph_commit(worktree),
            "the seed must date the tracked graph, not leave it undated",
        )
        self.assertFalse(
            served.get("stale"),
            "an undrifted Mode B checkout must read fresh over MCP too",
        )


class TelemetryRootTests(FrozenRefreshFixture):
    """The event lands in the ledger of the checkout that answered."""

    def setUp(self) -> None:
        super().setUp()
        self._saved = os.environ.pop("WELD_TELEMETRY", None)
        self.addCleanup(self._restore_telemetry)

    def _restore_telemetry(self) -> None:
        if self._saved is not None:
            os.environ["WELD_TELEMETRY"] = self._saved

    def _events(self, root: Path) -> list[dict]:
        from weld._telemetry import TELEMETRY_FILENAME

        path = root / ".weld" / TELEMETRY_FILENAME
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_event_is_recorded_against_the_resolved_root(self) -> None:
        worktree = self.branch_worktree()

        mcp_server.dispatch(
            "weld_query", {"term": "gamma", "root": str(worktree)},
            root=str(self.origin),
        )

        commands = [e["command"] for e in self._events(worktree)]
        self.assertIn("weld_query", commands)
        self.assertEqual(
            [e["command"] for e in self._events(self.origin)], [],
            "the launch checkout did not answer, so it must record nothing",
        )

    def test_a_refused_root_is_still_one_recorded_call(self) -> None:
        """A refusal is a served call: dropping it from the ledger would hide
        exactly the pattern an operator would want to notice."""
        served = mcp_server.dispatch(
            "weld_query", {"term": "alpha", "root": str(self.tmp / "nowhere")},
            root=str(self.origin),
        )

        self.assertEqual(served.get("error_code"), ROOT_OUT_OF_BOUNDS)
        self.assertEqual(
            [e["command"] for e in self._events(self.origin)], ["weld_query"],
        )


if __name__ == "__main__":
    unittest.main()

"""Finding 09 on the MCP surface: the cause the CLI names reaches the payload.

The CLI half landed first (:mod:`weld_worktree_seed_message_test`): a linked
worktree of a repository that gitignores all of ``.weld/`` can never seed,
and ``wd query`` there now says so instead of printing the ordinary
first-run guidance. MCP served the same checkout the bare payload.

That gap is worse than the one already fixed, not milder. ADR 0134's thesis
is that the agent is the consumer which cannot tell a cannot-answer from an
empty answer, and an agent driving MCP from a fresh worktree *is* the
reporter in finding 09 -- it has no terminal to read a second opinion from,
so whatever the payload does not say is simply not available to it.

What is asserted here:

* the promise -- the cause reaches ``error`` on a graphless worktree of an
  ignore-all repository, through the real dispatch path;
* parity -- the payload's three text fields reassemble
  :func:`weld._graph_cli.missing_graph_message` byte for byte, so "MCP says
  what the CLI says" is a checked equality rather than a spot-check of
  shared vocabulary. Repo policy is that MCP is a thin wrapper of the
  product, and this is the form that policy takes here;
* coverage -- every guarded tool threads its root, enumerated from the live
  registry so a tool added later cannot quietly miss the wiring;
* restraint -- a root with nothing to explain, and a call with no root at
  all, produce exactly today's bytes.

The repository is the real ignore-all shape from the finding, built by the
shared Mode A fixture, because the claim is about a repository-wide policy
no worktree of it can escape -- deleting a config from a worktree would
prove something weaker.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld import mcp_server
from weld._errors import ERROR_HINTS, GRAPH_MISSING
from weld._gitignore_writer import IGNORE_ALL_GITIGNORE
from weld._graph_cli import missing_graph_message
from weld._mcp_guard import missing_graph_payload
from weld._worktree_seed import seed_blocked_reason
from weld.tests._mode_a_fixture import ModeAFixture, weld_listing

#: Minimal arguments per guarded tool, so dispatch reaches the guard rather
#: than failing on a missing required argument. Cross-checked against the
#: live registry below, which is what keeps it honest.
_GUARDED_TOOLS: dict[str, dict] = {
    "weld_query": {"term": "alpha"},
    "weld_context": {"node_id": "file:alpha"},
    "weld_path": {"from_id": "a", "to_id": "b"},
    "weld_brief": {"area": "anywhere"},
    "weld_callers": {"symbol_id": "anything"},
    "weld_export": {"format": "mermaid"},
    "weld_references": {"symbol_name": "anything"},
    "weld_diff": {},
    "weld_impact": {"target": "anything"},
    "weld_trace": {"term": "alpha"},
    "weld_enrich": {},
    "weld_review": {},
}

#: Exempt by design, and for the same reasons the CLI exempts them:
#: ``weld_find`` reads the file index rather than the graph, and
#: ``weld_stale`` already reports a missing graph as its own answer.
#: Exempt from *this* guard, not from having a precondition -- ``weld_find``
#: refuses on a missing index instead (``weld._find_precondition``, pinned by
#: ``weld_find_missing_index_test``).
_EXEMPT_TOOLS: frozenset[str] = frozenset({"weld_find", "weld_stale"})

#: Fragments of the cause a reader has to receive: what this checkout is,
#: which file is missing, and the repository-wide change that fixes it.
_CAUSE = ("linked git worktree", ".weld/discover.yaml",
          "git add -f .weld/discover.yaml")

#: The retry hint MCP hands back is the tool name, not a ``wd`` command.
#: Parity is claimed about the *cause*, so both surfaces are given the same
#: string and the comparison isolates the one field under test.
_RETRY = "weld_query"


class IgnoreAllWorktreeCauseTest(ModeAFixture):
    """A worktree that can never seed is told so over MCP too."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_precondition_the_repository_withholds_the_config(self) -> None:
        """The ignore policy, not the test, is what makes the worktree bare."""
        worktree = self.worktree()

        self.assertFalse((worktree / ".weld" / "discover.yaml").exists())
        self.assertFalse((worktree / ".weld" / "graph.json").exists())
        # The seed source is present and healthy: nothing else is wrong.
        self.assertTrue((self.origin / ".weld" / "graph.json").is_file())

    def test_the_payload_names_the_missing_prerequisite(self) -> None:
        payload = missing_graph_payload(_RETRY, root=self.worktree())

        for fragment in _CAUSE:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, payload["error"])

    def test_the_headline_still_leads_the_error(self) -> None:
        """``assertIn('No Weld graph found')`` holds for every existing
        consumer, and a log grep still finds the summary first."""
        payload = missing_graph_payload(_RETRY, root=self.worktree())

        self.assertTrue(payload["error"].startswith("No Weld graph found."))

    def test_the_machine_readable_vocabulary_is_untouched(self) -> None:
        """A cause is prose. Nothing an agent branches on may move with it."""
        payload = missing_graph_payload(_RETRY, root=self.worktree())
        bare = missing_graph_payload(_RETRY)

        self.assertEqual(payload["error_code"], GRAPH_MISSING)
        self.assertEqual(payload["hint"], ERROR_HINTS[GRAPH_MISSING])
        self.assertEqual(payload["hint"], bare["hint"])
        self.assertEqual(payload["retry"], bare["retry"])
        self.assertEqual(set(payload), set(bare))

    def test_explaining_the_decline_writes_nothing(self) -> None:
        """The cause is a probe, not a repair -- and this is the error path
        of a long-lived server, which must not acquire a write."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        missing_graph_payload(_RETRY, root=worktree)

        self.assertEqual(weld_listing(worktree), before)


class CliParityTest(ModeAFixture):
    """The payload reassembles the CLI's block, cause or no cause.

    MCP splits the CLI's four-line block across ``error`` / ``hint`` /
    ``retry``. Joining those three back with newlines must reproduce the
    CLI string exactly -- the strongest available statement of "the MCP
    server is a thin wrapper of the product", and one that fails if either
    surface ever grows a line the other does not.
    """

    gitignore = IGNORE_ALL_GITIGNORE

    def _assert_reassembles(self, root: Path) -> None:
        payload = missing_graph_payload(_RETRY, root=root)

        self.assertEqual(
            "\n".join(
                [payload["error"], payload["hint"], payload["retry"]],
            ),
            missing_graph_message(_RETRY, seed_blocked_reason(root)),
        )

    def test_parity_when_a_cause_applies(self) -> None:
        self._assert_reassembles(self.worktree())

    def test_parity_when_no_cause_applies(self) -> None:
        """The clone has no sibling to seed from, so both surfaces stay
        silent about worktrees -- and must stay silent identically."""
        clone = self.clone()

        self.assertIsNone(seed_blocked_reason(clone))
        self._assert_reassembles(clone)


class NoCauseKeepsTodaysBytesTest(ModeAFixture):
    """Only the one decline is explained; every other call is unchanged."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_a_call_with_no_root_is_byte_identical(self) -> None:
        """The argument is optional, and omitting it is the old behaviour."""
        self.assertEqual(
            missing_graph_payload(_RETRY),
            {
                "error": "No Weld graph found.",
                "error_code": GRAPH_MISSING,
                "hint": ERROR_HINTS[GRAPH_MISSING],
                "retry": f"Then retry: {_RETRY}.",
            },
        )

    def test_a_clone_gets_the_bare_payload(self) -> None:
        self.assertEqual(
            missing_graph_payload(_RETRY, root=self.clone()),
            missing_graph_payload(_RETRY),
        )

    def test_the_main_checkout_gets_the_bare_payload(self) -> None:
        self.assertEqual(
            missing_graph_payload(_RETRY, root=self.origin),
            missing_graph_payload(_RETRY),
        )

    def test_a_root_that_cannot_be_probed_degrades_to_the_bare_payload(
        self,
    ) -> None:
        """An unprobeable root must not raise. Dispatch bounds a request root
        to an existing directory, but this function is also reachable in
        process, and it runs on the error path of a long-lived server: a
        probe that could throw would convert a handled missing-graph answer
        into a transport-level failure."""
        self.assertEqual(
            missing_graph_payload(_RETRY, root=self.tmp / "no-such-checkout"),
            missing_graph_payload(_RETRY),
        )


class ConfigTrackedRepoTest(ModeAFixture):
    """Case B, the reported-working shape: a worktree that *has* its config
    is told nothing extra, because nothing was withheld from it."""

    def test_a_worktree_with_its_config_gets_the_bare_payload(self) -> None:
        worktree = self.worktree()
        # Gate 5 would seed this checkout on a read; the guard is asked
        # about it directly, before any read has run.
        self.assertTrue((worktree / ".weld" / "discover.yaml").is_file())

        self.assertEqual(
            missing_graph_payload(_RETRY, root=worktree),
            missing_graph_payload(_RETRY),
        )


class EveryGuardedToolThreadsItsRootTest(ModeAFixture):
    """The wiring claim: no guarded tool was left holding the bare payload.

    Driven through :func:`weld.mcp_server.dispatch` rather than the guard,
    because the defect this pins is a *dropped argument* at a call site --
    invisible to any test that calls the guard itself.
    """

    gitignore = IGNORE_ALL_GITIGNORE

    def test_the_table_covers_every_guarded_tool_in_the_registry(self) -> None:
        """Enumerated from the live registry so a tool added later either
        threads its root or fails here, rather than being silently missed."""
        registered = {tool.name for tool in mcp_server.build_tools()}

        self.assertEqual(registered - _EXEMPT_TOOLS, set(_GUARDED_TOOLS))

    def test_every_guarded_tool_reports_the_cause(self) -> None:
        worktree = self.worktree()

        for name, args in _GUARDED_TOOLS.items():
            with self.subTest(tool=name):
                served = mcp_server.dispatch(name, args, root=str(worktree))

                self.assertEqual(served.get("error_code"), GRAPH_MISSING)
                self.assertIn("linked git worktree", served.get("error", ""))

    def test_the_agent_scenario_a_request_root_names_the_worktree(self) -> None:
        """Finding 09 as an agent meets it: the server was launched in the
        main checkout, and the agent passes its own worktree as ``root``
        (which is what this repo's own guidance tells it to do)."""
        worktree = self.worktree()

        served = mcp_server.dispatch(
            "weld_query", {"term": "alpha", "root": str(worktree)},
            root=str(self.origin),
        )

        self.assertEqual(served.get("error_code"), GRAPH_MISSING)
        self.assertIn("git add -f .weld/discover.yaml", served.get("error", ""))

    def test_the_server_root_still_answers_when_it_has_a_graph(self) -> None:
        """The guard is not newly firing: the origin has a graph, so the
        same tool answers there. Guards against a cause that arrives by way
        of an error that should not have happened at all."""
        served = mcp_server.dispatch(
            "weld_query", {"term": "alpha"}, root=str(self.origin),
        )

        self.assertNotIn("error_code", served)


if __name__ == "__main__":
    unittest.main()

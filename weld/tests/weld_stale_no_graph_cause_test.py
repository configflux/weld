"""Finding 09 on the surface an agent reaches first: ``wd stale`` (bd kgx83).

The CLI and MCP halves of finding 09 both landed on ``ensure_graph_exists``
-- the ``_READ_COMMANDS`` funnel -- so ``wd query`` and every guarded MCP tool
now name the repository-wide policy that makes a worktree unseedable. ``stale``
is not in that set and must never join it: a freshness probe that exits instead
of reporting is useless, which is exactly why the ADR 0096 §2 amendment gave it
``_SEED_ONLY_COMMANDS`` (the same seed, no refusal).

The consequence is that the one command CLAUDE.md tells an agent to run
**first** in a new worktree was the one the cause never reached. It answered
``reason: no graph`` -- true, exit 0, and ADR 0134 leaves that exit code alone
-- while the remedy that reading implies (``wd discover``) is not the remedy:
no worktree of an ignore-all repository can ever seed, and the fix is a
repository-wide ``git add -f .weld/discover.yaml`` the payload never mentioned.

What is asserted here (ADR 0100 amendment, bd kgx83):

* the promise -- ``seed_blocked_reason`` reaches both the ``--json`` payload
  and the human block, through the real CLI at a real worktree;
* parity -- ``wd stale --json`` and ``weld_stale`` are equal *whole*, so the
  ADR 0083 thin-wrapper invariant covers the new key by construction rather
  than by a second call site;
* additivity -- every field the payload carried before is byte-identical, and
  the key is absent wherever no seed was blocked;
* the gate -- the key follows ``reason == "no graph"``, not merely "a cause
  could be computed";
* restraint -- explaining a decline still writes nothing.

The repository is the real ignore-all shape from the finding, built by the
shared Mode A fixture: the claim is about a repository-wide policy no worktree
of it can escape, and deleting a config out of one worktree would prove
something weaker.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._gitignore_writer import IGNORE_ALL_GITIGNORE
from weld._worktree_seed import seed_blocked_reason
from weld.tests._mode_a_fixture import ModeAFixture, weld_listing

#: The payload key under test.
_KEY = "seed_blocked_reason"

#: Fragments a reader has to receive: what this checkout is, which file is
#: missing, and the repository-wide change that fixes it. Same three the CLI
#: and MCP missing-graph suites assert, because it is the same sentence.
_CAUSE = ("linked git worktree", ".weld/discover.yaml",
          "git add -f .weld/discover.yaml")


def _run(root: Path, *argv: str) -> tuple[int, str]:
    """Drive the real graph CLI at *root*; return ``(exit_code, stdout)``."""
    from weld._graph_cli import main as graph_main

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            graph_main(["--root", str(root), *argv])
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue()


def _stale_json(root: Path) -> dict:
    code, out = _run(root, "stale", "--json")
    if code != 0:
        raise AssertionError(f"wd stale must answer, not refuse (exit {code})")
    return json.loads(out)


class UnseedableWorktreeNamesTheCauseTest(ModeAFixture):
    """A worktree that can never seed says so in its freshness answer."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_precondition_the_repository_withholds_the_config(self) -> None:
        """The ignore policy, not the test, is what makes the worktree bare."""
        worktree = self.worktree()

        self.assertFalse((worktree / ".weld" / "discover.yaml").exists())
        self.assertFalse((worktree / ".weld" / "graph.json").exists())
        # The seed source is present and healthy: nothing else is wrong here.
        self.assertTrue((self.origin / ".weld" / "graph.json").is_file())

    def test_the_json_payload_names_the_missing_prerequisite(self) -> None:
        payload = _stale_json(self.worktree())

        self.assertEqual(payload["reason"], "no graph")
        for fragment in _CAUSE:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, payload[_KEY])

    def test_the_human_block_names_it_too(self) -> None:
        """``--json`` is the agent's read; this is the one a person gets."""
        code, out = _run(self.worktree(), "stale")

        self.assertEqual(code, 0, "stale must keep answering")
        self.assertIn("reason: no graph", out)
        self.assertIn(f"{_KEY}:", out)
        for fragment in _CAUSE:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, out)

    def test_the_cause_is_the_functions_own_string(self) -> None:
        """Reused, not restated: a second copy is how surfaces disagree."""
        worktree = self.worktree()

        self.assertEqual(
            _stale_json(worktree)[_KEY], seed_blocked_reason(worktree),
        )

    def test_cli_json_equals_the_mcp_handler(self) -> None:
        """ADR 0083/0100 parity, compared whole -- ``weld_stale`` carries no
        transport stamp to strip, so nothing is whitelisted here."""
        worktree = self.worktree()

        cli = _stale_json(worktree)
        served = mcp_server.weld_stale(root=str(worktree))

        self.assertEqual(cli, served)
        self.assertIn(_KEY, served)

    def test_the_agent_scenario_a_request_root_names_the_worktree(self) -> None:
        """Finding 09 as an agent meets it: the server runs in the main
        checkout and the agent passes its own worktree as ``root``."""
        worktree = self.worktree()

        served = mcp_server.dispatch(
            "weld_stale", {"root": str(worktree)}, root=str(self.origin),
        )

        self.assertIn("git add -f .weld/discover.yaml", served[_KEY])

    def test_every_standing_field_is_untouched(self) -> None:
        """Additive means additive: the answer minus the new key is today's."""
        payload = _stale_json(self.worktree())

        self.assertEqual(
            {k: v for k, v in payload.items() if k != _KEY},
            {
                "stale": True,
                "source_stale": True,
                "sha_behind": False,
                "graph_sha": None,
                "current_sha": payload["current_sha"],
                "commits_behind": -1,
                "coverage_stale": False,
                "reason": "no graph",
                "stale_sources": [],
                "stale_sources_omitted": 0,
                "branch": "feature",
                "graph_branch": None,
            },
        )

    def test_explaining_the_decline_writes_nothing(self) -> None:
        """The cause is a probe, not a repair -- and ``wd stale`` is the
        surface that must stay a pure read."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        _stale_json(worktree)

        self.assertEqual(weld_listing(worktree), before)


class NoCauseKeepsTodaysPayloadTest(ModeAFixture):
    """Only the one decline is explained; every other answer is unchanged."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_a_clone_is_not_told_about_worktrees(self) -> None:
        """No sibling to seed from, so nothing was withheld from it -- even
        though it reports the same ``no graph``."""
        clone = self.clone()

        payload = _stale_json(clone)

        self.assertEqual(payload["reason"], "no graph")
        self.assertNotIn(_KEY, payload)

    def test_the_key_follows_the_reason_not_the_cause(self) -> None:
        """The gate is ``reason == "no graph"``, isolated.

        This worktree still has no config, so a cause *is* computable -- but
        it has a graph, so there is no seeding question left to answer and
        the payload must stay silent.
        """
        worktree = self.worktree("has-graph")
        (worktree / ".weld").mkdir(exist_ok=True)
        (worktree / ".weld" / "graph.json").write_text(
            '{"meta": {}, "nodes": {}, "edges": []}', encoding="utf-8",
        )
        self.assertIsNotNone(seed_blocked_reason(worktree))

        payload = _stale_json(worktree)

        self.assertNotIn("reason", payload)
        self.assertNotIn(_KEY, payload)


class NothingToSeedFromTest(ModeAFixture):
    """A worktree whose config is tracked reports ``no graph`` bare.

    The sharpest restraint case: same ``reason``, same graphless checkout,
    same linked worktree -- and no cause, because the prerequisite this key
    exists to name is satisfied. Whatever stopped that seed, ``wd discover``
    is still the answer.
    """

    discover_primary = False  # a primary with no graph: nothing to seed from

    def test_a_configured_worktree_gets_no_key(self) -> None:
        worktree = self.worktree()
        self.assertTrue((worktree / ".weld" / "discover.yaml").is_file())

        payload = _stale_json(worktree)

        self.assertEqual(payload["reason"], "no graph")
        self.assertNotIn(_KEY, payload)


if __name__ == "__main__":
    unittest.main()

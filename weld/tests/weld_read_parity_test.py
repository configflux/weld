"""CLI == MCP parity for the read surface (ADR 0083 thin-wrapper invariant).

All six agent-facing read commands must return the same answer on the CLI
(``--json``) and the MCP tool handler. ``query`` / ``context`` / ``brief`` route
through the one product read command (:mod:`weld.read`); ``callers`` / ``path``
call the same ``Graph`` method on both surfaces with no shaping; ``stale``
routes through :func:`weld._stale_payload.stale_payload`, the shaping
the CLI has always used. This pins the byte-identity of the answer fields the
ADR promises.

Scope notes:

* The comparison is at the MCP *handler* level (``mcp_server.weld_query`` etc.),
  before the dispatch layer stamps the transport-only ``freshness`` object and
  before ``children_status`` is attached at a federated root -- ADR 0083 rules
  those additive stamps as transport, not part of the shaped answer.
* ADR 0083 resolves the ADR 0078 speculative-match asymmetry *in favour of the
  CLI*: ``weld_query`` now runs the same speculative-match filter as ``wd
  query`` via :func:`weld.read.read_query`. So default-flag ``query`` is
  byte-identical on both surfaces and the unresolved sentinel leaves ``matches``
  (``test_query_default_drops_speculative_on_both_surfaces``); the shaping with
  that filter *bypassed* is also identical (``include_speculative``).
* Every shaped ``query`` / ``context`` envelope must carry the
  ``size_capped`` omission reason, proving both surfaces went through
  :mod:`weld.read` (and not the bare ADR 0078 diet).
* :class:`PerRequestRootParityTest` is the one case that goes through the
  full MCP ``dispatch`` rather than a handler, because the thing it pins --
  a request naming its own checkout (ADR 0096 §4) -- only exists there. It
  strips the transport stamps named above before comparing.
* Every fixture here sets ``WELD_AUTO_REFRESH=0``, which pins *shape* parity
  against a frozen graph and says nothing about whether either surface
  refreshes. Refresh behavior is pinned separately, with the variable
  explicitly **on**, in :mod:`weld.tests.weld_stale_refresh_exemption_test`
  (ADR 0102): the freshness oracle measures and never heals.
* ``stale`` is deliberately compared *whole*, not answer-fields-only:
  ``weld_stale`` is the freshness surface, so it is excluded from
  :data:`weld._mcp_read.FRESHNESS_TOOLS` and carries no ``freshness`` stamp
  to whitelist. Its branch-identity pair (ADR 0096 §3) is the field that
  tells an agent *which checkout* answered, so an MCP payload missing it is
  the wrong-branch failure the pair exists to expose.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._envelope_diet import OMISSION_REASONS
from weld._graph_cli import main as cli_main
from weld.brief import main as brief_main
from weld.read import SIZE_CAPPED_REASON

_TERM = "store"


def _write_graph(root: Path) -> None:
    """A fixture with a resolved match, an unresolved sentinel match, a project
    neighbor, and a stdlib neighbor the diet removes -- enough that shaping is
    observable and the query speculative-filter asymmetry is exercised."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes = {
        "entity:Store": {
            "type": "entity", "label": "Store",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "app/models.py"},
        },
        "symbol:py:app.store:save_store": {
            "type": "symbol", "label": "save_store",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "app/store.py"},
        },
        "symbol:unresolved:store": {
            "type": "symbol", "label": "store",
            "props": {"origin": "unresolved", "confidence": "speculative",
                      "resolution": "unresolved"},
        },
        "symbol:py:os:getcwd": {
            "type": "symbol", "label": "getcwd",
            "props": {"origin": "stdlib", "confidence": "definite"},
        },
    }
    edges = [
        {"from": "symbol:py:app.store:save_store", "to": "entity:Store",
         "type": "references"},
        {"from": "entity:Store", "to": "symbol:py:os:getcwd", "type": "calls"},
    ]
    payload = {"meta": {"version": 1, "schema_version": 1},
               "nodes": nodes, "edges": edges}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


class CliMcpReadParityTest(unittest.TestCase):
    """The CLI ``--json`` envelope equals the MCP handler payload."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_graph(self.root)
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        # Drop any cross-test cached graph so this fixture is served fresh.
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cli(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), *args, "--no-refresh"])
        return json.loads(buf.getvalue())

    def _cli_brief(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            brief_main(["--root", str(self.root), *args, "--no-refresh"])
        return json.loads(buf.getvalue())

    def _cli_stale(self) -> dict:
        # ``stale`` takes no --no-refresh: it is not a _READ_COMMANDS member
        # and never auto-refreshes, so the flag the other helpers pass would
        # be an argparse error rather than a no-op here.
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), "stale", "--json"])
        return json.loads(buf.getvalue())

    def test_query_default_drops_speculative_on_both_surfaces(self) -> None:
        # The recorded behavior change (ADR 0083): with default flags the
        # unresolved sentinel leaves ``matches`` on the MCP surface too, so the
        # default CLI and default MCP answers are byte-identical.
        cli_env = self._cli("query", _TERM, "--limit", "20", "--json")
        mcp_env = mcp_server.weld_query(_TERM, limit=20, root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        for surface, env in (("cli", cli_env), ("mcp", mcp_env)):
            match_ids = {m["id"] for m in env["matches"]}
            self.assertIn("entity:Store", match_ids, surface)
            self.assertNotIn("symbol:unresolved:store", match_ids, surface)

    def test_query_include_speculative_parity(self) -> None:
        # Bypassing the filter on both surfaces still yields identical shaping,
        # and now the sentinel survives on both (the positive control).
        cli_env = self._cli(
            "query", _TERM, "--limit", "20", "--include-speculative", "--json",
        )
        mcp_env = mcp_server.weld_query(
            _TERM, limit=20, include_speculative=True, root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(
            "symbol:unresolved:store", {m["id"] for m in cli_env["matches"]},
        )
        self.assertIn(SIZE_CAPPED_REASON, cli_env["omitted_neighbors"])
        self.assertEqual(
            tuple(cli_env["omitted_neighbors"].keys()),
            OMISSION_REASONS + (SIZE_CAPPED_REASON,),
        )

    def test_context_cli_equals_mcp_handler(self) -> None:
        cli_env = self._cli("context", "entity:Store", "--json")
        mcp_env = mcp_server.weld_context("entity:Store", root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(SIZE_CAPPED_REASON, cli_env["omitted_neighbors"])

    def test_brief_cli_equals_mcp_handler(self) -> None:
        cli_env = self._cli_brief(_TERM, "--limit", "20")
        mcp_env = mcp_server.weld_brief(_TERM, limit=20, root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        # Brief edges are de-dangled to emitted bucket nodes on both surfaces.
        node_ids = {
            n["id"]
            for bucket in ("primary", "interfaces", "docs", "build", "boundaries")
            for n in cli_env.get(bucket, [])
        }
        for edge in cli_env["edges"]:
            self.assertIn(edge["from"], node_ids)
            self.assertIn(edge["to"], node_ids)

    def test_full_size_parity(self) -> None:
        # The escape hatch is symmetric too: --full-size (CLI) == full_size (MCP).
        cli_env = self._cli(
            "context", "entity:Store", "--json", "--full-size",
        )
        mcp_env = mcp_server.weld_context(
            "entity:Store", full_size=True, root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)

    def test_callers_cli_equals_mcp_handler(self) -> None:
        # Both surfaces call Graph.callers and then the one shared shaper
        # (weld.read_traversal.shape_callers), so the answer must be identical.
        # This fixture is far under the byte budget, so nothing is dropped and
        # the assertion is about parity, not about capping -- the cap itself is
        # exercised in weld_read_traversal_parity_test. getcwd is called by
        # Store (a `calls` edge), so the result is non-degenerate.
        cli_env = self._cli("callers", "symbol:py:os:getcwd", "--json")
        mcp_env = mcp_server.weld_callers(
            "symbol:py:os:getcwd", root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(
            "entity:Store", {c["id"] for c in mcp_env.get("callers", [])},
        )

    def test_path_cli_equals_mcp_handler(self) -> None:
        # path has no shaping either; the single-repo MCP handler's
        # children_status attach is a no-op, so the answer must be identical.
        cli_env = self._cli(
            "path", "entity:Store", "symbol:py:os:getcwd", "--json",
        )
        mcp_env = mcp_server.weld_path(
            "entity:Store", "symbol:py:os:getcwd", root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertNotIn("children_status", mcp_env)

    def test_stale_cli_equals_mcp_handler(self) -> None:
        # Both surfaces must go through stale_payload, so the ADR 0096 §3
        # branch-identity pair is present on MCP as well. Keys, not values:
        # this fixture root is not a repository, so both fields are None here
        # -- PerRequestRootParityTest pins the non-degenerate values.
        cli_env = self._cli_stale()
        mcp_env = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        # stale_sources/stale_sources_omitted are additive on the same
        # shaper, so parity is automatic -- assert presence too.
        for key in (
            "branch", "graph_branch", "stale_sources", "stale_sources_omitted",
        ):
            self.assertIn(key, mcp_env)


class PerRequestRootParityTest(unittest.TestCase):
    """``--root <checkout>`` and ``{"root": "<checkout>"}`` answer alike.

    ADR 0083 lets MCP re-expose CLI capability and nothing more, so the
    per-request root added in ADR 0096 §4 has to land on the same bytes the
    flag already produces. Unlike the rest of this module the comparison runs
    through ``dispatch``: resolving and bounding the requested root is what is
    under test, and that lives above the handlers.

    A real ``git worktree add`` is required, not a bare temp directory --
    the request-root bound is repository identity, so a checkout with no
    repository behind it would be refused before any answer is shaped.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        tmp = Path(self._tmp)
        self.server_root = tmp / "repo"
        self._init_repo(self.server_root)
        self.checkout = tmp / "feature"
        self._git(self.server_root, "worktree", "add", "-q", "-b", "f", str(self.checkout))
        # Only the linked checkout gets the fixture graph: an answer shaped
        # from the server's own root would be visibly empty, not merely equal.
        _write_graph(self.checkout)
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore(self) -> None:
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(root), check=True, capture_output=True,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        )

    def _init_repo(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._git(root, "init", "-q")
        self._git(root, "config", "user.email", "test@example.com")
        self._git(root, "config", "user.name", "Weld Test")
        (root / "models.py").write_text("class Store:\n    pass\n", encoding="utf-8")
        self._git(root, "add", "models.py")
        self._git(root, "commit", "-q", "-m", "seed")

    def test_cli_root_flag_equals_mcp_root_argument(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main([
                "--root", str(self.checkout), "query", _TERM, "--limit", "20",
                "--json", "--no-refresh",
            ])
        cli_env = json.loads(buf.getvalue())

        served = mcp_server.dispatch(
            "weld_query", {"term": _TERM, "limit": 20, "root": str(self.checkout)},
            root=str(self.server_root),
        )

        # Transport-only stamps (see the module docstring) are not part of
        # the shaped answer; asserting they were present keeps this from
        # quietly becoming a comparison of two error payloads.
        self.assertIn("freshness", served)
        served.pop("freshness")
        self.assertEqual(cli_env, served)
        self.assertIn("entity:Store", {m["id"] for m in served["matches"]})

    def test_stale_root_flag_equals_mcp_root_argument(self) -> None:
        """``branch``/``graph_branch`` survive the MCP path with real values.

        The recorded branch is seeded to something the checkout is *not* on,
        so the pair disagrees -- the silent wrong-branch answer ADR 0096 §3
        exists to expose. A test where both fields were ``None`` would pass
        against a handler that hard-coded them.
        """
        (self.checkout / ".weld" / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_branch": "recorded"}) + "\n",
            encoding="utf-8",
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.checkout), "stale", "--json"])
        cli_env = json.loads(buf.getvalue())

        served = mcp_server.dispatch(
            "weld_stale", {"root": str(self.checkout)}, root=str(self.server_root),
        )

        # weld_stale is the freshness surface itself, so it is not in
        # FRESHNESS_TOOLS and carries no transport stamp to strip.
        self.assertNotIn("freshness", served)
        self.assertEqual(cli_env, served)
        self.assertEqual(served["branch"], "f")
        self.assertEqual(served["graph_branch"], "recorded")


if __name__ == "__main__":
    unittest.main()

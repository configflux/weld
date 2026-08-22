"""``weld_stale`` never refreshes -- pinned with auto-refresh ENABLED (ADR 0102).

The freshness oracle is exempt from ADR 0051's refresh-before-serve: it reports
the state it finds and never rewrites the graph. ``wd stale`` has always behaved
that way (it is not a ``_READ_COMMANDS`` member); this module is what keeps the
MCP handler from drifting back to healing first and answering second.

It is a separate file from :mod:`weld.tests.weld_read_parity_test` and
:mod:`weld.tests.weld_read_parity_federated_test` for one reason: **both of
those set ``WELD_AUTO_REFRESH=0``**, and a divergence that only exists when
auto-refresh is on is invisible to a fixture that freezes it off. That masking
is why the asymmetry survived ADR 0100. Here the variable is explicitly set
*on*, never inherited: a surrounding build or CI run may export
``WELD_AUTO_REFRESH=0`` for its whole duration to stop reads rewriting a
tracked graph, so a test that merely assumed "unset means enabled" would
silently become the masked test it exists to replace.

Each fixture is refresh-*capable* by construction, and each proves it with a
control that runs ``weld_query`` over the identical repo and asserts the
refresh landed (:class:`RefreshCapableFixtureTest` for the single repo, the
first test of :class:`FederatedStaleIsExemptFromRefreshTest` for the
workspace). Without those, "nothing changed" would also pass against a
handler that refreshed a fixture nothing could refresh -- the assertions would
be measuring the fixture, not the handler.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._graph_cli import main as cli_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TERM = "helper"
_TS = "2026-04-15T21:00:00+00:00"


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        check=True, env={**os.environ, "LC_ALL": "C"},
    )
    return proc.stdout.strip()


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    _git(root, "config", "commit.gpgsign", "false")


def _seed_repo(root: Path) -> None:
    """A real repo with a discoverable module and a graph built from it."""
    _init_repo(root)
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "alpha.py").write_text("def helper_alpha():\n    return 1\n", encoding="utf-8")
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "seed")
    # ``_discover_single_repo`` builds the graph; only the standalone
    # ``wd discover`` CLI persists it. Mirror that so freshness has a real
    # on-disk graph (and its ADR 0065 sidecar) to be measured against.
    from weld.discover import _discover_single_repo
    from weld.serializer import dumps_graph
    from weld.workspace_state import atomic_write_text
    graph = _discover_single_repo(root, incremental=False, safe=False)
    atomic_write_text(weld_dir / "graph.json", dumps_graph(graph))


def _drift(root: Path) -> str:
    """Commit a source change so the graph is behind HEAD with content drift."""
    (root / "src" / "alpha.py").write_text(
        "def helper_alpha():\n    return 1\n\n\ndef helper_beta():\n    return 2\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "drift")
    return _git(root, "rev-parse", "HEAD")


def _write_fixture_graph(
    root: Path, nodes: dict, *, tracked: list[str], sv: int = 1,
) -> None:
    """A hand-written graph, as the federated parity fixture writes one."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {"meta": {"version": 1, "updated_at": _TS, "schema_version": sv,
                      "discovered_from": tracked},
             "nodes": nodes, "edges": []},
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def _write_sidecar(root: Path, sha: str) -> None:
    """Record the discovered-from SHA where ADR 0065 puts it: the sidecar."""
    (root / ".weld" / "graph-meta.json").write_text(
        json.dumps({"version": 1, "git_sha": sha}) + "\n", encoding="utf-8",
    )


#: Files under ``.weld/`` that a *read* legitimately touches, so they are not
#: evidence that a refresh ran. Neither is part of the graph:
#:
#: * ``telemetry.jsonl`` -- the ADR 0035 event stream the MCP dispatch boundary
#:   appends to for every tool call. It records that a read happened.
#: * ``query_state.bin`` -- the derived query index ``Graph.load()`` rebuilds
#:   lazily when it does not match the graph it is loading. ``wd stale`` writes
#:   it on exactly the same loads, so it is symmetric rather than a divergence,
#:   and it is idempotent: a second call rewrites nothing.
#:
#: Everything else -- ``graph.json``, the ADR 0065 ``graph-meta.json`` sidecar,
#: the incremental ``discovery-state.json``, the file index -- is what a
#: refresh would rewrite, and what "never mutates the graph" has to mean.
_READ_TOUCHABLE = frozenset({"telemetry.jsonl", "query_state.bin"})


def _weld_dir_snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``.weld/`` by content, minus :data:`_READ_TOUCHABLE`."""
    weld_dir = root / ".weld"
    return {
        str(p.relative_to(weld_dir)): p.read_bytes()
        for p in sorted(weld_dir.rglob("*"))
        if p.is_file() and p.name not in _READ_TOUCHABLE
    }


def _recorded_sha(root: Path) -> str | None:
    """The SHA the graph was discovered from, read where ADR 0065 puts it."""
    from weld._graph_meta_sidecar import load_graph_meta

    path = root / ".weld" / "graph.json"
    return load_graph_meta(path).get("git_sha") if path.is_file() else None


class _UnmaskedRefreshMixin:
    """Auto-refresh explicitly **on**, a clean read cache, and a temp root.

    The env var is set rather than assumed: a surrounding build or CI run may
    export ``WELD_AUTO_REFRESH=0`` for its whole duration, so a fixture that
    merely left the variable alone would inherit the mask this file exists to
    remove.
    """

    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "1"
        self.addCleanup(self._restore_env)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore_env(self) -> None:
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def _cli_stale(self) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), "stale", "--json"])
        return json.loads(buf.getvalue())


class _StaleFixtureMixin(_UnmaskedRefreshMixin):
    """A stale graph in a single repo that a refresh *could* heal."""

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()
        self.root = Path(self._tmp.name) / "repo"
        _seed_repo(self.root)
        self.seeded_sha = _recorded_sha(self.root)
        self.head_sha = _drift(self.root)
        self.assertIsNotNone(self.seeded_sha)
        self.assertNotEqual(self.seeded_sha, self.head_sha)


class RefreshCapableFixtureTest(_StaleFixtureMixin, unittest.TestCase):
    """The control: this repo really is one a refresh would heal."""

    def test_a_content_read_refreshes_the_same_fixture(self) -> None:
        # weld_query serves graph content, so ADR 0051 applies to it unchanged.
        # It advancing the recorded SHA is what makes the assertions in
        # StaleIsExemptFromRefreshTest statements about the handler rather than
        # about a fixture nothing could have refreshed.
        mcp_server.weld_query(_TERM, limit=20, root=str(self.root))
        self.assertEqual(_recorded_sha(self.root), self.head_sha)


class SidecarOnlyTouchInvalidatesCacheTest(_StaleFixtureMixin, unittest.TestCase):
    """A sidecar-only ``wd touch`` must bust the cached ``weld_stale`` answer
    (ADR 0083 "cache hit equals cold load", bd 7bjw)."""

    def test_touch_after_cached_stale_read_flips_the_verdict(self) -> None:
        from weld.graph import Graph

        # _seed_repo writes git_sha in-body with no sidecar; one untouched
        # save migrates to the ADR 0065 split form so the real touch below
        # is a genuine sidecar-only write.
        pre = Graph(self.root)
        pre.load()
        pre.save()
        primed = mcp_server.weld_stale(root=str(self.root))  # populates cache
        self.assertEqual((primed["graph_sha"], primed["commits_behind"]), (self.seeded_sha, 1))

        body_before = (self.root / ".weld" / "graph.json").read_bytes()
        g = Graph(self.root)
        g.load()
        g.save(touch_git_sha=True)  # what ``wd touch`` runs
        self.assertEqual(  # sanity: a genuine sidecar-only write
            (self.root / ".weld" / "graph.json").read_bytes(), body_before,
        )

        # graph_sha/commits_behind must track the touch (``stale`` legitimately
        # stays True via coverage_stale -- a re-stamp with no rediscovery is a
        # separate ADR 0101 signal). Full equality is the ADR 0083 claim.
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual((served["graph_sha"], served["commits_behind"]), (self.head_sha, 0))
        self.assertEqual(served, self._cli_stale())


class StaleIsExemptFromRefreshTest(_StaleFixtureMixin, unittest.TestCase):
    """``weld_stale`` measures; it does not heal (ADR 0102)."""

    def test_weld_stale_leaves_the_graph_untouched(self) -> None:
        # The whole of ``.weld/`` (minus _READ_TOUCHABLE), not just graph.json:
        # a refresh also rewrites the ADR 0065 sidecar and the incremental
        # state, so checking one file would let a partial refresh through.
        # "Advisory; never mutates the graph" is the tool's published contract.
        before = _weld_dir_snapshot(self.root)
        mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(_weld_dir_snapshot(self.root), before)
        self.assertEqual(_recorded_sha(self.root), self.seeded_sha)

    def test_weld_stale_reports_the_pre_refresh_state(self) -> None:
        # The self-defeating-observer half: a handler that heals first can only
        # ever answer stale=false, and would report the *post*-refresh SHA.
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertTrue(served["stale"])
        self.assertTrue(served["source_stale"])
        self.assertEqual(served["graph_sha"], self.seeded_sha)
        self.assertEqual(served["current_sha"], self.head_sha)

    def test_cli_equals_mcp_with_auto_refresh_enabled(self) -> None:
        """The ADR 0083 parity claim, unmasked.

        The CLI runs **first** on purpose: a healing handler would leave the
        graph fresh, so an MCP-first ordering would find both surfaces agreeing
        on ``stale=false`` and the equality alone would pass. Asserting the
        verdict as well makes the case order-independent.
        """
        cli_env = self._cli_stale()
        mcp_env = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        self.assertTrue(cli_env["stale"])
        self.assertTrue(mcp_env["stale"])

    def test_repeated_probes_keep_answering_stale(self) -> None:
        # Idempotence is the property an agent gate depends on: polling the
        # oracle must not be what changes the thing it measures.
        first = mcp_server.weld_stale(root=str(self.root))
        second = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(first, second)
        self.assertTrue(second["stale"])


class FederatedStaleIsExemptFromRefreshTest(_UnmaskedRefreshMixin, unittest.TestCase):
    """The federated half -- where the exemption is worth the most (ADR 0102).

    ADR 0100's headline fix is that child drift raises ``weld_stale``'s
    top-level ``stale`` at a polyrepo root (the ADR 0066 §2 agent gate). A
    refreshing handler recurses the stale children *first*, so the fold has no
    stale child left to fold: the fix held only under the ``WELD_AUTO_REFRESH=0``
    its own test sets. This runs with refresh on, which is what production is.

    The discriminator here is the **children projection**, not the top-level
    ``stale``: that flag reads ``true`` either way at this fixture, while a
    healed child flips from ``stale``/``source_changed``/1 to
    ``fresh``/``fresh``/0. The child carries its own ``discover.yaml`` for the
    same reason -- without one the federated recurse has nothing to run, and
    every assertion below would pass against a refreshing handler too.
    """

    def setUp(self) -> None:
        super().setUp()
        self.root = Path(self._tmp.name) / "workspace"
        _init_repo(self.root)
        (self.root / "README.md").write_text("# fixture\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "--quiet", "-m", "seed")

        # A child whose graph is one commit behind its own HEAD, and which a
        # recurse could re-discover.
        self.child = self.root / "svc-api"
        _init_repo(self.child)
        (self.child / ".weld").mkdir(parents=True, exist_ok=True)
        (self.child / ".weld" / "discover.yaml").write_text(
            "sources:\n  - strategy: python_module\n    glob: '**/*.py'\n"
            "    type: file\n",
            encoding="utf-8",
        )
        (self.child / "seed.py").write_text("x = 0\n", encoding="utf-8")
        _git(self.child, "add", "-A")
        _git(self.child, "commit", "--quiet", "-m", "child seed")
        _write_fixture_graph(
            self.child,
            {"entity:Store": {"type": "entity", "label": "Store",
                              "props": {"file": "seed.py"}}},
            tracked=["./"],
        )
        _write_sidecar(self.child, _git(self.child, "rev-parse", "HEAD"))
        (self.child / "drift.py").write_text("y = 1\n", encoding="utf-8")
        _git(self.child, "add", "-A")
        _git(self.child, "commit", "--quiet", "-m", "child drift")

        # Root meta-graph: fresh, tracking only README.md so a commit inside
        # the child can never register as root drift.
        _write_fixture_graph(
            self.root,
            {"repo:svc-api": {"type": "repo", "label": "svc-api",
                              "props": {"path": "svc-api"}}},
            tracked=["README.md"], sv=2,
        )
        _write_sidecar(self.root, _git(self.root, "rev-parse", "HEAD"))
        dump_workspaces_yaml(
            WorkspaceConfig(
                children=[ChildEntry(name="svc-api", path="svc-api")],
                cross_repo_strategies=[],
            ),
            self.root / ".weld" / "workspaces.yaml",
        )

    @staticmethod
    def _child_row(payload: dict) -> dict:
        return next(c for c in payload["children"] if c["name"] == "svc-api")

    def test_a_federated_content_read_refreshes_the_child(self) -> None:
        # The control: the ADR 0066 §3 recurse really does heal this child, so
        # the assertions below are about the handler, not about a workspace
        # nothing could refresh.
        mcp_server.weld_query("Store", limit=20, root=str(self.root))
        self.assertEqual(
            self._child_row(mcp_server.weld_stale(root=str(self.root))),
            {"name": "svc-api", "state": "fresh", "reason": "fresh",
             "commits_behind": 0},
        )

    def test_weld_stale_does_not_heal_the_drifted_child(self) -> None:
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(
            self._child_row(served),
            {"name": "svc-api", "state": "stale", "reason": "source_changed",
             "commits_behind": 1},
        )
        self.assertTrue(served["stale"])
        self.assertEqual(_recorded_sha(self.child),
                         _git(self.child, "rev-parse", "HEAD~1"))

    def test_federated_cli_equals_mcp_with_auto_refresh_enabled(self) -> None:
        cli_env = self._cli_stale()
        mcp_env = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        self.assertEqual(self._child_row(mcp_env)["state"], "stale")


if __name__ == "__main__":
    unittest.main()

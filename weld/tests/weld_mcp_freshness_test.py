"""MCP reads inherit the CLI freshness contract (85tb.3).

The CLI self-heals on every read: ``auto_refresh_if_stale`` runs before the
graph is loaded (ADR 0051) and freshness is reported via ``wd stale``
(ADR 0017). Historically the MCP surface did neither -- ``mcp_server`` never
called ``auto_refresh_if_stale`` and no payload carried a freshness signal, so
an agent driving the MCP server could silently consume stale answers.

These tests lock the unified contract on the MCP boundary:

1. Every graph-backed read tool routes through ``auto_refresh_if_stale`` before
   loading (so a stale graph self-heals, or carries ``stale=True`` under the
   ``WELD_AUTO_REFRESH=0`` opt-out).
2. Every successful read payload returned through the dispatch boundary (the
   path MCP clients actually hit) carries an additive ``freshness`` object
   ``{stale, commits_behind}``.
3. The freshness object leaks no paths / SHAs / secrets.
4. Error payloads (corrupt / missing graph, node-not-found) are NOT stamped.
5. Repeat reads against an unchanged graph hit the in-process cache rather than
   reloading the (potentially ~14MB) graph each call.

They are SDK-free (the ``mcp`` Python SDK is not required) and patch the
discovery pipeline / staleness oracle so they assert *control flow*, not the
cost of real discovery -- mirroring ``auto_refresh_helper_test``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from weld import _errors  # noqa: E402
from weld import _mcp_read  # noqa: E402
from weld import mcp_server  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NODES: dict[str, dict] = {
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {"file": "store.py", "exports": ["Store"]},
    },
    "symbol:py:m:helper": {
        "type": "symbol",
        "label": "helper",
        "props": {"module": "m", "qualname": "helper", "language": "python"},
    },
    "symbol:py:m:caller": {
        "type": "symbol",
        "label": "caller",
        "props": {"module": "m", "qualname": "caller", "language": "python"},
    },
}

_EDGES: list[dict] = [
    {
        "from": "symbol:py:m:caller",
        "to": "symbol:py:m:helper",
        "type": "calls",
        "props": {"resolved": True},
    },
]


def _seed_root(*, secret: str | None = None) -> Path:
    """Write a minimal valid graph + file index and return the root.

    When *secret* is given it is embedded in ``meta`` so leak assertions can
    confirm the freshness object never echoes graph content.
    """
    tmp = Path(tempfile.mkdtemp())
    weld = tmp / ".weld"
    weld.mkdir(parents=True, exist_ok=True)
    meta: dict = {"version": SCHEMA_VERSION, "git_sha": "deadbeef"}
    if secret is not None:
        meta["token"] = secret
    (weld / "graph.json").write_text(
        json.dumps({"meta": meta, "nodes": _NODES, "edges": _EDGES}),
        encoding="utf-8",
    )
    (weld / "file-index.json").write_text(
        json.dumps({"meta": {"version": 1}, "files": {"store.py": ["store", "Store"]}}),
        encoding="utf-8",
    )
    # discover.yaml so the auto-refresh oracle does not short-circuit on a
    # hand-seeded graph (mirrors auto_refresh_helper_test._seed_graph).
    (weld / "discover.yaml").write_text("sources: []\n", encoding="utf-8")
    return tmp


class _FreshnessTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _seed_root()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        # Cache is process-global; isolate each test.
        _mcp_read.clear_graph_cache()
        self.addCleanup(_mcp_read.clear_graph_cache)


# ---------------------------------------------------------------------------
# 1. Auto-refresh fires on the MCP read path
# ---------------------------------------------------------------------------

class AutoRefreshOnReadTest(_FreshnessTestBase):
    def test_weld_query_calls_auto_refresh_before_load(self) -> None:
        with mock.patch.object(_mcp_read, "auto_refresh_if_stale") as ar:
            mcp_server.weld_query("Store", root=self.root)
        ar.assert_called_once()
        # Called with the read root; positional or kw both acceptable.
        called_root = (ar.call_args.args[0] if ar.call_args.args
                       else ar.call_args.kwargs.get("root"))
        self.assertEqual(Path(called_root), Path(self.root))

    def test_weld_context_calls_auto_refresh_before_load(self) -> None:
        with mock.patch.object(_mcp_read, "auto_refresh_if_stale") as ar:
            mcp_server.weld_context("entity:Store", root=self.root)
        ar.assert_called_once()

    def test_stale_graph_self_heals_then_serves(self) -> None:
        # Stale -> auto_refresh runs real-ish discovery (patched) -> serve.
        # Force WELD_AUTO_REFRESH on: the repo gate sets it to 0 (the freeze),
        # but this test asserts the *enabled* self-heal control flow.
        with mock.patch.dict(
            "os.environ", {"WELD_AUTO_REFRESH": "1"}, clear=False,
        ), mock.patch(
            "weld._staleness.compute_stale_info",
            return_value={"stale": True, "commits_behind": 2},
        ), mock.patch(
            "weld.discover._discover_single_repo"
        ) as discover, mock.patch(
            "weld.discovery_state.load_state", return_value=None,
        ), mock.patch(
            "weld._auto_refresh._record_telemetry_event"
        ):
            result = mcp_server.weld_query("Store", root=self.root)
        discover.assert_called_once()  # refresh actually fired
        self.assertIn("matches", result)

    def test_env_opt_out_skips_refresh_but_signals_stale(self) -> None:
        # WELD_AUTO_REFRESH=0: refresh suppressed, freshness still stamped
        # stale=True (at the dispatch boundary) so the agent is never silently
        # served stale data.
        with mock.patch(
            "weld._staleness.compute_stale_info",
            return_value={"stale": True, "commits_behind": 3},
        ), mock.patch(
            "weld._auto_refresh._do_refresh"
        ) as did, mock.patch.dict(
            "os.environ", {"WELD_AUTO_REFRESH": "0"}, clear=False,
        ):
            result = mcp_server.dispatch(
                "weld_query", {"term": "Store"}, root=self.root
            )
        did.assert_not_called()
        self.assertTrue(result["freshness"]["stale"])
        self.assertEqual(result["freshness"]["commits_behind"], 3)


# ---------------------------------------------------------------------------
# 2. Every read payload carries a freshness object
# ---------------------------------------------------------------------------

#: Every graph-backed read tool dispatched here must carry freshness.
_READ_DISPATCHES: list[tuple[str, dict]] = [
    ("weld_query", {"term": "Store"}),
    ("weld_context", {"node_id": "entity:Store"}),
    ("weld_path", {"from_id": "symbol:py:m:caller", "to_id": "symbol:py:m:helper"}),
    ("weld_callers", {"symbol_id": "symbol:py:m:helper"}),
    ("weld_references", {"symbol_name": "helper"}),
    ("weld_brief", {"area": "Store"}),
    ("weld_trace", {"term": "Store"}),
    ("weld_impact", {"target": "entity:Store"}),
]


class FreshnessStampedOnReadsTest(_FreshnessTestBase):
    def _assert_freshness(self, result: dict) -> None:
        self.assertIn("freshness", result)
        fr = result["freshness"]
        self.assertIsInstance(fr, dict)
        self.assertIn("stale", fr)
        self.assertIsInstance(fr["stale"], bool)
        self.assertIn("commits_behind", fr)
        self.assertIsInstance(fr["commits_behind"], int)

    def test_every_read_tool_carries_freshness(self) -> None:
        for name, args in _READ_DISPATCHES:
            with self.subTest(tool=name):
                result = mcp_server.dispatch(name, args, root=self.root)
                self._assert_freshness(result)

    def test_every_read_tool_freshness_survives_text_payload(self) -> None:
        for name, args in _READ_DISPATCHES:
            with self.subTest(tool=name):
                payload = mcp_server.dispatch_to_text_payload(
                    name, args, root=self.root
                )
                self._assert_freshness(json.loads(payload))

    def test_non_read_tools_are_not_stamped(self) -> None:
        # weld_stale is itself the freshness surface; weld_find reads the file
        # index, not the graph. Neither is stamped with the freshness object
        # (no double-signal / no graph-staleness on a file-index read).
        stale = mcp_server.dispatch("weld_stale", {}, root=self.root)
        self.assertNotIn("freshness", stale)
        found = mcp_server.dispatch("weld_find", {"term": "store"}, root=self.root)
        self.assertNotIn("freshness", found)


# ---------------------------------------------------------------------------
# 3. Safety: the freshness object leaks nothing
# ---------------------------------------------------------------------------

class FreshnessNoLeakTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _seed_root(secret="MCP-FRESHNESS-SECRET-XYZ")
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        _mcp_read.clear_graph_cache()
        self.addCleanup(_mcp_read.clear_graph_cache)

    def test_freshness_object_is_only_stale_and_commits_behind(self) -> None:
        result = mcp_server.dispatch("weld_query", {"term": "Store"}, root=self.root)
        fr = result["freshness"]
        # Exactly the two whitelisted scalar keys -- no paths/SHAs/secrets.
        self.assertEqual(set(fr.keys()), {"stale", "commits_behind"})

    def test_freshness_does_not_echo_graph_secret(self) -> None:
        result = mcp_server.dispatch("weld_query", {"term": "Store"}, root=self.root)
        self.assertNotIn(
            "MCP-FRESHNESS-SECRET-XYZ", json.dumps(result["freshness"])
        )

    def test_freshness_does_not_echo_root_path(self) -> None:
        result = mcp_server.dispatch("weld_query", {"term": "Store"}, root=self.root)
        self.assertNotIn(str(self.root), json.dumps(result["freshness"]))


# ---------------------------------------------------------------------------
# 4. Error payloads are not stamped with freshness
# ---------------------------------------------------------------------------

class ErrorPayloadsUnstampedTest(unittest.TestCase):
    def _root_with(self, graph_text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / ".weld").mkdir(parents=True, exist_ok=True)
        (tmp / ".weld" / "graph.json").write_text(graph_text, encoding="utf-8")
        return tmp

    def setUp(self) -> None:
        _mcp_read.clear_graph_cache()
        self.addCleanup(_mcp_read.clear_graph_cache)

    def test_corrupt_graph_error_has_no_freshness(self) -> None:
        root = self._root_with('{"meta": {"token": "SECRET"')
        result = mcp_server.dispatch("weld_query", {"term": "x"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertNotIn("freshness", result)

    def test_missing_graph_error_has_no_freshness(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        result = mcp_server.dispatch("weld_query", {"term": "x"}, root=tmp)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_MISSING)
        self.assertNotIn("freshness", result)

    def test_node_not_found_has_no_freshness(self) -> None:
        root = _seed_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        result = mcp_server.dispatch(
            "weld_context", {"node_id": "entity:Nope"}, root=root
        )
        self.assertEqual(result.get("error_code"), _errors.NODE_NOT_FOUND)
        self.assertNotIn("freshness", result)


# ---------------------------------------------------------------------------
# 5. In-process cache: repeat reads skip the reload
# ---------------------------------------------------------------------------

class GraphCacheTest(_FreshnessTestBase):
    def test_repeat_reads_skip_reload(self) -> None:
        # First read populates the cache; the second must not call Graph.load
        # again for the unchanged graph (sha-keyed cache hit).
        from weld.graph import Graph

        # Prime the cache.
        _mcp_read.load_graph_for_read(self.root)
        with mock.patch.object(
            Graph, "load", autospec=True,
        ) as load:
            g = _mcp_read.load_graph_for_read(self.root)
        load.assert_not_called()
        self.assertIsNotNone(g)

    def test_changed_graph_invalidates_cache(self) -> None:
        from weld.graph import Graph

        first = _mcp_read.load_graph_for_read(self.root)
        self.assertIn("entity:Store", first.dump().get("nodes", {}))
        # Mutate the graph file on disk -> sha changes -> cache miss -> reload.
        graph_path = self.root / ".weld" / "graph.json"
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        data["nodes"]["entity:NewThing"] = {
            "type": "entity", "label": "NewThing", "props": {},
        }
        graph_path.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(Graph, "load", wraps=Graph.load, autospec=True) as load:
            second = _mcp_read.load_graph_for_read(self.root)
        load.assert_called()  # reloaded because bytes changed
        self.assertIn("entity:NewThing", second.dump().get("nodes", {}))

    def test_clear_graph_cache_forces_reload(self) -> None:
        from weld.graph import Graph

        _mcp_read.load_graph_for_read(self.root)
        _mcp_read.clear_graph_cache()
        with mock.patch.object(Graph, "load", wraps=Graph.load, autospec=True) as load:
            _mcp_read.load_graph_for_read(self.root)
        load.assert_called()


# ---------------------------------------------------------------------------
# 6. Federated roots: freshness derived cheaply from the root graph meta
# ---------------------------------------------------------------------------

class FederatedFreshnessTest(_FreshnessTestBase):
    def test_freshness_at_federated_root_is_cheap_and_leak_safe(self) -> None:
        # Mark the seeded root as a federated workspace. ``freshness_for`` must
        # NOT construct a second FederatedGraph (which would re-load every
        # child); it reads the root graph meta -- so it stays a cheap inline
        # scalar pair and cannot leak the workspace path or recorded SHA.
        (self.root / ".weld" / "workspaces.yaml").write_text(
            "version: 1\n", encoding="utf-8",
        )
        with mock.patch(
            "weld.federation_tools.federated_stale"
        ) as fed_stale:
            fr = _mcp_read.freshness_for(self.root)
        fed_stale.assert_not_called()  # no expensive child fan-out
        self.assertEqual(set(fr.keys()), {"stale", "commits_behind"})
        self.assertNotIn("deadbeef", json.dumps(fr))  # recorded git_sha


if __name__ == "__main__":
    unittest.main()

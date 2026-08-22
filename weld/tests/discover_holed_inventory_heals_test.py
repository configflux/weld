"""A graph with a hole in it heals in one pass, over real discovery (bd qmbp).

The unit half (``weld_inventory_coverage_audit_test``) pins that
``mark_state_published`` refuses to vouch for a body its inventory does not
describe. This is the half that matters to a reader: that refusing converges
instead of looping, and that the symbol which was unqueryable becomes
queryable.

The shape reproduced here is the one that was found on a real checkout: a
newly added module tracked in the inventory, with no node anywhere in the
graph a reader loads, and freshness reporting nothing wrong. ``ensure_seeded``
is the symbol from that report.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._discover_state_check import mark_state_published, state_vouches_for_graph
from weld._graph_meta_sidecar import write_graph_with_meta
from weld._staleness_coverage import coverage_stale
from weld.discover import discover
from weld.discovery_state import load_state

CONFIG = """sources:
  - glob: "pkg/**/*.py"
    type: file
    strategy: python_module
  - glob: "pkg/**/*.py"
    type: symbol
    strategy: python_callgraph
"""


class HoledInventoryHealsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.graph = self.root / ".weld" / "graph.json"
        (self.root / ".weld").mkdir(parents=True)
        (self.root / ".weld" / "discover.yaml").write_text(CONFIG, encoding="utf-8")
        (self.root / "pkg").mkdir()
        self._write("pkg/present.py", "def present():\n    return 1\n")
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@test.com"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "commit.gpgsign", "false"],
        ):
            self._git(cmd)
        self._commit("initial")

    def _write(self, rel: str, body: str) -> None:
        (self.root / rel).write_text(body, encoding="utf-8")

    def _git(self, cmd: list[str]) -> None:
        subprocess.run(
            cmd, cwd=str(self.root), capture_output=True, text=True,
            timeout=30, check=True, env={**os.environ, "LC_ALL": "C"},
        )

    def _commit(self, msg: str) -> None:
        self._git(["git", "add", "-A"])
        self._git(["git", "commit", "-m", msg, "--quiet"])

    def _publish(self) -> dict:
        """The real ``wd discover`` / ``wd warm`` tail: build, land, stamp."""
        graph = discover(self.root, with_sqlite=False)
        write_graph_with_meta(self.graph, graph)
        mark_state_published(self.root, self.graph)
        return graph

    def _nodes(self) -> dict:
        return json.loads(self.graph.read_text(encoding="utf-8"))["nodes"]

    def _has_symbol(self, name: str) -> bool:
        return any(k.endswith(f":{name}") for k in self._nodes())

    def test_hole_is_reported_then_closed_in_one_pass(self) -> None:
        self._publish()
        self.assertFalse(coverage_stale(self.root))

        # A new tracked module lands, and an inventory covering it is written
        # beside the OLD graph -- the ``write_graph=False`` shape, which is
        # what leaves a state describing a graph no reader can see.
        self._write("pkg/seed.py", "def ensure_seeded():\n    return 2\n")
        self._commit("add seed")
        discover(self.root, with_sqlite=False)  # state advances, graph does not

        self.assertFalse(
            self._has_symbol("ensure_seeded"),
            "precondition: the graph a reader loads has the hole",
        )
        state = load_state(self.root)
        self.assertIn("pkg/seed.py", state.files)
        self.assertNotIn("pkg/seed.py", state.files_with_no_nodes)

        # The inventory claims a node-bearing file the body does not anchor,
        # so nothing may vouch for it and freshness must say so.
        self.assertFalse(state_vouches_for_graph(state, self.graph))
        self.assertTrue(coverage_stale(self.root))

        # One refresh closes it: the symbol resolves and the pair is coherent.
        self._publish()
        self.assertTrue(self._has_symbol("ensure_seeded"))
        self.assertTrue(
            state_vouches_for_graph(load_state(self.root), self.graph),
        )
        self.assertFalse(coverage_stale(self.root))

    def test_stamp_is_refused_while_the_body_still_has_the_hole(self) -> None:
        """Landing an inventory over a stale body must not silence the hole."""
        self._publish()
        stale_body = self.graph.read_bytes()

        self._write("pkg/seed.py", "def ensure_seeded():\n    return 2\n")
        self._commit("add seed")
        discover(self.root, with_sqlite=False)

        # Put the old body back under the new inventory and try to vouch.
        self.graph.write_bytes(stale_body)
        mark_state_published(self.root, self.graph)

        self.assertIsNone(load_state(self.root).published_graph)
        self.assertTrue(coverage_stale(self.root))


if __name__ == "__main__":
    unittest.main()

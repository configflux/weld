"""End-to-end regression for intra-repo cross-batch origin (ADR 0042).

Builds a tiny single repo whose ``.weld/discover.yaml`` splits its
Python sources into two *disjoint* ``python_callgraph`` globs -- exactly
the shape of this project's own config (``weld/*.py`` vs
``weld/strategies/*.py``). A symbol in the second glob calls a function
defined in the first glob. Before the intra-repo origin reconciliation
pass, the second batch minted the resolved cross-glob target with
``origin="external"`` (its batch never saw the first glob's modules) and
clobbered the first batch's definite ``project`` node via the
orchestrator's ``dict.update`` merge.

This test runs the real ``python_callgraph`` strategy through the real
``discover`` orchestrator and asserts that the cross-glob first-party
target classifies as ``origin="project"`` via the canonical
``classify_node`` predicate -- the bug surfaced in ``wd viz`` on this
repo's own graph (``symbol:py:weld.discover:discover`` shown as
``external``), which made the "Hide third-party dependencies" filter
drop real project code.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._graph_origin import classify_node
from weld.discover import discover


_DISCOVER_YAML = textwrap.dedent(
    """
    sources:
      - glob: "core/*.py"
        type: symbol
        strategy: python_callgraph
      - glob: "plugins/*.py"
        type: symbol
        strategy: python_callgraph
    topology: {}
    """
).lstrip()


class IntraRepoCrossBatchOriginTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()
        (self.root / ".weld" / "discover.yaml").write_text(
            _DISCOVER_YAML, encoding="utf-8"
        )

        # First glob batch (core/*.py): defines the callee.
        (self.root / "core").mkdir()
        (self.root / "core" / "engine.py").write_text(
            textwrap.dedent(
                """
                def run():
                    return 1
                """
            ).lstrip(),
            encoding="utf-8",
        )

        # Second glob batch (plugins/*.py): calls core.engine.run, a
        # first-party symbol the plugins batch never walked. This is the
        # cross-batch reference that used to mint origin="external".
        (self.root / "plugins").mkdir()
        (self.root / "plugins" / "addon.py").write_text(
            textwrap.dedent(
                """
                from core.engine import run

                def go():
                    return run()
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _graph(self) -> dict:
        # Non-incremental, no sqlite, no on-disk write: pure build+return.
        return discover(
            self.root,
            incremental=False,
            with_sqlite=False,
        )

    def test_cross_batch_first_party_symbol_is_project(self) -> None:
        graph = self._graph()
        nodes = graph["nodes"]
        target_id = "symbol:py:core.engine:run"
        self.assertIn(
            target_id,
            nodes,
            "cross-batch target node must exist in the merged graph",
        )
        node = nodes[target_id]
        self.assertEqual(
            classify_node(node),
            "project",
            "a callgraph symbol resolving to a first-party module must "
            "classify origin=project, not external",
        )
        self.assertEqual(node["props"]["origin"], "project")

    def test_no_first_party_symbol_left_external(self) -> None:
        graph = self._graph()
        leaked = [
            nid
            for nid, n in graph["nodes"].items()
            if isinstance(n, dict)
            and n.get("type") == "symbol"
            and (n.get("props") or {}).get("language") == "python"
            and (n.get("props") or {}).get("origin") == "external"
            and (n.get("props") or {}).get("module")
            in {"core.engine", "plugins.addon"}
        ]
        self.assertEqual(
            leaked,
            [],
            f"first-party modules wrongly tagged external: {leaked}",
        )


if __name__ == "__main__":
    unittest.main()

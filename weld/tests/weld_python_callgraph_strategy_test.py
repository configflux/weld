"""Tests for the python_callgraph extraction strategy.

``weld/docs/adr/0004-call-graph-schema-extension.md``.

Builds a small fixture project on disk with known intra-module and
cross-module calls and asserts that the strategy emits the expected
``symbol`` nodes and ``calls`` edges, including the unresolved sentinel
form for unknown call targets.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies import python_callgraph  # noqa: E402

class PythonCallgraphStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        # Module A: defines helper() and main() which calls helper().
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.tmp / "pkg" / "a.py").write_text(
            textwrap.dedent(
                """
                from pkg.b import other_helper

                def helper():
                    return 1

                def main():
                    helper()
                    other_helper()
                    unknown_func()
                    int("3")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        # Module B: defines other_helper().
        (self.tmp / "pkg" / "b.py").write_text(
            textwrap.dedent(
                """
                def other_helper():
                    return 2
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _run(self) -> tuple[dict, list]:
        result = python_callgraph.extract(
            self.tmp,
            {"glob": "pkg/**/*.py"},
            {},
        )
        return result.nodes, result.edges

    def test_extracts_symbol_nodes(self) -> None:
        nodes, _ = self._run()
        # Expect symbols for helper, main, other_helper
        self.assertIn("symbol:py:pkg.a:helper", nodes)
        self.assertIn("symbol:py:pkg.a:main", nodes)
        self.assertIn("symbol:py:pkg.b:other_helper", nodes)
        for nid in (
            "symbol:py:pkg.a:helper",
            "symbol:py:pkg.a:main",
            "symbol:py:pkg.b:other_helper",
        ):
            self.assertEqual(nodes[nid]["type"], "symbol")
            self.assertEqual(
                nodes[nid]["props"]["source_strategy"], "python_callgraph"
            )
            self.assertEqual(nodes[nid]["props"]["language"], "python")

    def test_resolves_same_module_call(self) -> None:
        _, edges = self._run()
        wanted = {
            "from": "symbol:py:pkg.a:main",
            "to": "symbol:py:pkg.a:helper",
            "type": "calls",
        }
        match = next(
            (
                e
                for e in edges
                if e["from"] == wanted["from"]
                and e["to"] == wanted["to"]
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing same-module calls edge: {edges}")
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "helper")
        self.assertEqual(match["props"]["resolution"], "local")
        self.assertEqual(match["props"]["provenance"], {"file": "pkg/a.py", "line": 7})

    def test_resolves_imported_call(self) -> None:
        _, edges = self._run()
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == "symbol:py:pkg.b:other_helper"
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing import-resolved calls edge: {edges}")
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "other_helper")
        self.assertEqual(match["props"]["resolution"], "import")

    def test_unresolved_call_uses_sentinel(self) -> None:
        nodes, edges = self._run()
        sentinel = "symbol:unresolved:unknown_func"
        self.assertIn(sentinel, nodes)
        self.assertEqual(nodes[sentinel]["type"], "symbol")
        self.assertFalse(nodes[sentinel]["props"]["resolved"])
        # And there is a calls edge ending at the sentinel.
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == sentinel
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing unresolved calls edge: {edges}")
        self.assertFalse(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "unknown_func")
        self.assertEqual(match["props"]["resolution"], "unresolved")
        self.assertEqual(match["props"]["provenance"], {"file": "pkg/a.py", "line": 9})

    def test_builtin_call_is_classified(self) -> None:
        nodes, edges = self._run()
        sentinel = "symbol:unresolved:int"
        self.assertIn(sentinel, nodes)
        self.assertEqual(nodes[sentinel]["props"]["resolution"], "builtin")
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == sentinel
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing builtin calls edge: {edges}")
        self.assertFalse(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "int")
        self.assertEqual(match["props"]["resolution"], "builtin")

    def test_strategy_handles_syntax_error_files(self) -> None:
        bad = self.tmp / "pkg" / "broken.py"
        bad.write_text("def oops(:\n", encoding="utf-8")
        # Must not raise.
        nodes, edges = self._run()
        self.assertNotIn("symbol:py:pkg.broken:oops", nodes)


class PythonCallgraphOriginTaggingTest(unittest.TestCase):
    """ADR 0042 Python rules: every emitted node carries ``props.origin``."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="weld_origin_"))
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        # ``a.py``: project def + project-import + stdlib imports + builtin
        # call + genuinely-unresolved call + third-party import.
        (self.tmp / "pkg" / "a.py").write_text(
            textwrap.dedent(
                """
                from os.path import join
                from pkg.b import other_helper
                from third_party_pkg import foo

                def helper():
                    return 1

                def main():
                    helper()
                    other_helper()
                    join("a", "b")
                    foo()
                    print("hi")
                    nope_unknown()
                """
            ).lstrip(),
            encoding="utf-8",
        )
        (self.tmp / "pkg" / "b.py").write_text(
            textwrap.dedent(
                """
                def other_helper():
                    return 2
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _run(self) -> dict:
        from weld.strategies import python_callgraph as pc

        result = pc.extract(
            self.tmp,
            {"glob": "pkg/**/*.py"},
            {},
        )
        return result.nodes

    def test_project_symbol_origin_is_project(self) -> None:
        """Locally-defined symbols carry origin=project."""
        nodes = self._run()
        for nid in (
            "symbol:py:pkg.a:helper",
            "symbol:py:pkg.a:main",
            "symbol:py:pkg.b:other_helper",
        ):
            self.assertEqual(
                nodes[nid]["props"].get("origin"),
                "project",
                f"{nid} should be origin=project",
            )

    def test_project_import_origin_is_project(self) -> None:
        """An import resolved to another project module is origin=project."""
        nodes = self._run()
        # ``other_helper`` is owned by pkg.b and walked as a project def;
        # the symbols-pass already emits it with origin=project. The
        # cross-module-import ``setdefault`` from pkg.a's main() must
        # not overwrite that.
        node = nodes["symbol:py:pkg.b:other_helper"]
        self.assertEqual(node["props"].get("origin"), "project")

    def test_stdlib_resolved_import_origin_is_stdlib(self) -> None:
        """``from os.path import join`` then ``join(...)`` -> origin=stdlib."""
        nodes = self._run()
        nid = "symbol:py:os.path:join"
        self.assertIn(nid, nodes, "stdlib import target node not minted")
        self.assertEqual(nodes[nid]["props"].get("origin"), "stdlib")

    def test_builtin_sentinel_origin_is_stdlib(self) -> None:
        """``print(...)`` -> sentinel with resolution=builtin -> origin=stdlib."""
        nodes = self._run()
        nid = "symbol:unresolved:print"
        self.assertIn(nid, nodes)
        self.assertEqual(nodes[nid]["props"].get("origin"), "stdlib")
        self.assertEqual(nodes[nid]["props"].get("resolution"), "builtin")

    def test_external_import_origin_is_external(self) -> None:
        """Third-party import (not in stdlib, not in project) -> external."""
        nodes = self._run()
        nid = "symbol:py:third_party_pkg:foo"
        self.assertIn(nid, nodes)
        self.assertEqual(nodes[nid]["props"].get("origin"), "external")

    def test_unresolved_sentinel_origin_is_unresolved(self) -> None:
        """A name that resolves nowhere -> sentinel with origin=unresolved."""
        nodes = self._run()
        nid = "symbol:unresolved:nope_unknown"
        self.assertIn(nid, nodes)
        self.assertEqual(nodes[nid]["props"].get("origin"), "unresolved")
        self.assertEqual(nodes[nid]["props"].get("resolution"), "unresolved")

    def test_every_emitted_node_has_origin(self) -> None:
        """ADR 0042 contract: every node from this strategy carries origin."""
        nodes = self._run()
        self.assertGreater(len(nodes), 0)
        missing = [
            nid
            for nid, node in nodes.items()
            if "origin" not in (node.get("props") or {})
        ]
        self.assertEqual(missing, [], f"nodes missing props.origin: {missing}")

    def test_origin_set_is_subset_of_taxonomy(self) -> None:
        """Only the four ADR 0042 origin values may appear."""
        nodes = self._run()
        seen = {node["props"]["origin"] for node in nodes.values()}
        allowed = {"project", "stdlib", "external", "unresolved"}
        self.assertTrue(
            seen.issubset(allowed), f"unexpected origin values: {seen - allowed}"
        )

    def test_origin_is_deterministic(self) -> None:
        """Re-running extraction yields identical origin tags per node."""
        first = {nid: node["props"]["origin"] for nid, node in self._run().items()}
        second = {nid: node["props"]["origin"] for nid, node in self._run().items()}
        self.assertEqual(first, second)


class PythonCallgraphOriginPackageMembershipTest(unittest.TestCase):
    """An import to a module that is itself a project file is origin=project.

    Distinct from ``test_project_import_origin_is_project`` above: that
    case relies on the symbol-walking pass already emitting the target.
    Here, the imported module is a sibling project file but the call
    site does not also walk it directly -- so the resolved-target
    ``setdefault`` is the one that mints the node, and it must still
    classify as project via ``project_modules`` membership.
    """

    def test_resolved_setdefault_uses_project_module_set(self) -> None:
        from weld.strategies import python_callgraph as pc

        td = Path(tempfile.mkdtemp(prefix="weld_proj_origin_"))
        (td / "myapp").mkdir()
        (td / "myapp" / "__init__.py").write_text("", encoding="utf-8")
        # Module ``myapp.config`` only declares a constant; no ``def``.
        # ``python_callgraph`` will walk the file but mint no symbol
        # nodes for it -- so any ``symbol:py:myapp.config:*`` must come
        # from a ``setdefault`` in the resolved-target path.
        (td / "myapp" / "config.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (td / "myapp" / "main.py").write_text(
            textwrap.dedent(
                """
                from myapp.config import lookup

                def go():
                    lookup()
                """
            ).lstrip(),
            encoding="utf-8",
        )
        result = pc.extract(td, {"glob": "myapp/**/*.py"}, {})
        nid = "symbol:py:myapp.config:lookup"
        self.assertIn(nid, result.nodes)
        self.assertEqual(
            result.nodes[nid]["props"].get("origin"),
            "project",
            "resolved import to a project module must classify as project",
        )

if __name__ == "__main__":
    unittest.main()

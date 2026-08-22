"""Tests for ``weld._graph_edge_provenance_lint`` (ADR 0074, sixth amendment).

Covers every branch of the ``cross-source-edge-provenance`` rule: the
cross-entry violation it exists to catch, the four exemptions that keep it
from false-positiving on provably-safe shapes, and the entry-vs-strategy-name
distinction that is the whole reason a naive "same strategy name" exemption
would be unsound (it would silently re-open the original ADR 0074 defect for
any strategy registered on more than one disjoint glob).

Fixtures write real (near-empty) files under a temp root because the rule
resolves discover.yaml source entries with the same filesystem glob walker
the strategies themselves use -- there is no lighter-weight way to prove the
entry-membership exemption without exercising that walk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._graph_edge_provenance_lint import (
    NARROWS_OWN_DIRTY_SCOPE,
    check_cross_source_edge_provenance,
)


def _write(root: Path, rel: str, text: str = "# x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _node(file_: str | None = None, roles: list[str] | None = None) -> dict:
    props: dict = {}
    if file_ is not None:
        props["file"] = file_
    if roles is not None:
        props["roles"] = roles
    return {"type": "file", "label": "x", "props": props}


def _edge(
    from_id: str,
    to_id: str,
    strategy: str,
    *,
    edge_type: str = "refs",
    provenance: str | None = None,
) -> dict:
    props: dict = {"source_strategy": strategy}
    if provenance is not None:
        props["provenance"] = {"file": provenance}
    return {"from": from_id, "to": to_id, "type": edge_type, "props": props}


def _yaml(*entries: str) -> str:
    return "sources:\n" + "".join(entries)


def _glob_entry(strategy: str, glob: str) -> str:
    return f"  - glob: {glob}\n    type: file\n    strategy: {strategy}\n"


class CrossSourceEdgeProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / ".weld").mkdir()

    def _discover_yaml(self, text: str) -> None:
        (self.root / ".weld" / "discover.yaml").write_text(
            text, encoding="utf-8",
        )

    def _check(self, nodes: dict, edges: list[dict]) -> list:
        return list(check_cross_source_edge_provenance(self.root, nodes, edges))

    def test_cross_entry_edge_without_provenance_is_flagged(self) -> None:
        """The core case: two disjoint source entries, no stamp -- bd 57lra."""
        _write(self.root, "a/producer.py")
        _write(self.root, "b/target.py")
        self._discover_yaml(_yaml(
            _glob_entry("alpha", "a/*.py"),
            _glob_entry("beta", "b/*.py"),
        ))
        nodes = {
            "file:a/producer": _node("a/producer.py"),
            "file:b/target": _node("b/target.py"),
        }
        edges = [_edge("file:a/producer", "file:b/target", "alpha")]
        violations = self._check(nodes, edges)
        self.assertEqual(1, len(violations))
        self.assertIn("alpha", violations[0].message)
        self.assertIn("provenance.file", violations[0].message)

    def test_cross_entry_edge_with_provenance_is_not_flagged(self) -> None:
        _write(self.root, "a/producer.py")
        _write(self.root, "b/target.py")
        self._discover_yaml(_yaml(
            _glob_entry("alpha", "a/*.py"),
            _glob_entry("beta", "b/*.py"),
        ))
        nodes = {
            "file:a/producer": _node("a/producer.py"),
            "file:b/target": _node("b/target.py"),
        }
        edges = [_edge(
            "file:a/producer", "file:b/target", "alpha",
            provenance="a/producer.py",
        )]
        self.assertEqual([], self._check(nodes, edges))

    def test_same_entry_edge_without_provenance_is_exempt(self) -> None:
        """bd 41vw shape: one glob owns both endpoints -- a whole-glob
        re-run on either dirty file re-mints the edge regardless."""
        _write(self.root, "docs/guide.md")
        _write(self.root, "docs/target.md")
        self._discover_yaml(_yaml(_glob_entry("markdown", "docs/*.md")))
        self.assertNotIn("markdown", NARROWS_OWN_DIRTY_SCOPE)
        nodes = {
            "doc:guide": _node("docs/guide.md"),
            "doc:target": _node("docs/target.md"),
        }
        edges = [_edge("doc:guide", "doc:target", "markdown", edge_type="relates_to")]
        self.assertEqual([], self._check(nodes, edges))

    def test_narrows_own_dirty_scope_same_entry_is_still_flagged(self) -> None:
        """The sabotage shape: same entry does NOT exempt python_callgraph.

        Reproduces the original cjij.2 defect's exact edge topology (a
        cross-file symbol edge within ONE glob) to prove the entry-
        membership exemption does not silently re-open it.
        """
        self.assertIn("python_callgraph", NARROWS_OWN_DIRTY_SCOPE)
        _write(self.root, "weld/caller.py")
        _write(self.root, "weld/callee.py")
        self._discover_yaml(_yaml(_glob_entry("python_callgraph", "weld/*.py")))
        nodes = {
            "symbol:caller": _node("weld/caller.py"),
            "symbol:callee": _node("weld/callee.py"),
        }
        edges = [_edge(
            "symbol:caller", "symbol:callee", "python_callgraph",
            edge_type="calls",
        )]
        violations = self._check(nodes, edges)
        self.assertEqual(1, len(violations))
        self.assertIn("python_callgraph", violations[0].message)

    def test_multi_entry_same_strategy_cross_glob_edge_is_flagged(self) -> None:
        """Entry granularity, not strategy-name granularity.

        One strategy registered on two *disjoint* glob entries is two
        independent dirty-scopes -- collapsing them (as a naive "merge by
        strategy name" resolver would) is exactly the unsoundness this
        rule's entry-level resolution avoids. Uses a strategy name outside
        NARROWS_OWN_DIRTY_SCOPE so this failure mode is isolated from that
        exemption entirely: entry granularity alone must catch it.
        """
        _write(self.root, "a/one.py")
        _write(self.root, "b/two.py")
        self._discover_yaml(_yaml(
            _glob_entry("widget", "a/*.py"),
            _glob_entry("widget", "b/*.py"),
        ))
        nodes = {
            "file:a/one": _node("a/one.py"),
            "file:b/two": _node("b/two.py"),
        }
        edges = [_edge("file:a/one", "file:b/two", "widget")]
        violations = self._check(nodes, edges)
        self.assertEqual(1, len(violations))

    def test_package_role_from_node_is_exempt(self) -> None:
        """python_package's self-repair mechanism, not ADR 0074 provenance."""
        _write(self.root, "pkg/mod.py")
        self._discover_yaml(_yaml(_glob_entry("python_package", "pkg/*.py")))
        nodes = {
            "package:pkg": _node(None, roles=["package"]),
            "file:pkg/mod": _node("pkg/mod.py"),
        }
        edges = [_edge(
            "package:pkg", "file:pkg/mod", "python_package",
            edge_type="contains",
        )]
        self.assertEqual([], self._check(nodes, edges))

    def test_fileless_to_node_is_exempt(self) -> None:
        """An endpoint purge_stale_nodes can never purge by file-dirtying."""
        _write(self.root, "a/producer.py")
        self._discover_yaml(_yaml(_glob_entry("alpha", "a/*.py")))
        nodes = {
            "file:a/producer": _node("a/producer.py"),
            "external-dep:left-pad": _node(None),
        }
        edges = [_edge(
            "file:a/producer", "external-dep:left-pad", "alpha",
            edge_type="depends_on",
        )]
        self.assertEqual([], self._check(nodes, edges))

    def test_from_node_missing_file_is_flagged(self) -> None:
        """Conservative default: cannot prove safety without a from-file."""
        _write(self.root, "b/target.py")
        # concept_from_bd is a path:-declared (non-glob) strategy in real
        # discover.yaml -- declared_strategies() must see it too, not just
        # glob entries, so this fixture pins that alongside the branch
        # under test.
        self._discover_yaml(_yaml(
            _glob_entry("beta", "b/*.py"),
            '  - path: "issues.jsonl"\n    type: concept\n'
            "    strategy: concept_from_bd\n",
        ))
        nodes = {
            "concept:widget": _node(None, roles=["doc"]),
            "file:b/target": _node("b/target.py"),
        }
        edges = [_edge(
            "concept:widget", "file:b/target", "concept_from_bd",
            edge_type="relates_to",
        )]
        self.assertEqual(1, len(self._check(nodes, edges)))

    def test_intra_file_edge_is_exempt(self) -> None:
        _write(self.root, "a/self.py")
        self._discover_yaml(_yaml(_glob_entry("alpha", "a/*.py")))
        nodes = {
            "symbol:a": _node("a/self.py"),
            "symbol:b": _node("a/self.py"),
        }
        edges = [_edge("symbol:a", "symbol:b", "alpha")]
        self.assertEqual([], self._check(nodes, edges))

    def test_undeclared_strategy_is_skipped(self) -> None:
        """Post-processing synthesis (graph_closure) is never in sources:
        and re-runs in full every pass -- never at ADR 0074 purge risk."""
        _write(self.root, "a/one.py")
        _write(self.root, "b/two.py")
        self._discover_yaml(_yaml(_glob_entry("alpha", "a/*.py")))
        nodes = {
            "file:a/one": _node("a/one.py"),
            "file:b/two": _node("b/two.py"),
        }
        edges = [_edge("file:a/one", "file:b/two", "graph_closure")]
        self.assertEqual([], self._check(nodes, edges))

    def test_no_discover_yaml_yields_no_violations(self) -> None:
        nodes = {
            "file:a/one": _node("a/one.py"),
            "file:b/two": _node("b/two.py"),
        }
        edges = [_edge("file:a/one", "file:b/two", "alpha")]
        self.assertEqual([], self._check(nodes, edges))


if __name__ == "__main__":
    unittest.main()

"""Tests for the upgraded Mermaid serializer (:mod:`weld._export_mermaid`).

Covers the czmj upgrade: per-file/module ``subgraph`` clustering, per-type
``classDef`` styling, human-readable escaped labels, external-endpoint
placeholders, and explicit truncation annotation. Kept separate from
``weld_export_test.py`` so both files stay under the 400-line cap.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _dummy_graph() -> Graph:
    """A loadable graph. Content is irrelevant: every test passes explicit
    ``nodes=``/``edges=`` to ``to_mermaid``, so the graph is only the object
    the serializer signature requires."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(
        json.dumps({
            "meta": {"version": SCHEMA_VERSION, "git_sha": "deadbeef",
                     "updated_at": "2026-04-06T00:00:00+00:00"},
            "nodes": {}, "edges": [],
        }),
        encoding="utf-8",
    )
    g = Graph(tmp)
    g.load()
    return g


_CLUSTER_NODES: dict[str, dict] = {
    "file:pkg/mod": {"type": "file", "label": "mod",
        "props": {"file": "pkg/mod.py"}},
    "symbol:py:pkg.mod:foo": {"type": "symbol", "label": "pkg.mod:foo",
        "props": {"file": "pkg/mod.py", "module": "pkg.mod"}},
    "symbol:py:pkg.mod:bar": {"type": "symbol", "label": "pkg.mod:bar",
        "props": {"file": "pkg/mod.py", "module": "pkg.mod"}},
    "doc:readme": {"type": "doc", "label": "Readme",
        "props": {"file": "README.md"}},
    "package:py:os": {"type": "package", "label": "os", "props": {}},
}
_CLUSTER_EDGES: list[dict] = [
    {"from": "symbol:py:pkg.mod:foo", "to": "file:pkg/mod",
     "type": "contains", "props": {}},
]


def _parse_membership(mermaid: str) -> dict[str, str]:
    """Map each declared node key to its containing subgraph id ('' = top)."""
    membership: dict[str, str] = {}
    current = ""
    for raw in mermaid.splitlines():
        line = raw.strip()
        if line.startswith("subgraph "):
            current = line.split()[1].split("[")[0]
        elif line == "end":
            current = ""
        elif (
            "[" in line
            and "-->" not in line
            and not line.startswith(("classDef", "class ", "%%", "flowchart"))
        ):
            membership[line.split("[")[0].strip()] = current
    return membership


class MermaidClusteringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def _render(self, **kw) -> str:
        from weld.export import to_mermaid
        return to_mermaid(
            self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES, **kw)

    def test_has_subgraph_blocks(self) -> None:
        out = self._render()
        self.assertIn("subgraph ", out)
        headers = [ln for ln in out.splitlines() if ln.strip().startswith("subgraph ")]
        ends = [ln for ln in out.splitlines() if ln.strip() == "end"]
        self.assertEqual(len(headers), 2)  # pkg/mod.py and README.md
        self.assertEqual(len(headers), len(ends))

    def test_same_file_nodes_share_subgraph(self) -> None:
        membership = _parse_membership(self._render())
        keys = ["file_pkg_mod", "symbol_py_pkg_mod_foo", "symbol_py_pkg_mod_bar"]
        groups = {membership[k] for k in keys}
        self.assertEqual(len(groups), 1)
        self.assertTrue(next(iter(groups)).startswith("grp_file_"))

    def test_ungrouped_node_is_top_level(self) -> None:
        membership = _parse_membership(self._render())
        self.assertEqual(membership["package_py_os"], "")

    def test_subgraph_title_is_readable_path(self) -> None:
        out = self._render()
        self.assertIn('["pkg/mod.py"]', out)
        self.assertIn('["README.md"]', out)

    def test_comments_at_column_zero(self) -> None:
        # Mermaid documents %% comments on their own line at column 0;
        # indentation before %% is not guaranteed to parse.
        out = self._render()
        comment_lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("%%")]
        self.assertTrue(comment_lines)
        for line in comment_lines:
            self.assertFalse(line.startswith(" "), f"indented comment: {line!r}")


class MermaidStylingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def _render(self) -> str:
        from weld.export import to_mermaid
        return to_mermaid(self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES)

    def test_classdef_per_present_type(self) -> None:
        out = self._render()
        for cls in ("weld_file", "weld_symbol", "weld_doc", "weld_package"):
            self.assertIn(f"classDef {cls} ", out)

    def test_nodes_assigned_to_type_class(self) -> None:
        out = self._render()
        line = next(ln for ln in out.splitlines() if ln.strip().startswith("class ")
                    and ln.strip().endswith("weld_symbol;"))
        self.assertIn("symbol_py_pkg_mod_foo", line)
        self.assertIn("symbol_py_pkg_mod_bar", line)


class MermaidLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def test_labels_stay_human_readable(self) -> None:
        from weld.export import to_mermaid
        out = to_mermaid(self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES)
        # sanitized KEY for the node, but readable dotted/colon label text.
        self.assertIn("symbol_py_pkg_mod_foo[", out)
        self.assertIn('"pkg.mod:foo (symbol)"', out)

    def test_special_chars_escaped_with_entities(self) -> None:
        from weld.export import to_mermaid
        nodes = {"symbol:x:weird": {"type": "symbol",
            "label": 'say "hi" <b> #1', "props": {"file": "x.py"}}}
        out = to_mermaid(self.graph, nodes=nodes, edges=[])
        self.assertIn("say #quot;hi#quot; #lt;b#gt; #35;1 (symbol)", out)
        body = next(ln for ln in out.splitlines() if "symbol_x_weird[" in ln)
        self.assertEqual(body.count('"'), 2)  # only the two wrapping quotes

    def test_unit_separator_shown_as_slash(self) -> None:
        from weld.export import to_mermaid
        nodes = {"repo:a\x1frpc:call": {"type": "rpc", "label": "a\x1frpc:call",
            "props": {}}}
        out = to_mermaid(self.graph, nodes=nodes, edges=[])
        self.assertNotIn("\x1f", out)
        self.assertIn("a/rpc:call (rpc)", out)


def _homogeneous_lexical_graph() -> tuple[dict[str, dict], list[dict]]:
    """Lexical-first slice is homogeneous: ten ``file:*`` ids sort before the
    ``route:*``/``symbol:*`` ids, so ``sorted(nodes)[:6]`` is all ``file``."""
    nodes: dict[str, dict] = {
        f"{t}:{t[0]}{i:02d}": {"type": t, "label": f"{t[0]}{i}", "props": {}}
        for t, n in (("file", 10), ("route", 3), ("symbol", 4)) for i in range(n)
    }
    return nodes, []


class MermaidTruncationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _dummy_graph()
        # Mixed types whose lexical order groups by type (all ``file:*`` sort
        # before all ``symbol:*``): the old first-N slice would be all-file,
        # while balanced round-robin keeps the top node of each type.
        self.nodes = {f"{t}:{c}": {"type": t, "label": f"{t[0]}{c}", "props": {}}
                      for t, cs in (("file", "abc"), ("symbol", "ab")) for c in cs}
        # file:a->file:b; the cap-2 selection drops file:b so the edge is gone.
        self.edges = [{"from": "file:a", "to": "file:b",
                       "type": "calls", "props": {}}]

    def _render(self, max_nodes: int):
        from weld.export import to_mermaid
        return to_mermaid(
            self.graph, nodes=self.nodes, edges=self.edges, max_nodes=max_nodes)

    def test_truncation_note_and_comment_present(self) -> None:
        out = self._render(max_nodes=2)
        self.assertIn("%% NOTE: diagram truncated", out)
        self.assertIn("weld_truncation_note[", out)
        self.assertIn("of 5 nodes", out)
        # Reserved meta style is namespaced away from any per-type ``weld_note``.
        self.assertIn("class weld_truncation_note weldmeta_note;", out)

    def test_only_capped_nodes_rendered(self) -> None:
        membership = _parse_membership(self._render(max_nodes=2))
        kept = set(membership) - {"weld_truncation_note"}
        # Top of each type bucket (file:a, symbol:a) -- NOT the lexical first-2
        # {file:a, file:b}, which would be an all-``file`` slice.
        self.assertEqual(kept, {"file_a", "symbol_a"})

    def test_edge_to_dropped_node_omitted(self) -> None:
        out = self._render(max_nodes=2)
        # file:b is dropped by the cap-2 selection, so its edge is omitted.
        self.assertNotIn("-->|calls|", out)

    def test_no_truncation_when_under_cap(self) -> None:
        out = self._render(max_nodes=100)
        self.assertNotIn("truncated", out)
        self.assertNotIn("weld_truncation_note", out)

    def test_truncation_is_deterministic(self) -> None:
        # Balanced selection is a pure fn of (nodes, edges, cap), emitted sorted.
        self.assertEqual(self._render(max_nodes=2), self._render(max_nodes=2))

    def test_balanced_selection_spans_types_and_is_deterministic(self) -> None:
        from weld.export import to_mermaid
        nodes, edges = _homogeneous_lexical_graph()
        # Precondition: the old lexical-first slice would be type-homogeneous.
        self.assertEqual({nodes[n]["type"] for n in sorted(nodes)[:6]}, {"file"})
        out = to_mermaid(self.graph, nodes=nodes, edges=edges, max_nodes=6)
        again = to_mermaid(self.graph, nodes=nodes, edges=edges, max_nodes=6)
        self.assertEqual(out, again)  # byte-identical on the truncated path
        # Balanced selection spans the types present (per-type class weld_<type>).
        per_type = {c for c in _class_assignments(out) if c.startswith("weld_")}
        self.assertGreater(len(per_type), 1)
        self.assertEqual(per_type, {"weld_file", "weld_route", "weld_symbol"})

    def test_balanced_selection_prefers_high_degree_within_type(self) -> None:
        from weld._export_mermaid import _balanced_selection
        nodes = {f"symbol:s{i}": {"type": "symbol", "label": f"s{i}",
                                  "props": {}} for i in range(4)}
        # Within one type, degree wins: s3 (3 edges) beats lexical-first s0.
        edges = [{"from": "symbol:s3", "to": f"symbol:s{i}"} for i in range(3)]
        self.assertEqual(_balanced_selection(nodes, edges, 1), {"symbol:s3"})


class MermaidExternalEdgeTest(unittest.TestCase):
    """Cross-repo/dangling edges keep their edge + a marked placeholder."""

    def setUp(self) -> None:
        self.graph = _dummy_graph()
        self.nodes = {"repo:a": {"type": "repo", "label": "a", "props": {}}}
        # endpoint 'rpc:a:call' is not a node in this set (lives elsewhere).
        self.edges = [{"from": "repo:a", "to": "rpc:a:call",
                       "type": "cross_repo:calls", "props": {}}]

    def _render(self) -> str:
        from weld.export import to_mermaid
        return to_mermaid(self.graph, nodes=self.nodes, edges=self.edges)

    def test_edge_to_external_endpoint_preserved(self) -> None:
        self.assertIn("-->|cross_repo:calls|", self._render())

    def test_external_endpoint_declared_and_marked(self) -> None:
        out = self._render()
        self.assertIn("(external)", out)
        # Reserved meta style is namespaced away from any per-type ``weld_external``.
        self.assertIn("classDef weldmeta_external ", out)
        self.assertIn("weldmeta_external;", out)


class MermaidDeterminismTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def test_full_render_byte_identical(self) -> None:
        from weld.export import to_mermaid
        a = to_mermaid(self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES)
        b = to_mermaid(self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES)
        self.assertEqual(a, b)

    def test_subgraph_end_balance(self) -> None:
        from weld.export import to_mermaid
        out = to_mermaid(self.graph, nodes=_CLUSTER_NODES, edges=_CLUSTER_EDGES)
        headers = sum(1 for ln in out.splitlines()
                      if ln.strip().startswith("subgraph "))
        ends = sum(1 for ln in out.splitlines() if ln.strip() == "end")
        self.assertEqual(headers, ends)


def _class_assignments(mermaid: str) -> dict[str, set[str]]:
    """Map each classDef name to the set of node keys assigned to it via
    ``class <ids> <name>;`` lines (``classDef`` declarations are excluded)."""
    out: dict[str, set[str]] = {}
    for raw in mermaid.splitlines():
        line = raw.strip()
        if line.startswith("class ") and line.endswith(";"):
            ids, _, cls = line[len("class "):-1].rpartition(" ")
            out.setdefault(cls, set()).update(ids.split(","))
    return out


def _subgraph_ids(mermaid: str) -> list[str]:
    return [ln.strip().split()[1].split("[")[0]
            for ln in mermaid.splitlines() if ln.strip().startswith("subgraph ")]


class MermaidReservedNamespaceTest(unittest.TestCase):
    """Item 1: reserved meta styles cannot collide with per-type ``classDef``s
    even for a future node type literally named ``note`` or ``external``."""

    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def test_reserved_names_are_unreachable_by_classdef_name(self) -> None:
        from weld._export_mermaid import (
            _EXTERNAL_CLASS, _NOTE_CLASS, _classdef_name,
        )
        # A node type named 'note'/'external' produces a per-type class that is
        # NOT the reserved meta style, and the reserved names break the
        # ``weld_`` shape that ``_classdef_name`` always emits.
        self.assertNotEqual(_classdef_name("note"), _NOTE_CLASS)
        self.assertNotEqual(_classdef_name("external"), _EXTERNAL_CLASS)
        self.assertFalse(_NOTE_CLASS.startswith("weld_"))
        self.assertFalse(_EXTERNAL_CLASS.startswith("weld_"))

    def test_note_type_coexists_with_truncation_note(self) -> None:
        from weld.export import to_mermaid
        nodes = {f"x:{i}": {"type": "note", "label": f"n{i}", "props": {}}
                 for i in range(3)}
        out = to_mermaid(self.graph, nodes=nodes, edges=[], max_nodes=1)
        # per-type 'note' style AND the reserved truncation style both present,
        # and they are distinct classDefs.
        self.assertIn("classDef weld_note ", out)
        self.assertIn("classDef weldmeta_note ", out)
        assigns = _class_assignments(out)
        self.assertEqual(assigns.get("weldmeta_note"), {"weld_truncation_note"})
        self.assertTrue(assigns.get("weld_note"))  # the kept 'note'-typed node
        self.assertNotIn("weld_truncation_note", assigns["weld_note"])

    def test_external_type_coexists_with_placeholder(self) -> None:
        from weld.export import to_mermaid
        nodes = {"n:1": {"type": "external", "label": "real", "props": {}}}
        edges = [{"from": "n:1", "to": "ext:1", "type": "calls", "props": {}}]
        out = to_mermaid(self.graph, nodes=nodes, edges=edges)
        self.assertIn("classDef weld_external ", out)       # per-type
        self.assertIn("classDef weldmeta_external ", out)   # reserved placeholder
        assigns = _class_assignments(out)
        self.assertEqual(assigns.get("weldmeta_external"), {"ext_1"})
        self.assertEqual(assigns.get("weld_external"), {"n_1"})


class MermaidIdCollisionTest(unittest.TestCase):
    """Item 2: distinct source ids/group values that char-sanitize alike must
    map to distinct mermaid ids instead of silently merging."""

    def setUp(self) -> None:
        self.graph = _dummy_graph()

    def _render(self, nodes, edges=()):
        from weld.export import to_mermaid
        return to_mermaid(self.graph, nodes=nodes, edges=list(edges))

    def test_colliding_node_ids_do_not_merge(self) -> None:
        nodes = {"a:b": {"type": "symbol", "label": "AB1", "props": {}},
                 "a-b": {"type": "symbol", "label": "AB2", "props": {}}}
        out = self._render(nodes)
        keys = set(_parse_membership(out))
        self.assertEqual(len(keys), 2)                 # two distinct keys, no merge
        self.assertTrue(all(k.startswith("a_b") for k in keys))
        self.assertEqual(out, self._render(nodes))     # deterministic

    def test_non_colliding_ids_keep_bare_sanitized_form(self) -> None:
        nodes = {"a:b": {"type": "symbol", "label": "AB", "props": {}},
                 "c:d": {"type": "symbol", "label": "CD", "props": {}}}
        keys = set(_parse_membership(self._render(nodes)))
        self.assertEqual(keys, {"a_b", "c_d"})         # no gratuitous suffixes

    def test_colliding_group_values_do_not_merge(self) -> None:
        nodes = {"s:1": {"type": "symbol", "label": "s1", "props": {"file": "a/b"}},
                 "s:2": {"type": "symbol", "label": "s2", "props": {"file": "a.b"}}}
        out = self._render(nodes)
        subs = _subgraph_ids(out)
        self.assertEqual(len(subs), 2)                 # two distinct subgraphs
        self.assertEqual(len(set(subs)), 2)
        self.assertTrue(all(s.startswith("grp_file_a_b") for s in subs))

    def test_non_colliding_group_values_keep_bare_form(self) -> None:
        nodes = {"s:1": {"type": "symbol", "label": "s1", "props": {"file": "a/b"}},
                 "s:2": {"type": "symbol", "label": "s2", "props": {"file": "c/d"}}}
        subs = set(_subgraph_ids(self._render(nodes)))
        self.assertEqual(subs, {"grp_file_a_b", "grp_file_c_d"})


if __name__ == "__main__":
    unittest.main()

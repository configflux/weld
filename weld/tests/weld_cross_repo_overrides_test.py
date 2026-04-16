"""Tests for weld.cross_repo.overrides: schema, YAML loader, merge logic."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from weld.cross_repo.base import CrossRepoEdge
from weld.cross_repo.overrides import (
    Override,
    OverrideParseError,
    apply_overrides,
    load_overrides,
)

S = "\x1f"  # unit separator shorthand


def _ws(yaml: str | None = None) -> str:
    """Create a temp workspace root with an optional override file."""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".weld"), exist_ok=True)
    if yaml is not None:
        with open(os.path.join(root, ".weld", "cross_repo_overrides.yaml"), "w") as f:
            f.write(yaml)
    return root


def _ovr(from_id="a", to_id="b", typ="invokes", action="add", **props):
    return Override(from_id=from_id, to_id=to_id, type=typ, action=action, props=props)


def _edge(from_id=f"a{S}n1", to_id=f"b{S}n2", typ="invokes", **props):
    return CrossRepoEdge(from_id=from_id, to_id=to_id, type=typ, props=props)


class OverrideDataclassTests(unittest.TestCase):

    def test_frozen(self) -> None:
        e = _ovr()
        with self.assertRaises(AttributeError):
            e.action = "remove"  # type: ignore[misc]

    def test_defaults_to_empty_props(self) -> None:
        self.assertEqual(Override(from_id="a", to_id="b", type="t", action="add").props, {})

    def test_to_edge_shape(self) -> None:
        edge = _ovr(from_id=f"a{S}n1", to_id=f"b{S}n2", typ="invokes", path="/api").to_edge()
        self.assertIsInstance(edge, CrossRepoEdge)
        self.assertEqual(edge.from_id, f"a{S}n1")
        self.assertEqual(edge.type, "invokes")
        self.assertEqual(edge.props["path"], "/api")
        self.assertEqual(edge.props["source"], "manual_override")

    def test_to_edge_preserves_user_source(self) -> None:
        edge = _ovr(source="custom").to_edge()
        self.assertEqual(edge.props["source"], "custom")


class LoadOverridesTests(unittest.TestCase):

    def test_missing_file(self) -> None:
        self.assertEqual(load_overrides(tempfile.mkdtemp()), [])

    def test_empty_file(self) -> None:
        self.assertEqual(load_overrides(_ws("")), [])

    def test_whitespace_only(self) -> None:
        self.assertEqual(load_overrides(_ws("  \n  ")), [])

    def test_add_entry(self) -> None:
        entries = load_overrides(_ws(
            "overrides:\n"
            "  - from: caller\n"
            "    to: endpoint\n"
            "    type: invokes\n"
            "    action: add\n"
            "    props:\n"
            "      path: /api\n"
        ))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].from_id, "caller")
        self.assertEqual(entries[0].action, "add")
        self.assertEqual(entries[0].props.get("path"), "/api")

    def test_remove_entry(self) -> None:
        entries = load_overrides(_ws(
            "overrides:\n"
            "  - from: a\n    to: b\n    type: t\n    action: remove\n"
        ))
        self.assertEqual(entries[0].action, "remove")

    def test_multiple_entries(self) -> None:
        entries = load_overrides(_ws(
            "overrides:\n"
            "  - from: a\n    to: b\n    type: t1\n    action: add\n"
            "  - from: c\n    to: d\n    type: t2\n    action: remove\n"
        ))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].action, "add")
        self.assertEqual(entries[1].action, "remove")

    def test_missing_required_key(self) -> None:
        with self.assertRaises(OverrideParseError) as ctx:
            load_overrides(_ws("overrides:\n  - from: a\n    to: b\n    action: add\n"))
        self.assertIn("type", str(ctx.exception))

    def test_invalid_action(self) -> None:
        with self.assertRaises(OverrideParseError) as ctx:
            load_overrides(_ws(
                "overrides:\n  - from: a\n    to: b\n    type: t\n    action: modify\n"
            ))
        self.assertIn("modify", str(ctx.exception))

    def test_non_mapping_entry(self) -> None:
        with self.assertRaises(OverrideParseError) as ctx:
            load_overrides(_ws("overrides:\n  - just_a_string\n"))
        self.assertIn("mapping", str(ctx.exception))

    def test_non_mapping_top_level(self) -> None:
        with self.assertRaises(OverrideParseError) as ctx:
            load_overrides(_ws("- item1\n- item2\n"))
        self.assertIn("mapping", str(ctx.exception))

    def test_overrides_not_a_list(self) -> None:
        with self.assertRaises(OverrideParseError) as ctx:
            load_overrides(_ws("overrides: not_a_list\n"))
        self.assertIn("sequence", str(ctx.exception))

    def test_action_case_insensitive(self) -> None:
        entries = load_overrides(_ws(
            "overrides:\n  - from: a\n    to: b\n    type: t\n    action: ADD\n"
        ))
        self.assertEqual(entries[0].action, "add")

    def test_empty_overrides_list(self) -> None:
        self.assertEqual(load_overrides(_ws("overrides: []\n")), [])

    def test_props_defaults_to_empty(self) -> None:
        entries = load_overrides(_ws(
            "overrides:\n  - from: a\n    to: b\n    type: t\n    action: add\n"
        ))
        self.assertEqual(entries[0].props, {})


class ApplyOverridesTests(unittest.TestCase):

    def test_empty_overrides_returns_copy(self) -> None:
        edges = [_edge()]
        result = apply_overrides(edges, [])
        self.assertEqual(len(result), 1)
        self.assertIsNot(result, edges)

    def test_add_appends_edge(self) -> None:
        result = apply_overrides([_edge()], [_ovr(f"c{S}n3", f"d{S}n4", "depends_on", reason="m")])
        self.assertEqual(len(result), 2)
        added = result[1]
        self.assertEqual(added.type, "depends_on")
        self.assertEqual(added.props["source"], "manual_override")
        self.assertEqual(added.props["reason"], "m")

    def test_remove_suppresses_matching(self) -> None:
        edges = [_edge(), _edge(f"c{S}n3", f"d{S}n4", "depends_on")]
        result = apply_overrides(edges, [_ovr(f"a{S}n1", f"b{S}n2", "invokes", "remove")])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].from_id, f"c{S}n3")

    def test_remove_ignores_non_matching(self) -> None:
        result = apply_overrides([_edge()], [_ovr(f"x{S}o", f"y{S}o", "invokes", "remove")])
        self.assertEqual(len(result), 1)

    def test_unknown_child_warns_and_skips(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = apply_overrides(
                [_edge()],
                [_ovr(f"unknown{S}n1", f"b{S}n2")],
                known_children=frozenset({"a", "b"}),
            )
        self.assertEqual(len(result), 1)
        self.assertIn("unknown", buf.getvalue())

    def test_unknown_child_to_side_warns(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = apply_overrides(
                [], [_ovr(f"a{S}n1", f"ghost{S}n2")], known_children=frozenset({"a", "b"}),
            )
        self.assertEqual(len(result), 0)
        self.assertIn("ghost", buf.getvalue())

    def test_no_known_children_skips_validation(self) -> None:
        result = apply_overrides([], [_ovr(f"any{S}n", f"thing{S}n")], known_children=None)
        self.assertEqual(len(result), 1)

    def test_mixed_add_and_remove(self) -> None:
        edges = [_edge(), _edge(f"c{S}n3", f"d{S}n4", "depends_on")]
        overrides = [
            _ovr(f"a{S}n1", f"b{S}n2", "invokes", "remove"),
            _ovr(f"e{S}n5", f"f{S}n6", "calls"),
        ]
        result = apply_overrides(edges, overrides)
        types = {e.type for e in result}
        self.assertEqual(len(result), 2)
        self.assertIn("depends_on", types)
        self.assertIn("calls", types)

    def test_deterministic(self) -> None:
        edges, ovr = [_edge()], [_ovr(f"x{S}n", f"y{S}n", "t", k="v")]
        r1 = apply_overrides(edges, ovr)
        r2 = apply_overrides(edges, ovr)
        for e1, e2 in zip(r1, r2):
            self.assertEqual(e1.to_dict(), e2.to_dict())

    def test_original_not_mutated(self) -> None:
        edges = [_edge()]
        apply_overrides(edges, [_ovr(f"a{S}n1", f"b{S}n2", "invokes", "remove")])
        self.assertEqual(len(edges), 1)

    def test_bare_ids_pass_child_check(self) -> None:
        result = apply_overrides(
            [], [_ovr("bare", "also-bare")], known_children=frozenset({"a"}),
        )
        self.assertEqual(len(result), 1)


class RoundtripIntegrationTest(unittest.TestCase):

    def test_load_and_apply(self) -> None:
        root = _ws(
            "overrides:\n"
            "  - from: svc-a\n    to: svc-b\n    type: cross_repo:calls\n    action: remove\n"
            "  - from: svc-c\n    to: svc-d\n    type: invokes\n    action: add\n"
            "    props:\n      path: /health\n"
        )
        overrides = load_overrides(root)
        existing = [
            CrossRepoEdge("svc-a", "svc-b", "cross_repo:calls", {"src": "sg"}),
            CrossRepoEdge("x", "y", "depends_on"),
        ]
        result = apply_overrides(existing, overrides)
        self.assertEqual(len(result), 2)
        types = [e.type for e in result]
        self.assertIn("depends_on", types)
        self.assertIn("invokes", types)
        self.assertNotIn("cross_repo:calls", types)
        added = [e for e in result if e.type == "invokes"][0]
        self.assertEqual(added.props["source"], "manual_override")

    def test_delete_file_restores_resolver_output(self) -> None:
        root = _ws("overrides:\n  - from: a\n    to: b\n    type: t\n    action: add\n")
        self.assertEqual(len(load_overrides(root)), 1)
        (Path(root) / ".weld" / "cross_repo_overrides.yaml").unlink()
        self.assertEqual(load_overrides(root), [])


if __name__ == "__main__":
    unittest.main()

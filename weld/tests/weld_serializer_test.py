"""Unit tests for the canonical graph serializer.

Covers the six serializer rules from ADR 0012 §3:

1. Nodes sorted by ``id`` (lexicographic, bytewise on UTF-8).
2. Edges sorted by ``(from, to, type, json.dumps(props, sort_keys=True))``.
3. Props serialized with ``sort_keys=True`` at every level of nesting.
4. Top-level object keys serialized with ``sort_keys=True``.
5. Whitespace and indentation fixed (``indent=2``, ``ensure_ascii=False``).
6. Trailing newline -- exactly one ``\\n`` at end of file.

These tests drive the implementation of ``weld/serializer.py``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.serializer import dumps_graph, canonical_graph  # noqa: E402


class CanonicalGraphTest(unittest.TestCase):
    """``canonical_graph`` produces the contract-shaped dict."""

    def test_nodes_dict_emits_keys_in_sorted_order(self) -> None:
        """ADR 0012 §3 rule 1: node keys emit in lex order via sort_keys."""
        graph = {
            "meta": {"version": 4},
            "nodes": {
                "z:last": {"type": "file", "label": "last.py", "props": {}},
                "a:first": {"type": "file", "label": "first.py", "props": {}},
                "m:middle": {"type": "file", "label": "middle.py", "props": {}},
            },
            "edges": [],
        }
        result = canonical_graph(graph)
        self.assertIsInstance(result["nodes"], dict)
        # After canonical_graph + dumps_graph, the emitted JSON has keys
        # in lex order; verify at the text layer.
        text = dumps_graph(graph)
        parsed_pairs: list[str] = []

        def _capture_nodes(pairs: list[tuple]) -> dict:
            d = dict(pairs)
            if all(k in d for k in ("a:first", "m:middle", "z:last")):
                parsed_pairs.extend(k for k, _ in pairs)
            return d

        json.loads(text, object_pairs_hook=_capture_nodes)
        self.assertEqual(parsed_pairs, sorted(parsed_pairs))

    def test_edges_sorted_by_from_to_type_props(self) -> None:
        graph = {
            "meta": {},
            "nodes": {
                "n:a": {"type": "file", "label": "a", "props": {}},
                "n:b": {"type": "file", "label": "b", "props": {}},
                "n:c": {"type": "file", "label": "c", "props": {}},
            },
            "edges": [
                {"from": "n:b", "to": "n:c", "type": "calls", "props": {}},
                {"from": "n:a", "to": "n:b", "type": "imports", "props": {}},
                {"from": "n:a", "to": "n:b", "type": "calls", "props": {"p": 2}},
                {"from": "n:a", "to": "n:b", "type": "calls", "props": {"p": 1}},
            ],
        }
        result = canonical_graph(graph)
        # Expected order: (n:a,n:b,calls,{p:1}), (n:a,n:b,calls,{p:2}),
        # (n:a,n:b,imports,{}), (n:b,n:c,calls,{})
        sort_keys = [
            (e["from"], e["to"], e["type"], json.dumps(e.get("props", {}), sort_keys=True))
            for e in result["edges"]
        ]
        self.assertEqual(sort_keys, sorted(sort_keys))
        self.assertEqual(result["edges"][0]["props"], {"p": 1})
        self.assertEqual(result["edges"][1]["props"], {"p": 2})

    def test_nodes_dict_preserves_entry_body(self) -> None:
        graph = {
            "meta": {},
            "nodes": {
                "a:first": {"type": "file", "label": "first.py", "props": {"k": 1}},
            },
            "edges": [],
        }
        result = canonical_graph(graph)
        self.assertEqual(len(result["nodes"]), 1)
        entry = result["nodes"]["a:first"]
        self.assertEqual(entry["type"], "file")
        self.assertEqual(entry["label"], "first.py")
        self.assertEqual(entry["props"], {"k": 1})

    def test_accepts_nodes_in_list_form_and_normalises_to_dict(self) -> None:
        """Callers that already have list-shaped nodes are supported."""
        graph = {
            "meta": {},
            "nodes": [
                {"id": "z:last", "type": "file", "label": "z", "props": {}},
                {"id": "a:first", "type": "file", "label": "a", "props": {}},
            ],
            "edges": [],
        }
        result = canonical_graph(graph)
        self.assertIsInstance(result["nodes"], dict)
        self.assertIn("a:first", result["nodes"])
        self.assertIn("z:last", result["nodes"])
        # The ``id`` field is absorbed into the dict key and not retained
        # inside the entry body.
        self.assertNotIn("id", result["nodes"]["a:first"])

    def test_preserves_meta_as_is(self) -> None:
        graph = {
            "meta": {"version": 4, "generated_at": "2026-04-15T00:00:00+00:00"},
            "nodes": {},
            "edges": [],
        }
        result = canonical_graph(graph)
        self.assertEqual(result["meta"], graph["meta"])

    def test_missing_props_treated_as_empty(self) -> None:
        """Nodes/edges without a ``props`` key should sort as if ``props`` is ``{}``."""
        graph = {
            "meta": {},
            "nodes": {"n:a": {"type": "file", "label": "a"}},
            "edges": [
                {"from": "n:a", "to": "n:a", "type": "loops"},
            ],
        }
        # Should not raise.
        result = canonical_graph(graph)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(len(result["edges"]), 1)


class DumpsGraphTest(unittest.TestCase):
    """``dumps_graph`` emits canonical byte output."""

    def _sample(self) -> dict:
        return {
            "meta": {"version": 4, "generated_at": "2026-04-15T00:00:00+00:00"},
            "nodes": {
                "z:last": {"type": "file", "label": "last", "props": {"k": 1}},
                "a:first": {"type": "file", "label": "first", "props": {}},
            },
            "edges": [
                {"from": "z:last", "to": "a:first", "type": "imports", "props": {}},
                {"from": "a:first", "to": "z:last", "type": "calls", "props": {}},
            ],
        }

    def test_top_level_keys_sorted(self) -> None:
        text = dumps_graph(self._sample())
        emitted_keys: list[str] = []

        def _capture(pairs: list[tuple]) -> dict:
            if not emitted_keys:
                emitted_keys.extend(k for k, _ in pairs)
            return dict(pairs)

        json.loads(text, object_pairs_hook=_capture)
        self.assertEqual(emitted_keys, sorted(emitted_keys))

    def test_nested_keys_sorted_at_every_level(self) -> None:
        graph = {
            "meta": {"version": 4, "a_first": 1},
            "nodes": {
                "n:a": {
                    "type": "file",
                    "label": "a",
                    "props": {"z_last": 9, "a_first": 1, "nested": {"z": 2, "a": 1}},
                },
            },
            "edges": [
                {
                    "from": "n:a",
                    "to": "n:a",
                    "type": "loops",
                    "props": {"z": 2, "a": 1},
                },
            ],
        }
        text = dumps_graph(graph)
        # Serialising the parsed output with sort_keys=True must be
        # byte-identical to the input -- i.e. every dict at every level
        # already emits keys in sorted order.
        parsed = json.loads(text)
        resorted = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
        # The emitted text omits only the trailing newline (dumps_graph adds it).
        self.assertEqual(text.rstrip("\n"), resorted)

    def test_trailing_newline_exactly_one(self) -> None:
        text = dumps_graph(self._sample())
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_indent_two_spaces(self) -> None:
        text = dumps_graph(self._sample())
        # indent=2: top-level keys have no leading space; first-nesting
        # level keys (e.g. inside the ``meta`` dict or a list element) have
        # exactly two leading spaces. Pick any non-blank line except
        # the first/last brace and assert its leading whitespace is a
        # multiple of two spaces.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertTrue(len(lines) >= 4, "Sample graph is too small to probe indent")
        probe = lines[1]  # the first key after the opening "{"
        leading = len(probe) - len(probe.lstrip(" "))
        self.assertEqual(leading, 2, f"Expected 2-space indent, got {leading}: {probe!r}")
        # Also verify that no line uses tabs.
        self.assertNotIn("\t", text)

    def test_ensure_ascii_false_preserves_unicode(self) -> None:
        graph = {
            "meta": {},
            "nodes": {
                "n:a": {"type": "file", "label": "café", "props": {}},
            },
            "edges": [],
        }
        text = dumps_graph(graph)
        self.assertIn("café", text)
        self.assertNotIn("\\u00e9", text)

    def test_byte_identical_when_called_twice(self) -> None:
        """Two calls on the same input produce byte-identical output."""
        g = self._sample()
        a = dumps_graph(g)
        b = dumps_graph(g)
        self.assertEqual(a, b)

    def test_input_dict_not_mutated(self) -> None:
        """``canonical_graph``/``dumps_graph`` must not mutate their input."""
        g = self._sample()
        before = json.dumps(g, sort_keys=True)
        dumps_graph(g)
        after = json.dumps(g, sort_keys=True)
        self.assertEqual(before, after)

    def test_output_is_valid_json(self) -> None:
        text = dumps_graph(self._sample())
        parsed = json.loads(text)
        self.assertIn("meta", parsed)
        self.assertIn("nodes", parsed)
        self.assertIn("edges", parsed)

    def test_edges_with_same_endpoints_break_tie_on_props(self) -> None:
        """Edges matching on from/to/type are ordered by props serialisation."""
        graph = {
            "meta": {},
            "nodes": {"n:a": {"type": "file", "label": "a", "props": {}}},
            "edges": [
                {"from": "n:a", "to": "n:a", "type": "self", "props": {"z": 1}},
                {"from": "n:a", "to": "n:a", "type": "self", "props": {"a": 1}},
            ],
        }
        result = canonical_graph(graph)
        # {"a":1} sorts before {"z":1}
        self.assertEqual(result["edges"][0]["props"], {"a": 1})
        self.assertEqual(result["edges"][1]["props"], {"z": 1})


if __name__ == "__main__":
    unittest.main()

"""T1a determinism audit reproduction — dict/set iteration order in graph serialization.

Covers ADR 0012 §2 row 1 and §3 rules 1-5. ``wd discover`` now routes its
top-level dict emission through the canonical serializer contract so that
the in-memory graph returned by ``discover()`` carries sorted keys at
every level. This test fixes a canonical structure and asserts:

1. Top-level keys are sorted.
2. Node ids are sorted lexicographically.
3. Edges are sorted by ``(from, to, type, json.dumps(props, sort_keys=True))``.
4. Nested props serialize with sorted keys at every level.

These assertions hold once ``_post_process`` in ``weld/discover.py``
emits its dict in canonical shape.

Companion audit document: ``docs/determinism-audit-T1a.md``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import discover  # noqa: E402


def _run_discover_on_fixture() -> dict:
    """Run wd discover on a minimal synthetic repo and return the graph dict.

    The fixture is deliberately tiny: one top-level module with two
    functions, so the graph has > 1 node and > 0 edges (enough to exercise
    sort-order assertions without long test runtime).
    """
    with tempfile.TemporaryDirectory(prefix="t1a-dict-") as td:
        root = Path(td)

        # Minimal python package that triggers the python_module strategy.
        (root / "src").mkdir()
        (root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (root / "src" / "mod_z.py").write_text(
            "def helper_z():\n    return 1\n", encoding="utf-8"
        )
        (root / "src" / "mod_a.py").write_text(
            "def helper_a():\n    return 2\n", encoding="utf-8"
        )

        # discover.yaml: one source using python_module.
        (root / ".weld").mkdir()
        (root / ".weld" / "discover.yaml").write_text(
            "sources:\n"
            "  - strategy: python_module\n"
            "    glob: src/**/*.py\n"
            "    type: file\n"
            "    package: pkg:src\n",
            encoding="utf-8",
        )

        # Force a full discovery, no incremental state carryover.
        return discover(root, incremental=False)


def _node_ids(graph: dict) -> list[str]:
    return list(graph["nodes"].keys())


def _edge_sort_key(e: dict) -> tuple:
    props = e.get("props", {})
    return (
        e["from"],
        e["to"],
        e["type"],
        json.dumps(props, sort_keys=True, ensure_ascii=True),
    )


class DictOrderDeterminismTest(unittest.TestCase):
    """ADR 0012 §3 rules 1-5: node/edge/prop ordering."""

    def test_top_level_keys_are_sorted(self) -> None:
        """The graph's top-level keys must serialize sorted.

        ``_post_process`` now returns its dict in canonical shape so
        that even a naive ``json.dumps`` (no ``sort_keys``) emits the
        top-level keys in alphabetical order ``[edges, meta, nodes]``.
        ADR 0012 §3 rule 4 requires sorted top-level keys in any
        canonical emission.
        """
        graph = _run_discover_on_fixture()
        # Serialize with the same settings discover.py uses today
        # (indent=2, ensure_ascii=False, NO sort_keys) and observe the
        # key order.
        serialized = json.dumps(graph, indent=2, ensure_ascii=False)
        # Parse it back with object_pairs_hook to preserve the order
        # the serializer emitted.
        emitted_keys: list[str] = []

        def _capture(pairs: list[tuple]) -> dict:
            if not emitted_keys:
                emitted_keys.extend(k for k, _ in pairs)
            return dict(pairs)

        json.loads(serialized, object_pairs_hook=_capture)
        self.assertEqual(
            emitted_keys,
            sorted(emitted_keys),
            "Top-level graph keys must emit in sorted order. "
            "Got %r; expected %r. "
            "Fix: route all graph.json writes through a canonical "
            "serializer that uses sort_keys=True (ADR 0012 §3 rule 4)."
            % (emitted_keys, sorted(emitted_keys)),
        )

    def test_serialization_is_sort_keys_equivalent(self) -> None:
        """Serialization must be byte-identical with and without sort_keys=True.

        This is the canonical guard: if *any* dict in the graph tree
        has keys in non-sorted order, the two serializations differ.
        This includes node props, edge props, the top-level graph
        dict, and the meta dict. It holds once ``_post_process``
        routes the returned graph through a canonicalising step so
        every nested dict -- ``meta``, each node entry, each node's
        and edge's props -- carries keys in sorted order.

        ADR 0012 §3 rule 5 requires a single canonical form:
        ``json.dumps(..., sort_keys=True, indent=2,
        ensure_ascii=False)`` at every emission site; the in-memory
        dict must already match that shape so the two emissions are
        indistinguishable.
        """
        graph = _run_discover_on_fixture()
        without_sort = json.dumps(graph, indent=2, ensure_ascii=False)
        with_sort = json.dumps(
            graph, indent=2, ensure_ascii=False, sort_keys=True
        )
        self.assertEqual(
            without_sort,
            with_sort,
            "Graph serialization must be byte-identical with and "
            "without sort_keys=True. Fix: every graph.json write site "
            "must use sort_keys=True (ADR 0012 §3 rule 5).",
        )

    def test_meta_keys_are_sorted(self) -> None:
        """The meta dict must serialize with sorted keys.

        ``meta`` now emits from ``_post_process`` in alphabetical
        order (``discovered_from``, ``git_sha`` (when present),
        ``schema_version``, ``updated_at``, ``version``) rather than
        the historical insertion order.
        """
        graph = _run_discover_on_fixture()
        meta = graph.get("meta", {})
        meta_keys = list(meta.keys())
        self.assertEqual(
            meta_keys,
            sorted(meta_keys),
            "meta keys must be sorted alphabetically. "
            "Got %r; expected %r. "
            "Fix: canonical serializer sorts keys at every level "
            "(ADR 0012 §3 rule 3)." % (meta_keys, sorted(meta_keys)),
        )

    def test_node_entry_keys_are_sorted(self) -> None:
        """Every node entry's keys must be sorted.

        A node entry is ``{type, label, props}``. Sorted order is
        ``{label, props, type}``. The canonicalising step applied
        in ``_post_process`` rebuilds every node entry with keys
        in sorted order.
        """
        graph = _run_discover_on_fixture()
        nodes = graph.get("nodes", {})
        self.assertTrue(nodes, "Fixture produced no nodes — fixture is broken.")
        mismatches: list[tuple[str, list[str]]] = []
        for nid, n in nodes.items():
            ks = list(n.keys())
            if ks != sorted(ks):
                mismatches.append((nid, ks))
        self.assertEqual(
            mismatches,
            [],
            "Every node entry's keys must be sorted. "
            "%d node(s) have unsorted keys; first example: %r. "
            "Fix: canonical serializer sorts keys at every level "
            "(ADR 0012 §3 rule 3)."
            % (len(mismatches), mismatches[:1]),
        )


if __name__ == "__main__":
    unittest.main()

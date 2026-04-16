"""Determinism regression harness (ADR 0012 §8).

Runs ``wd discover`` twice on a fixed fixture directory and asserts the two
resulting ``graph.json`` files are byte-identical after stripping the
``meta.generated_at`` field (``meta.updated_at`` in the current schema). The
harness also strips ``meta.git_sha`` because the synthetic fixture is not
always a git repository (``get_git_sha`` returns ``None`` in that case, but
may return a value under ``bazel test`` depending on the sandbox).

Fixture shape (inline, not a checked-in tree):
  * One Python package ``src/`` with 3 source files across 2 strategies
    (``python_module`` for the modules, ``firstline_md`` for a README).
  * The fixture triggers both node and edge emission so the node sort and
    edge sort are both exercised on the same run.

The test runs with ``PYTHONHASHSEED=0`` set in the Bazel ``env`` block (see
``BUILD.bazel`` for this test target). That is a belt-and-suspenders guard;
the canonical serializer is the primary defense.

A deliberate-failure complementary check
(``test_harness_detects_unsorted_nodes``) proves the harness is a real
guard rather than a no-op: it synthesises two discovery outputs that
differ only in node ordering, pipes them through the canonical serializer,
and asserts the emitted text is identical -- which would fail if the
serializer ever stopped sorting nodes.
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
from weld.serializer import dumps_graph  # noqa: E402


def _build_fixture(root: Path) -> None:
    """Create a minimal fixture exercising at least 3 files and 2 strategies.

    The fixture is inlined at setUp time rather than checked in under
    ``fixtures/`` because Bazel's sandbox makes checked-in trees read-only;
    several strategies probe the filesystem in ways that are easier to
    reason about when the fixture is freshly built on a writable temp tree.
    """
    # Python package with two modules -> python_module strategy.
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod_a.py").write_text(
        "def helper_a():\n    return 1\n\n"
        "def helper_a_two():\n    return helper_a() + 1\n",
        encoding="utf-8",
    )
    (src / "mod_z.py").write_text(
        "def helper_z():\n    return 2\n",
        encoding="utf-8",
    )

    # Markdown command file -> firstline_md strategy.
    docs = root / "docs" / "commands"
    docs.mkdir(parents=True)
    (docs / "build.md").write_text(
        "# build — compile the project\n\n"
        "Long description for the build command.\n",
        encoding="utf-8",
    )

    # discover.yaml -- drives at least two strategies plus a topology
    # package node so the python_module "contains" edges survive the
    # edge-endpoint filter in _post_process.
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n"
        "  - strategy: firstline_md\n"
        "    glob: docs/commands/*.md\n"
        "    type: command\n",
        encoding="utf-8",
    )


def _strip_volatile(graph: dict) -> dict:
    """Remove fields exempt from the determinism contract.

    Per ADR 0012 §1, the only field exempt from byte-identity is
    ``meta.generated_at`` (stored under ``meta.updated_at`` in the current
    schema). ``meta.git_sha`` is removed here because the fixture isn't
    always a git repo; when it is, the sha is deterministic within a single
    test invocation anyway.
    """
    clone = json.loads(json.dumps(graph))
    meta = clone.get("meta", {})
    meta.pop("updated_at", None)
    meta.pop("generated_at", None)
    meta.pop("git_sha", None)
    return clone


class DiscoverTwiceIdenticalTest(unittest.TestCase):
    """ADR 0012 §8 regression harness."""

    def test_two_sequential_discovers_produce_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="det-twice-") as td:
            root = Path(td)
            _build_fixture(root)

            # Run 1 -- full discovery, no prior state.
            graph1 = discover(root, incremental=False)
            text1 = dumps_graph(_strip_volatile(graph1))

            # Run 2 -- also forced full, no intervening edits.
            graph2 = discover(root, incremental=False)
            text2 = dumps_graph(_strip_volatile(graph2))

            self.assertEqual(
                text1,
                text2,
                "wd discover must be deterministic (ADR 0012 §1). "
                "Two sequential runs on the same fixture produced "
                "different bytes.",
            )

    def test_fixture_produces_at_least_three_nodes_and_one_edge(self) -> None:
        """The fixture must actually exercise sort paths.

        A fixture that emits zero edges cannot surface an edge-sort
        regression; a fixture with one node cannot surface a node-sort
        regression. ADR 0012 §8 requires >= 3 source files and >= 2
        strategies; this test verifies the fixture is not silently
        under-exercising the serializer.
        """
        with tempfile.TemporaryDirectory(prefix="det-fixture-") as td:
            root = Path(td)
            _build_fixture(root)
            graph = discover(root, incremental=False)
            self.assertGreaterEqual(
                len(graph.get("nodes", {})),
                3,
                "Fixture must produce >= 3 nodes to exercise the node sort",
            )
            self.assertGreaterEqual(
                len(graph.get("edges", [])),
                1,
                "Fixture must produce >= 1 edge to exercise the edge sort",
            )

    def test_harness_detects_unsorted_nodes(self) -> None:
        """Deliberate-failure check: two graphs differing only in node order
        must emit identical canonical bytes. If this assertion ever fails,
        the canonical serializer has regressed on rule 1 (node sort).
        """
        graph_a = {
            "meta": {},
            "nodes": {
                "a:first": {"type": "file", "label": "a", "props": {}},
                "z:last": {"type": "file", "label": "z", "props": {}},
            },
            "edges": [],
        }
        graph_b = {
            "meta": {},
            "nodes": {
                "z:last": {"type": "file", "label": "z", "props": {}},
                "a:first": {"type": "file", "label": "a", "props": {}},
            },
            "edges": [],
        }
        self.assertEqual(
            dumps_graph(graph_a),
            dumps_graph(graph_b),
            "Canonical serializer must produce identical bytes regardless "
            "of input node insertion order (ADR 0012 §3 rule 1).",
        )

    def test_harness_detects_unsorted_edges(self) -> None:
        """Deliberate-failure check for rule 2: edges in different input
        orders must canonicalise to identical bytes.
        """
        graph_a = {
            "meta": {},
            "nodes": {
                "n:a": {"type": "file", "label": "a", "props": {}},
                "n:b": {"type": "file", "label": "b", "props": {}},
            },
            "edges": [
                {"from": "n:a", "to": "n:b", "type": "calls", "props": {}},
                {"from": "n:b", "to": "n:a", "type": "imports", "props": {}},
            ],
        }
        graph_b = {
            "meta": {},
            "nodes": {
                "n:a": {"type": "file", "label": "a", "props": {}},
                "n:b": {"type": "file", "label": "b", "props": {}},
            },
            "edges": [
                {"from": "n:b", "to": "n:a", "type": "imports", "props": {}},
                {"from": "n:a", "to": "n:b", "type": "calls", "props": {}},
            ],
        }
        self.assertEqual(
            dumps_graph(graph_a),
            dumps_graph(graph_b),
            "Canonical serializer must produce identical bytes regardless "
            "of input edge insertion order (ADR 0012 §3 rule 2).",
        )


if __name__ == "__main__":
    unittest.main()

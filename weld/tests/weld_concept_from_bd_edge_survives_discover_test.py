"""Full-discovery regression: concept->file relates_to edges must survive.

Third instance of one root cause. ``concept_from_bd`` minted its
``relates_to`` targets as ``file:<rel-WITH-.py>`` and ``file:<bare-stem>``,
the spellings that predate the ADR 0041 rename; ``python_module`` mints the
canonical file node as ``file:<rel-WITHOUT-extension>``
(``weld._node_ids.file_id``). Because
``weld._discover_postprocess._clean_and_dedup_edges`` prunes any edge whose
endpoint is not a node id -- exact match, no extension normalization and no
alias resolution -- every concept->file edge was swept during post-process
while the concept *nodes* survived. The symptom on the weld repo itself was
twelve orphan ``concept:`` nodes reported by ``wd lint``: the relationship
the strategy exists to record had not landed once since the rename.

The same bug was fixed for ``events`` and for ``http_client`` before it
(see the sibling ``*_survive*_discover_test`` modules); this test is the
concept-node instance and fails with zero surviving relates_to edges
before the canonical-spelling fix.

The fixture drives the *real* discover pipeline: ``python_module`` mints the
file nodes, ``config_file`` mints the root-config node, and
``concept_from_bd`` reads a JSON-lines issue store whose description cites
both. It also sets a trap for the opposite failure: the issue cites a shell
script whose stem matches an *uncited* Python module. ``file_id`` strips the
final extension, so widening the ``file:`` offer to ``.sh`` would land that
citation on the Python module -- a confidently wrong claim the dangling
sweep cannot catch, because the node it hits is real. The Python module is
left uncited on purpose: were it cited too, the edge would arrive
legitimately and the dedup pass would make the assertion vacuous.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.discover import discover

# A fully-wired discover config: the file-node minter (``python_module``),
# the root-config minter (``config_file``), and the strategy under test.
_DISCOVER_YAML = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
    "  - strategy: config_file\n"
    "    files: [\"CLAUDE.md\"]\n"
    "    type: config\n"
    "  - strategy: concept_from_bd\n"
    "    path: issues.jsonl\n"
    "    type: concept\n"
)

# Every module has a public function so ``python_module`` mints its
# canonical ``file:`` node rather than skipping it as export-less.
# ``svc/wrapper.py`` is deliberately NOT cited by the issue below: it exists
# only so that a ``file:svc/wrapper`` node is real, which is what makes the
# same-stem trap below a genuine assertion rather than a vacuous one.
_FILES = {
    "svc/collector.py": """\
        def collect():
            return []
    """,
    "svc/nested/probe.py": """\
        def probe():
            return None
    """,
    "svc/wrapper.py": """\
        def wrap():
            return None
    """,
}

# Cited by the issue; shares a stem with the *uncited* ``svc/wrapper.py``.
# ``file_id`` strips the final extension, so both slug to
# ``file:svc/wrapper``. Widening the ``file:`` offer to shell scripts would
# therefore land this citation on the Python module -- a real node, so the
# dangling sweep cannot catch it -- and that is exactly what
# ``test_shell_script_does_not_borrow_a_same_stem_python_node`` fails on.
_SCRIPT_REL = "svc/wrapper.sh"

_ISSUE = {
    "id": "fixture-repo-0000-zzzz",
    "title": "weld dogfood gap: collector relationships are not in the graph",
    "description": (
        "Query attempted: collector\n"
        "Fallback used: grep over svc/collector.py and svc/nested/probe.py\n"
        "Policy lives in CLAUDE.md; the wrapper is svc/wrapper.sh\n"
    ),
    "status": "open",
    "priority": 2,
    "labels": ["weld-dogfood-gap"],
}

_CONCEPT_ID = "concept:collector-relationships-are-not-in-the-graph"


def _build_fixture(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        _DISCOVER_YAML, encoding="utf-8"
    )
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    (root / _SCRIPT_REL).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# policy\n", encoding="utf-8")
    (root / "issues.jsonl").write_text(
        json.dumps(_ISSUE) + "\n", encoding="utf-8"
    )


class ConceptFromBdEdgeSurvivesDiscoverTest(unittest.TestCase):
    """concept->file relates_to edges survive the full post-process."""

    graph: dict
    nodes: dict
    relates_to: list

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _build_fixture(root)
        cls.graph = discover(root)
        cls.nodes = cls.graph["nodes"]
        cls.relates_to = [
            e
            for e in cls.graph["edges"]
            if e["type"] == "relates_to" and e["from"].startswith("concept:")
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_concept_node_is_minted(self) -> None:
        self.assertIn(_CONCEPT_ID, self.nodes)

    def test_concept_node_is_not_an_orphan(self) -> None:
        # The regression itself: before the fix the concept node landed
        # with zero surviving edges, which is what ``wd lint`` reports as
        # an orphan.
        self.assertTrue(
            self.relates_to,
            "concept node has no surviving relates_to edge -- every "
            "candidate spelling was swept as dangling",
        )

    def test_edges_land_on_canonical_file_nodes(self) -> None:
        targets = {e["to"] for e in self.relates_to}
        self.assertIn("file:svc/collector", targets)
        self.assertIn("file:svc/nested/probe", targets)

    def test_every_surviving_endpoint_is_a_real_node(self) -> None:
        # Restates the post-process invariant from the consumer side: a
        # surviving edge is by construction resolvable, so a query that
        # follows it never hits a hole.
        for edge in self.relates_to:
            self.assertIn(edge["from"], self.nodes)
            self.assertIn(edge["to"], self.nodes)

    def test_root_config_citation_lands_on_config_node(self) -> None:
        targets = {e["to"] for e in self.relates_to}
        self.assertIn("config:CLAUDE_md", targets)

    def test_shell_script_does_not_borrow_a_same_stem_python_node(self) -> None:
        # The issue cites ``svc/wrapper.sh`` and never mentions
        # ``svc/wrapper.py``, but both slug to ``file:svc/wrapper`` once
        # ``file_id`` strips the extension -- and that node is real,
        # because ``python_module`` minted it. So an edge to it here could
        # only have come from the shell citation borrowing the Python
        # module's identity, which no dangling-edge sweep can detect.
        self.assertIn(
            "file:svc/wrapper",
            self.nodes,
            "fixture precondition: the same-stem python node must exist, "
            "otherwise this test passes for the wrong reason",
        )
        self.assertEqual(
            self.nodes["file:svc/wrapper"]["props"].get("file"),
            "svc/wrapper.py",
        )
        self.assertNotIn(
            "file:svc/wrapper",
            {e["to"] for e in self.relates_to},
            "a cited .sh path claimed a same-stem python module's file node",
        )


if __name__ == "__main__":
    unittest.main()

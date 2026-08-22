"""A re-run source's node for a file that did not change (bd n0p2).

``validator_targets`` mints a ``file:`` stub for the export-less
``__init__.py`` a lint governs -- the one file class ``python_module``
deliberately anchors nothing for, and so the one class that has no node unless
the validator mints it. Editing the lint marks the *lint* dirty and leaves the
``__init__.py`` clean, so the incremental merge dropped the stub, the
``validates`` edge that needed it dangled, and the dangling sweep removed it.
The governed file appeared in the graph only after someone ran ``--full``.

These tests run the real ``_discover_single_repo`` over a temp tree and compare
it against a full discover of the *same* tree, because that comparison is the
actual contract (ADR 0008: an incremental run and a full run over one tree
agree). Asserting the stub alone would pass on a merge that had simply stopped
guarding anything, so the inverse -- a re-run source may still not overwrite a
clean file's incumbent node -- is pinned here too.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._discover_state_check import mark_state_published
from weld.discover import _discover_single_repo

#: A lint naming only ``pkg/mod.py``; ``pkg/__init__.py`` is not yet governed.
_LINT_BEFORE = '"""A lint."""\nTARGET = "pkg/mod.py"\n'

#: The single edit under test: the lint now also governs the export-less
#: ``__init__.py``. Nothing else in the tree changes.
_LINT_AFTER = '"""A lint."""\nTARGET = "pkg/mod.py"\nALSO = "pkg/__init__.py"\n'

_STUB_ID = "file:pkg/__init__"
_VALIDATOR_ID = "file:tools/lint_thing"


def _fixture(root: Path) -> None:
    """Two sources: ``python_module`` over both trees, ``validator_targets``.

    ``python_module`` covers ``tools/**`` as well so the validator itself has a
    node -- without it the ``validates`` edge dangles at its *source* end and
    the assertions could not tell a dropped stub from a missing validator.
    """
    (root / "pkg").mkdir()
    (root / "tools").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8",
    )
    (root / "tools" / "lint_thing.py").write_text(_LINT_BEFORE, encoding="utf-8")

    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: pkg/**/*.py\n"
        "    type: file\n"
        "  - strategy: python_module\n"
        "    glob: tools/**/*.py\n"
        "    type: file\n"
        "  - strategy: validator_targets\n"
        "    glob: tools/**/*.py\n"
        "    include_names:\n"
        '      - "lint_*.py"\n',
        encoding="utf-8",
    )


def _publish(root: Path, graph: dict) -> None:
    """Land *graph* as the on-disk graph the next incremental run builds on.

    ``_discover_single_repo(write_graph=False)`` leaves the state saying no
    graph was ever published, and an unpublished state downgrades the next run
    to a full discover (bd nwyq) -- which would quietly test nothing.
    """
    (root / ".weld" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    mark_state_published(root, root / ".weld" / "graph.json")


def _comparable(graph: dict) -> dict:
    """Strip the meta that legitimately differs between the two paths.

    ``updated_at``/``git_sha`` are volatile, and ``discovered_from``'s two
    construction orders differ (full rebuilds top-down by source;
    incremental keeps an old-list prefix) even though the SET now matches
    (bd 8084). Nodes and edges must match exactly; that is the whole
    contract.
    """
    meta = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "git_branch", "discovered_from")
    }
    return {**{k: v for k, v in graph.items() if k != "meta"}, "meta": meta}


def _validates(graph: dict) -> set[str]:
    """Return the targets of every ``validates`` edge out of the lint."""
    return {
        e["to"] for e in graph.get("edges", [])
        if e["type"] == "validates" and e["from"] == _VALIDATOR_ID
    }


class NewlyGovernedFileTest(unittest.TestCase):
    """The reported bug, end to end through the orchestrator."""

    def _run_edit(self, root: Path) -> dict:
        """Seed a published graph, make the lint edit, return the incremental."""
        _fixture(root)
        seed = _discover_single_repo(root, incremental=False)
        self.assertNotIn(
            _STUB_ID, seed.get("nodes", {}),
            "fixture invariant: the stub must be absent before the lint "
            "names the file, or the test proves nothing about minting it",
        )
        _publish(root, seed)

        (root / "tools" / "lint_thing.py").write_text(_LINT_AFTER, encoding="utf-8")
        return _discover_single_repo(root, incremental=True)

    def test_stub_is_minted_for_a_file_that_did_not_change(self) -> None:
        """The node the merge used to drop."""
        with tempfile.TemporaryDirectory(prefix="n0p2-mint-") as td:
            graph = self._run_edit(Path(td))

        self.assertIn(
            _STUB_ID, graph.get("nodes", {}),
            "incremental discover dropped a re-run source's node for a file "
            "that is not itself dirty, so a newly governed file has no node "
            "until someone runs a full discover",
        )

    def test_the_edge_that_needed_the_stub_survives(self) -> None:
        """The reader-visible half: a dropped node takes its edge with it.

        Asserted separately from the node because they fail for one reason but
        are two different losses -- the edge is what an agent asking "what
        constrains this file" actually reads.
        """
        with tempfile.TemporaryDirectory(prefix="n0p2-edge-") as td:
            graph = self._run_edit(Path(td))

        self.assertIn(_STUB_ID, _validates(graph))

    def test_incremental_matches_full_over_the_same_tree(self) -> None:
        """The contract the missing node broke (ADR 0008).

        This is the assertion that cannot be satisfied by a merge that mints
        the stub but gets anything else wrong.
        """
        with tempfile.TemporaryDirectory(prefix="n0p2-equiv-") as td:
            root = Path(td)
            incremental = self._run_edit(root)
            full = _discover_single_repo(root, incremental=False)

        self.assertEqual(_comparable(full), _comparable(incremental))


class CleanFileIncumbentTest(unittest.TestCase):
    """The inverse: minting a missing node is not licence to overwrite one.

    Rigged so the re-run source genuinely *contends* for the ID. Starting from
    a lint that already governs the ``__init__.py``, the seeded graph holds a
    node for it and the re-run mints a claim on that same ID every time -- so
    only the dirty-scope guard decides which one survives. A fixture where
    nothing contends would assert "the graph did not change", which passes on
    a merge that has stopped guarding entirely.
    """

    #: Stamped onto the seeded node and onto nothing else. ``validator_targets``
    #: cannot mint it, and ``reattach_enrichment`` only carries ``enrichment``
    #: forward, so its survival means the incumbent node itself survived.
    MARK = ("weld_test_marker", "incumbent")

    def test_re_run_source_does_not_replace_a_clean_file_node(self) -> None:
        """The guard's original job, through the real orchestrator."""
        with tempfile.TemporaryDirectory(prefix="n0p2-incumbent-") as td:
            root = Path(td)
            _fixture(root)
            # Governed from the first run, so the stub is already in the graph.
            (root / "tools" / "lint_thing.py").write_text(
                _LINT_AFTER, encoding="utf-8",
            )
            seed = _discover_single_repo(root, incremental=False)
            self.assertIn(
                _STUB_ID, seed["nodes"],
                "fixture invariant: the seeded graph must already hold the "
                "node the re-run source will contend for",
            )
            seed["nodes"][_STUB_ID]["props"][self.MARK[0]] = self.MARK[1]
            _publish(root, seed)

            # Dirty the lint without changing which files it governs, so
            # validator_targets re-runs and re-mints its claim on _STUB_ID.
            (root / "tools" / "lint_thing.py").write_text(
                _LINT_AFTER + "# touched\n", encoding="utf-8",
            )
            graph = _discover_single_repo(root, incremental=True)

        self.assertEqual(
            self.MARK[1], graph["nodes"][_STUB_ID]["props"].get(self.MARK[0]),
            "a re-run source overwrote the node of a file that did not "
            "change; the full run's winner for a clean file is decided by "
            "source ordering across every entry, and incrementally the clean "
            "entries never run to defend it",
        )


if __name__ == "__main__":
    unittest.main()

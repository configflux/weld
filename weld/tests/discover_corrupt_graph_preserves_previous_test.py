"""Regression: a corrupt graph.json must not destroy graph-previous.json.

``_discover_single_repo`` used to copy ``.weld/graph.json`` to
``.weld/graph-previous.json`` before attempting to parse it. A corrupt
``graph.json`` therefore silently overwrote the last good recovery
snapshot, weakening ``wd diff`` and any manual recovery.

The test seeds a valid previous snapshot, writes invalid bytes into
``graph.json``, runs :func:`discover`, and asserts the previous snapshot
bytes are unchanged.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import discover  # noqa: E402


class CorruptGraphPreservesPreviousTest(unittest.TestCase):
    def _build_fixture(self, root: Path) -> None:
        src = root / "src"
        src.mkdir()
        (src / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / ".weld").mkdir()
        (root / ".weld" / "discover.yaml").write_text(
            "sources:\n"
            "  - strategy: python_module\n"
            "    glob: src/**/*.py\n"
            "    type: file\n",
            encoding="utf-8",
        )

    def test_corrupt_graph_json_leaves_graph_previous_intact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="weld-corrupt-prev-") as td:
            root = Path(td)
            self._build_fixture(root)

            weld_dir = root / ".weld"
            prev_path = weld_dir / "graph-previous.json"
            graph_path = weld_dir / "graph.json"

            good_prev_bytes = (
                b'{"meta": {"known": "good-previous"},'
                b' "nodes": {}, "edges": []}\n'
            )
            prev_path.write_bytes(good_prev_bytes)
            graph_path.write_text("{not valid json", encoding="utf-8")

            discover(root, incremental=False)

            self.assertEqual(
                prev_path.read_bytes(),
                good_prev_bytes,
                "graph-previous.json must not be overwritten when graph.json "
                "fails to parse; snapshot the current graph only after "
                "validating it.",
            )

    def test_valid_graph_json_is_snapshotted_to_previous(self) -> None:
        """Positive control: when graph.json is valid, graph-previous.json is
        updated to match its pre-run bytes (the existing `wd diff` contract).
        """
        with tempfile.TemporaryDirectory(prefix="weld-valid-prev-") as td:
            root = Path(td)
            self._build_fixture(root)

            weld_dir = root / ".weld"
            graph_path = weld_dir / "graph.json"
            prev_path = weld_dir / "graph-previous.json"

            prior_graph_bytes = (
                b'{"meta": {"marker": "before-run"},'
                b' "nodes": {}, "edges": []}\n'
            )
            graph_path.write_bytes(prior_graph_bytes)

            discover(root, incremental=False)

            self.assertEqual(
                prev_path.read_bytes(),
                prior_graph_bytes,
                "When graph.json parses cleanly, its pre-run bytes must be "
                "copied to graph-previous.json so `wd diff` has a baseline.",
            )


if __name__ == "__main__":
    unittest.main()

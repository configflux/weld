"""Tests for the worker_stage discovery strategy.

The strategy walks immediate subdirectories of the parent of ``glob`` and
emits a ``stage:<dir-name>`` node for each subdir that contains an
``__init__.py``. The node's ``exports`` property comes from the
module's ``__all__`` list (when present).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.worker_stage import extract


_INIT_WITH_ALL = """\
\"\"\"Acquisition stage public surface.\"\"\"

from .runner import run

__all__ = ["run", "Acquirer"]


class Acquirer:
    pass
"""

_INIT_WITHOUT_ALL = "x = 1\n"

_INIT_SYNTAX_ERROR = "def broken(:\n"


class TestWorkerStageEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_worker_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

    def test_subdir_without_init_py_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workers" / "lonely").mkdir(parents=True)
            (root / "workers" / "lonely" / "main.py").write_text("# no init\n")
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertEqual(result.nodes, {})


class TestWorkerStageHappyPath(unittest.TestCase):
    """A subdir with __init__.py and __all__ becomes a stage node."""

    def test_emits_stage_node_with_exports_and_titlecase_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "workers" / "acquisition"
            stage.mkdir(parents=True)
            (stage / "__init__.py").write_text(
                _INIT_WITH_ALL, encoding="utf-8"
            )
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertIn("stage:acquisition", result.nodes)
            node = result.nodes["stage:acquisition"]
            self.assertEqual(node["type"], "stage")
            self.assertEqual(node["label"], "Acquisition")
            props = node["props"]
            self.assertEqual(props["file"], "workers/acquisition/__init__.py")
            self.assertCountEqual(props["exports"], ["run", "Acquirer"])
            self.assertEqual(props["source_strategy"], "worker_stage")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["implementation"])

    def test_init_without_all_emits_node_with_empty_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "workers" / "extraction"
            stage.mkdir(parents=True)
            (stage / "__init__.py").write_text(
                _INIT_WITHOUT_ALL, encoding="utf-8"
            )
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertIn("stage:extraction", result.nodes)
            self.assertEqual(
                result.nodes["stage:extraction"]["props"]["exports"], []
            )


class TestWorkerStageEdgeCases(unittest.TestCase):
    """Bad input must be skipped silently rather than aborting discovery."""

    def test_syntax_error_in_init_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "workers" / "broken"
            bad.mkdir(parents=True)
            (bad / "__init__.py").write_text(
                _INIT_SYNTAX_ERROR, encoding="utf-8"
            )
            # A second, valid stage must still be discovered alongside.
            good = root / "workers" / "acquisition"
            good.mkdir(parents=True)
            (good / "__init__.py").write_text(
                _INIT_WITH_ALL, encoding="utf-8"
            )
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertNotIn("stage:broken", result.nodes)
            self.assertIn("stage:acquisition", result.nodes)

    def test_files_at_worker_dir_top_level_are_ignored(self) -> None:
        # Only directories qualify as stages; loose files in the parent
        # must not register as stages.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wd = root / "workers"
            wd.mkdir()
            (wd / "loose.py").write_text("x = 1\n")
            stage = wd / "acquisition"
            stage.mkdir()
            (stage / "__init__.py").write_text(
                _INIT_WITH_ALL, encoding="utf-8"
            )
            result = extract(root, {"glob": "workers/*"}, {})
            self.assertEqual(list(result.nodes), ["stage:acquisition"])


if __name__ == "__main__":
    unittest.main()

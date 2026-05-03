"""Strategy-pair consistency: ``python_module`` + ``python_callgraph``.

ADR 0041 § Layer 3 (rule ``strategy-pair-consistency``) requires that
both members of a registered strategy pair process the same input file
set under the same skip rules. Before PR 1, ``python_module`` skipped
files starting with ``_`` (other than ``__init__.py``) while
``python_callgraph`` did not, which produced file anchors with
outgoing ``contains`` edges to symbols that the module strategy never
imported. The ``_ros2_py`` orphan was the visible symptom.

This test materialises a synthetic tree containing several files that
historically differed between the strategies (private files, public
files, ``__init__.py``, an excluded file) and asserts the two
strategies process exactly the same set. PR 3 ships the lint rule
that enforces this against the live repo; this test guards the
strategy-level invariant directly.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies import python_callgraph, python_module  # noqa: E402


_FIXTURE_FILES: dict[str, str] = {
    "public_module.py": "def foo():\n    return 1\n",
    "_private_module.py": "def bar():\n    return 2\n",
    "__init__.py": "from .public_module import foo\n",
    "nested/_inner.py": "def inner():\n    return 3\n",
    "nested/__init__.py": "",
    "nested/public.py": "def baz():\n    return 4\n",
    "excluded/skip_me.py": "def skipped():\n    return 5\n",
}


def _module_files(nodes: dict[str, dict]) -> set[str]:
    """Return the set of source files surfaced by ``python_module``."""
    files: set[str] = set()
    for node in nodes.values():
        rel = node.get("props", {}).get("file")
        if rel:
            files.add(rel)
    return files


def _callgraph_files(nodes: dict[str, dict]) -> set[str]:
    """Return the set of source files surfaced by ``python_callgraph``."""
    files: set[str] = set()
    for node in nodes.values():
        rel = node.get("props", {}).get("file")
        if rel:
            files.add(rel)
    return files


class PythonStrategyPairTest(unittest.TestCase):
    """Both Python strategies must observe the same input set."""

    def _materialise(self) -> Path:
        td = Path(tempfile.mkdtemp(prefix="weld_pair_"))
        for rel, body in _FIXTURE_FILES.items():
            target = td / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self.addCleanup(self._cleanup, td)
        return td

    @staticmethod
    def _cleanup(td: Path) -> None:
        import shutil

        shutil.rmtree(td, ignore_errors=True)

    def test_pair_processes_same_file_set_no_excludes(self) -> None:
        """Without any excludes, both strategies must visit the same set
        of files. The previously divergent ``_*``-skip in
        ``python_module`` is gone (ADR 0041 § Layer 3); both halves now
        defer to the config-driven ``should_skip`` only.
        """
        root = self._materialise()
        glob = "**/*.py"
        excludes: list[str] = []
        module_result = python_module.extract(
            root, {"glob": glob, "exclude": excludes, "package": ""}, {}
        )
        callgraph_result = python_callgraph.extract(
            root, {"glob": glob, "exclude": excludes}, {}
        )
        module_files = _module_files(module_result.nodes)
        callgraph_files = _callgraph_files(callgraph_result.nodes)
        # Both strategies emit nodes only for files that contain at
        # least one ``def`` / ``class`` definition. ``__init__.py`` with
        # only re-exports yields zero nodes from both halves -- the
        # callgraph emits symbols only for definitions, and the module
        # strategy explicitly drops contentless ``__init__.py`` files
        # (it has no exports to record).
        definition_bearing = {
            "public_module.py",
            "_private_module.py",
            "nested/_inner.py",
            "nested/public.py",
            "excluded/skip_me.py",
        }
        # Symmetric difference must be empty: every file one strategy
        # emits, the other emits too.
        self.assertEqual(
            module_files,
            callgraph_files,
            (
                "strategy-pair drift: "
                f"only in python_module: {module_files - callgraph_files}; "
                f"only in python_callgraph: {callgraph_files - module_files}"
            ),
        )
        self.assertEqual(module_files, definition_bearing)

    def test_pair_honours_same_exclude(self) -> None:
        """When the config excludes a path, both strategies must skip it."""
        root = self._materialise()
        glob = "**/*.py"
        excludes = ["excluded/**"]
        module_result = python_module.extract(
            root, {"glob": glob, "exclude": excludes, "package": ""}, {}
        )
        callgraph_result = python_callgraph.extract(
            root, {"glob": glob, "exclude": excludes}, {}
        )
        module_files = _module_files(module_result.nodes)
        callgraph_files = _callgraph_files(callgraph_result.nodes)
        # The excluded subtree must vanish from both halves.
        self.assertNotIn("excluded/skip_me.py", module_files)
        self.assertNotIn("excluded/skip_me.py", callgraph_files)

    def test_underscore_module_appears_in_module_strategy(self) -> None:
        """The historically dropped ``_*`` files now surface as file
        anchors in ``python_module`` (closing the ``_ros2_py`` symptom).
        """
        root = self._materialise()
        module_result = python_module.extract(
            root, {"glob": "**/*.py", "exclude": [], "package": ""}, {}
        )
        files = _module_files(module_result.nodes)
        self.assertIn("_private_module.py", files)
        self.assertIn("nested/_inner.py", files)


class PythonModuleFileIdShapeTest(unittest.TestCase):
    """The ``python_module`` strategy must mint canonical file IDs.

    ADR 0041 § Layer 1 specifies ``file:{rel_posix_path_without_ext}``;
    the legacy ``file:{stem}`` form is retired because two files with
    the same stem in different directories used to collide.
    """

    def test_file_id_uses_full_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "weld" / "strategies").mkdir(parents=True, exist_ok=True)
            (root / "weld" / "strategies" / "_ros2_py.py").write_text(
                "def foo():\n    return 1\n", encoding="utf-8"
            )
            result = python_module.extract(
                root,
                {"glob": "weld/**/*.py", "exclude": [], "package": ""},
                {},
            )
            self.assertIn("file:weld/strategies/_ros2_py", result.nodes)
            # And the legacy stem-only form must not appear.
            self.assertNotIn("file:_ros2_py", result.nodes)

    def test_stem_collision_resolves_to_distinct_ids(self) -> None:
        """Two files with the same basename in different directories
        must mint distinct IDs."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir(parents=True, exist_ok=True)
            (root / "b").mkdir(parents=True, exist_ok=True)
            (root / "a" / "shared.py").write_text(
                "def x():\n    return 1\n", encoding="utf-8"
            )
            (root / "b" / "shared.py").write_text(
                "def y():\n    return 2\n", encoding="utf-8"
            )
            result = python_module.extract(
                root, {"glob": "**/*.py", "exclude": [], "package": ""}, {}
            )
            self.assertIn("file:a/shared", result.nodes)
            self.assertIn("file:b/shared", result.nodes)


if __name__ == "__main__":
    unittest.main()

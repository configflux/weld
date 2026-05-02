"""Regression: python_module strategy must record module-level constants.

Pairs with ``weld_file_index_module_constants_test``. Whereas the
file-index test guards ``wd find``, this test guards ``wd query`` --
``wd query`` consults the in-graph token index, which reads from
``props`` on each node. The python_module strategy must therefore stash
the same constant set on the file node so the query path can surface it.

Acceptance: a file with ``_NAMED_REF_RE`` at module scope must produce
a ``file:`` node whose ``props.constants`` list contains
``_NAMED_REF_RE``. Lowercase or mixed-case module-level assignments
must not appear; class- and function-scope assignments must not appear.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies.python_module import extract  # noqa: E402


_FIXTURE = '''\
"""Fixture module."""

import re

PUBLIC_CONST = 1
_PRIVATE_CONST = 2
_NAMED_REF_RE = re.compile(r"\\bfoo\\b")
ANNOTATED_CONST: int = 7

runtime_state = {}
mixed_Case = []
_internal = "x"


class Holder:
    CLASS_LEVEL = 9


def helper():
    FUNCTION_LEVEL = 10
    return FUNCTION_LEVEL


class PublicClass:
    pass


def public_function():
    return None
'''


class PythonModuleStrategyConstantsTest(unittest.TestCase):
    """``python_module.extract`` must populate ``props.constants``."""

    def _run(self, glob: str = "*.py") -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fixture.py").write_text(_FIXTURE, encoding="utf-8")
            result = extract(
                root,
                {"glob": glob, "package": ""},
                {},
            )
        return result.nodes

    def test_constants_recorded_on_file_node(self) -> None:
        """The file node for ``fixture.py`` must carry the module-level
        constants in ``props.constants``."""
        nodes = self._run()
        self.assertEqual(len(nodes), 1, f"expected one file node, got {nodes!r}")
        node = next(iter(nodes.values()))
        constants = node["props"].get("constants", [])
        self.assertIn("_NAMED_REF_RE", constants)
        self.assertIn("PUBLIC_CONST", constants)
        self.assertIn("_PRIVATE_CONST", constants)
        self.assertIn("ANNOTATED_CONST", constants)

    def test_non_constants_excluded(self) -> None:
        """Lowercase / mixed-case module assigns and class/function-body
        assigns must not appear in ``props.constants``.
        """
        nodes = self._run()
        node = next(iter(nodes.values()))
        constants = set(node["props"].get("constants", []))
        for forbidden in (
            "runtime_state", "mixed_Case", "_internal",
            "CLASS_LEVEL", "FUNCTION_LEVEL",
        ):
            self.assertNotIn(forbidden, constants)

    def test_constants_list_sorted_and_deduped(self) -> None:
        """``props.constants`` must be a sorted, deduplicated list -- the
        graph-determinism contract (ADR 0012) forbids order to vary
        across runs.
        """
        nodes = self._run()
        node = next(iter(nodes.values()))
        constants = node["props"].get("constants", [])
        self.assertEqual(constants, sorted(set(constants)))

    def test_existing_exports_unchanged(self) -> None:
        """Adding constants must not pollute ``props.exports`` -- that
        field still names public classes and functions.
        """
        nodes = self._run()
        node = next(iter(nodes.values()))
        exports = set(node["props"]["exports"])
        self.assertEqual(
            exports, {"Holder", "helper", "PublicClass", "public_function"},
        )


if __name__ == "__main__":
    unittest.main()

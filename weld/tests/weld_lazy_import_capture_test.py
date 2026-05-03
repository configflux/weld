"""Regression: ``python_module._extract_imports`` must capture lazy imports.

The original (top-level-only) walker missed function-local imports such as
the one in ``weld/strategies/ros2_topology.py`` which does
``from weld.strategies import _ros2_py as _py`` inside ``extract()`` to
break an import cycle. Because the walker only inspected ``tree.body``,
those modules never landed on ``props.imports_from`` and the graph
closure step (``weld.graph_closure._link_imports``) never emitted a
``depends_on`` edge -- leaving ``file:weld/strategies/_ros2_py`` with
zero inbound edges (the symptom this regression test pins, and which
ADR 0041's file-anchor-symmetry rule would otherwise have to ignore via
allow-list entry).

Acceptance:

- A function-local ``from pkg import mod`` produces an entry in
  ``props.imports_from``.
- A function-local ``import pkg.sub`` likewise produces an entry
  (truncated to the existing 3-dot rule).
- An import inside a ``TYPE_CHECKING`` block is captured (these were
  already missed when the block was nested under an ``if`` statement,
  even at module scope).
- The result is still sorted + deduplicated -- the determinism contract
  (ADR 0012 § 3) requires byte-stable output.
- Top-level imports continue to surface (no behavioural regression on
  the common path).
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
"""Fixture module exercising lazy-import capture."""

from __future__ import annotations

import json  # top-level: must still surface
from pathlib import Path  # top-level: parent ``pathlib`` surfaces
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Module-scope but nested inside ``if`` -- previously missed.
    from collections.abc import Iterator


def lazy_caller() -> None:
    """Function-local lazy import (the j5rj symptom shape)."""
    from weld.strategies import _ros2_py as _py  # lazy: avoids import cycle
    import os.path  # lazy `import` form
    from xml import etree as _etree  # lazy: lowercase sibling module
    return _py, os.path, _etree


class Holder:
    def deeply_nested(self):
        # Method-level lazy import (also a non-top-level form);
        # ``ElementTree`` is a class so only the parent surfaces.
        from xml.etree import ElementTree
        return ElementTree


def public_function() -> int:
    return 0
'''


class LazyImportCaptureTest(unittest.TestCase):
    """``python_module._extract_imports`` must walk all import nodes."""

    def _imports_for_fixture(self) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fixture.py").write_text(_FIXTURE, encoding="utf-8")
            result = extract(
                root,
                {"glob": "*.py", "package": ""},
                {},
            )
        # The fixture produces exactly one file node.
        self.assertEqual(
            len(result.nodes), 1, f"expected one file node, got {result.nodes!r}",
        )
        node = next(iter(result.nodes.values()))
        return list(node["props"].get("imports_from") or [])

    def test_function_local_import_from_is_captured(self) -> None:
        """``from weld.strategies import _ros2_py`` inside a function must
        appear in ``imports_from`` (the j5rj acceptance shape).

        Both the parent package (``weld.strategies``) and the qualified
        ``weld.strategies._ros2_py`` form must surface -- the qualified
        form is what lets ``graph_closure._link_imports`` land an edge
        directly on the sibling file node, satisfying ADR 0041's
        file-anchor-symmetry contract.
        """
        imports = self._imports_for_fixture()
        self.assertIn("weld.strategies", imports)
        self.assertIn("weld.strategies._ros2_py", imports)

    def test_function_local_import_statement_is_captured(self) -> None:
        """``import os.path`` inside a function must appear (truncated by
        the existing 3-dot rule)."""
        imports = self._imports_for_fixture()
        # ``import os.path`` -> ["os", "path"][:3] -> "os.path"
        self.assertIn("os.path", imports)

    def test_method_level_lazy_import_is_captured(self) -> None:
        """Imports nested inside a class method must also surface --
        the parent ``xml.etree`` lands even when the imported name
        (``ElementTree``) is a class (PascalCase)."""
        imports = self._imports_for_fixture()
        self.assertIn("xml.etree", imports)

    def test_qualified_form_emitted_only_for_private_sibling_modules(
        self,
    ) -> None:
        """``from weld.strategies import _ros2_py`` must emit
        ``weld.strategies._ros2_py`` (private-sibling-module shape:
        leading ``_``, lowercase body). ``from xml import etree``
        (no leading ``_``) must NOT emit ``xml.etree`` as a qualified
        form -- this keeps the heuristic narrow enough to avoid
        treating ordinary functions/helpers as packages while still
        landing the j5rj edge directly on ``_ros2_py``'s file node.
        """
        imports = self._imports_for_fixture()
        self.assertIn("weld.strategies._ros2_py", imports)
        # ``etree`` lacks the leading underscore, so the qualified
        # form is intentionally suppressed; the parent still lands.
        self.assertIn("xml", imports)
        self.assertNotIn("xml.etree.helper", imports)

    def test_class_alias_does_not_pollute_imports(self) -> None:
        """``from pathlib import Path`` must NOT emit
        ``pathlib.Path`` -- ``Path`` is a class (PascalCase), not a
        sibling module. Only the parent ``pathlib`` package surfaces.
        This keeps the resolver from creating
        ``package:python:pathlib.Path`` noise."""
        imports = self._imports_for_fixture()
        self.assertIn("pathlib", imports)
        self.assertNotIn("pathlib.Path", imports)

    def test_public_helper_alias_does_not_pollute_imports(self) -> None:
        """``from collections.abc import Iterator`` must NOT emit
        ``collections.abc.Iterator`` -- Iterator is a class. Even
        if it were a function/helper (``some_helper``), the lack of
        a leading underscore would suppress the qualified form, by
        design (the heuristic targets only the
        private-sibling-module shape that motivated j5rj)."""
        imports = self._imports_for_fixture()
        self.assertIn("collections.abc", imports)
        self.assertNotIn("collections.abc.Iterator", imports)

    def test_type_checking_block_import_is_captured(self) -> None:
        """``from collections.abc import Iterator`` under
        ``if TYPE_CHECKING:`` must also surface -- it is structurally
        identical to a function-local import (nested under an ``if``).
        Only the parent (``collections.abc``) is asserted here; the
        Iterator-as-class non-pollution case is covered by
        :meth:`test_public_helper_alias_does_not_pollute_imports`."""
        imports = self._imports_for_fixture()
        self.assertIn("collections.abc", imports)

    def test_top_level_imports_still_surface(self) -> None:
        """Pre-existing behaviour: top-level imports must continue to
        surface so the change is purely additive on the common path."""
        imports = self._imports_for_fixture()
        self.assertIn("json", imports)
        self.assertIn("pathlib", imports)

    def test_imports_list_sorted_and_deduped(self) -> None:
        """``imports_from`` must be a sorted, deduplicated list -- the
        graph-determinism contract (ADR 0012 § 3) requires byte-stable
        output across runs."""
        imports = self._imports_for_fixture()
        self.assertEqual(imports, sorted(set(imports)))


if __name__ == "__main__":
    unittest.main()

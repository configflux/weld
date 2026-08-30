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
  (at its full dotted depth -- see :class:`DeepImportPathTest`).
- An import inside a ``TYPE_CHECKING`` block is captured (these were
  already missed when the block was nested under an ``if`` statement,
  even at module scope).
- The result is still sorted + deduplicated -- the determinism contract
  (ADR 0012 § 3) requires byte-stable output.
- Top-level imports continue to surface (no behavioural regression on
  the common path).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


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


# Finding 04 (uuxaz.5): reverse-DNS namespaces deeper than three segments
# must NOT collapse. protoc --python_out emits
# ``acme.platform.order.schema.v1.event_pb2`` and the contract version
# (``v1`` vs ``v2``) is exactly the segment impact analysis needs. The
# old ``parts[:3]`` truncation merged v1 and v2 into a single
# ``acme.platform.order`` node -- and collapsed it against the sibling
# ``acme.platform.billing.schema.v1`` dependency too. This fixture
# reproduces the transcript-04 shape directly.
_DEEP_FIXTURE = '''\
"""Imports three distinct schema packages sharing a three-segment prefix."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent
from acme.platform.order.schema.v2.event_pb2 import OrderPlacedEventV2
from acme.platform.billing.schema.v1.event_pb2 import InvoiceIssuedEvent

import acme.platform.order.schema.v1 as _v1_alias


def register(subscriber) -> None:
    subscriber.subscribe(OrderPlacedEvent)
    subscriber.subscribe(OrderPlacedEventV2)
    subscriber.subscribe(InvoiceIssuedEvent)
    return _v1_alias
'''


class DeepImportPathTest(unittest.TestCase):
    """``_extract_imports`` must keep the full dotted module path.

    Finding 04: the old ``parts[:3]`` truncation collapsed distinct
    reverse-DNS namespaces onto one node, so ``schema.v1`` and
    ``schema.v2`` -- the exact contract version impact analysis needs --
    became indistinguishable. C# keeps the full namespace, so the same
    dependency was separated in C# and collapsed in Python.
    """

    def _imports_for_deep_fixture(self) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "multi_schema.py").write_text(_DEEP_FIXTURE, encoding="utf-8")
            result = extract(root, {"glob": "*.py", "package": ""}, {})
        self.assertEqual(
            len(result.nodes), 1, f"expected one file node, got {result.nodes!r}",
        )
        node = next(iter(result.nodes.values()))
        return list(node["props"].get("imports_from") or [])

    def test_full_dotted_path_preserved_for_import_from(self) -> None:
        """``from acme.platform.order.schema.v1.event_pb2 import Y`` must
        land the full imported module ``...schema.v1.event_pb2`` and must
        NOT collapse to the truncated ``acme.platform.order``."""
        imports = self._imports_for_deep_fixture()
        self.assertIn("acme.platform.order.schema.v1.event_pb2", imports)
        self.assertNotIn("acme.platform.order", imports)

    def test_schema_versions_do_not_collapse(self) -> None:
        """v1 and v2 of the same namespace must be two distinct entries --
        the whole point of the finding. The distinguishing ``v1``/``v2``
        segment survives at full depth."""
        imports = self._imports_for_deep_fixture()
        self.assertIn("acme.platform.order.schema.v1.event_pb2", imports)
        self.assertIn("acme.platform.order.schema.v2.event_pb2", imports)
        # No shared truncated prefix swallows both.
        self.assertNotIn("acme.platform.order", imports)

    def test_sibling_deep_namespace_is_distinct(self) -> None:
        """A different second-level branch (billing vs order) must remain
        its own dependency at full depth, not collapse onto a shared
        three-segment prefix."""
        imports = self._imports_for_deep_fixture()
        self.assertIn("acme.platform.billing.schema.v1.event_pb2", imports)
        self.assertNotIn("acme.platform.billing", imports)

    def test_plain_deep_import_statement_preserved(self) -> None:
        """``import acme.platform.order.schema.v1`` (plain ``import``
        form) keeps its full dotted path too -- no ``parts[:3]``."""
        imports = self._imports_for_deep_fixture()
        self.assertIn("acme.platform.order.schema.v1", imports)

    def test_deep_imports_sorted_and_deduped(self) -> None:
        """Determinism contract (ADR 0012 § 3) still holds at full depth."""
        imports = self._imports_for_deep_fixture()
        self.assertEqual(imports, sorted(set(imports)))


# uuxaz.6 (Finding 04 secondary): an imported name USED AS A REFERENCE
# (a handler argument, an annotation, a registry value) but never *called*
# leaves no symbol/dependency evidence. The event-driven shape
# ``subscriber.subscribe(OrderPlacedEvent)`` -- the dominant pattern in
# event-driven Python -- imports a contract class and passes it as a value.
# python_callgraph deliberately does not record cross-module references
# (ADR 0127), and python_module previously emitted only the *parent package*
# for a ``from x import SomeClass`` import, so "which services depend on this
# contract" under-reported the Python consumer while C# reported it. The fix:
# when the imported name is actually *referenced* in the module body, emit the
# qualified ``module.Name`` form too, so ``graph_closure._link_imports`` mints
# a ``package:python:module.Name`` node and a ``depends_on`` edge. Noise
# control: an imported-but-unreferenced name still emits only the parent.
_EVENT_HANDLER_FIXTURE = '''\
"""Event-driven handler registration: contracts imported and passed by value."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent
from acme.platform.billing.schema.v1.event_pb2 import InvoiceIssuedEvent
from acme.platform.audit.schema.v1.event_pb2 import UnusedEvent


def register(subscriber) -> None:
    # OrderPlacedEvent used as a call ARGUMENT (a reference, not a call).
    subscriber.subscribe(OrderPlacedEvent)
    # InvoiceIssuedEvent used as an annotation (also a reference).
    handler: InvoiceIssuedEvent = None
    return handler
    # UnusedEvent is imported but never referenced -- must NOT emit the
    # qualified form (noise control).
'''


class ReferencedImportSymbolTest(unittest.TestCase):
    """Imported-but-uncalled names used as references emit qualified evidence."""

    def _imports_for_event_fixture(self) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notify.py").write_text(_EVENT_HANDLER_FIXTURE, encoding="utf-8")
            result = extract(root, {"glob": "*.py", "package": ""}, {})
        self.assertEqual(
            len(result.nodes), 1, f"expected one file node, got {result.nodes!r}",
        )
        node = next(iter(result.nodes.values()))
        return list(node["props"].get("imports_from") or [])

    def test_referenced_import_emits_qualified_symbol(self) -> None:
        """``subscriber.subscribe(OrderPlacedEvent)`` -- the name passed as a
        value must land the qualified ``...event_pb2.OrderPlacedEvent`` so the
        closure mints a dependency node for the contract, not just its
        parent module."""
        imports = self._imports_for_event_fixture()
        self.assertIn(
            "acme.platform.order.schema.v1.event_pb2.OrderPlacedEvent", imports,
        )

    def test_annotation_reference_emits_qualified_symbol(self) -> None:
        """A name used only as a type annotation is a reference too."""
        imports = self._imports_for_event_fixture()
        self.assertIn(
            "acme.platform.billing.schema.v1.event_pb2.InvoiceIssuedEvent",
            imports,
        )

    def test_unreferenced_import_suppresses_qualified_symbol(self) -> None:
        """Noise control: an imported name never referenced in the body must
        NOT emit the qualified form -- only its parent package."""
        imports = self._imports_for_event_fixture()
        self.assertNotIn(
            "acme.platform.audit.schema.v1.event_pb2.UnusedEvent", imports,
        )
        # Parent still lands so the module-level dependency is recorded.
        self.assertIn("acme.platform.audit.schema.v1.event_pb2", imports)

    def test_parent_package_always_emitted_for_referenced_import(self) -> None:
        """The qualified form is additive -- the parent package still lands
        (existing module-level dependency behaviour is preserved)."""
        imports = self._imports_for_event_fixture()
        self.assertIn("acme.platform.order.schema.v1.event_pb2", imports)
        self.assertIn("acme.platform.billing.schema.v1.event_pb2", imports)

    def test_event_fixture_imports_sorted_and_deduped(self) -> None:
        """Determinism contract (ADR 0012 § 3) holds with qualified forms."""
        imports = self._imports_for_event_fixture()
        self.assertEqual(imports, sorted(set(imports)))

    def test_aliased_referenced_import_emits_real_symbol_id(self) -> None:
        """``from x import Foo as F`` used via ``F`` must emit the *real*
        symbol ``x.Foo`` (the graph identity), not the local alias ``x.F`` --
        usage is detected on the bound name, the edge lands on the true node."""
        src = (
            "from acme.platform.order.schema.v1.event_pb2 import "
            "OrderPlacedEvent as OPE\n\n\n"
            "def register(subscriber) -> None:\n"
            "    subscriber.subscribe(OPE)\n"
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "aliased.py").write_text(src, encoding="utf-8")
            result = extract(root, {"glob": "*.py", "package": ""}, {})
        node = next(iter(result.nodes.values()))
        imports = list(node["props"].get("imports_from") or [])
        self.assertIn(
            "acme.platform.order.schema.v1.event_pb2.OrderPlacedEvent", imports,
        )
        self.assertNotIn(
            "acme.platform.order.schema.v1.event_pb2.OPE", imports,
        )


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
        """``import os.path`` inside a function must appear at full
        dotted depth."""
        imports = self._imports_for_fixture()
        # ``import os.path`` -> full dotted path "os.path"
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

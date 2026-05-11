"""Unit tests for the libclang TU-walk helpers (ADR 0057 Wave 3).

The walks live in :mod:`weld.strategies._cpp_libclang_macros`,
:mod:`weld.strategies._cpp_libclang_templates`, and
:mod:`weld.strategies._cpp_libclang_calls`. Each helper accepts the
``cindex`` module and a translation-unit object via parameters so we
can substitute a small fake for testing without installing libclang.

The fake mirrors the narrow surface area the helpers actually read
(``cursor.kind``, ``cursor.spelling``, ``cursor.location.file``,
``cursor.referenced`` for some kinds, ``cursor.semantic_parent`` for
qualified-name walks). Anything beyond that returns None / empty so the
helpers stay defensive.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


# ---------------------------------------------------------------------------
# Fake libclang scaffolding
# ---------------------------------------------------------------------------


class _FakeCursorKind:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<FakeCursorKind {self.name}>"


class _FakeKindEnum:
    MACRO_DEFINITION = _FakeCursorKind("MACRO_DEFINITION")
    MACRO_INSTANTIATION = _FakeCursorKind("MACRO_INSTANTIATION")
    CLASS_TEMPLATE = _FakeCursorKind("CLASS_TEMPLATE")
    FUNCTION_TEMPLATE = _FakeCursorKind("FUNCTION_TEMPLATE")
    CLASS_TEMPLATE_PARTIAL_SPECIALIZATION = _FakeCursorKind(
        "CLASS_TEMPLATE_PARTIAL_SPECIALIZATION",
    )
    TYPE_REF = _FakeCursorKind("TYPE_REF")
    TEMPLATE_REF = _FakeCursorKind("TEMPLATE_REF")
    CALL_EXPR = _FakeCursorKind("CALL_EXPR")


class _FakeCindex:
    CursorKind = _FakeKindEnum

    def __init__(self, cursors: list) -> None:
        self.cursors = cursors


class _FakeTU:
    def __init__(self, cursors: list) -> None:
        self.cursor = _FakeWalkable(cursors)


class _FakeWalkable:
    def __init__(self, cursors: list) -> None:
        self._cursors = cursors

    def walk_preorder(self):
        yield from self._cursors


class _FakeLocation:
    def __init__(self, file_path: str) -> None:
        self.file = file_path


def _make_cursor(
    *,
    kind: str,
    spelling: str,
    file_path: str,
    referenced=None,
    semantic_parent=None,
    type_spelling: str = "",
) -> object:
    cursor_kind = getattr(_FakeKindEnum, kind)
    type_obj = type("FakeType", (), {"spelling": type_spelling})() if type_spelling else None
    return type("FakeCursor", (), {
        "kind": cursor_kind,
        "spelling": spelling,
        "location": _FakeLocation(file_path),
        "referenced": referenced,
        "type": type_obj,
        "semantic_parent": semantic_parent,
    })()


def _ns_parent(name: str = "ns") -> object:
    """A two-level semantic_parent chain whose outer is the TU."""
    top = type("Top", (), {
        "kind": _FakeCursorKind("TRANSLATION_UNIT"),
        "spelling": "",
        "semantic_parent": None,
    })()
    return type("Parent", (), {
        "kind": _FakeKindEnum.CLASS_TEMPLATE,
        "spelling": name,
        "semantic_parent": top,
    })()


def _make_call_cursor(
    *, callee_name: str, qualified: str, file_path: str,
) -> object:
    """CALL_EXPR cursor whose referenced resolves to *qualified*.

    The qualified name is assumed to be ``ns::<callee_name>``; we
    construct the semantic-parent chain to match.
    """
    referenced = type("FakeReferenced", (), {
        "spelling": callee_name,
        "semantic_parent": _ns_parent("ns"),
    })()
    return _make_cursor(
        kind="CALL_EXPR",
        spelling="",
        file_path=file_path,
        referenced=referenced,
    )


# ---------------------------------------------------------------------------
# Macro walks
# ---------------------------------------------------------------------------


class MacroWalkTest(unittest.TestCase):

    def test_emits_defines_macro_for_in_tree_file(self) -> None:
        from weld.strategies import _cpp_libclang_macros as macros_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "a.cpp").write_text("#define FOO 1\n", encoding="utf-8")
            cindex = _FakeCindex(cursors=[
                _make_cursor(
                    kind="MACRO_DEFINITION",
                    spelling="FOO",
                    file_path=str(tmp / "a.cpp"),
                ),
            ])
            nodes: dict[str, dict] = {}
            edges: list[dict] = []
            tu = _FakeTU(cindex.cursors)
            macros_mod.walk_translation_unit(
                cindex, tu, root=tmp, nodes=nodes, edges=edges,
            )
            self.assertIn("macro:FOO", nodes)
            self.assertEqual(len(edges), 1)
            edge = edges[0]
            self.assertEqual(edge["type"], "defines_macro")
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["to"], "macro:FOO")

    def test_skips_define_outside_repo(self) -> None:
        from weld.strategies import _cpp_libclang_macros as macros_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cindex = _FakeCindex(cursors=[
                _make_cursor(
                    kind="MACRO_DEFINITION",
                    spelling="STDLIB",
                    file_path="/usr/include/stdio.h",
                ),
            ])
            nodes: dict[str, dict] = {}
            edges: list[dict] = []
            tu = _FakeTU(cindex.cursors)
            macros_mod.walk_translation_unit(
                cindex, tu, root=tmp, nodes=nodes, edges=edges,
            )
            self.assertEqual(nodes, {})
            self.assertEqual(edges, [])


# ---------------------------------------------------------------------------
# Template walks
# ---------------------------------------------------------------------------


class TemplateWalkTest(unittest.TestCase):

    def test_class_template_records_definition_node(self) -> None:
        from weld.strategies import _cpp_libclang_templates as tmpl_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "t.hpp").write_text("template<typename T> class Foo {};\n")
            cindex = _FakeCindex(cursors=[
                _make_cursor(
                    kind="CLASS_TEMPLATE",
                    spelling="Foo",
                    file_path=str(tmp / "t.hpp"),
                ),
            ])
            nodes: dict[str, dict] = {}
            edges: list[dict] = []
            tu = _FakeTU(cindex.cursors)
            tmpl_mod.walk_translation_unit(
                cindex, tu, root=tmp, nodes=nodes, edges=edges,
            )
            self.assertIn("template:Foo", nodes)
            self.assertEqual(nodes["template:Foo"]["type"], "template_definition")

    def test_type_ref_emits_instantiated_by(self) -> None:
        from weld.strategies import _cpp_libclang_templates as tmpl_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "u.cpp").write_text("Foo<int> x;\n")
            tu_cursor = type("Top", (), {
                "kind": _FakeCursorKind("TRANSLATION_UNIT"),
                "spelling": "",
                "semantic_parent": None,
            })()
            referenced = type("FakeRef", (), {
                "spelling": "Foo",
                "semantic_parent": tu_cursor,
            })()
            cindex = _FakeCindex(cursors=[
                _make_cursor(
                    kind="TYPE_REF",
                    spelling="Foo",
                    file_path=str(tmp / "u.cpp"),
                    referenced=referenced,
                    type_spelling="Foo<int>",
                ),
            ])
            nodes: dict[str, dict] = {}
            edges: list[dict] = []
            tu = _FakeTU(cindex.cursors)
            tmpl_mod.walk_translation_unit(
                cindex, tu, root=tmp, nodes=nodes, edges=edges,
            )
            self.assertEqual(len(edges), 1)
            edge = edges[0]
            self.assertEqual(edge["type"], "instantiated_by")
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["from"], "template:Foo")


# ---------------------------------------------------------------------------
# Cross-TU call upgrades (precedence rule)
# ---------------------------------------------------------------------------


class CallUpgradeTest(unittest.TestCase):

    def test_upgrade_rewrites_to_and_confidence(self) -> None:
        from weld.strategies import _cpp_libclang_calls as calls_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "a.cpp").write_text("int x;\n", encoding="utf-8")

            edges: list[dict] = [
                {
                    "from": "symbol:bar",
                    "to": "symbol:unresolved:foo",
                    "type": "calls",
                    "props": {
                        "source_strategy": "tree_sitter",
                        "confidence": "inferred",
                        "file": "a.cpp",
                    },
                },
            ]
            cindex = _FakeCindex(cursors=[
                _make_call_cursor(
                    callee_name="foo",
                    qualified="ns::foo",
                    file_path=str(tmp / "a.cpp"),
                ),
            ])
            tu = _FakeTU(cindex.cursors)
            upgraded = calls_mod.upgrade_unresolved_calls(
                cindex, tu, root=tmp, edges=edges,
            )
            self.assertEqual(upgraded, 1)
            edge = edges[0]
            self.assertEqual(edge["to"], "symbol:ns::foo")
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["props"]["provenance"], "libclang")
            self.assertTrue(edge["props"]["resolved"])

    def test_upgrade_skips_when_no_unresolved_edges(self) -> None:
        from weld.strategies import _cpp_libclang_calls as calls_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            edges: list[dict] = []
            cindex = _FakeCindex(cursors=[
                _make_call_cursor(
                    callee_name="foo",
                    qualified="ns::foo",
                    file_path=str(tmp / "a.cpp"),
                ),
            ])
            tu = _FakeTU(cindex.cursors)
            upgraded = calls_mod.upgrade_unresolved_calls(
                cindex, tu, root=tmp, edges=edges,
            )
            self.assertEqual(upgraded, 0)

    def test_upgrade_skips_definite_edges(self) -> None:
        """Edges already at ``definite`` are left alone."""
        from weld.strategies import _cpp_libclang_calls as calls_mod

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            edges: list[dict] = [
                {
                    "from": "symbol:bar",
                    "to": "symbol:foo",
                    "type": "calls",
                    "props": {
                        "source_strategy": "tree_sitter",
                        "confidence": "definite",
                        "file": "a.cpp",
                    },
                },
            ]
            cindex = _FakeCindex(cursors=[
                _make_call_cursor(
                    callee_name="foo",
                    qualified="ns::foo",
                    file_path=str(tmp / "a.cpp"),
                ),
            ])
            tu = _FakeTU(cindex.cursors)
            upgraded = calls_mod.upgrade_unresolved_calls(
                cindex, tu, root=tmp, edges=edges,
            )
            self.assertEqual(upgraded, 0)
            # Edge unchanged.
            self.assertEqual(edges[0]["to"], "symbol:foo")
            self.assertEqual(edges[0]["props"]["confidence"], "definite")


if __name__ == "__main__":
    unittest.main()

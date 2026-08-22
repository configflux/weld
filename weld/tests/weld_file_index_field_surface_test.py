"""bd 2peg: ``wd find`` must not silently return a subset for Python.

The reported failure: ``wd references created_at`` under-reported its textual
hits. Measured in the worktree it was sharper than reported -- ``wd find
created_at`` returned three files and *none of them were Python*, while the
term occurs in nine Python files including ``weld/discovery_state.py``, which
declares the field. Every hit came from the generic tokenizer used for ``.sh``
and ``.md``; the rich Python extractor contributed nothing, because it
harvested only the definition surface (classes, public functions, imports,
``__all__``, module constants) and a field name is none of those.

A textual channel that silently returns a subset is worse than one that
returns nothing, because it looks like an answer. These tests pin the three
forms a field name takes in Python, the bounds that keep the harvest from
becoming full-text indexing, and the renderer half that discarded the hits
after they had already been computed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._cli_render import render_references
from weld._file_index_extractors import (
    _MAX_PYTHON_FIELD_NAMES,
    _extract_python_tokens,
    _field_surface_names,
    _is_field_name,
)
from weld.file_index import build_file_index, find_files


class FieldSurfaceFormTest(unittest.TestCase):
    """The three shapes a field name takes in Python source."""

    def test_dataclass_field_annotation_is_harvested(self) -> None:
        source = (
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class DiscoveryState:\n"
            '    created_at: str = ""\n'
        )
        self.assertIn("created_at", _extract_python_tokens(source))

    def test_plain_class_attribute_is_harvested(self) -> None:
        source = 'class S:\n    created_at = ""\n'
        self.assertIn("created_at", _extract_python_tokens(source))

    def test_keyword_argument_name_is_harvested(self) -> None:
        source = "state = DiscoveryState(created_at=now())\n"
        self.assertIn("created_at", _extract_python_tokens(source))

    def test_identifier_shaped_string_literal_is_harvested(self) -> None:
        """The majority form: a dict key, which is how a field is read."""
        source = 'payload = {"created_at": _utc_now(), "pid": os.getpid()}\n'
        tokens = _extract_python_tokens(source)
        self.assertIn("created_at", tokens)
        self.assertIn("pid", tokens)

    def test_method_local_names_are_not_harvested(self) -> None:
        """Body-only, or this becomes full-text indexing of every local."""
        source = "class S:\n    def m(self):\n        scratch_local = 1\n"
        self.assertNotIn("scratch_local", _extract_python_tokens(source))

    def test_definition_surface_is_still_harvested(self) -> None:
        """The pre-existing extraction must survive the addition."""
        source = (
            "import json\n"
            "MAX_THINGS = 4\n"
            "class Widget:\n    pass\n"
            "def public_fn():\n    pass\n"
        )
        tokens = _extract_python_tokens(source)
        for expected in ("json", "MAX_THINGS", "Widget", "public_fn"):
            self.assertIn(expected, tokens)


class FieldSurfaceBoundsTest(unittest.TestCase):
    """What keeps the harvest bounded and shape-checked."""

    def test_non_identifier_strings_are_rejected(self) -> None:
        for value in ("utf-8", "a/b/c", "hello world", "%s items", "", "3rd"):
            self.assertFalse(_is_field_name(value), value)

    def test_single_characters_are_rejected(self) -> None:
        """``"r"`` / ``"w"`` mode strings are not fields."""
        self.assertFalse(_is_field_name("r"))
        self.assertTrue(_is_field_name("id"))

    def test_overlong_names_are_rejected(self) -> None:
        self.assertFalse(_is_field_name("a" * 200))

    def test_harvest_is_capped(self) -> None:
        source = "\n".join(
            f'x = {{"field_{i}": {i}}}' for i in range(_MAX_PYTHON_FIELD_NAMES * 2)
        )
        import ast

        harvested = _field_surface_names(ast.parse(source))
        self.assertEqual(len(harvested), _MAX_PYTHON_FIELD_NAMES)

    def test_harvest_is_deterministic(self) -> None:
        source = (
            "class S:\n    beta: int = 0\n    alpha: int = 0\n"
            'x = {"zulu": 1, "yankee": 2}\n'
        )
        import ast

        first = _field_surface_names(ast.parse(source))
        second = _field_surface_names(ast.parse(source))
        self.assertEqual(first, second)

    def test_declared_names_win_the_cap_over_mentioned_ones(self) -> None:
        """Class attributes are ordered ahead of string literals on purpose."""
        import ast

        source = "class S:\n    declared_field: int = 0\n" + "\n".join(
            f'x = {{"mentioned_{i}": {i}}}'
            for i in range(_MAX_PYTHON_FIELD_NAMES * 2)
        )
        harvested = _field_surface_names(ast.parse(source))
        self.assertIn("declared_field", harvested)


class FindRecallEndToEndTest(unittest.TestCase):
    """The reported command, over a fixture carrying the measured forms."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_every_form_is_findable_from_the_built_index(self) -> None:
        self._write(
            "declares.py",
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class State:\n"
            '    created_at: str = ""\n',
        )
        self._write(
            "dict_key.py",
            'def lock():\n    return {"created_at": 1}\n',
        )
        self._write("kwarg.py", "s = State(created_at=1)\n")
        self._write("unrelated.py", "def other():\n    return 2\n")

        index = build_file_index(self.root)
        found = {
            entry["path"]
            for entry in find_files(index, "created_at").get("files", [])
        }
        self.assertEqual(found, {"declares.py", "dict_key.py", "kwarg.py"})


class ReferencesRendererTest(unittest.TestCase):
    """The half that threw the hits away after computing them."""

    def test_textual_hits_survive_a_node_not_found_error(self) -> None:
        payload = {
            "symbol": "created_at",
            "matches": [],
            "callers": [],
            "edges": [],
            "error": "node not found: created_at",
            "files": [{"path": "weld/discovery_state.py", "score": 1}],
        }
        rendered = render_references(payload)
        self.assertIn("error: node not found: created_at", rendered)
        self.assertIn("textual hits (1)", rendered)
        self.assertIn("weld/discovery_state.py", rendered)

    def test_error_with_no_hits_still_reports_only_the_error(self) -> None:
        payload = {
            "symbol": "nope",
            "matches": [],
            "callers": [],
            "edges": [],
            "error": "node not found: nope",
            "files": [],
        }
        rendered = render_references(payload)
        self.assertIn("error: node not found: nope", rendered)
        self.assertNotIn("textual hits", rendered)

    def test_clean_envelope_with_nothing_still_says_no_references(self) -> None:
        rendered = render_references(
            {"symbol": "x", "matches": [], "callers": [], "files": []}
        )
        self.assertIn("no references", rendered)


if __name__ == "__main__":
    unittest.main()

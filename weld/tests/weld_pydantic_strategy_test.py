"""Tests for the pydantic discovery strategy.

The strategy walks Python modules in a contracts directory and emits
``contract:<Name>`` nodes for ``BaseModel`` subclasses (capturing the
declared field names) plus ``enum:<Name>`` nodes for ``StrEnum``
subclasses defined alongside.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.pydantic import extract


_HAPPY_CONTRACT = """\
\"\"\"Module docstring.\"\"\"

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    \"\"\"Payload for registering a new user.\"\"\"

    email: str
    display_name: str
    age: int
"""

_ENUM_MODULE = """\
from enum import StrEnum


class Status(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
"""

_PRIVATE_MODULE = """\
from pydantic import BaseModel


class HiddenContract(BaseModel):
    secret: str
"""

_SYNTAX_ERROR = "class Broken(:\n"


class TestPydanticEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_contracts_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_directory_with_no_python_files_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts").mkdir()
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertEqual(result.nodes, {})

    def test_syntax_error_module_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts").mkdir()
            (root / "contracts" / "broken.py").write_text(
                _SYNTAX_ERROR, encoding="utf-8"
            )
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertEqual(result.nodes, {})


class TestPydanticHappyPath(unittest.TestCase):
    """BaseModel subclasses produce contract nodes with field lists."""

    def test_extracts_contract_with_fields_and_docstring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts").mkdir()
            (root / "contracts" / "register.py").write_text(
                _HAPPY_CONTRACT, encoding="utf-8"
            )
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertIn("contract:RegisterRequest", result.nodes)
            node = result.nodes["contract:RegisterRequest"]
            self.assertEqual(node["type"], "contract")
            self.assertEqual(node["label"], "RegisterRequest")
            props = node["props"]
            self.assertEqual(props["file"], "contracts/register.py")
            self.assertEqual(
                props["fields"], ["email", "display_name", "age"]
            )
            self.assertEqual(
                props["description"], "Payload for registering a new user."
            )
            self.assertEqual(props["source_strategy"], "pydantic")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")


class TestPydanticEdgeCases(unittest.TestCase):
    """Filename rules and StrEnum handling are tested independently."""

    def test_underscore_prefixed_modules_are_skipped(self) -> None:
        # ``_helpers.py`` and similar private modules must not contribute
        # contract nodes even if they declare BaseModel subclasses.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts").mkdir()
            (root / "contracts" / "_helpers.py").write_text(
                _PRIVATE_MODULE, encoding="utf-8"
            )
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertEqual(result.nodes, {})

    def test_str_enum_classes_are_emitted_as_enum_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts").mkdir()
            (root / "contracts" / "status.py").write_text(
                _ENUM_MODULE, encoding="utf-8"
            )
            result = extract(root, {"glob": "contracts/*.py"}, {})
            self.assertIn("enum:Status", result.nodes)
            node = result.nodes["enum:Status"]
            self.assertEqual(node["type"], "enum")
            members = node["props"]["members"]
            names = {m["name"] for m in members}
            self.assertEqual(names, {"ACTIVE", "SUSPENDED"})
            self.assertEqual(node["props"]["source_strategy"], "pydantic")


if __name__ == "__main__":
    unittest.main()

"""Dormant-path and contract tests for ``cpp_libclang`` (ADR 0057 Wave 3).

Covers:

  * Dormant-path contract: the strategy must return an empty result when
    any of the three preconditions fails (libclang missing, compile-db
    missing, or env var unset).
  * Compile-database parser: defensive parsing of
    ``compile_commands.json``.
  * Edge-vocabulary additions: ``macro``, ``template_definition`` node
    types and ``defines_macro``, ``expands_to``, ``instantiated_by``
    edge types are present in the shared contract.

Active libclang walks are covered in
:mod:`weld.tests.weld_cpp_libclang_walks_test` (which uses fakes for
the binding) and the import-time probe is guarded with
``importorskip``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies import cpp_libclang  # noqa: E402
from weld.strategies import _cpp_libclang_db as db_mod  # noqa: E402


def _write_db(tmp: Path, entries: list[dict]) -> Path:
    db = tmp / "compile_commands.json"
    db.write_text(json.dumps(entries), encoding="utf-8")
    return db


# ---------------------------------------------------------------------------
# Dormant-path contract
# ---------------------------------------------------------------------------


class DormantPathTest(unittest.TestCase):
    """``cpp_libclang.extract`` returns empty unless all three gates pass."""

    def test_dormant_when_libclang_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_db(tmp, [])
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: "1"}, clear=False,
            ), mock.patch.object(
                db_mod, "is_libclang_available", return_value=False,
            ):
                result = cpp_libclang.extract(tmp, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

    def test_dormant_when_compile_db_missing(self) -> None:
        """No ``compile_commands.json`` -> strategy stays empty."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: "1"}, clear=False,
            ), mock.patch.object(
                db_mod, "is_libclang_available", return_value=True,
            ):
                result = cpp_libclang.extract(tmp, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_dormant_when_env_var_unset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_db(tmp, [{"file": "a.cpp", "directory": str(tmp),
                              "arguments": ["clang++", "a.cpp"]}])
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: ""}, clear=False,
            ), mock.patch.object(
                db_mod, "is_libclang_available", return_value=True,
            ):
                result = cpp_libclang.extract(tmp, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_active_when_all_three_gates_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_db(tmp, [{"file": "a.cpp", "directory": str(tmp),
                              "arguments": ["clang++", "a.cpp"]}])
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: "1"}, clear=False,
            ), mock.patch.object(
                db_mod, "is_libclang_available", return_value=True,
            ):
                active, reason = db_mod.is_libclang_active(tmp)
            self.assertTrue(active, f"expected active, got reason={reason}")
            self.assertEqual(reason, "active")


# ---------------------------------------------------------------------------
# Compile-database parser
# ---------------------------------------------------------------------------


class CompileDbParserTest(unittest.TestCase):
    """Defensive parsing of ``compile_commands.json`` entries."""

    def test_parses_well_formed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "src").mkdir()
            (tmp / "src" / "main.cpp").write_text("int main() { return 0; }\n")
            db = _write_db(tmp, [
                {
                    "directory": str(tmp),
                    "file": "src/main.cpp",
                    "arguments": ["clang++", "-c", "src/main.cpp"],
                }
            ])
            entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry.file_rel, "src/main.cpp")
            self.assertIn("clang++", entry.arguments)

    def test_normalises_command_form(self) -> None:
        """An entry that uses ``command`` (not ``arguments``) is accepted."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "a.cpp").write_text("int x;\n")
            db = _write_db(tmp, [
                {
                    "directory": str(tmp),
                    "file": "a.cpp",
                    "command": "clang++ -c a.cpp",
                }
            ])
            entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(len(entries), 1)

    def test_drops_malformed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = _write_db(tmp, [
                {},  # missing required fields
                {"directory": str(tmp)},  # missing file
                {"file": "a.cpp"},  # missing directory
                {"file": 42, "directory": str(tmp), "arguments": []},
                {"file": "a.cpp", "directory": str(tmp),
                 "arguments": ["clang++", "a.cpp"]},  # valid
            ])
            entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(len(entries), 1)

    def test_returns_empty_on_oversize_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "compile_commands.json"
            db.write_text("[]", encoding="utf-8")
            with mock.patch.object(db_mod, "_MAX_DB_BYTES", 0):
                entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(entries, [])

    def test_returns_empty_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "compile_commands.json"
            db.write_text("{not json", encoding="utf-8")
            entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(entries, [])

    def test_returns_empty_on_non_list_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "compile_commands.json"
            db.write_text(json.dumps({"oops": True}), encoding="utf-8")
            entries = db_mod.parse_entries(db, root=tmp)
            self.assertEqual(entries, [])

    def test_find_compile_db_scans_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self.assertIsNone(db_mod.find_compile_db(tmp))
            (tmp / "build").mkdir()
            (tmp / "build" / "compile_commands.json").write_text("[]")
            found = db_mod.find_compile_db(tmp)
            self.assertIsNotNone(found)
            self.assertEqual(
                found.relative_to(tmp).as_posix(),
                "build/compile_commands.json",
            )

    def test_find_compile_db_prefers_root_over_build(self) -> None:
        """Root-level database wins over a nested one (candidate order)."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "build").mkdir()
            (tmp / "build" / "compile_commands.json").write_text("[]")
            (tmp / "compile_commands.json").write_text("[]")
            found = db_mod.find_compile_db(tmp)
            self.assertEqual(found, tmp / "compile_commands.json")


# ---------------------------------------------------------------------------
# Edge contract: vocabulary additions
# ---------------------------------------------------------------------------


class EdgeContractTest(unittest.TestCase):
    """All new vocabulary additions are present in the shared contract."""

    def test_macro_node_type_in_valid_vocabulary(self) -> None:
        from weld.contract import VALID_NODE_TYPES
        self.assertIn("macro", VALID_NODE_TYPES)

    def test_template_definition_node_type_in_valid_vocabulary(self) -> None:
        from weld.contract import VALID_NODE_TYPES
        self.assertIn("template_definition", VALID_NODE_TYPES)

    def test_defines_macro_edge_type_in_valid_vocabulary(self) -> None:
        from weld.contract import VALID_EDGE_TYPES
        self.assertIn("defines_macro", VALID_EDGE_TYPES)

    def test_expands_to_edge_type_in_valid_vocabulary(self) -> None:
        from weld.contract import VALID_EDGE_TYPES
        self.assertIn("expands_to", VALID_EDGE_TYPES)

    def test_instantiated_by_edge_type_in_valid_vocabulary(self) -> None:
        from weld.contract import VALID_EDGE_TYPES
        self.assertIn("instantiated_by", VALID_EDGE_TYPES)


# ---------------------------------------------------------------------------
# Capability registration
# ---------------------------------------------------------------------------


class CapabilityRegistrationTest(unittest.TestCase):
    def test_cpp_libclang_registered_in_capabilities(self) -> None:
        from weld._capabilities_registry import STRATEGY_CAPABILITIES
        self.assertIn("cpp_libclang", STRATEGY_CAPABILITIES)
        cap = STRATEGY_CAPABILITIES["cpp_libclang"]
        # The strategy attributes to a distinct framework so the
        # capability matrix lights up per-ecosystem.
        self.assertEqual(cap.framework, "cpp_libclang")
        self.assertIn("compile_commands.json", cap.file_basenames)


# ---------------------------------------------------------------------------
# libclang import-time probe (skipped when extra is not installed)
# ---------------------------------------------------------------------------


class LibclangAvailabilityProbeTest(unittest.TestCase):
    """Skipped when libclang is not installed; otherwise verifies the probe."""

    def test_probe_reflects_availability(self) -> None:
        try:
            import clang.cindex  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("clang.cindex not installed")
        self.assertTrue(db_mod.is_libclang_available())


if __name__ == "__main__":
    unittest.main()

"""Tests for the ``csharp_efcore`` strategy (ADR 0056 Wave 2)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES, VALID_NODE_TYPES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_efcore import (
    _entity_id,
    _pluralise,
    _resolve_table,
    _symbol_id,
    extract,
)


class PluraliseTest(unittest.TestCase):
    """Pluralisation heuristic for the fallback table-name case."""

    def test_regular_noun_appends_s(self) -> None:
        self.assertEqual(_pluralise("Order"), "orders")
        self.assertEqual(_pluralise("Customer"), "customers")

    def test_consonant_y_becomes_ies(self) -> None:
        self.assertEqual(_pluralise("Category"), "categories")
        self.assertEqual(_pluralise("History"), "histories")

    def test_vowel_y_keeps_y_plus_s(self) -> None:
        # Defensive: vowel-y nouns ('Day' -> 'days') must not become
        # 'dies'. Standard English rule.
        self.assertEqual(_pluralise("Day"), "days")

    def test_sibilant_endings_append_es(self) -> None:
        self.assertEqual(_pluralise("Bus"), "buses")
        self.assertEqual(_pluralise("Box"), "boxes")
        self.assertEqual(_pluralise("Branch"), "branches")
        self.assertEqual(_pluralise("Dish"), "dishes")

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(_pluralise(""), "")


class IdShapeTest(unittest.TestCase):
    """ID conventions match other strategies."""

    def test_symbol_id_with_namespace(self) -> None:
        self.assertEqual(
            _symbol_id("Sample.Dal", "OrderDbContext"),
            "symbol:csharp:Sample.Dal.OrderDbContext",
        )

    def test_symbol_id_without_namespace(self) -> None:
        self.assertEqual(
            _symbol_id("", "OrderDbContext"),
            "symbol:csharp:OrderDbContext",
        )

    def test_entity_id_shape(self) -> None:
        # Mirrors :mod:`weld.strategies.sqlalchemy` for cross-strategy
        # ID compatibility.
        self.assertEqual(_entity_id("Order"), "entity:Order")


class ResolveTableTest(unittest.TestCase):
    """Table-name resolution order: attribute first, plural fallback."""

    def test_attribute_wins(self) -> None:
        table_attrs = {"Order": "ord"}
        self.assertEqual(_resolve_table("Order", table_attrs), ("ord", "definite"))

    def test_falls_back_to_plural(self) -> None:
        self.assertEqual(_resolve_table("Order", {}), ("orders", "inferred"))


class DbContextDetectionTest(unittest.TestCase):
    """End-to-end ``extract()`` cases."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def test_dbcontext_with_two_dbsets_emits_symbol_and_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Dal" / "AppDb.cs",
                """\
                using Microsoft.EntityFrameworkCore;
                namespace Sample.Dal;
                public class AppDb : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                    public DbSet<Customer> Customers { get; set; } = null!;
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIsInstance(result, StrategyResult)

            dbcontext_id = "symbol:csharp:Sample.Dal.AppDb"
            self.assertIn(dbcontext_id, result.nodes)
            dbcontext_node = result.nodes[dbcontext_id]
            self.assertEqual(dbcontext_node["type"], "symbol")
            self.assertEqual(dbcontext_node["props"]["kind"], "dbcontext")
            self.assertEqual(dbcontext_node["props"]["language"], "csharp")
            self.assertEqual(
                dbcontext_node["props"]["entities"], ["Customer", "Order"],
            )
            self.assertEqual(dbcontext_node["props"]["confidence"], "definite")

            self.assertIn("entity:Order", result.nodes)
            self.assertIn("entity:Customer", result.nodes)
            self.assertIn("entity", VALID_NODE_TYPES)

    def test_table_attribute_takes_precedence_over_pluralisation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Entities" / "Order.cs",
                """\
                using System.ComponentModel.DataAnnotations.Schema;
                namespace App.Entities;
                [Table("orders_v2")]
                public class Order
                {
                    public int Id { get; set; }
                }
                """,
            )
            self._write(
                root / "Dal" / "Db.cs",
                """\
                using Microsoft.EntityFrameworkCore;
                namespace App.Dal;
                public class Db : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            entity = result.nodes["entity:Order"]
            self.assertEqual(entity["props"]["table"], "orders_v2")
            self.assertEqual(entity["props"]["table_confidence"], "definite")
            self.assertEqual(entity["props"]["authority"], "canonical")

    def test_pluralisation_fallback_marks_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Db.cs",
                """\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            entity = result.nodes["entity:Order"]
            self.assertEqual(entity["props"]["table"], "orders")
            self.assertEqual(entity["props"]["table_confidence"], "inferred")
            self.assertEqual(entity["props"]["authority"], "derived")

    def test_namespace_qualified_dbset_type_strips_to_basename(self) -> None:
        # ``DbSet<App.Entities.Order>`` should still resolve to
        # ``entity:Order`` (basename), matching the cross-strategy
        # convention.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<App.Entities.Order> Orders { get; set; } = null!;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIn("entity:Order", result.nodes)

    def test_class_without_dbcontext_base_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Plain.cs").write_text(
                textwrap.dedent("""\
                namespace App;
                public class Plain
                {
                    public int Value { get; set; }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})


class EdgeContractTest(unittest.TestCase):
    """ADR 0050: every edge must carry a CONFIDENCE_VALUES value."""

    def test_contains_edge_links_dbcontext_to_entity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            edge = next(e for e in result.edges if e["type"] == "contains")
            self.assertEqual(edge["from"], "symbol:csharp:App.Db")
            self.assertEqual(edge["to"], "entity:Order")
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(
                edge["props"]["source_strategy"], "csharp_efcore",
            )

    def test_every_emitted_edge_has_valid_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                    public DbSet<Customer> Customers { get; set; } = null!;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


class RobustnessTest(unittest.TestCase):
    """Pathological inputs do not crash discovery."""

    def test_unreadable_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Bad.cs").write_bytes(b"\xff\xfe\xfd not utf-8")
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_dbcontext_with_no_dbsets_emits_symbol_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class EmptyDb : DbContext { }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIn("symbol:csharp:App.EmptyDb", result.nodes)
            self.assertEqual(result.edges, [])


class DeterminismTest(unittest.TestCase):
    """Two consecutive runs on the same tree produce identical output."""

    def test_entities_list_is_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<Zebra> Zs { get; set; } = null!;
                    public DbSet<Antelope> As { get; set; } = null!;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            entities = result.nodes["symbol:csharp:App.Db"]["props"][
                "entities"
            ]
            self.assertEqual(entities, ["Antelope", "Zebra"])

    def test_consecutive_runs_yield_identical_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Db.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.EntityFrameworkCore;
                namespace App;
                public class Db : DbContext
                {
                    public DbSet<Order> Orders { get; set; } = null!;
                }
                """),
                encoding="utf-8",
            )
            first = extract(root, {"glob": "**/*.cs"}, {})
            second = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(first.nodes, second.nodes)
            self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()

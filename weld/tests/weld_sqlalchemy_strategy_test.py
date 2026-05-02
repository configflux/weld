"""Tests for the sqlalchemy discovery strategy.

The strategy walks Python modules in a domain directory and emits one
``entity:<Class>`` node per ``Base`` subclass (capturing tablename,
column names, and any mixins) and one ``enum:<Class>`` node per
``StrEnum`` subclass. ForeignKey columns enqueue a pending edge in the
shared ``context`` so the orchestrator can resolve cross-module refs
once every entity has been seen.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.sqlalchemy import extract


_HAPPY_ENTITY = """\
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str]
    display_name: Mapped[str]
"""

_FK_ENTITY = """\
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE")
    )
"""

_ENUM_MODULE = """\
from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "pending"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"
"""

_SYNTAX_ERROR = "class Broken(:\n"


class TestSqlalchemyEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_domain_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "domain/*.py"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_directory_with_no_python_files_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            result = extract(root, {"glob": "domain/*.py"}, {})
            self.assertEqual(result.nodes, {})

    def test_syntax_error_module_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            (root / "domain" / "broken.py").write_text(
                _SYNTAX_ERROR, encoding="utf-8"
            )
            result = extract(root, {"glob": "domain/*.py"}, {})
            self.assertEqual(result.nodes, {})


class TestSqlalchemyHappyPath(unittest.TestCase):
    """Base subclasses become entity nodes with tablename and columns."""

    def test_extracts_entity_with_tablename_and_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            (root / "domain" / "user.py").write_text(
                _HAPPY_ENTITY, encoding="utf-8"
            )
            ctx: dict = {}
            result = extract(root, {"glob": "domain/*.py"}, ctx)
            self.assertIn("entity:User", result.nodes)
            node = result.nodes["entity:User"]
            self.assertEqual(node["type"], "entity")
            self.assertEqual(node["label"], "User")
            props = node["props"]
            self.assertEqual(props["table"], "users")
            self.assertEqual(props["file"], "domain/user.py")
            self.assertCountEqual(
                props["columns"], ["id", "email", "display_name"]
            )
            self.assertEqual(props["mixins"], [])
            self.assertEqual(props["source_strategy"], "sqlalchemy")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            # The shared context's table-to-entity index must be populated
            # so cross-module FK resolution can find this row later.
            self.assertEqual(ctx["table_to_entity"]["users"], "entity:User")


class TestSqlalchemyEdgeCases(unittest.TestCase):
    """ForeignKey edges and StrEnum class handling are tested separately."""

    def test_foreign_key_enqueues_pending_edge_in_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            (root / "domain" / "order.py").write_text(
                _FK_ENTITY, encoding="utf-8"
            )
            ctx: dict = {}
            result = extract(root, {"glob": "domain/*.py"}, ctx)
            self.assertIn("entity:Order", result.nodes)
            pending = ctx.get("pending_fk_edges", [])
            self.assertEqual(len(pending), 1)
            edge = pending[0]
            self.assertEqual(edge["from"], "entity:Order")
            self.assertEqual(edge["to"], "__table__:users")
            self.assertEqual(edge["type"], "depends_on")
            self.assertEqual(edge["props"]["fk"], "users.id")
            self.assertEqual(edge["props"]["ondelete"], "CASCADE")

    def test_str_enum_class_emits_enum_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            (root / "domain" / "status.py").write_text(
                _ENUM_MODULE, encoding="utf-8"
            )
            result = extract(root, {"glob": "domain/*.py"}, {})
            self.assertIn("enum:OrderStatus", result.nodes)
            node = result.nodes["enum:OrderStatus"]
            self.assertEqual(node["type"], "enum")
            members = {m["name"] for m in node["props"]["members"]}
            self.assertEqual(members, {"PENDING", "SHIPPED", "CANCELLED"})
            self.assertEqual(node["props"]["source_strategy"], "sqlalchemy")

    def test_underscore_module_other_than_init_is_skipped(self) -> None:
        # Domain folders sometimes hold private helpers like ``_mixins.py``;
        # they must not contribute entities, but ``__init__.py`` is allowed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "domain").mkdir()
            (root / "domain" / "_mixins.py").write_text(
                _HAPPY_ENTITY, encoding="utf-8"
            )
            result = extract(root, {"glob": "domain/*.py"}, {})
            self.assertNotIn("entity:User", result.nodes)


if __name__ == "__main__":
    unittest.main()

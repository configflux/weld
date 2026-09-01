"""Shared harness for the import-table cases -- how a module slot gets read.

Three test files now share it, covering both halves of one subject: what
``weld.strategies._python_sibling_import`` infers from a bare name and what it
refuses (bd ``sigz2``), and what
``weld.strategies._python_relative_import`` computes for an explicit
``from .x import y`` and what it refuses (bd ``zr486``). Every split among them
is the repo line-count cap rather than a difference in kind, so the plumbing
lives here instead of being written three times.

Every case builds real files on disk and runs ``python_callgraph.extract`` over
them. Nothing here assembles a node or edge payload by hand: what the
assertions read is what the strategy actually wrote (ADR 0139 mechanism 1).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies import python_callgraph as pc


def write(root: Path, rel: str, body: str) -> None:
    """Write *body* to ``root/rel``, dedented and with the leading blank gone."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


class ExtractCase(unittest.TestCase):
    """One temp tree, one ``extract`` over :attr:`GLOB`.

    Subclasses supply :meth:`build_tree`. ``GLOB`` defaults to everything the
    tree holds; a case that needs a sibling file *outside* the configured glob
    narrows it.
    """

    GLOB = "**/*.py"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="weld_sibling_import_"))
        self.build_tree()

    def build_tree(self) -> None:  # pragma: no cover - always overridden
        raise NotImplementedError

    def run_extract(self) -> tuple[dict, list]:
        result = pc.extract(self.tmp, {"glob": self.GLOB}, {})
        return result.nodes, result.edges

    def targets(self, edges: list, caller: str) -> set[str]:
        """Every ``calls`` target recorded for *caller*."""
        return {e["to"] for e in edges if e["type"] == "calls" and e["from"] == caller}

    def edge_between(self, edges: list, caller: str, target: str) -> dict:
        return next(
            e
            for e in edges
            if e["type"] == "calls" and e["from"] == caller and e["to"] == target
        )


__all__ = ["ExtractCase", "write"]

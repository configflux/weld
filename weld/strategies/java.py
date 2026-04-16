"""Strategy: Java symbols via the shared tree-sitter extractor."""

from __future__ import annotations

from pathlib import Path

from weld.strategies import tree_sitter
from weld.strategies._helpers import StrategyResult


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Java source files using the shared tree-sitter strategy."""
    configured = dict(source)
    configured["language"] = "java"
    configured.setdefault("source_strategy", "java")
    return tree_sitter.extract(root, configured, context)

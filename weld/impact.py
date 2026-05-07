"""Compatibility shim for the split impact module.

The blast-radius implementation lives in :mod:`weld.impact_core` (pure
helpers + the BFS engine) and :mod:`weld.impact_cli` (argparse, git, the
``wd impact`` entry point). Existing callers continue to import
``weld.impact`` -- in particular :mod:`weld.mcp_helpers` and the
``missing-graph-guidance`` test suite both rely on it.
"""

from __future__ import annotations

from weld.impact_cli import main
from weld.impact_core import IMPACT_VERSION, format_human, impact

__all__ = ["IMPACT_VERSION", "format_human", "impact", "main"]

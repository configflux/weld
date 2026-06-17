"""Diff helper for the viz API (bd h6z0.9).

Kept in a sibling module so ``weld/viz/api.py`` stays under the 400-line
cap (the same split pattern used by ``weld/viz/_search.py``). This
module is intentionally thin: it wraps ``weld.diff.load_and_diff``
exactly as documented in ``docs/`` and wraps the stable contract in the
shared ``viz_api_version`` envelope used by every other viz endpoint.
"""

from __future__ import annotations

from pathlib import Path

from weld.diff import load_and_diff
from weld.viz import VIZ_API_VERSION


def load_diff_payload(root: Path) -> dict:
    """Return ``compute_graph_diff`` output for the snapshots under *root*.

    Reuses ``weld.diff.load_and_diff`` so the in-UI "Changes" tab and
    ``wd diff --json`` always emit the same stable JSON contract.
    """
    result = load_and_diff(root)
    return {"viz_api_version": VIZ_API_VERSION, **result}

"""Doctor checks for the sqlite sidecar (ADR 0058).

Carved out of :mod:`weld.doctor` so the dispatcher stays under the
400-line CLAUDE.md cap. The check is intentionally narrow:

- absent sidecar: silent (the sidecar is opt-in by file presence; a
  missing file is normal for repos that have not been re-discovered
  since the feature shipped);
- present and fresh: emits an ``ok`` line so operators see the cache
  is doing its job;
- present and stale: emits a ``warn`` pointing at
  ``wd graph index --rebuild``.

The freshness check itself lives in :mod:`weld._sqlite_reader` so the
reader and the doctor agree on the exact contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def check_sqlite_sidecar(
    weld_dir: Path, result_cls: type[Any],
) -> list[Any]:
    """Return doctor results describing the sqlite sidecar's health."""
    graph_path = Path(weld_dir) / "graph.json"
    if not graph_path.is_file():
        return []
    from weld._sqlite_reader import SIDECAR_FILENAME, sidecar_freshness

    db_path = Path(weld_dir) / SIDECAR_FILENAME
    if not db_path.is_file():
        return []
    fresh, _meta = sidecar_freshness(graph_path)
    if fresh:
        return [
            result_cls("ok", "sqlite sidecar fresh (.weld/graph.db)", "Graph"),
        ]
    return [
        result_cls(
            "warn",
            "sqlite sidecar is stale (.weld/graph.db) -- run "
            "`wd graph index --rebuild`",
            "Graph",
        ),
    ]

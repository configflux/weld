"""ADR 0058 federation handle-cache and memory-probe tests.

Split from :mod:`weld.tests.weld_sqlite_federation_test` to keep both
files within the project line-count cap. Covers:

- ``_load_child`` does not materialise a JSON :class:`Graph` when the
  sidecar is fresh (the heavy ``_data`` dict is absent).
- Repeated ``_load_child`` calls reuse the same sqlite handle within a
  :class:`FederatedGraph` instance, avoiding sqlite3 reopen churn that
  ``path`` BFS over many children would otherwise cause.
- ``close`` and the context-manager protocol release cached sqlite
  connections deterministically.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _sqlite_reader as reader  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
)


class FederationMemoryProbeTest(unittest.TestCase):
    """Probe: sqlite-backed `_load_child` does not materialise child JSON.

    The hard memory benefit is hard to assert reliably in CI (it depends
    on Python's allocator and the OS RSS reporting). The behavioural
    proxy this probe checks is: ``_load_child`` returns the sqlite handle
    when the sidecar is fresh, and that handle's ``_data`` attribute is
    absent (JSON-parsed Graph would have it populated). This is the
    invariant that makes the memory peak drop possible.
    """

    def test_sqlite_handle_holds_no_in_memory_graph_data(self) -> None:
        payload = graph_payload(
            nodes={
                "service:alpha": {
                    "type": "service",
                    "label": "alpha",
                    "props": {"file": "alpha.py"},
                },
            },
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            handle = fg._load_child("child-a")
            self.assertIsInstance(handle, reader.SqliteBackedGraph)
            # SqliteBackedGraph has no in-memory _data dict (the heavy bit).
            self.assertFalse(hasattr(handle, "_data"))
            # It still answers queries via the connection.
            self.assertIsNotNone(handle.get_node("service:alpha"))


class FederationSqliteHandleCacheTest(unittest.TestCase):
    """Repeated ``_load_child`` calls reuse the same sqlite handle.

    Without the in-instance handle cache, each call would reopen
    ``graph.db`` (new sqlite3 connection + meta query + JSON sha
    recompute). The behavioural invariant is identity: the second
    call returns the *same* handle object, not just an equivalent
    one. This is the cheap-reuse property federation read paths
    (``_exact_context``, ``_adjacent``, BFS over ``path``) rely on.
    """

    def _payload(self) -> dict:
        return graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {},
            },
        })

    def test_repeated_load_reuses_handle_instance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", self._payload(), True)])
            fg = FederatedGraph(root)
            try:
                first = fg._load_child("child-a")
                second = fg._load_child("child-a")
                self.assertIsInstance(first, reader.SqliteBackedGraph)
                self.assertIs(first, second)
            finally:
                fg.close()

    def test_close_releases_cached_handles(self) -> None:
        """``close`` drains the sqlite cache and closes each handle."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", self._payload(), True)])
            fg = FederatedGraph(root)
            handle = fg._load_child("child-a")
            self.assertIsInstance(handle, reader.SqliteBackedGraph)
            self.assertEqual(len(fg._sqlite_cache), 1)

            fg.close()

            self.assertEqual(len(fg._sqlite_cache), 0)
            # The closed connection raises on use; verify the handle
            # was actually closed rather than merely evicted.
            with self.assertRaises(Exception):
                handle.get_node("service:alpha")

    def test_context_manager_closes_handles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", self._payload(), True)])
            with FederatedGraph(root) as fg:
                fg._load_child("child-a")
                self.assertEqual(len(fg._sqlite_cache), 1)
            self.assertEqual(len(fg._sqlite_cache), 0)


if __name__ == "__main__":
    unittest.main()

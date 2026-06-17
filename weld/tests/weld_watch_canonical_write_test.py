"""Watch-triggered ``graph.json`` writes must go through the canonical
serializer so repeated rewrites of the same discovered state are
byte-identical (ADR 0012 section 3).

The watch engine invokes ``weld.watch._default_discover_cb`` once per
debounced flush. That callback rediscovers the project and rewrites
``.weld/graph.json``. If it wrote via a raw ``json.dumps`` bypassing
``weld.serializer.dumps_graph`` the determinism contract would hold for
``wd discover`` but silently break for ``wd watch``, so every keystroke
could churn the on-disk bytes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


from weld import watch  # noqa: E402


class DefaultDiscoverCbCanonicalWriteTests(unittest.TestCase):
    # Reverse-ordered keys and edges -- the canonical serializer re-sorts
    # these; a raw ``json.dumps(..., indent=2)`` would preserve insertion
    # order, which is the regression this test guards against.
    _GRAPH = {
        "meta": {"schema_version": 1, "tool": "weld"},
        "nodes": {"z": {"type": "file"}, "a": {"type": "file"}},
        "edges": [
            {"from": "z", "to": "a", "type": "uses", "props": {"b": 2}},
            {"from": "a", "to": "z", "type": "uses", "props": {"a": 1}},
        ],
    }

    def test_two_watch_writes_are_byte_identical_and_canonical(self) -> None:
        from weld import diff as diff_mod
        from weld import discover as discover_mod
        from weld.serializer import dumps_graph

        def run(root: Path) -> bytes:
            with mock.patch.object(
                discover_mod, "discover", return_value=self._GRAPH
            ), mock.patch.object(
                diff_mod, "load_and_diff", return_value={}
            ), mock.patch.object(diff_mod, "format_human", return_value=""):
                watch._default_discover_cb(root)({"a.py"})
            return (root / ".weld" / "graph.json").read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run(root)
            # Clobber: prove the second write is full-content, not a no-op.
            (root / ".weld" / "graph.json").write_bytes(b"clobbered\n")
            second = run(root)

        self.assertEqual(first, second)
        self.assertEqual(second.decode("utf-8"), dumps_graph(self._GRAPH))

    def test_watch_write_routes_volatile_meta_to_sidecar(self) -> None:
        """ADR 0065: watch graph.json carries no volatile keys; sidecar does.

        When discovery stamps ``updated_at`` / ``git_sha`` into the
        in-memory graph (the real discover path always does), the watch
        write must strip them to ``graph-meta.json`` so the on-disk
        ``graph.json`` is byte-stable at a fixed commit instead of churning
        those two fields on every flush.
        """
        import json

        from weld import diff as diff_mod
        from weld import discover as discover_mod
        from weld._graph_meta_sidecar import (
            SIDECAR_NAME,
            VOLATILE_META_KEYS,
        )

        graph_with_volatile = {
            "meta": {
                "schema_version": 1,
                "tool": "weld",
                "updated_at": "2026-06-14T00:00:00+00:00",
                "git_sha": "feedface",
            },
            "nodes": {"a": {"type": "file"}},
            "edges": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                discover_mod, "discover", return_value=graph_with_volatile
            ), mock.patch.object(
                diff_mod, "load_and_diff", return_value={}
            ), mock.patch.object(diff_mod, "format_human", return_value=""):
                watch._default_discover_cb(root)({"a.py"})

            graph_path = root / ".weld" / "graph.json"
            sidecar_path = root / ".weld" / SIDECAR_NAME
            on_disk = json.loads(graph_path.read_text(encoding="utf-8"))
            for key in VOLATILE_META_KEYS:
                self.assertNotIn(
                    key, on_disk["meta"],
                    f"watch graph.json must not carry volatile key {key!r}",
                )
            self.assertTrue(
                sidecar_path.is_file(),
                "watch must write the volatile-meta sidecar",
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar.get("version"), 1)
            self.assertEqual(sidecar.get("git_sha"), "feedface")
            self.assertEqual(
                sidecar.get("updated_at"), "2026-06-14T00:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()

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

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

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


if __name__ == "__main__":
    unittest.main()

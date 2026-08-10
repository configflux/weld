"""Tests for the ADR 0094 graph write lock.

Covers the lock primitive (exclusion, release, timeout, env override)
and the end-to-end invariant it exists for: N concurrent ``wd add-node``
processes must all land their nodes -- the unlocked load -> mutate ->
save cycle used to let the last writer silently discard the others'
nodes (bd 1fgk: 34 nodes lost across 12 parallel enrichment agents).
"""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from weld._graph_write_lock import (
    GraphWriteLockTimeout,
    graph_write_lock,
)


def _empty_graph(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps({
            "meta": {"version": 1, "schema_version": 1},
            "nodes": {},
            "edges": [],
        }),
        encoding="utf-8",
    )


def _add_node_proc(root_str: str, index: int) -> None:
    from weld._graph_cli import main as cli_main

    with redirect_stdout(StringIO()):
        cli_main([
            "--root", root_str,
            "add-node",
            f"entity:Node{index}",
            "--type", "entity",
            "--label", f"Node {index}",
        ])


class GraphWriteLockPrimitiveTest(unittest.TestCase):
    def test_lock_excludes_second_acquirer_until_released(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with graph_write_lock(root):
                with self.assertRaises(GraphWriteLockTimeout):
                    with graph_write_lock(root, timeout_s=0.2):
                        pass
            # Released on exit: a fresh acquire succeeds immediately.
            with graph_write_lock(root, timeout_s=0.2):
                pass

    def test_timeout_error_names_lock_path_and_env_knob(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with graph_write_lock(root):
                with self.assertRaises(GraphWriteLockTimeout) as ctx:
                    with graph_write_lock(root, timeout_s=0.0):
                        pass
            message = str(ctx.exception)
            self.assertIn("graph.write.lock", message)
            self.assertIn("WELD_GRAPH_LOCK_TIMEOUT", message)


class ConcurrentAddNodeTest(unittest.TestCase):
    def test_parallel_add_node_processes_lose_no_nodes(self) -> None:
        try:
            mp = multiprocessing.get_context("fork")
        except ValueError:
            self.skipTest("fork start method unavailable on this platform")
        writers = 8
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _empty_graph(root)
            procs = [
                mp.Process(target=_add_node_proc, args=(str(root), i))
                for i in range(writers)
            ]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(timeout=120)
            for proc in procs:
                self.assertEqual(proc.exitcode, 0)
            data = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )
            present = {f"entity:Node{i}" for i in range(writers)} & set(
                data["nodes"],
            )
            self.assertEqual(
                len(present), writers,
                f"lost nodes: {sorted(set(f'entity:Node{i}' for i in range(writers)) - present)}",
            )


if __name__ == "__main__":
    unittest.main()

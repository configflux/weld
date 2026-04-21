"""Tests for `wd touch` and `Graph.save(touch_git_sha=True)` (ADR 0017).

`wd touch` is the explicit escape hatch that stamps `meta.git_sha` to
HEAD without mutating nodes/edges. `Graph.save(touch_git_sha=True)` is
the underlying primitive used by the CLI's mutating commands.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._git import get_git_sha  # noqa: E402
from weld._graph_cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.serializer import dumps_graph as _dumps_graph  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _git_init(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "test@test.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-m", "init", "--quiet"], root)


def _write_graph(root: Path, *, git_sha: str | None, nodes: dict, edges: list) -> None:
    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": "2026-04-20T12:00:00+00:00",
        "discovered_from": ["src/"],
    }
    if git_sha is not None:
        meta["git_sha"] = git_sha
    payload = {"meta": meta, "nodes": nodes, "edges": edges}
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


def _structural_bytes(graph_dict: dict) -> bytes:
    """Serialize only nodes/edges for byte-stability comparison."""
    structural = {
        "meta": {
            "version": graph_dict.get("meta", {}).get("version", SCHEMA_VERSION),
        },
        "nodes": graph_dict.get("nodes", {}),
        "edges": graph_dict.get("edges", []),
    }
    return _dumps_graph(structural).encode("utf-8")


class WdTouchTest(unittest.TestCase):
    """`wd touch` stamps git_sha to HEAD and preserves graph structure."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        self._head = get_git_sha(self.root)
        assert self._head is not None

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _invoke_cli(self, args: list[str]) -> tuple[str, int]:
        """Run cli_main(args) with cwd set to self.root, capture stdout.

        Returns (stdout, exit_code). SystemExit(0) is treated as success.
        """
        buf = io.StringIO()
        old_stdout = sys.stdout
        old_cwd = os.getcwd()
        rc = 0
        try:
            sys.stdout = buf
            os.chdir(self.root)
            cli_main(args)
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
        finally:
            sys.stdout = old_stdout
            os.chdir(old_cwd)
        return buf.getvalue(), rc

    def test_touch_stamps_git_sha_to_head(self) -> None:
        nodes = {
            "entity:A": {"type": "entity", "label": "A", "props": {"x": 1}},
        }
        edges = [
            {"from": "entity:A", "to": "entity:A", "type": "relates_to", "props": {}},
        ]
        # start with no git_sha recorded
        _write_graph(self.root, git_sha=None, nodes=nodes, edges=edges)

        out, rc = self._invoke_cli(["touch"])
        self.assertEqual(rc, 0, f"wd touch failed: rc={rc} out={out!r}")
        payload = json.loads(out)
        self.assertEqual(payload["git_sha"], self._head)
        self.assertIn("updated_at", payload)

        # graph.json now has git_sha == HEAD
        data = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["meta"]["git_sha"], self._head)

    def test_touch_preserves_nodes_and_edges_byte_stable(self) -> None:
        """Structural bytes (nodes + edges) must be identical before and
        after a touch."""
        nodes = {
            "entity:A": {"type": "entity", "label": "A", "props": {"x": 1}},
            "entity:B": {"type": "entity", "label": "B", "props": {}},
        }
        edges = [
            {"from": "entity:A", "to": "entity:B", "type": "relates_to",
             "props": {"source": "llm"}},
        ]
        _write_graph(self.root, git_sha=None, nodes=nodes, edges=edges)
        before = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        before_bytes = _structural_bytes(before)

        _, rc = self._invoke_cli(["touch"])
        self.assertEqual(rc, 0)

        after = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        after_bytes = _structural_bytes(after)

        self.assertEqual(
            before_bytes, after_bytes,
            "wd touch must not change nodes/edges (byte-for-byte).",
        )

    def test_touch_is_idempotent(self) -> None:
        """Running touch twice in a row against the same HEAD yields the
        same git_sha; the graph remains structurally stable."""
        nodes = {"entity:A": {"type": "entity", "label": "A", "props": {}}}
        _write_graph(self.root, git_sha=None, nodes=nodes, edges=[])

        _, rc1 = self._invoke_cli(["touch"])
        first = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        first_bytes = _structural_bytes(first)

        _, rc2 = self._invoke_cli(["touch"])
        second = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(first["meta"]["git_sha"], self._head)
        self.assertEqual(second["meta"]["git_sha"], self._head)
        self.assertEqual(first_bytes, _structural_bytes(second))


class GraphSaveTouchFlagTest(unittest.TestCase):
    """`Graph.save(touch_git_sha=...)` controls whether git_sha moves."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        self._head = get_git_sha(self.root)
        assert self._head is not None

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_save_does_not_stamp_git_sha(self) -> None:
        _write_graph(self.root, git_sha="old-sha", nodes={}, edges=[])
        g = Graph(self.root)
        g.load()
        g.save()   # default: touch_git_sha=False
        data = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["meta"].get("git_sha"), "old-sha",
            "default save() must preserve existing git_sha")

    def test_touch_save_stamps_git_sha_to_head(self) -> None:
        _write_graph(self.root, git_sha="old-sha", nodes={}, edges=[])
        g = Graph(self.root)
        g.load()
        g.save(touch_git_sha=True)
        data = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["meta"]["git_sha"], self._head)

    def test_touch_save_in_non_git_is_noop(self) -> None:
        """Outside a git repo, touch_git_sha=True silently skips the stamp."""
        non_git = Path(tempfile.mkdtemp())
        try:
            (non_git / ".weld").mkdir()
            _write_graph(non_git, git_sha="keep-me", nodes={}, edges=[])
            g = Graph(non_git)
            g.load()
            g.save(touch_git_sha=True)
            data = json.loads(
                (non_git / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            # No git -> git_sha preserved (no HEAD to copy from).
            self.assertEqual(data["meta"].get("git_sha"), "keep-me")
        finally:
            import shutil
            shutil.rmtree(non_git, ignore_errors=True)


class MutatingCliStampsGitShaTest(unittest.TestCase):
    """add-node / add-edge / rm-node / rm-edge / import advance git_sha."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _git_init(self.root)
        self._head = get_git_sha(self.root)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _invoke_cli(self, args: list[str]) -> int:
        buf = io.StringIO()
        old_stdout = sys.stdout
        old_cwd = os.getcwd()
        rc = 0
        try:
            sys.stdout = buf
            os.chdir(self.root)
            cli_main(args)
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
        finally:
            sys.stdout = old_stdout
            os.chdir(old_cwd)
        return rc

    def test_add_node_stamps_git_sha(self) -> None:
        _write_graph(self.root, git_sha="ancient", nodes={}, edges=[])
        rc = self._invoke_cli([
            "add-node", "entity:X", "--type", "entity",
            "--label", "X", "--props", "{}",
        ])
        self.assertEqual(rc, 0)
        data = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["meta"]["git_sha"], self._head)

    def test_add_edge_stamps_git_sha(self) -> None:
        _write_graph(
            self.root, git_sha="ancient",
            nodes={
                "entity:X": {"type": "entity", "label": "X", "props": {}},
                "entity:Y": {"type": "entity", "label": "Y", "props": {}},
            },
            edges=[],
        )
        rc = self._invoke_cli([
            "add-edge", "entity:X", "entity:Y", "--type", "relates_to",
            "--props", "{}",
        ])
        self.assertEqual(rc, 0)
        data = json.loads(
            (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["meta"]["git_sha"], self._head)


if __name__ == "__main__":
    unittest.main()

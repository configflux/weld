"""A non-dict top-level graph payload classifies as ``corrupt`` (bd 5038-9jz2).

Split from ``weld_workspace_state_test.py`` because that file is at the
400-line cap (the same reason ``weld_workspace_state_gate_test.py`` split
off earlier, bd cpzx) -- not a new concern, just nowhere left to add a line.

``_graph_status`` (``weld._workspace_inspect``) used to re-implement the
top-level dict-shape check as a hand-typed, hardcoded error string
independent of ``weld._graph_schema.validate_dict_payload`` -- the guard
``load_graph_file`` and ``load_graph_bytes`` both call. This test pins the
now-shared behavior end to end through ``build_workspace_state``: a
syntactically valid but non-dict ``graph.json`` (a bare JSON array) still
classifies as ``corrupt``, and the ledger's ``error`` field now carries the
validator's own message text rather than a copy that could drift from it.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import build_workspace_state


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    readme = repo_root / "README.md"
    readme.write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


class WorkspaceStateDictShapeTest(unittest.TestCase):
    def test_non_dict_payload_classifies_as_corrupt(self) -> None:
        """A bare JSON array top level is valid JSON but not a graph.

        Asserted loosely (``assertIn``, matching the sibling
        ``JSONDecodeError`` convention in ``weld_workspace_state_test.py``)
        since the exact wording belongs to
        ``weld._graph_schema.validate_dict_payload``, not to this test.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "apps" / "array-payload")
            weld_dir = child / ".weld"
            weld_dir.mkdir(parents=True, exist_ok=True)
            (weld_dir / "graph.json").write_text("[]\n", encoding="utf-8")

            config = _write_workspaces(
                root,
                [ChildEntry(name="apps-array-payload", path="apps/array-payload")],
            )
            state = build_workspace_state(root, config, now="2026-04-15T19:34:00Z")

            child_state = state.children["apps-array-payload"]
            self.assertEqual(child_state.status, "corrupt")
            self.assertRegex(child_state.graph_sha256 or "", r"^[0-9a-f]{64}$")
            self.assertIn(
                "graph payload must be a JSON object", child_state.error or ""
            )
            self.assertIn("got list", child_state.error or "")


if __name__ == "__main__":
    unittest.main()

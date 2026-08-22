"""``save_workspace_state`` refuses to write at a non-polyrepo root (bd cpzx).

The ledger is meaningful only where a workspace registry declares children.
Every caller already checks ``load_workspace_config`` first, but a caller-side
check is a convention: a transient ``workspaces.yaml``, or a caller added later
that forgets it, still produces a stray ``{children: {}, version: 1}`` file at
an ordinary single-repo root. These tests pin the check to the writer itself,
where no caller can route around it.

The gate resolves the registry through ``find_workspaces_yaml``, so it accepts
**both** documented locations -- ``.weld/workspaces.yaml`` and a top-level
``workspaces.yaml`` (``_WORKSPACES_CANDIDATES``). A gate hardcoded to the
``.weld/`` form would refuse legitimate roots that use the other one, which is
why the top-level case has a test of its own rather than riding along.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import (
    WORKSPACE_STATE_FILENAME,
    WorkspaceChildState,
    WorkspaceState,
    WorkspaceStateError,
    save_workspace_state,
)


def _state() -> WorkspaceState:
    return WorkspaceState(
        children={
            "services-api": WorkspaceChildState(
                status="present",
                head_sha="a" * 40,
                head_ref="refs/heads/main",
                is_dirty=False,
                graph_path="services/api/.weld/graph.json",
                graph_sha256="b" * 64,
                last_seen_utc="2026-08-15T12:00:00Z",
            )
        }
    )


def _write_registry(path: Path) -> None:
    config = WorkspaceConfig(
        children=[ChildEntry(name="services-api", path="services/api")],
        cross_repo_strategies=[],
    )
    dump_workspaces_yaml(config, path)


class SaveWorkspaceStateGateTest(unittest.TestCase):
    def test_refuses_root_with_no_registry(self) -> None:
        """No registry -> raise, and leave nothing behind."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(WorkspaceStateError) as caught:
                save_workspace_state(root, _state())

            # The message has to name the file the caller must create, or the
            # operator is left guessing why a polyrepo command refused.
            self.assertIn("workspaces.yaml", str(caught.exception))
            self.assertFalse((root / ".weld" / WORKSPACE_STATE_FILENAME).exists())

    def test_refusal_creates_no_weld_directory(self) -> None:
        """The refusal must precede the write, which would mkdir ``.weld/``."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(WorkspaceStateError):
                save_workspace_state(root, _state())

            self.assertFalse((root / ".weld").exists())

    def test_accepts_dot_weld_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_registry(root / ".weld" / "workspaces.yaml")

            save_workspace_state(root, _state())

            self.assertTrue((root / ".weld" / WORKSPACE_STATE_FILENAME).is_file())

    def test_accepts_top_level_registry(self) -> None:
        """The second ``_WORKSPACES_CANDIDATES`` location is equally valid."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_registry(root / "workspaces.yaml")

            save_workspace_state(root, _state())

            self.assertTrue((root / ".weld" / WORKSPACE_STATE_FILENAME).is_file())

    def test_refusal_leaves_a_pre_existing_ledger_intact(self) -> None:
        """A root that stops being a workspace keeps its last ledger verbatim.

        The gate refuses to *write*; it is not a cleanup pass. Truncating or
        replacing the existing file on the way out would turn a refusal into
        data loss.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / ".weld" / "workspaces.yaml"
            _write_registry(registry)
            save_workspace_state(root, _state())
            state_path = root / ".weld" / WORKSPACE_STATE_FILENAME
            before = state_path.read_bytes()

            registry.unlink()

            with self.assertRaises(WorkspaceStateError):
                save_workspace_state(root, _state())

            self.assertEqual(state_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

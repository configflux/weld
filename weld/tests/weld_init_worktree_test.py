"""Linked-worktree polyrepo init tests (bd-cacx).

Pin the contract that ``wd init`` inside a linked git worktree of a
bootstrapped polyrepo mirrors the main checkout's ``workspaces.yaml``.

The user's repro (against ``wd 0.11.4``):

1. Bootstrap the main checkout of a polyrepo.
2. ``cd`` into a linked worktree (``git worktree add ...``).
3. ``wd init`` in the worktree -- only ``.weld/discover.yaml`` is created;
   no ``.weld/workspaces.yaml`` -> federation downgrades to single-service.

Root cause: ``init_workspace`` calls ``scan_nested_repos`` which finds no
nested git children in the worktree (git does not clone nested-git
children into worktrees). Returns False, no yaml.

Fix: when local FS scan is empty AND ``git_main_checkout_path`` returns
a path AND main has ``workspaces.yaml``, mirror it. The federation
discover path already handles linked worktrees via ``resolve_child_root``
worktree fallback (ADR 0028), so the mirrored yaml just works.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.init import main as init_main  # noqa: E402
from weld.init_workspace import init_workspace  # noqa: E402


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _build_polyrepo(root: Path) -> None:
    """Build a synthetic polyrepo at ``root`` with two children."""
    _init_repo(root / "services" / "api")
    _init_repo(root / "services" / "auth")


def _silenced(callable_, *args, **kwargs):
    """Run ``callable_`` with stdout/stderr swallowed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return callable_(*args, **kwargs)


class InitWorkspaceWorktreeMirrorTest(unittest.TestCase):
    """``init_workspace`` in a linked worktree mirrors main's yaml."""

    def test_init_in_linked_worktree_mirrors_main_workspaces_yaml(self) -> None:
        """Worktree has no children locally; main yaml is mirrored verbatim."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main = tmp_path / "main"
            _init_repo(main)
            _build_polyrepo(main)

            main_yaml = main / ".weld" / "workspaces.yaml"
            wrote_main = init_workspace(main, main_yaml)
            self.assertTrue(wrote_main, "main checkout init must write yaml")
            main_text = main_yaml.read_text(encoding="utf-8")
            self.assertIn("services-api", main_text)
            self.assertIn("services-auth", main_text)

            wt = tmp_path / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            wt_yaml = wt / ".weld" / "workspaces.yaml"
            wrote_wt = init_workspace(wt, wt_yaml)
            self.assertTrue(
                wrote_wt,
                "linked-worktree init must mirror main's yaml even though "
                "the local FS scan finds no nested git children",
            )
            self.assertEqual(
                wt_yaml.read_text(encoding="utf-8"),
                main_text,
                "worktree yaml must be byte-identical to main's",
            )

    def test_init_in_main_checkout_unaffected_by_worktree_fallback(self) -> None:
        """Sanity: main-checkout path still uses the local FS scan."""
        with TemporaryDirectory() as tmp:
            main = Path(tmp) / "main"
            _init_repo(main)
            _build_polyrepo(main)

            main_yaml = main / ".weld" / "workspaces.yaml"
            wrote = init_workspace(main, main_yaml)
            self.assertTrue(wrote)

            text = main_yaml.read_text(encoding="utf-8")
            self.assertIn("services-api", text)
            self.assertIn("services-auth", text)

    def test_init_in_worktree_with_no_main_yaml_falls_through(self) -> None:
        """No yaml at main + no children locally -> still returns False.

        We must not fabricate a yaml. The only inheritance source is an
        already-bootstrapped main checkout; without one, the worktree
        legitimately has nothing to federate over.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main = tmp_path / "main"
            _init_repo(main)
            _build_polyrepo(main)
            # Deliberately do NOT call init_workspace at main.

            wt = tmp_path / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            wt_yaml = wt / ".weld" / "workspaces.yaml"
            wrote = init_workspace(wt, wt_yaml)
            self.assertFalse(
                wrote,
                "no main yaml to mirror, no local children -> nothing to "
                "scaffold",
            )
            self.assertFalse(
                wt_yaml.exists(),
                "yaml must NOT be created when there is nothing to inherit",
            )

    def test_init_in_worktree_does_not_overwrite_existing_local_yaml(self) -> None:
        """A worktree-local override is honoured by ``force=False``.

        Mirror semantics are a fallback, not an override. If the operator
        has placed a worktree-local yaml on purpose, our code must respect
        ``force=False`` and not clobber it.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main = tmp_path / "main"
            _init_repo(main)
            _build_polyrepo(main)
            init_workspace(main, main / ".weld" / "workspaces.yaml")

            wt = tmp_path / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            wt_yaml = wt / ".weld" / "workspaces.yaml"
            wt_yaml.parent.mkdir(parents=True, exist_ok=True)
            wt_yaml.write_text("# operator-authored\nversion: 1\n",
                               encoding="utf-8")

            wrote = init_workspace(wt, wt_yaml, force=False)
            self.assertFalse(wrote)
            self.assertEqual(
                wt_yaml.read_text(encoding="utf-8"),
                "# operator-authored\nversion: 1\n",
                "operator-authored yaml must be preserved when force=False",
            )


class WdInitInWorktreeEndToEndTest(unittest.TestCase):
    """End-to-end: ``wd init`` in a worktree produces federated state."""

    def test_wd_init_in_linked_worktree_produces_federated_state(self) -> None:
        """``init.main`` in worktree -> yaml + workspace-state present.

        Proves the full ``wd init`` orchestration -- discover.yaml, yaml
        mirror, _maybe_bootstrap_polyrepo -- produces a workspace-state
        ledger in the worktree by leaning on
        ``resolve_child_root``'s worktree fallback for the merged set.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main = tmp_path / "main"
            _init_repo(main)
            _build_polyrepo(main)

            # Bootstrap main: discover.yaml + workspaces.yaml + per-child
            # init + workspace-state.json all materialise.
            _silenced(init_main, [str(main), "--force"])
            self.assertTrue(
                (main / ".weld" / "workspaces.yaml").is_file(),
                "main yaml must exist after main-checkout init",
            )

            # Fork a linked worktree and wipe its weld dir explicitly.
            wt = tmp_path / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
            wt_weld = wt / ".weld"
            if wt_weld.exists():
                import shutil
                shutil.rmtree(wt_weld)

            _silenced(init_main, [str(wt), "--force"])

            self.assertTrue(
                (wt / ".weld" / "discover.yaml").is_file(),
                "wt discover.yaml must be written by wd init",
            )
            self.assertTrue(
                (wt / ".weld" / "workspaces.yaml").is_file(),
                "wt yaml must be mirrored from main when local scan empty",
            )
            self.assertTrue(
                (wt / ".weld" / "workspace-state.json").is_file(),
                "wt workspace-state must materialise via "
                "_maybe_bootstrap_polyrepo + worktree-aware inspect_child",
            )

            state_payload = json.loads(
                (wt / ".weld" / "workspace-state.json").read_text(
                    encoding="utf-8",
                ),
            )
            children = state_payload.get("children", {})
            present = [
                name for name, entry in children.items()
                if entry.get("status") == "present"
            ]
            self.assertEqual(
                sorted(present),
                ["services-api", "services-auth"],
                "every child must be reachable via worktree fallback "
                "(resolve_child_root, ADR 0028) and reported as present "
                f"in workspace-state.json: {state_payload}",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

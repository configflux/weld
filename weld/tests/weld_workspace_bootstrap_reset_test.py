"""Bootstrap-after-reset regression tests (bd-9slg).

External user report: after ``rm -rf .weld/`` at a polyrepo root, rerunning
``wd workspace bootstrap`` misroutes the root to single-service mode. Even
a manually-restored ``.weld/workspaces.yaml`` listing valid children is
ignored. Root cause: two disagreeing federation predicates.
- ``wd discover`` decides federation by config presence.
- ``wd workspace bootstrap`` decides by FS scan only and early-returns
  when the scan is empty -- the FS scan itself honours root .gitignore,
  ``DEFAULT_MAX_DEPTH=4``, and ``_BUILTIN_EXCLUDE_DIRS``.

The fix unifies both predicates: yaml is authoritative when present, FS
scan augments. These tests pin every branch of the merged behaviour.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._workspace_bootstrap import bootstrap_workspace  # noqa: E402
from weld.workspace_state import main as workspace_main  # noqa: E402


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
    """Create a real git repo with one commit so it looks real to scanners."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _wipe_weld_dir(root: Path) -> None:
    """Simulate ``rm -rf .weld/`` at a polyrepo root."""
    weld = root / ".weld"
    if weld.is_dir():
        import shutil

        shutil.rmtree(weld)


def _wipe_weld_dir_keep_yaml(root: Path) -> str:
    """Snapshot ``.weld/workspaces.yaml`` text, wipe ``.weld/``, restore yaml.

    Mirrors the user-reported recovery flow: the operator preserves the
    yaml that lists their children and re-runs bootstrap.
    """
    yaml_path = root / ".weld" / "workspaces.yaml"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    _wipe_weld_dir(root)
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return yaml_text


class BootstrapAfterResetTest(unittest.TestCase):
    """Recovery-after-wipe acceptance suite."""

    def test_post_reset_with_intact_workspaces_yaml_recovers(self) -> None:
        """HEADLINE BUG: full reset minus yaml -> rerun bootstrap rebuilds."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            first = bootstrap_workspace(root)
            self.assertEqual(
                sorted(first.children_present),
                ["services-api", "services-auth"],
            )

            _wipe_weld_dir_keep_yaml(root)

            second = bootstrap_workspace(root)

            self.assertEqual(
                sorted(second.children_discovered),
                ["services-api", "services-auth"],
                "post-reset bootstrap must re-discover children listed in yaml",
            )
            self.assertEqual(
                sorted(second.children_present),
                ["services-api", "services-auth"],
                "every child listed in restored yaml must reach status=present",
            )
            self.assertTrue(
                (root / ".weld" / "workspace-state.json").is_file(),
                "workspace-state.json must materialize on a recovery rerun",
            )

    def test_post_reset_full_wipe_rediscovers_via_fs_scan(self) -> None:
        """Full reset (no yaml left): rerun rediscovers via FS scan."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")

            bootstrap_workspace(root)
            _wipe_weld_dir(root)

            second = bootstrap_workspace(root)

            self.assertEqual(second.children_discovered, ["services-api"])
            self.assertEqual(second.children_present, ["services-api"])
            self.assertTrue((root / ".weld" / "workspaces.yaml").is_file())

    def test_yaml_listed_child_outside_max_depth_honored(self) -> None:
        """Children at depth>max_depth: FS scan misses, yaml lists, honoured."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # depth-5 child lives at a/b/c/d/leaf; default max_depth=4 misses it.
            _init_repo(root / "a" / "b" / "c" / "d" / "leaf")

            yaml_text = (
                "version: 1\n"
                "scan:\n"
                "  max_depth: 8\n"
                "  exclude_paths: []\n"
                "children:\n"
                "  - name: leaf\n"
                "    path: a/b/c/d/leaf\n"
                "cross_repo_strategies: []\n"
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                yaml_text, encoding="utf-8",
            )

            result = bootstrap_workspace(root)

            self.assertIn("leaf", result.children_discovered)
            self.assertIn("leaf", result.children_present)

    def test_yaml_listed_child_under_gitignored_dir_honored(self) -> None:
        """Root .gitignore masks children dir: yaml authoritative, child kept."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("/services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")

            yaml_text = (
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths: []\n"
                "children:\n"
                "  - name: services-api\n"
                "    path: services/api\n"
                "cross_repo_strategies: []\n"
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                yaml_text, encoding="utf-8",
            )

            result = bootstrap_workspace(root)

            self.assertIn("services-api", result.children_discovered)
            self.assertIn("services-api", result.children_present)
            # The masking is surfaced for operator visibility.
            self.assertTrue(
                hasattr(result, "excluded_by_gitignore"),
                "BootstrapResult must expose excluded_by_gitignore",
            )

    def test_yaml_listed_child_with_missing_path_warns_not_crashes(self) -> None:
        """Yaml lists nonexistent child path: warn in errors, don't crash."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")

            yaml_text = (
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths: []\n"
                "children:\n"
                "  - name: services-api\n"
                "    path: services/api\n"
                "  - name: services-ghost\n"
                "    path: services/ghost\n"
                "cross_repo_strategies: []\n"
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                yaml_text, encoding="utf-8",
            )

            # Must not raise.
            result = bootstrap_workspace(root)

            self.assertIn("services-api", result.children_present)
            self.assertTrue(
                hasattr(result, "yaml_listed_but_missing"),
                "BootstrapResult must expose yaml_listed_but_missing",
            )
            self.assertIn("services-ghost", result.yaml_listed_but_missing)
            # An entry mentioning the missing child path also lands in errors.
            self.assertTrue(
                any("services-ghost" in err or "services/ghost" in err
                    for err in result.errors),
                f"missing child must be flagged in errors; got: {result.errors}",
            )

    def test_corrupt_workspaces_yaml_falls_back_to_fs_scan(self) -> None:
        """Invalid yaml: log error, fall back to FS scan, no crash."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")

            (root / ".weld").mkdir(parents=True, exist_ok=True)
            # Truncated/garbled yaml: load_workspaces_yaml will reject this.
            (root / ".weld" / "workspaces.yaml").write_text(
                "version: 1\nscan:\n  max_depth: not-a-number\n",
                encoding="utf-8",
            )

            # Must not raise; FS fallback should still discover services/api.
            result = bootstrap_workspace(root)

            self.assertIn("services-api", result.children_discovered)
            self.assertIn("services-api", result.children_present)
            # The corruption is surfaced in errors so operators can act.
            self.assertTrue(
                any("workspaces.yaml" in err.lower()
                    or "yaml" in err.lower()
                    or "parse" in err.lower()
                    for err in result.errors),
                f"corrupt-yaml fallback must surface an error; got: "
                f"{result.errors}",
            )

    def test_workspace_status_works_after_reset_then_bootstrap(self) -> None:
        """``wd workspace status`` succeeds after reset + bootstrap."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")

            bootstrap_workspace(root)
            _wipe_weld_dir_keep_yaml(root)
            bootstrap_workspace(root)

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                exit_code = workspace_main(
                    ["status", "--root", str(root), "--json"],
                )
            self.assertEqual(
                exit_code, 0,
                "wd workspace status must exit 0 after reset+bootstrap "
                "recovery",
            )
            payload = stdout_buf.getvalue()
            self.assertIn("services-api", payload)

    def test_wd_init_polyrepo_root_initializes_children(self) -> None:
        """``wd init`` at a polyrepo root must run per-child init.

        Before the fix, ``wd init`` at a polyrepo root only wrote the root
        ``discover.yaml`` and ``workspaces.yaml`` -- children kept no
        per-child ``.weld/discover.yaml``. The fix loops over the
        discovered children and runs init inside each.
        """
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            init_main([str(root), "--force"])

            for rel in ("services/api", "services/auth"):
                self.assertTrue(
                    (root / rel / ".weld" / "discover.yaml").is_file(),
                    f"wd init must scaffold discover.yaml inside {rel}",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Tests for ``wd workspace bootstrap`` (ADR 0018).

The bootstrap command must, on a fresh polyrepo root:

* init the root (discover.yaml + workspaces.yaml),
* init every nested child that has ``.git/`` but no ``.weld/discover.yaml``,
* recurse-discover every child so each has a fresh ``.weld/graph.json``,
* rebuild the ledger + root meta-graph so every child lands at
  ``status=present`` and appears as a ``repo:<name>`` node in the root
  graph.

Re-running on an already-bootstrapped workspace must be a no-op modulo
``meta.updated_at`` and must never blow away existing child graphs.
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

from weld._workspace_bootstrap import bootstrap_workspace  # noqa: E402
from weld.workspace_state import (  # noqa: E402
    load_workspace_state_json,
    main as workspace_main,
)


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
    """Create a git repo with one commit so it looks real to the scanner."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _make_polyrepo(root: Path, child_rels: list[str]) -> None:
    """Initialize a git repo at each child path under *root*.

    The root itself does not need to be a git repo for bootstrap; bootstrap
    is designed for a polyrepo *container* directory.
    """
    for rel in child_rels:
        _init_repo(root / rel)


class BootstrapWorkspaceUnitTest(unittest.TestCase):
    """Direct calls to :func:`bootstrap_workspace`: small and deterministic."""

    def test_bootstrap_on_single_repo_root_is_safe_noop(self) -> None:
        """No nested children -> root init runs, bootstrap returns empty."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = bootstrap_workspace(root)
            self.assertEqual(result.children_discovered, [])
            self.assertEqual(result.children_present, [])
            self.assertTrue(result.root_init_ran)

    def test_bootstrap_initializes_missing_children(self) -> None:
        """Children with .git/ but no discover.yaml get a discover.yaml."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["services/api", "services/auth"])

            result = bootstrap_workspace(root)

            self.assertEqual(
                result.children_discovered,
                ["services-api", "services-auth"],
            )
            self.assertEqual(
                sorted(result.children_initialized),
                ["services-api", "services-auth"],
            )
            for child_path in ("services/api", "services/auth"):
                self.assertTrue(
                    (root / child_path / ".weld" / "discover.yaml").is_file(),
                    f"bootstrap must write discover.yaml inside {child_path}",
                )

    def test_bootstrap_leaves_pre_existing_child_configs_untouched(self) -> None:
        """Children that already have discover.yaml are skipped, contents intact."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["services/api"])
            pre_existing = root / "services" / "api" / ".weld" / "discover.yaml"
            pre_existing.parent.mkdir(parents=True, exist_ok=True)
            marker = "# hand-written, must not be overwritten\nsources: []\n"
            pre_existing.write_text(marker, encoding="utf-8")

            result = bootstrap_workspace(root)

            self.assertEqual(result.children_initialized, [])
            self.assertEqual(
                pre_existing.read_text(encoding="utf-8"),
                marker,
                "bootstrap must never overwrite an existing child discover.yaml",
            )

    def test_bootstrap_end_to_end_polyrepo_matches_acceptance(self) -> None:
        """The three acceptance conditions from tracked issue on a fresh polyrepo."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["services/api", "libs/shared"])

            result = bootstrap_workspace(root)

            # (a) every child present after bootstrap
            self.assertEqual(
                result.children_present,
                ["libs-shared", "services-api"],
                "every nested child must reach status=present after one invocation",
            )

            # bd-...-9slg: ``wd workspace status`` must succeed post-bootstrap.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(0, workspace_main(
                    ["status", "--root", str(root), "--json"]))
            self.assertIn("services-api", buf.getvalue())

            # (b) root graph.json contains repo:<name> per child
            root_graph_text = (root / ".weld" / "graph.json").read_text(
                encoding="utf-8",
            )
            root_graph = json.loads(root_graph_text)
            self.assertIn("repo:services-api", root_graph["nodes"])
            self.assertIn("repo:libs-shared", root_graph["nodes"])

            # (b, continued) ledger agrees
            state = load_workspace_state_json(root)
            child_ledger = state["children"]
            self.assertEqual(child_ledger["services-api"]["status"], "present")
            self.assertEqual(child_ledger["libs-shared"]["status"], "present")

            # (c) no further manual commands: child graphs on disk too
            for child_rel, child_name in (
                ("services/api", "services-api"),
                ("libs/shared", "libs-shared"),
            ):
                self.assertTrue(
                    (root / child_rel / ".weld" / "graph.json").is_file(),
                    f"child {child_name} must have .weld/graph.json after bootstrap",
                )

    def test_bootstrap_is_idempotent(self) -> None:
        """Re-running on a fully-bootstrapped polyrepo is a no-op modulo timestamps."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["services/api", "services/auth"])
            bootstrap_workspace(root)

            # Snapshot child graph bytes to prove re-run does not blow them away.
            child_graph = root / "services" / "api" / ".weld" / "graph.json"
            first_run_bytes = child_graph.read_bytes()

            # Second run: should not re-init any child.
            result = bootstrap_workspace(root)

            self.assertFalse(
                result.root_init_ran,
                "re-run must not touch already-present root discover.yaml",
            )
            self.assertEqual(
                result.children_initialized,
                [],
                "re-run must not re-init already-initialized children",
            )
            # Child graph is rewritten (recurse is unconditional) but content
            # is equivalent -- assert it is at minimum still a valid graph file.
            second_run_bytes = child_graph.read_bytes()
            self.assertTrue(
                second_run_bytes.strip(),
                "child graph must still exist and be non-empty after re-run",
            )
            # Child must still be present.
            state = load_workspace_state_json(root)
            self.assertEqual(
                state["children"]["services-api"]["status"], "present",
            )
            # Sanity: bytes are either identical or differ only in timestamps.
            if first_run_bytes != second_run_bytes:
                first_data = json.loads(first_run_bytes)
                second_data = json.loads(second_run_bytes)
                first_data.get("meta", {}).pop("updated_at", None)
                second_data.get("meta", {}).pop("updated_at", None)
                self.assertEqual(
                    first_data,
                    second_data,
                    "re-run diverged in content beyond meta.updated_at",
                )

    def test_bootstrap_on_partially_initialized_workspace(self) -> None:
        """Mixed state: some children pre-configured, some bare."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["a", "b"])
            # Pre-init child 'a' by hand so only 'b' needs per-child init.
            (root / "a" / ".weld").mkdir(parents=True, exist_ok=True)
            (root / "a" / ".weld" / "discover.yaml").write_text(
                "sources: []\n", encoding="utf-8",
            )

            result = bootstrap_workspace(root)

            self.assertEqual(result.children_initialized, ["b"])
            self.assertEqual(sorted(result.children_present), ["a", "b"])

    def test_bootstrap_picks_up_new_child_added_after_first_run(self) -> None:
        """tracked issue: rerun after adding a nested repo must reach status=present.

        Before the fix, the second bootstrap wrote the new child's
        discover.yaml but step 4 iterated the stale workspaces.yaml
        loaded from disk and skipped the new child, leaving it
        ``uninitialized`` until a second rerun.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["services/api"])
            first = bootstrap_workspace(root)
            self.assertEqual(first.children_present, ["services-api"])

            # User adds a second nested repo AFTER the first bootstrap.
            _init_repo(root / "services" / "auth")
            second = bootstrap_workspace(root)

            self.assertEqual(
                second.children_discovered,
                ["services-api", "services-auth"],
            )
            self.assertEqual(second.children_initialized, ["services-auth"])
            self.assertEqual(
                sorted(second.children_present),
                ["services-api", "services-auth"],
                "new child must reach status=present on a single rerun",
            )
            state = load_workspace_state_json(root)
            self.assertEqual(
                state["children"]["services-auth"]["status"], "present",
            )
            # Refreshed workspaces.yaml lists both children.
            yaml_text = (
                root / ".weld" / "workspaces.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("services-api", yaml_text)
            self.assertIn("services-auth", yaml_text)

    def test_bootstrap_records_recurse_failures_in_errors(self) -> None:
        """Step 4 recurse failures must be mirrored into BootstrapResult.errors.

        Regression test for the docstring contract landed in tracked issue: every
        child that step 4 attempted to visit must either appear in
        ``children_recursed`` (success) or leave a breadcrumb in
        ``errors`` (per-child discovery raised). A silent stderr-only log
        is not enough -- programmatic callers inspect ``result.errors``.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["svc/api", "svc/auth"])

            # Patch _discover_single_repo to raise for the "svc-auth" child
            # only. The "svc-api" child must still succeed.
            import weld.discover as _discover_mod
            original = _discover_mod._discover_single_repo

            def _maybe_raise(child_root: Path, *, incremental=None, safe=False):  # type: ignore[no-untyped-def]
                if child_root.name == "auth":
                    raise RuntimeError("simulated discover failure")
                return original(child_root, incremental=incremental, safe=safe)

            _discover_mod._discover_single_repo = _maybe_raise
            try:
                result = bootstrap_workspace(root)
            finally:
                _discover_mod._discover_single_repo = original

            # The failing child must NOT be in children_recursed.
            self.assertNotIn("svc-auth", result.children_recursed)
            # The succeeding child must be in children_recursed.
            self.assertIn("svc-api", result.children_recursed)
            # The failure must be captured in result.errors with enough
            # context to identify the child and the exception type/message.
            self.assertTrue(
                any(
                    "svc-auth" in err and "simulated discover failure" in err
                    for err in result.errors
                ),
                f"expected recurse failure for svc-auth in errors, got: "
                f"{result.errors}",
            )


class BootstrapCliIntegrationTest(unittest.TestCase):
    """End-to-end via the ``wd workspace bootstrap`` CLI entrypoint."""

    def test_cli_bootstrap_exits_zero_and_populates_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["svc/api", "svc/auth"])

            exit_code = workspace_main(
                ["bootstrap", "--root", str(root), "--json"],
            )

            self.assertEqual(exit_code, 0, "bootstrap must exit 0 on success")

            # All children present in ledger.
            state = load_workspace_state_json(root)
            for name in ("svc-api", "svc-auth"):
                self.assertEqual(
                    state["children"][name]["status"],
                    "present",
                    f"{name} must be present in ledger after CLI bootstrap",
                )

            # Root graph.json contains repo nodes.
            root_graph = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )
            self.assertIn("repo:svc-api", root_graph["nodes"])
            self.assertIn("repo:svc-auth", root_graph["nodes"])

    def test_cli_bootstrap_help_listed_under_workspace(self) -> None:
        """``wd workspace --help`` mentions the bootstrap subcommand."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # No args -> prints help.
            workspace_main([])
        self.assertIn("bootstrap", buf.getvalue())

    def test_cli_bootstrap_json_stdout_is_parseable(self) -> None:
        """tracked issue: ``bootstrap --json`` stdout must be JSON, narration on stderr.

        Regression guard: ``init()`` used to emit ``Scanning for files...``
        and ``Wrote <path>`` to stdout for every root and per-child call,
        so ``wd workspace bootstrap --json | jq`` saw invalid JSON and
        failed. Narration is now on stderr; stdout carries only the JSON
        payload.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_polyrepo(root, ["svc/api", "svc/auth"])

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout_buf),
                contextlib.redirect_stderr(stderr_buf),
            ):
                exit_code = workspace_main(
                    ["bootstrap", "--root", str(root), "--json"],
                )

            self.assertEqual(exit_code, 0)
            # stdout must be pure JSON -- no narration bleed.
            stdout_text = stdout_buf.getvalue()
            try:
                payload = json.loads(stdout_text)
            except json.JSONDecodeError as exc:  # pragma: no cover
                self.fail(
                    f"bootstrap --json stdout is not valid JSON: {exc}\n"
                    f"stdout was:\n{stdout_text!r}",
                )
            self.assertIn("children_present", payload)
            self.assertNotIn("Scanning for files", stdout_text)
            self.assertNotIn("Wrote ", stdout_text)
            # Narration stays visible to humans on stderr.
            stderr_text = stderr_buf.getvalue()
            self.assertIn("Scanning for files", stderr_text)


if __name__ == "__main__":  # pragma: no cover -- manual invocation only
    unittest.main()

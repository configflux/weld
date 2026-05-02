"""Acceptance tests for ``wd discover --output PATH`` (ADR 0019).

The ``--output`` flag is the documented, atomic write path for discovery.
It must:

* write canonical graph JSON atomically to the given path (temp file +
  :func:`os.replace` via :func:`atomic_write_text`);
* create missing parent directories on the way there;
* suppress stdout when ``--output`` is used (human status still goes to
  stderr);
* leave existing stdout behaviour untouched when ``--output`` is absent;
* work for both single-repo roots and federated roots;
* keep ``--write-root-graph`` working as before for backward compatibility.

These are black-box tests against the CLI entry point
(:func:`weld.discover.main`) so they exercise the argument parsing and
the write branch together.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld.discover import main as discover_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.contract import SCHEMA_VERSION


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
    readme = repo_root / "README.md"
    readme.write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_minimal_discover_yaml(root: Path) -> None:
    """Write a minimal .weld/discover.yaml so single-repo discovery runs."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources: []\n", encoding="utf-8"
    )


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    """Invoke :func:`discover_main` capturing stdout and stderr."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = discover_main(argv)
    return rc, out.getvalue(), err.getvalue()


class SingleRepoOutputFlagTests(unittest.TestCase):
    """--output on a single-repo root writes the graph atomically."""

    def test_output_writes_graph_and_suppresses_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / ".weld" / "graph.json"
            if target.exists():
                target.unlink()

            rc, stdout, _stderr = _run_main([str(root), "--output", str(target)])

            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "", "stdout must be empty when --output is set")
            self.assertTrue(target.is_file(), "--output must write the graph to disk")
            # Must be canonical JSON the serializer would have emitted.
            parsed = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("meta", parsed)
            self.assertIn("nodes", parsed)
            self.assertIn("edges", parsed)

    def test_output_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / "deep" / "nested" / "dir" / "graph.json"
            self.assertFalse(target.parent.exists())

            rc, stdout, _stderr = _run_main([str(root), "--output", str(target)])

            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertTrue(target.is_file())
            self.assertTrue(target.parent.is_dir())

    def test_stdout_behaviour_preserved_when_output_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)

            rc, stdout, _stderr = _run_main([str(root)])

            self.assertEqual(rc, 0)
            self.assertGreater(len(stdout), 0,
                               "stdout must still contain graph JSON when --output absent")
            parsed = json.loads(stdout)
            self.assertIn("meta", parsed)
            self.assertIn("nodes", parsed)

    def test_output_write_is_atomic(self) -> None:
        """Pre-existing file stays intact on failure; fresh bytes on success."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / "graph.json"
            # Pre-populate with dummy bytes.
            target.write_text("SENTINEL", encoding="utf-8")

            rc, stdout, _stderr = _run_main([str(root), "--output", str(target)])

            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            # After success the file must contain canonical JSON, not sentinel.
            text = target.read_text(encoding="utf-8")
            self.assertNotEqual(text, "SENTINEL")
            parsed = json.loads(text)
            self.assertIn("meta", parsed)

    def test_output_does_not_leave_tempfiles_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / ".weld" / "graph.json"

            rc, _stdout, _stderr = _run_main([str(root), "--output", str(target)])

            self.assertEqual(rc, 0)
            # No stray tempfiles matching the atomic writer's prefix.
            leftovers = [
                p.name for p in target.parent.iterdir()
                if p.name.startswith("graph.json.tmp.")
            ]
            self.assertEqual(leftovers, [],
                             f"atomic writer left temp files behind: {leftovers}")


class FederatedRootOutputFlagTests(unittest.TestCase):
    """--output on a federated root writes the root meta-graph atomically."""

    def _write_child(self, root: Path) -> None:
        weld_dir = root / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
            "nodes": {},
            "edges": [],
        }
        (weld_dir / "graph.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_federated_output_writes_graph_and_suppresses_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "root")
            child = _init_repo(root / "child")
            self._write_child(child)
            config = WorkspaceConfig(
                children=[ChildEntry(name="child", path="child", remote=None)],
                cross_repo_strategies=[],
            )
            dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")

            target = root / ".weld" / "federated-graph.json"

            rc, stdout, _stderr = _run_main([str(root), "--output", str(target)])

            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "",
                             "stdout must be empty on federated --output write")
            self.assertTrue(target.is_file())
            parsed = json.loads(target.read_text(encoding="utf-8"))
            # Federated meta-graph lives at schema_version 2 per ADR 0011.
            self.assertEqual(parsed["meta"].get("schema_version"), 2)

    def test_write_root_graph_still_works_without_output(self) -> None:
        """Backward compat: --write-root-graph alone still writes .weld/graph.json."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "root")
            child = _init_repo(root / "child")
            self._write_child(child)
            config = WorkspaceConfig(
                children=[ChildEntry(name="child", path="child", remote=None)],
                cross_repo_strategies=[],
            )
            dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")

            rc, stdout, _stderr = _run_main([str(root), "--write-root-graph"])

            self.assertEqual(rc, 0)
            # Legacy behaviour: stdout still carries the graph JSON.
            self.assertGreater(len(stdout), 0)
            # File is written to .weld/graph.json (default target).
            default_target = root / ".weld" / "graph.json"
            self.assertTrue(default_target.is_file())


class DiscoverSuccessSummaryTests(unittest.TestCase):
    """``wd discover`` emits a one-line stderr summary on success.

    Mirrors the UX of ``wd build-index`` (``Indexed N files -> path``).
    The summary goes to stderr so JSON consumers piping stdout are
    unaffected. ``--quiet`` suppresses the summary for scripted callers.
    """

    # Match strings like:
    #   wrote 0 nodes / 0 edges -> /tmp/.../graph.json (0.00s)
    #   wrote 12 nodes / 34 edges (0.12s)
    _SUMMARY_RE = re.compile(
        r"^wrote \d+ nodes / \d+ edges( -> .+)? \(\d+\.\d{2}s\)$",
        re.MULTILINE,
    )

    def test_default_stdout_mode_prints_summary_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)

            rc, stdout, stderr = _run_main([str(root)])

            self.assertEqual(rc, 0)
            self.assertRegex(stderr, self._SUMMARY_RE)
            # Stdout still carries the graph JSON unchanged.
            parsed = json.loads(stdout)
            self.assertIn("nodes", parsed)

    def test_output_mode_summary_includes_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / ".weld" / "graph.json"

            rc, stdout, stderr = _run_main(
                [str(root), "--output", str(target)]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertRegex(stderr, self._SUMMARY_RE)
            self.assertIn(str(target), stderr,
                          "summary must include the --output path")

    def test_quiet_flag_suppresses_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)
            target = root / ".weld" / "graph.json"

            rc, _stdout, stderr = _run_main(
                [str(root), "--output", str(target), "--quiet"]
            )

            self.assertEqual(rc, 0)
            self.assertNotRegex(stderr, self._SUMMARY_RE,
                                "--quiet must suppress the success summary")

    def test_quiet_flag_also_suppresses_stdout_mode_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "repo")
            _write_minimal_discover_yaml(root)

            rc, stdout, stderr = _run_main([str(root), "--quiet"])

            self.assertEqual(rc, 0)
            # Stdout still has graph JSON.
            parsed = json.loads(stdout)
            self.assertIn("nodes", parsed)
            # No summary on stderr.
            self.assertNotRegex(stderr, self._SUMMARY_RE)


if __name__ == "__main__":
    unittest.main()

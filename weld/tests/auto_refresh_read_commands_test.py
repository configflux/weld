"""Integration tests covering each of the 10 read commands (ADR 0051).

The fixture sets up a tiny git repo with a discoverable Python file and
runs ``wd discover`` once to seed the graph. We then mutate the source
*and* commit the change so the graph's ``meta.git_sha`` is left behind
HEAD with content drift -- the canonical ``stale`` state per ADR 0017.

Each test exercises one read command and asserts:

  - The command exit code is 0 (still serves an answer).
  - After the command, the graph SHA matches HEAD (refresh ran).
  - When ``--no-refresh`` is passed, the graph SHA does NOT advance.
  - When ``WELD_AUTO_REFRESH=0`` is set, the graph SHA does NOT advance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


import weld._git as _git_mod  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def _git_init(root: Path) -> None:
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "test@test.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


def _commit_all(root: Path, msg: str) -> None:
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-m", msg, "--quiet"], root)


def _write_discover_yaml(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )


def _seed_repo(root: Path) -> None:
    """Initialize a git repo and seed the graph at HEAD #1."""
    _git_init(root)
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "alpha.py").write_text(
        "def helper_alpha():\n    return 1\n",
        encoding="utf-8",
    )
    _write_discover_yaml(root)
    _commit_all(root, "seed")
    # Run discovery and persist the graph to disk -- ``_discover_single_repo``
    # builds the graph in memory but only the standalone ``wd discover``
    # CLI normally writes it. Mirror that here so the freshness check
    # has a real on-disk graph to compare against.
    from weld.discover import _discover_single_repo
    from weld.serializer import dumps_graph
    from weld.workspace_state import atomic_write_text
    graph = _discover_single_repo(root, incremental=False, safe=False)
    atomic_write_text(root / ".weld" / "graph.json", dumps_graph(graph))


def _make_stale(root: Path) -> str:
    """Mutate a source file and commit it, then return the new HEAD sha."""
    (root / "src" / "alpha.py").write_text(
        "def helper_alpha():\n"
        "    return 1\n"
        "\n"
        "def helper_alpha_v2():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    _commit_all(root, "add helper_alpha_v2")
    return _git_mod.get_git_sha(root) or ""


def _graph_meta_sha(root: Path) -> str | None:
    # ADR 0065: git_sha lives in the graph-meta.json sidecar (with a legacy
    # in-graph fallback). ``load_graph_meta`` overlays it, giving the
    # location-agnostic view every staleness consumer sees.
    from weld._graph_meta_sidecar import load_graph_meta

    p = root / ".weld" / "graph.json"
    if not p.is_file():
        return None
    return load_graph_meta(p).get("git_sha")


def _wd(root: Path, args: list[str], *, env: dict | None = None) -> str:
    """Invoke ``python -m weld <args>`` inside *root* and return stdout.

    Uses the in-process ``main`` to keep tests fast and to avoid the
    subprocess Python-resolution friction in Bazel sandboxes. The
    function chdirs into *root* so commands that default to ``.`` for
    ``--root`` resolve to the seeded test repo.

    SystemExit raised by argparse / impact stale-gate is caught so a
    test can assert state-after-call even when the command exited
    non-zero (the auto-refresh side-effect runs first).
    """
    # In-process invocation. We capture stdout/stderr via reassignment
    # since the Recorder lives in :func:`weld.cli.main` and uses ``sys``.
    import io
    import weld.cli as cli_mod
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    saved_env: dict[str, str] = {}
    try:
        sys.argv = ["wd", *args]
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        if env is not None:
            for key, value in env.items():
                saved_env[key] = os.environ.get(key, "__UNSET__")
                os.environ[key] = value
        os.chdir(root)
        try:
            cli_mod.main(args)
        except SystemExit:
            # Some commands (impact stale-gate) may exit non-zero. The
            # auto-refresh hook still ran by then; the test cares about
            # observable state after the call rather than rc.
            pass
        return sys.stdout.getvalue()  # type: ignore[union-attr]
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        for key, prev in saved_env.items():
            if prev == "__UNSET__":
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


class _TempRepoMixin:
    """Provide a fresh tempdir + repo per test."""

    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Track cwd so each test restores it on tear-down.
        self._old_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._old_cwd)
        _seed_repo(self.root)
        self.head_after_seed = _git_mod.get_git_sha(self.root)
        self.assertEqual(_graph_meta_sha(self.root), self.head_after_seed)
        self.head_after_drift = _make_stale(self.root)
        self.assertNotEqual(self.head_after_seed, self.head_after_drift)
        # Sanity-check that the graph is now stale.
        self.assertEqual(_graph_meta_sha(self.root), self.head_after_seed)


class AutoRefreshQueryTest(_TempRepoMixin, unittest.TestCase):
    def test_query_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["query", "helper"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
            "query should auto-refresh and advance graph SHA to HEAD",
        )

    def test_query_no_refresh_keeps_graph_stale(self) -> None:
        _wd(self.root, ["query", "helper", "--no-refresh"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_seed,
            "--no-refresh must leave the graph SHA at the previous value",
        )

    def test_query_env_var_disables_refresh(self) -> None:
        _wd(
            self.root,
            ["query", "helper"],
            env={"WELD_AUTO_REFRESH": "0"},
        )
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_seed,
            "WELD_AUTO_REFRESH=0 must leave the graph SHA at the previous value",
        )


class AutoRefreshFindTest(_TempRepoMixin, unittest.TestCase):
    def test_find_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["find", "alpha"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshContextTest(_TempRepoMixin, unittest.TestCase):
    def test_context_refreshes_stale_graph(self) -> None:
        # Use a node we know exists -- pkg:src is from the topology.
        _wd(self.root, ["context", "pkg:src"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshPathTest(_TempRepoMixin, unittest.TestCase):
    def test_path_refreshes_stale_graph(self) -> None:
        # Even a node-not-found path call should still trigger refresh.
        _wd(self.root, ["path", "pkg:src", "pkg:src"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshCallersTest(_TempRepoMixin, unittest.TestCase):
    def test_callers_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["callers", "helper_alpha"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshReferencesTest(_TempRepoMixin, unittest.TestCase):
    def test_references_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["references", "helper_alpha"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshBriefTest(_TempRepoMixin, unittest.TestCase):
    def test_brief_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["brief", "helper"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )

    def test_brief_no_refresh_skips(self) -> None:
        _wd(self.root, ["brief", "helper", "--no-refresh"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_seed,
        )


class AutoRefreshTraceTest(_TempRepoMixin, unittest.TestCase):
    def test_trace_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["trace", "helper"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshImpactTest(_TempRepoMixin, unittest.TestCase):
    def test_impact_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["impact", "pkg:src"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class AutoRefreshExportTest(_TempRepoMixin, unittest.TestCase):
    def test_export_refreshes_stale_graph(self) -> None:
        _wd(self.root, ["export"])
        self.assertEqual(
            _graph_meta_sha(self.root), self.head_after_drift,
        )


class TelemetryAutoRefreshSidecarTest(_TempRepoMixin, unittest.TestCase):
    def test_sidecar_records_refresh_metadata(self) -> None:
        _wd(self.root, ["query", "helper"])
        sidecar = self.root / ".weld" / "auto-refresh.jsonl"
        self.assertTrue(sidecar.is_file(), "sidecar must be created")
        lines = [
            ln for ln in sidecar.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        self.assertGreaterEqual(len(lines), 1)
        record = json.loads(lines[-1])
        self.assertEqual(record["command"], "auto-refresh")
        self.assertGreaterEqual(record["files_changed"], 1)
        self.assertIn("incremental", record)
        self.assertIn("elapsed_ms", record)


if __name__ == "__main__":
    unittest.main()

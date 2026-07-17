"""``--json`` stdout purity under auto-refresh notices (bd gcrf).

Regression guard for the reported defect: a ``[weld] ...`` operational notice
must never precede or interleave with a ``--json`` payload on stdout. We drive
a real read command through the in-process CLI while auto-refresh fires and a
``no files changed`` notice is emitted, then assert stdout is a clean
``json.loads`` with zero ``[weld]`` lines and that the notice landed on stderr.
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


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30, check=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def _git_init(root: Path) -> None:
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "t@t.com"], root)
    _run(["git", "config", "user.name", "T"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


def _write_discover_yaml(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
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


def _seed(root: Path) -> None:
    _git_init(root)
    (root / "src").mkdir()
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _write_discover_yaml(root)
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-m", "seed", "--quiet"], root)
    from weld._graph_meta_sidecar import write_graph_with_meta
    from weld.discover import _discover_single_repo

    graph = _discover_single_repo(root, incremental=False, safe=False)
    write_graph_with_meta(root / ".weld" / "graph.json", graph)


def _wd(root: Path, args: list[str]) -> tuple[str, str]:
    """Run ``wd <args>`` in-process; return ``(stdout, stderr)``."""
    import weld.cli as cli_mod

    old_argv, old_out, old_err, old_cwd = (
        sys.argv, sys.stdout, sys.stderr, os.getcwd(),
    )
    sys.argv = ["wd", *args]
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        os.chdir(root)
        try:
            cli_mod.main(args)
        except SystemExit:
            pass
        return sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
        os.chdir(old_cwd)


class JsonStdoutPurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _seed(self.root)

    def test_no_change_notice_does_not_pollute_json_stdout(self) -> None:
        # Dirty a tracked source file so staleness fires on every read.
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 2\n", encoding="utf-8"
        )
        # Read #1: auto-refresh records the dirty file's hash.
        self._clear_caches()
        _wd(self.root, ["query", "a", "--json"])
        # Read #2: still dirty, hash unchanged -> the "no files changed"
        # notice fires during auto-refresh, on the JSON read path.
        self._clear_caches()
        out, err = _wd(self.root, ["query", "a", "--json"])

        # stdout must be a clean JSON parse with zero operational lines.
        parsed = json.loads(out)
        self.assertIsInstance(parsed, dict)
        self.assertNotIn("[weld]", out)
        # The notice fired -- proving the read path emitted one -- but on stderr.
        self.assertIn("[weld] notice: no files changed", err)

    @staticmethod
    def _clear_caches() -> None:
        from weld._graph_digest import clear_digest_memo
        from weld._mcp_read import clear_graph_cache
        from weld._refresh_cache import clear_refresh_cache

        clear_digest_memo()
        clear_graph_cache()
        # bd o18k: the dirty-tree refresh cache would otherwise short-circuit
        # read #2, eliding the very "no files changed" notice this test drives
        # onto the JSON read path. Clearing it keeps the notice mechanism live;
        # the stdout-purity contract it guards is unchanged by that cache.
        clear_refresh_cache()


if __name__ == "__main__":
    unittest.main()

"""``wd stale --check`` turns the freshness report into a gate (ADR 0110).

A repository that commits its graph (Mode B) has no way to notice that the
committed graph is older than the source beside it -- nothing in git
compares the two, and the graph stays wrong until someone happens to
re-discover. ``--check`` is the missing gate: the same report, plus a
non-zero exit when the verdict is stale, so a CI job is one line.

Why not ``wd doctor``: it grades staleness a ``warn`` and exits 0, so it
reports the condition without failing on it. Making *it* fail would change
the meaning of every other doctor row.

The exit code is keyed on the payload's own top-level ``stale``, which is
already the aggregate verdict on both surfaces -- it aliases
``source_stale`` (which ADR 0101 folds ``coverage_stale`` into) at a single
repo, and ORs in child drift at a federated root. These tests pin that the
gate reads that field and does not re-derive its own opinion, and they
exercise both routes into it, because the two catch different mistakes a
Mode B repo actually makes: editing a file the graph read, and adding one
it never did.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._discover_state_check import published_graph_token
from weld._graph_cli import main as graph_cli_main
from weld.discovery_state import DiscoveryState, compute_hash, save_state

#: Identity supplied through the environment, and global/system config
#: scrubbed, so the fixture does not depend on whatever the machine
#: running it has configured (signing in particular would fail the commit).
_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "PATH": "/usr/bin:/bin",
}

_GRAPH = (
    '{\n"edges": [],\n"meta": {"discovered_from": ["a.py"]},\n'
    '"nodes": {\n"file:a": {"label": "a", "props": {}, "type": "file"}\n}\n}\n'
)


def _run(argv: list[str]) -> tuple[int, str]:
    """Invoke the graph CLI with *argv*; return ``(exit_code, stdout)``."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            graph_cli_main(argv)
    except SystemExit as exc:
        code = 0 if exc.code is None else (
            exc.code if isinstance(exc.code, int) else 1
        )
    return code, out.getvalue()


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=_ENV)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", message],
        cwd=root, check=True, env=_ENV,
    )
    got = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, env=_ENV,
    )
    return got.stdout.strip()


class StaleCheckExitTest(unittest.TestCase):
    """A Mode B checkout: graph plus the inventory that explains it."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        subprocess.run(
            ["git", "init", "--quiet"], cwd=self._tmp, check=True, env=_ENV,
        )
        (self._tmp / "a.py").write_text("X = 1\n", encoding="utf-8")
        weld = self._tmp / ".weld"
        weld.mkdir()
        (weld / "discover.yaml").write_text(
            'sources:\n  - glob: "*.py"\n    type: file\n'
            "    strategy: python_module\n",
            encoding="utf-8",
        )
        sha = _commit(self._tmp, "init")
        graph_path = weld / "graph.json"
        graph_path.write_text(_GRAPH, encoding="utf-8")
        (weld / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_sha": sha}) + "\n", encoding="utf-8",
        )
        # The committed inventory Mode B now ships beside the graph. Built
        # through the real helpers rather than hand-written, so the record
        # this pins is the one discovery writes.
        save_state(self._tmp, DiscoveryState(
            files={"a.py": compute_hash(self._tmp / "a.py")},
            published_graph=published_graph_token(graph_path),
        ))

    def _stale(self, *extra: str) -> tuple[int, dict]:
        code, out = _run(
            ["--root", str(self._tmp), "stale", "--json", "--no-refresh", *extra],
        )
        return code, json.loads(out)

    def test_fresh_graph_exits_zero_with_check(self) -> None:
        code, payload = self._stale("--check")
        self.assertFalse(payload["stale"], payload)
        self.assertEqual(code, 0)

    def test_edited_source_exits_one_with_check(self) -> None:
        """The graph read this file and its content moved on."""
        (self._tmp / "a.py").write_text("X = 2\n", encoding="utf-8")
        _commit(self._tmp, "edit a source the graph read")
        code, payload = self._stale("--check")
        self.assertTrue(payload["source_stale"], payload)
        self.assertEqual(code, 1)

    def test_added_in_scope_source_exits_one_with_check(self) -> None:
        """The mistake Mode B actually makes: commit a file, forget to discover.

        Invisible to every signal scoped to ``meta.discovered_from``; it is
        the committed inventory that makes it visible at all (ADR 0101,
        ADR 0110).
        """
        (self._tmp / "b.py").write_text("Y = 2\n", encoding="utf-8")
        _commit(self._tmp, "add a source the graph never read")
        code, payload = self._stale("--check")
        self.assertTrue(payload["coverage_stale"], payload)
        self.assertTrue(payload["stale"], payload)
        self.assertEqual(code, 1)

    def test_without_check_a_stale_graph_still_exits_zero(self) -> None:
        """The gate is opt-in: `wd stale` alone stays a report.

        Scripts already parse it; making the bare command exit non-zero
        would break every one of them.
        """
        (self._tmp / "a.py").write_text("X = 2\n", encoding="utf-8")
        _commit(self._tmp, "edit a source the graph read")
        code, payload = self._stale()
        self.assertTrue(payload["stale"], payload)
        self.assertEqual(code, 0)

    def test_check_still_prints_the_report_when_it_fails(self) -> None:
        """A failing CI job has to say why, not just exit 1."""
        (self._tmp / "a.py").write_text("X = 2\n", encoding="utf-8")
        _commit(self._tmp, "edit a source the graph read")
        code, out = _run(
            ["--root", str(self._tmp), "stale", "--check", "--no-refresh"],
        )
        self.assertEqual(code, 1)
        self.assertIn("stale", out)


if __name__ == "__main__":
    unittest.main()

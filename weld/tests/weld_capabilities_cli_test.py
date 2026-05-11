"""Tests for the ``wd capabilities`` CLI (ADR 0043 Layer B).

Split out of :mod:`weld.tests.weld_capabilities_test` to keep both files
under the 400-line cap. Covers happy-path human/JSON/--missing output
plus hardening against a corrupt or unreadable ``.weld/graph.json``.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402


def _make_repo(
    nodes: dict[str, dict] | None = None,
    *,
    yaml_strategies: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Build a throwaway repo with a graph.json and optional discover.yaml."""
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-05-03T00:00:00+00:00",
                },
                "nodes": nodes or {},
                "edges": [],
            },
        ),
        encoding="utf-8",
    )
    if yaml_strategies is not None:
        sources = "\n".join(
            f"  - glob: '*'\n    type: file\n    strategy: {s}"
            for s in yaml_strategies
        )
        (root / ".weld" / "discover.yaml").write_text(
            f"sources:\n{sources}\n", encoding="utf-8",
        )
    for relpath, body in (extra_files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    from weld._capabilities_cli import main as cap_main

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        rc = cap_main(argv)
    return int(rc or 0), stdout_buf.getvalue(), stderr_buf.getvalue()


class CapabilitiesCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_repo(
            {
                "file:weld/graph.py": {
                    "type": "file",
                    "props": {"file": "weld/graph.py"},
                },
            },
            yaml_strategies=["python_module"],
        )

    def test_human_output_non_empty_and_zero_exit(self) -> None:
        code, stdout, _ = _run_cli(["--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Languages", stdout)
        self.assertIn("python", stdout)

    def test_json_output_parses(self) -> None:
        code, stdout, _ = _run_cli(["--json", "--root", str(self.root)])
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertIn("languages", data)
        self.assertIn("frameworks", data)
        self.assertIn("python", data["languages"])

    def test_missing_returns_sorted_list(self) -> None:
        # Drop an uncovered manifest (``Cargo.toml``) so ``--missing``
        # has at least one framework to surface. ``.csproj`` is owned
        # by ``csharp_project`` post-ADR-0056, so it no longer appears
        # in the missing list; use a still-uncovered framework here.
        (self.root / "App").mkdir(exist_ok=True)
        (self.root / "App" / "Cargo.toml").write_text(
            "[package]\nname = \"x\"\n", encoding="utf-8",
        )
        code, stdout, _ = _run_cli(
            ["--missing", "--json", "--root", str(self.root)],
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(data, sorted(data))
        self.assertIn("cargo", data)


class CapabilitiesCliCorruptedGraphTest(unittest.TestCase):
    """Hardening: a malformed graph.json must degrade, not crash."""

    def _corrupt_repo(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        # Invalid JSON -- Graph.load() will raise.
        (root / ".weld" / "graph.json").write_text(
            "{not valid json", encoding="utf-8",
        )
        # Wire python_module so registry rows are populated and the
        # human matrix has something deterministic to assert against.
        (root / ".weld" / "discover.yaml").write_text(
            "sources:\n  - glob: '*'\n    type: file\n    strategy: python_module\n",
            encoding="utf-8",
        )
        return root

    def test_corrupted_graph_human_does_not_crash(self) -> None:
        root = self._corrupt_repo()
        code, stdout, stderr = _run_cli(["--root", str(root)])
        self.assertEqual(code, 0)
        # Registry-completeness rows still emitted (docstring promise).
        self.assertIn("Languages", stdout)
        self.assertIn("python", stdout)
        # Degradation signal on stderr.
        self.assertTrue(
            stderr.strip(),
            "expected a non-empty stderr warning on corrupted graph",
        )
        self.assertIn("graph", stderr.lower())

    def test_corrupted_graph_json_does_not_crash(self) -> None:
        root = self._corrupt_repo()
        code, stdout, stderr = _run_cli(["--json", "--root", str(root)])
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertIn("languages", data)
        self.assertIn("frameworks", data)
        self.assertIn("python", data["languages"])
        # All flags must be False -- no graph evidence reached us.
        self.assertFalse(any(data["languages"]["python"].values()))
        # Degradation signal on stderr.
        self.assertTrue(
            stderr.strip(),
            "expected a non-empty stderr warning on corrupted graph",
        )

    def test_corrupted_graph_missing_json_does_not_crash(self) -> None:
        # --missing path uses detect_missing(root), which scans disk and is
        # independent of the graph file. It must still work even when the
        # graph load fails. Use a still-uncovered framework -- ``cargo``
        # via ``Cargo.toml`` -- because ``.csproj`` is owned by the new
        # csharp_project strategy (ADR 0056 Wave 1).
        root = self._corrupt_repo()
        (root / "App").mkdir(exist_ok=True)
        (root / "App" / "Cargo.toml").write_text(
            "[package]\nname = \"x\"\n", encoding="utf-8",
        )
        code, stdout, _ = _run_cli(
            ["--missing", "--json", "--root", str(root)],
        )
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertIsInstance(data, list)
        self.assertIn("cargo", data)


if __name__ == "__main__":
    unittest.main()

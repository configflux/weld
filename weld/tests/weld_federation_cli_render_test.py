"""End-to-end CLI text-rendering test for federated workspaces.

The federation layer prefixes child-local node IDs with ``\\x1f``
(UNIT_SEPARATOR) for canonical IDs and exposes a printable form via
``display_id`` (``child::id``). The CLI text renderer must prefer
``display_id`` so users do not see the invisible control character
glued between child name and the rest of the ID.

This is the end-to-end seam: a synthetic two-child federation fixture
driven through ``wd query`` without ``--json``. Unit coverage of the
renderer helpers lives in ``weld_cli_render_helpers_test.py``.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.federation import prefix_node_id  # noqa: E402
from weld.graph import main as graph_main  # noqa: E402
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml  # noqa: E402

_TS = "2026-05-15T12:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, check=True,
    )


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")


def _write_graph(repo_root: Path, nodes: dict, *, schema_version: int = 1) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_text(root: Path, *args: str) -> str:
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        graph_main(["--root", str(root), *args])
    return stdout.getvalue()


class FederatedQueryTextRenderTest(unittest.TestCase):
    """Federated CLI text output uses ``::`` (display), never ``\\x1f``."""

    def _make_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _init_repo(root / "sharex")
        _init_repo(root / "lib-core")
        _write_graph(
            root / "sharex",
            {
                "symbol:csharp:ShareX.Foo": {
                    "type": "symbol",
                    "label": "ShareX.Foo",
                    "props": {"description": "ShareX entry"},
                },
            },
        )
        _write_graph(
            root / "lib-core",
            {
                "symbol:csharp:LibCore.Bar": {
                    "type": "symbol",
                    "label": "LibCore.Bar",
                    "props": {"description": "ShareX helper"},
                },
            },
        )
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="sharex", path="sharex"),
                ChildEntry(name="lib-core", path="lib-core"),
            ],
            cross_repo_strategies=[],
        )
        dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
        # Federated root needs a root graph with repo: nodes.
        repo_nodes = {
            f"repo:{n}": {"type": "repo", "label": n, "props": {"path": n}}
            for n in ("sharex", "lib-core")
        }
        _write_graph(root, repo_nodes, schema_version=2)
        return root

    def test_query_text_renders_visible_double_colon(self) -> None:
        root = self._make_workspace()

        text = _run_text(root, "query", "ShareX", "--limit", "5")

        # Display form must be present (visible '::' between child + id).
        self.assertIn("sharex::symbol:csharp:ShareX.Foo", text)
        # The canonical UNIT_SEPARATOR is invisible to humans and must
        # never leak into the rendered text output.
        self.assertNotIn("\x1f", text)
        # Sanity: the test exercised a federated payload (canonical id
        # uses '\x1f', not '::' or '/').
        canonical = prefix_node_id("sharex", "symbol:csharp:ShareX.Foo")
        self.assertIn("\x1f", canonical)


if __name__ == "__main__":
    unittest.main()

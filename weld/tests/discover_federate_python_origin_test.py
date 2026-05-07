"""End-to-end test for the federation Python origin contract (ADR 0042).

Mirrors a polyrepo workspace where child A defines a Python module
``lib_a.foo:bar`` and child B imports that symbol from ``lib_a.foo``.
After ``wd discover --recurse`` runs at the workspace root, child B's
graph must classify the speculatively-minted ``symbol:py:lib_a.foo:bar``
target node as ``origin="project"`` rather than ``"external"`` --
ADR 0042 §Federation says any federated child of the active root is
project code.

This is the integration counterpart to the focused helper tests in
``discover_federate_origin_test.py``: it exercises the real
``python_callgraph`` strategy, real ``wd discover`` orchestration, and
real on-disk child graphs so a regression in any layer surfaces here.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml


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


def _write_python_callgraph_yaml(repo_root: Path, glob: str) -> None:
    """Write a discover.yaml that runs ``python_callgraph`` over *glob*."""
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        textwrap.dedent(
            f"""\
            sources:
              - glob: "{glob}"
                strategy: python_callgraph
            """
        ),
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


class FederationPythonOriginContractTest(unittest.TestCase):
    """ADR 0042 §Federation: cross-child Python imports classify as project."""

    def test_cross_child_target_is_tagged_project_after_recurse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Child alpha: defines lib_a/foo.py with `def bar(): ...`.
            child_a = _init_repo(root / "alpha")
            (child_a / "lib_a").mkdir()
            (child_a / "lib_a" / "__init__.py").write_text(
                "", encoding="utf-8",
            )
            (child_a / "lib_a" / "foo.py").write_text(
                textwrap.dedent(
                    """
                    def bar():
                        return 1
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            _write_python_callgraph_yaml(child_a, "**/*.py")

            # Child beta: imports lib_a.foo:bar from a sibling repo.
            child_b = _init_repo(root / "beta")
            (child_b / "app").mkdir()
            (child_b / "app" / "__init__.py").write_text(
                "", encoding="utf-8",
            )
            (child_b / "app" / "main.py").write_text(
                textwrap.dedent(
                    """
                    from lib_a.foo import bar

                    def f():
                        bar()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            _write_python_callgraph_yaml(child_b, "**/*.py")

            _write_workspaces(
                root,
                [
                    ChildEntry(name="alpha", path="alpha"),
                    ChildEntry(name="beta", path="beta"),
                ],
            )

            # Run federated discover with recurse: each child runs its
            # own python_callgraph extract, then the federation origin
            # pass re-tags cross-child targets.
            discover(root, incremental=False, recurse=True)

            # Inspect child beta's on-disk graph: the speculatively
            # minted target node for lib_a.foo:bar must now carry
            # origin="project", not "external".
            beta_graph = json.loads(
                (child_b / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            target_id = "symbol:py:lib_a.foo:bar"
            self.assertIn(target_id, beta_graph["nodes"])
            target_node = beta_graph["nodes"][target_id]
            self.assertEqual(
                target_node["props"]["origin"],
                "project",
                "ADR 0042 §Federation: cross-child Python target must "
                "be classified project, not external.",
            )

            # Caller in beta and definition in alpha both remain
            # project (sanity).
            self.assertEqual(
                beta_graph["nodes"]["symbol:py:app.main:f"]["props"]["origin"],
                "project",
            )
            alpha_graph = json.loads(
                (child_a / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                alpha_graph["nodes"][target_id]["props"]["origin"],
                "project",
            )

    def test_truly_external_target_stays_external(self) -> None:
        """A third-party import (no sibling defines it) stays external."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "alpha")
            (child_a / "lib_a").mkdir()
            (child_a / "lib_a" / "__init__.py").write_text(
                "", encoding="utf-8",
            )
            (child_a / "lib_a" / "foo.py").write_text(
                "def bar():\n    return 1\n",
                encoding="utf-8",
            )
            _write_python_callgraph_yaml(child_a, "**/*.py")

            child_b = _init_repo(root / "beta")
            (child_b / "app").mkdir()
            (child_b / "app" / "__init__.py").write_text(
                "", encoding="utf-8",
            )
            # Import from a module that no sibling repo declares: must
            # stay origin="external" after the federation pass.
            (child_b / "app" / "main.py").write_text(
                textwrap.dedent(
                    """
                    from third_party_pkg import some_fn

                    def f():
                        some_fn()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            _write_python_callgraph_yaml(child_b, "**/*.py")

            _write_workspaces(
                root,
                [
                    ChildEntry(name="alpha", path="alpha"),
                    ChildEntry(name="beta", path="beta"),
                ],
            )

            discover(root, incremental=False, recurse=True)

            beta_graph = json.loads(
                (child_b / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            target_id = "symbol:py:third_party_pkg:some_fn"
            self.assertIn(target_id, beta_graph["nodes"])
            self.assertEqual(
                beta_graph["nodes"][target_id]["props"]["origin"],
                "external",
            )


if __name__ == "__main__":
    unittest.main()

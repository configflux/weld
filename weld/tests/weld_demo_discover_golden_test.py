"""Golden tests for discover output from the public demo examples."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_EXAMPLES_DIR = _REPO_ROOT / "examples"
_GOLDEN_DIR = _REPO_ROOT / "weld" / "tests" / "golden" / "demo_discover"
_UPDATE_ENV = "UPDATE_WELD_DEMO_DISCOVER_GOLDENS"
_UPDATE_COMMAND = (
    f"{_UPDATE_ENV}=1 python3 weld/tests/weld_demo_discover_golden_test.py"
)

_GENERATED_NAMES = {
    ".git",
    "discovery-state.json",
    "graph-previous.json",
    "graph.json",
    "workspace-state.json",
    "workspace.lock",
}


def _copy_demo(name: str, destination: Path) -> Path:
    source = _EXAMPLES_DIR / name

    def ignore_generated(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _GENERATED_NAMES}

    copy = destination / name
    shutil.copytree(source, copy, ignore=ignore_generated)
    return copy


def _discover_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(_REPO_ROOT)
        if not pythonpath
        else os.pathsep.join([str(_REPO_ROOT), pythonpath])
    )
    env["LC_ALL"] = "C"
    env["PYTHONHASHSEED"] = "0"
    return env


def _run_discover(root: Path, *args: str, expect_stdout: bool = True) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "weld", "discover", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        env=_discover_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"wd discover failed in {root} with exit {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if not expect_stdout:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"wd discover did not emit JSON in {root}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        ) from exc


def _read_graph(root: Path) -> dict:
    graph_path = root / ".weld" / "graph.json"
    if not graph_path.is_file():
        raise AssertionError(f"wd discover did not write {graph_path}")
    return json.loads(graph_path.read_text(encoding="utf-8"))


def _run_validate(root: Path) -> None:
    graph_path = root / ".weld" / "graph.json"
    if not graph_path.is_file():
        raise AssertionError(f"Cannot validate missing graph: {graph_path}")
    proc = subprocess.run(
        [sys.executable, "-m", "weld", "validate"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        env=_discover_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"wd validate failed in {root} with exit {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"wd validate did not emit JSON in {root}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        ) from exc
    if payload != {"valid": True, "errors": []}:
        raise AssertionError(f"wd validate reported errors in {root}: {payload}")


def _git(child: Path, *args: str) -> None:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_AUTHOR_NAME": "Weld Test",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Weld Test",
        "LC_ALL": "C",
    })
    proc = subprocess.run(
        ["git", *args],
        cwd=str(child),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {child}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _seed_child_repo(child: Path) -> None:
    _git(child, "init", "-q")
    _git(child, "add", "-A")
    _git(child, "commit", "-q", "-m", "demo seed")


def _discover_monorepo() -> dict:
    with TemporaryDirectory() as tmp:
        demo = _copy_demo("04-monorepo-typescript", Path(tmp))
        _run_discover(
            demo,
            "--full",
            "--output",
            ".weld/graph.json",
            expect_stdout=False,
        )
        _run_validate(demo)
        return _read_graph(demo)


def _discover_polyrepo() -> dict:
    with TemporaryDirectory() as tmp:
        demo = _copy_demo("05-polyrepo", Path(tmp))
        for rel_path in ("services/api", "services/auth", "libs/shared-models"):
            child = demo / rel_path
            _seed_child_repo(child)
            _run_discover(
                child,
                "--full",
                "--output",
                ".weld/graph.json",
                expect_stdout=False,
            )
        _run_discover(
            demo,
            "--full",
            "--output",
            ".weld/graph.json",
            expect_stdout=False,
        )
        _run_validate(demo)
        return _read_graph(demo)


def _normalise_graph(graph: dict[str, Any]) -> dict[str, Any]:
    normalised = json.loads(json.dumps(graph))
    meta = normalised.get("meta")
    if isinstance(meta, dict):
        meta.pop("updated_at", None)
        meta.pop("git_sha", None)
        discovered_from = meta.get("discovered_from")
        if isinstance(discovered_from, list):
            meta["discovered_from"] = sorted(discovered_from)

    nodes = normalised.get("nodes")
    if isinstance(nodes, dict):
        normalised["nodes"] = {
            node_id: nodes[node_id]
            for node_id in sorted(nodes)
        }

    edges = normalised.get("edges")
    if isinstance(edges, list):
        normalised["edges"] = sorted(
            edges,
            key=lambda edge: json.dumps(edge, sort_keys=True),
        )
    return normalised


def _snapshot_text(graph: dict[str, Any]) -> str:
    return json.dumps(graph, indent=2, sort_keys=True) + "\n"


class DemoDiscoverGoldenTest(unittest.TestCase):
    maxDiff = None

    def assertMatchesGolden(self, name: str, graph: dict[str, Any]) -> None:
        actual = _normalise_graph(graph)
        golden_path = _GOLDEN_DIR / f"{name}.json"

        if os.environ.get(_UPDATE_ENV) == "1":
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(_snapshot_text(actual), encoding="utf-8")

        if not golden_path.is_file():
            self.fail(
                f"Missing golden snapshot: {golden_path}. "
                f"Run `{_UPDATE_COMMAND}` to create it."
            )

        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        self.assertEqual(
            actual,
            expected,
            f"{name} discover output drifted. "
            f"Run `{_UPDATE_COMMAND}` to accept an intentional schema change.",
        )

    def test_monorepo_typescript_discover_matches_golden(self) -> None:
        self.assertMatchesGolden("04-monorepo-typescript", _discover_monorepo())

    def test_polyrepo_root_discover_matches_golden(self) -> None:
        self.assertMatchesGolden("05-polyrepo", _discover_polyrepo())


if __name__ == "__main__":
    unittest.main()

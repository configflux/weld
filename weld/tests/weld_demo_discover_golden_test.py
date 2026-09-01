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
from unittest import mock

from weld.tests._golden_invariants import (
    GoldenScope,
    check_golden_graph,
    child_graphs_from_repo_nodes,
)
from weld.tests._golden_violation_fixtures import with_fabricated_external

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_EXAMPLES_DIR = _REPO_ROOT / "examples"
_GOLDEN_DIR = _REPO_ROOT / "weld" / "tests" / "golden" / "demo_discover"
_UPDATE_ENV = "UPDATE_WELD_DEMO_DISCOVER_GOLDENS"
_UPDATE_COMMAND = (
    f"{_UPDATE_ENV}=1 python3 weld/tests/weld_demo_discover_golden_test.py"
)

#: ADR 0139 mechanism 5 / bd 5038-ipa1e. Both demo goldens close over their own
#: edges -- the monorepo because it is one repo, the polyrepo because its two
#: cross-repo endpoints resolve into the child graphs the run writes into the
#: scratch tree and :func:`child_graphs_from_repo_nodes` hands back.
_SCOPE = GoldenScope(family="demo_discover")

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


def _discover_monorepo() -> tuple[dict, dict[str, Any]]:
    """The root graph and, explicitly, no children (ADR 0139 R6).

    Both discover helpers return the pair so the child roster is always stated
    rather than defaulted: ``{}`` here is the claim that these edges close over
    one repo, and it is checkable, where a missing argument would only mean
    somebody did not think about it.
    """
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
        return _read_graph(demo), {}


def _discover_polyrepo() -> tuple[dict, dict[str, Any]]:
    """The federated root graph plus every child graph its edges reach into.

    The children are read before the scratch tree is torn down, because the
    root's only edge is a ``cross_repo:calls`` whose endpoints are
    ``<child>\\x1f<child-local-id>`` -- resolvable in the children's graphs and
    nowhere else. Without them ``assert_edges_resolve`` would have to be skipped
    on the one shipped golden that has a cross-repo edge to check.
    """
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
        graph = _read_graph(demo)
        return graph, child_graphs_from_repo_nodes(graph, demo)


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

    def assertMatchesGolden(
        self,
        name: str,
        graph: dict[str, Any],
        child_graphs: dict[str, Any],
    ) -> None:
        actual = _normalise_graph(graph)
        golden_path = _GOLDEN_DIR / f"{name}.json"

        # ADR 0139 mechanism 5: checked before the write, so the regen path
        # cannot bake a violation the next compare would then assert faithfully.
        check_golden_graph(
            actual, scope=_SCOPE, label=f"{name} (discovered)",
            child_graphs=child_graphs,
        )

        if os.environ.get(_UPDATE_ENV) == "1":
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(_snapshot_text(actual), encoding="utf-8")

        if not golden_path.is_file():
            self.fail(
                f"Missing golden snapshot: {golden_path}. "
                f"Run `{_UPDATE_COMMAND}` to create it."
            )

        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        # Before the equality assertion: a violating golden should name the
        # invariant it breaks, not print a diff and leave the reader to infer it.
        check_golden_graph(
            expected, scope=_SCOPE, label=f"{name}.json",
            child_graphs=child_graphs,
        )
        self.assertEqual(
            actual,
            expected,
            f"{name} discover output drifted. "
            f"Run `{_UPDATE_COMMAND}` to accept an intentional schema change.",
        )

    def test_monorepo_typescript_discover_matches_golden(self) -> None:
        graph, children = _discover_monorepo()
        self.assertMatchesGolden("04-monorepo-typescript", graph, children)

    def test_polyrepo_root_discover_matches_golden(self) -> None:
        graph, children = _discover_polyrepo()
        self.assertMatchesGolden("05-polyrepo", graph, children)


#: This module, for redirecting ``_GOLDEN_DIR`` at a scratch copy. Under Bazel
#: the module is imported as ``__main__``, so a dotted patch target would miss.
_MODULE = sys.modules[__name__]

_MONOREPO_GOLDEN = "04-monorepo-typescript"


class GoldenInvariantHookTest(unittest.TestCase):
    """``assertMatchesGolden`` rejects a violating payload on either path.

    bd 5038-ipa1e / ADR 0139 mechanism 5. Discovery is not re-run: the base is
    the shipped golden read off disk, which is the producer's own output, and the
    violation is minted onto a copy of it by ``weld.graph_closure``'s minters.
    """

    def _harness(self) -> DemoDiscoverGoldenTest:
        """An instance borrowed for ``assertMatchesGolden``; constructing a
        TestCase does not run the named method."""
        return DemoDiscoverGoldenTest("test_monorepo_typescript_discover_matches_golden")

    def _clean_golden(self) -> dict:
        path = _GOLDEN_DIR / f"{_MONOREPO_GOLDEN}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_compare_path_rejects_a_violating_golden(self) -> None:
        clean = self._clean_golden()
        with TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch / f"{_MONOREPO_GOLDEN}.json").write_text(
                _snapshot_text(with_fabricated_external(clean)), encoding="utf-8",
            )
            # The regen switch is forced off, not merely assumed off. Whoever
            # runs the documented `UPDATE_WELD_DEMO_DISCOVER_GOLDENS=1 python3
            # weld/tests/weld_demo_discover_golden_test.py` has it set for the
            # whole process, and this case would then overwrite its own injected
            # golden with the clean payload and find nothing to reject -- passing
            # in the fast loop and failing only in the hands of the one person
            # regenerating.
            with mock.patch.object(_MODULE, "_GOLDEN_DIR", scratch), \
                 mock.patch.dict(os.environ, {_UPDATE_ENV: "0"}), \
                 self.assertRaises(AssertionError) as caught:
                self._harness().assertMatchesGolden(_MONOREPO_GOLDEN, clean, {})
        self.assertIn("already holds first-party", str(caught.exception))

    def test_regen_path_refuses_to_bake_a_violation(self) -> None:
        violating = with_fabricated_external(self._clean_golden())
        with TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            written = scratch / f"{_MONOREPO_GOLDEN}.json"
            with mock.patch.object(_MODULE, "_GOLDEN_DIR", scratch), \
                 mock.patch.dict(os.environ, {_UPDATE_ENV: "1"}), \
                 self.assertRaises(AssertionError) as caught:
                self._harness().assertMatchesGolden(_MONOREPO_GOLDEN, violating, {})
            self.assertIn("already holds first-party", str(caught.exception))
            self.assertFalse(
                written.exists(),
                "regen wrote a graph carrying a fabricated external to the golden",
            )


if __name__ == "__main__":
    unittest.main()

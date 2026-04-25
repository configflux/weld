"""Golden tests that lock the ``wd discover`` output shape for demo examples.

Runs the in-process discover entry point against ``examples/04-monorepo-typescript``
and ``examples/05-polyrepo`` in scratch copies, normalises the result (drops
volatile ``meta`` fields: ``updated_at``, ``git_sha``, ``discovered_from``),
and compares against a checked-in snapshot in
``weld/tests/fixtures/examples_discover/``.

Regression model:
  - A run that matches the golden is a pass.
  - A run that drifts fails the test and prints the diff.
  - When a schema change is intentional, regenerate the golden by running
    this test file with ``REGEN_EXAMPLE_GOLDENS=1`` set in the environment.

The polyrepo variant uses ``--recurse`` after git-initialising each child so
federation discovery sees ``status=present`` and emits ``repo:*`` nodes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.discover import discover  # noqa: E402

_EXAMPLES_DIR = _repo_root / "examples"
_GOLDEN_DIR = _repo_root / "weld" / "tests" / "fixtures" / "examples_discover"
_MONOREPO_EXAMPLE = _EXAMPLES_DIR / "04-monorepo-typescript"
_POLYREPO_EXAMPLE = _EXAMPLES_DIR / "05-polyrepo"
_POLYREPO_CHILDREN = ("services/api", "services/auth", "libs/shared-models")

_REGEN_ENV_VAR = "REGEN_EXAMPLE_GOLDENS"
_REGEN_HINT = (
    "Golden drift detected. If the change is intentional, regenerate with:\n"
    "  REGEN_EXAMPLE_GOLDENS=1 bazel test \\\n"
    "    //weld/tests:weld_examples_discover_golden_test --test_output=all\n"
    "Then review the updated golden JSON before committing."
)


def _normalise(graph: dict) -> dict:
    """Strip volatile meta fields so two runs are byte-identical.

    ``meta.updated_at`` changes every run, ``meta.git_sha`` depends on the
    scratch repo's commit hash, and ``meta.discovered_from`` can reflect
    filesystem traversal order in some strategies. All three are removed
    before comparison. The graph is deep-copied via a JSON round-trip to
    avoid mutating the caller's object.
    """
    copy = json.loads(json.dumps(graph))
    meta = copy.get("meta")
    if isinstance(meta, dict):
        meta.pop("updated_at", None)
        meta.pop("git_sha", None)
        meta.pop("discovered_from", None)
    return copy


def _git_init_child(child_root: Path) -> None:
    """Initialise *child_root* as a git repo with one commit.

    Federation discovery requires children to be ``status=present``, which
    means each child must have a ``.git`` directory and a ``graph.json``.
    The ``.git`` is created here; ``graph.json`` is written by the
    ``--recurse`` pass of the root discover.
    """
    env = {"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(
        ["git", "init", "-q"], cwd=str(child_root), env=env, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "golden-test@weld.internal"],
        cwd=str(child_root), env=env, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "golden-test"],
        cwd=str(child_root), env=env, check=True,
    )
    subprocess.run(
        ["git", "add", "-A"], cwd=str(child_root), env=env, check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=str(child_root), env=env, check=True,
    )


def _discover_monorepo(scratch: Path) -> dict:
    target = scratch / "04-monorepo-typescript"
    shutil.copytree(_MONOREPO_EXAMPLE, target)
    # Strip any state files that may have been copied in so the test runs
    # a full, deterministic discovery.
    state = target / ".weld" / "discovery-state.json"
    if state.exists():
        state.unlink()
    graph = target / ".weld" / "graph.json"
    if graph.exists():
        graph.unlink()
    return discover(target, incremental=False)


def _discover_polyrepo(scratch: Path) -> dict:
    target = scratch / "05-polyrepo"
    shutil.copytree(_POLYREPO_EXAMPLE, target)
    for rel in _POLYREPO_CHILDREN:
        _git_init_child(target / rel)
    return discover(target, incremental=False, recurse=True)


def _load_golden(name: str) -> dict:
    with (_GOLDEN_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_golden(name: str, payload: dict) -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (_GOLDEN_DIR / name).write_text(serialised, encoding="utf-8")


def _regen_mode() -> bool:
    return os.environ.get(_REGEN_ENV_VAR, "").strip() not in ("", "0", "false", "False")


class MonorepoDemoDiscoverGoldenTest(unittest.TestCase):
    """Discover against ``examples/04-monorepo-typescript`` matches golden."""

    GOLDEN_NAME = "04-monorepo-typescript.golden.json"

    def test_normalised_discover_matches_golden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actual = _normalise(_discover_monorepo(Path(tmp)))

        if _regen_mode():
            _write_golden(self.GOLDEN_NAME, actual)
            self.skipTest(
                f"Regenerated {self.GOLDEN_NAME}; re-run without "
                f"{_REGEN_ENV_VAR} to verify.",
            )

        expected = _load_golden(self.GOLDEN_NAME)
        self.assertEqual(actual, expected, msg=_REGEN_HINT)

    def test_expected_node_types_present(self) -> None:
        """Smoke: key node types from the demo must always appear.

        This complements the exact-match golden by giving a clearer
        failure message when a whole strategy (e.g. dockerfile) drops
        out silently. The golden comparison catches the same regression
        but prints a large diff instead of a targeted message.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = _discover_monorepo(Path(tmp))
        types = {n.get("type") for n in graph.get("nodes", {}).values()}
        required = {
            "build-target",
            "config",
            "doc",
            "dockerfile",
            "file",
            "test-target",
            "workflow",
        }
        missing = required - types
        self.assertFalse(
            missing,
            f"Expected node types missing from demo discover: {missing}",
        )


class PolyrepoDemoDiscoverGoldenTest(unittest.TestCase):
    """Discover against ``examples/05-polyrepo`` matches golden (federated)."""

    GOLDEN_NAME = "05-polyrepo.golden.json"

    def test_normalised_discover_matches_golden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actual = _normalise(_discover_polyrepo(Path(tmp)))

        if _regen_mode():
            _write_golden(self.GOLDEN_NAME, actual)
            self.skipTest(
                f"Regenerated {self.GOLDEN_NAME}; re-run without "
                f"{_REGEN_ENV_VAR} to verify.",
            )

        expected = _load_golden(self.GOLDEN_NAME)
        self.assertEqual(actual, expected, msg=_REGEN_HINT)

    def test_federation_meta_graph_shape(self) -> None:
        """Smoke: root must emit schema_version=2 and one repo-node per child.

        The polyrepo demo's PM criterion is "root graph federates child
        graphs." That contract is encoded here so a regression (e.g. a
        federation bug that drops the meta-schema version) fails loudly
        and independently of the big golden diff.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = _discover_polyrepo(Path(tmp))
        self.assertEqual(graph.get("meta", {}).get("schema_version"), 2)
        node_ids = set(graph.get("nodes", {}).keys())
        expected_repos = {
            "repo:services-api",
            "repo:services-auth",
            "repo:libs-shared-models",
        }
        self.assertTrue(
            expected_repos <= node_ids,
            f"Federation root missing repo nodes: "
            f"{expected_repos - node_ids}",
        )


if __name__ == "__main__":
    unittest.main()

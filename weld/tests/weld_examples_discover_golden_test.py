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
from typing import Any
from unittest import mock

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.discover import discover  # noqa: E402
from weld.tests._golden_invariants import (  # noqa: E402
    GoldenScope,
    check_golden_graph,
    child_graphs_from_repo_nodes,
)
from weld.tests._golden_violation_fixtures import (  # noqa: E402
    with_fabricated_external,
)

_EXAMPLES_DIR = _repo_root / "examples"
_GOLDEN_DIR = _repo_root / "weld" / "tests" / "fixtures" / "examples_discover"
_MONOREPO_EXAMPLE = _EXAMPLES_DIR / "04-monorepo-typescript"
_POLYREPO_EXAMPLE = _EXAMPLES_DIR / "05-polyrepo"
_POLYREPO_CHILDREN = ("services/api", "services/auth", "libs/shared-models")

#: ADR 0139 mechanism 5 / bd 5038-ipa1e. Same two examples as the demo family
#: and the same verdict: edges close, over ``{}`` for the monorepo and over the
#: children read from the scratch tree for the polyrepo.
_SCOPE = GoldenScope(family="examples_discover")

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


def _discover_monorepo(scratch: Path) -> tuple[dict, dict[str, Any]]:
    """The root graph and, explicitly, no children (ADR 0139 R6)."""
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
    return discover(target, incremental=False), {}


def _discover_polyrepo(scratch: Path) -> tuple[dict, dict[str, Any]]:
    """The federated root graph plus the child graphs its edges reach into.

    Read here rather than by the caller because the scratch tree is torn down on
    the way out: the root's cross-repo endpoints are spelled
    ``<child>\\x1f<child-local-id>`` and resolve only against these.
    """
    target = scratch / "05-polyrepo"
    shutil.copytree(_POLYREPO_EXAMPLE, target)
    for rel in _POLYREPO_CHILDREN:
        _git_init_child(target / rel)
    graph = discover(target, incremental=False, recurse=True)
    return graph, child_graphs_from_repo_nodes(graph, target)


def _load_golden(name: str, child_graphs: dict[str, Any]) -> dict:
    """Load the EXPECTED golden, and check it before anything compares to it.

    Architect ruling R6 puts the hook on both this and :func:`_write_golden`
    rather than on one shared point, because this function returns the golden --
    never the discovered graph -- and the regen path writes and then
    ``skipTest``s without ever calling it. Hooking only here would leave
    regeneration free to bake in a violation; hooking only the writer would leave
    a hand-edited golden unchecked.
    """
    with (_GOLDEN_DIR / name).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    check_golden_graph(
        payload, scope=_SCOPE, label=name, child_graphs=child_graphs,
    )
    return payload


def _write_golden(name: str, payload: dict, child_graphs: dict[str, Any]) -> None:
    """Check before the write: a regeneration must not re-bake a violation."""
    check_golden_graph(
        payload, scope=_SCOPE, label=f"{name} (discovered)",
        child_graphs=child_graphs,
    )
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
            graph, children = _discover_monorepo(Path(tmp))
            actual = _normalise(graph)

        if _regen_mode():
            _write_golden(self.GOLDEN_NAME, actual, children)
            self.skipTest(
                f"Regenerated {self.GOLDEN_NAME}; re-run without "
                f"{_REGEN_ENV_VAR} to verify.",
            )

        expected = _load_golden(self.GOLDEN_NAME, children)
        self.assertEqual(actual, expected, msg=_REGEN_HINT)

    def test_expected_node_types_present(self) -> None:
        """Smoke: key node types from the demo must always appear.

        This complements the exact-match golden by giving a clearer
        failure message when a whole strategy (e.g. dockerfile) drops
        out silently. The golden comparison catches the same regression
        but prints a large diff instead of a targeted message.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph, _ = _discover_monorepo(Path(tmp))
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
            graph, children = _discover_polyrepo(Path(tmp))
            actual = _normalise(graph)

        if _regen_mode():
            _write_golden(self.GOLDEN_NAME, actual, children)
            self.skipTest(
                f"Regenerated {self.GOLDEN_NAME}; re-run without "
                f"{_REGEN_ENV_VAR} to verify.",
            )

        expected = _load_golden(self.GOLDEN_NAME, children)
        self.assertEqual(actual, expected, msg=_REGEN_HINT)

    def test_federation_meta_graph_shape(self) -> None:
        """Smoke: root must emit schema_version=2 and one repo-node per child.

        The polyrepo demo's PM criterion is "root graph federates child
        graphs." That contract is encoded here so a regression (e.g. a
        federation bug that drops the meta-schema version) fails loudly
        and independently of the big golden diff.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph, _ = _discover_polyrepo(Path(tmp))
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


#: This module, for redirecting ``_GOLDEN_DIR`` at a scratch copy. Under Bazel
#: it is imported as ``__main__``, so a dotted patch target would miss.
_MODULE = sys.modules[__name__]


class GoldenInvariantHookTest(unittest.TestCase):
    """Both golden functions reject a violating payload (bd 5038-ipa1e).

    The two are tested separately because they are the two paths: R6's point is
    that ``_load_golden`` never sees the discovered graph and ``_write_golden``
    is never reached on a compare, so neither one alone covers regeneration.
    Discovery is not re-run -- the base is the shipped golden, and the violation
    is minted onto a copy of it by ``weld.graph_closure``'s own minters.
    """

    GOLDEN_NAME = "04-monorepo-typescript.golden.json"

    def _clean_golden(self) -> dict:
        return json.loads(
            (_GOLDEN_DIR / self.GOLDEN_NAME).read_text(encoding="utf-8"),
        )

    def test_load_golden_rejects_a_violating_golden(self) -> None:
        violating = with_fabricated_external(self._clean_golden())
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch / self.GOLDEN_NAME).write_text(
                json.dumps(violating), encoding="utf-8",
            )
            with mock.patch.object(_MODULE, "_GOLDEN_DIR", scratch), \
                 self.assertRaises(AssertionError) as caught:
                _load_golden(self.GOLDEN_NAME, {})
        self.assertIn("already holds first-party", str(caught.exception))

    def test_write_golden_refuses_to_bake_a_violation(self) -> None:
        violating = with_fabricated_external(self._clean_golden())
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            written = scratch / self.GOLDEN_NAME
            with mock.patch.object(_MODULE, "_GOLDEN_DIR", scratch), \
                 self.assertRaises(AssertionError) as caught:
                _write_golden(self.GOLDEN_NAME, violating, {})
            self.assertIn("already holds first-party", str(caught.exception))
            self.assertFalse(
                written.exists(),
                "regen wrote a graph carrying a fabricated external to the golden",
            )


if __name__ == "__main__":
    unittest.main()

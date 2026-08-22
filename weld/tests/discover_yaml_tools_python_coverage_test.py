"""Regression: ``.weld/discover.yaml`` covers ``tools/publish_overlays/*.py``
with the same trio ``tools/*.py`` gets (bd aluca).

``tools/*.py`` is a flat glob -- a single ``*`` never spans ``/`` -- so it
never reached one directory level down into
``tools/publish_overlays/release_mcp_handshake.py``, the overlay counterpart
that replaces ``tools/release_mcp_handshake.py`` in the public tree (per
docs/publish.md). Discovery had no source entry that walked that directory,
so the file minted no node of any type: ``wd context
file:tools/publish_overlays/release_mcp_handshake`` answered node-not-found
for a script the release pipeline's own overlay workflow ships beside.

A recursive ``tools/**/*.py`` widening was considered and rejected: on this
tree it would newly match exactly one file besides the reported one --
``tools/testdata/check_action_pins/fake_github_server.py``, a fixture under a
directory named ``testdata/`` that this repo's discover.yaml header policy
excludes ("not test fixtures"). This test pins both directions of that
decision: the reported file is in scope, and the fixture stays out.

Same shape as ``discover_yaml_tool_script_coverage_test.py`` and
``discover_yaml_workflow_coverage_test.py``: a strategy's own unit tests
cannot catch a source entry that was never configured, since they call
``extract()`` directly and pass identically either way. Scope is decided
with :func:`weld._staleness_coverage.in_scope_files`, the product's own
"would discovery resolve this path?" matcher (ADR 0101), so the config is
asserted through the same code that decides coverage staleness, with no
discovery run and no git.

The repo's ``.weld/discover.yaml`` is internal state and is absent from the
published source tree, so the suite skips cleanly when it is not present.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml
from weld.glob_match import walk_glob

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

# python_module emits the ``file:`` anchor, python_callgraph the ``symbol:``
# nodes and calls edges, python_package the ``package:python:*`` node plus
# the inbound ``contains`` edge file-anchor-symmetry requires.
_TRIO = ("python_module", "python_callgraph", "python_package")

# The file the originating gap was filed against.
_OVERLAY_FILE = "tools/publish_overlays/release_mcp_handshake.py"

# The fixture a recursive ``tools/**/*.py`` widening would have swept in.
# Pinned so the bounded-widening decision stays a conscious edit rather than
# silently drifting the moment someone "simplifies" the config to ``**``.
_EXCLUDED_FIXTURE = "tools/testdata/check_action_pins/fake_github_server.py"


def _sources() -> list[dict]:
    """Parse the repo's checked-in ``.weld/discover.yaml`` source entries."""
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    return [entry for entry in sources if isinstance(entry, dict)]


def _entries_for(strategy: str) -> list[dict]:
    return [e for e in _sources() if e.get("strategy") == strategy]


def _overlay_scoped_entries() -> list[dict]:
    """Trio entries whose glob targets ``tools/publish_overlays/``."""
    return [
        e for e in _sources()
        if e.get("strategy") in _TRIO
        and str(e.get("glob") or "").startswith("tools/publish_overlays/")
    ]


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class DiscoverYamlToolsPythonCoverageTest(unittest.TestCase):
    """The tools/publish_overlays/*.py trio must mirror tools/*.py's."""

    def test_overlay_python_entries_exist(self) -> None:
        entries = _overlay_scoped_entries()
        self.assertEqual(
            {e.get("strategy") for e in entries}, set(_TRIO),
            "tools/publish_overlays/*.py must be covered by exactly the "
            f"canonical trio; got {sorted({e.get('strategy') for e in entries})}",
        )

    def test_overlay_file_is_in_scope(self) -> None:
        self.assertTrue(
            (_REPO_ROOT / _OVERLAY_FILE).is_file(),
            "fixture path drifted; update _OVERLAY_FILE",
        )
        for strategy in _TRIO:
            with self.subTest(strategy=strategy):
                covered = in_scope_files(_entries_for(strategy), [_OVERLAY_FILE])
                self.assertIn(
                    _OVERLAY_FILE, covered,
                    f"{_OVERLAY_FILE} is not covered by {strategy}, so it "
                    "mints no node -- the shape the originating dogfood gap "
                    "was filed against",
                )

    def test_excluded_fixture_stays_out_of_scope(self) -> None:
        # The other direction: proves the widening was the three scoped
        # entries, not a recursive tools/**/*.py that would also claim
        # test material.
        self.assertTrue(
            (_REPO_ROOT / _EXCLUDED_FIXTURE).is_file(),
            "fixture path drifted; update _EXCLUDED_FIXTURE",
        )
        for strategy in _TRIO:
            with self.subTest(strategy=strategy):
                covered = in_scope_files(
                    _entries_for(strategy), [_EXCLUDED_FIXTURE]
                )
                self.assertEqual(
                    covered, set(),
                    f"{strategy} claims {_EXCLUDED_FIXTURE}, a testdata "
                    "fixture; the bounded-widening decision has regressed "
                    "to a recursive glob",
                )

    def test_overlay_entries_share_one_glob_with_no_excludes(self) -> None:
        # python_module and python_callgraph are a declared strategy pair
        # (ADR 0041 § Layer 3): they must visit the same file set or
        # ``wd lint`` reports strategy-pair-consistency violations.
        # python_package rides the same glob so the file anchor gets its
        # inbound contains edge.
        entries = _overlay_scoped_entries()
        shapes = {
            (e.get("glob"), tuple(e.get("exclude") or ())) for e in entries
        }
        self.assertEqual(
            shapes, {("tools/publish_overlays/*.py", ())},
            f"tools/publish_overlays/*.py trio entries must carry the "
            f"identical glob and no excludes; got {sorted(shapes)}",
        )

    def test_package_entry_does_not_collide_with_the_tools_package(self) -> None:
        # The tools/*.py package entry pins an explicit ``package: tools``
        # override. python_package's node dict is a last-write-wins plain
        # assignment keyed by package ID, so folding this directory into
        # that same override would let whichever entry runs last silently
        # overwrite the other's props.dir. The overlay package entry must
        # carry no override, so it mints its own distinct package node.
        package_entries = [
            e for e in _overlay_scoped_entries()
            if e.get("strategy") == "python_package"
        ]
        self.assertEqual(len(package_entries), 1)
        self.assertIsNone(
            package_entries[0].get("package"),
            "tools/publish_overlays/*.py's python_package entry must not "
            "set an explicit `package:` override -- doing so risks "
            "collision with the tools/*.py entry's `package: tools`",
        )

    def test_python_module_and_callgraph_stay_pair_consistent(self) -> None:
        # Reproduces the aggregation weld._graph_strategy_pair applies: the
        # union of every python_module entry's resolved files must equal the
        # union of every python_callgraph entry's, across the whole config
        # -- not just the two entries this change adds.
        module_files: set[str] = set()
        callgraph_files: set[str] = set()
        for entry in _entries_for("python_module"):
            glob = entry.get("glob")
            if not glob:
                continue
            excludes = entry.get("exclude") or []
            module_files.update(
                p.relative_to(_REPO_ROOT).as_posix()
                for p in walk_glob(_REPO_ROOT, str(glob), excludes=excludes)
            )
        for entry in _entries_for("python_callgraph"):
            glob = entry.get("glob")
            if not glob:
                continue
            excludes = entry.get("exclude") or []
            callgraph_files.update(
                p.relative_to(_REPO_ROOT).as_posix()
                for p in walk_glob(_REPO_ROOT, str(glob), excludes=excludes)
            )
        self.assertEqual(
            module_files, callgraph_files,
            "python_module and python_callgraph resolve different file "
            "sets after this change; strategy-pair-consistency would flag "
            "this in `wd lint`",
        )
        self.assertIn(_OVERLAY_FILE, module_files)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

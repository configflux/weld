"""Regression: ``.weld/discover.yaml`` covers every real workflow file, not
only the ones physically under ``.github/workflows/`` (bd lwrh).

``tools/publish_overlays/publish-pypi.yml`` is a complete, standalone GitHub
Actions workflow -- docs/publish.md: overlay files "replace or add their
counterparts in the public repo", and this one becomes
``.github/workflows/publish-pypi.yml`` there. It is not a fragment or a
template; it has its own ``name:``/``on:``/``jobs:``, including the
pre-tag-verify job that runs ``tools/release_claims_lint.py`` before every
PyPI upload. Discovery had no source entry for it, so it minted no
``workflow:`` node at all: ``wd context file:tools/release_claims_lint``
could not show an edge from a node that did not exist, no matter how well a
``run:`` parser read it.

Widening the workflow source to cover ``tools/publish_overlays/*.yml`` makes
a real collision reachable: ``tools/publish_overlays/install-test.yml`` and
``.github/workflows/install-test.yml`` are two different files (different
env, different comments -- confirmed by reading both) that would mint the
identical bare-stem ``workflow:install-test`` under the pre-lwrh ID rule.
``weld._node_ids.workflow_id`` path-qualifies the ID the same way
``tool_id`` did for ``tool:`` (ADR 0106); this file only proves both paths
are resolved by *some* workflow source entry, so a node exists for each --
the ID-collision guarantee itself is
``WorkflowIdTest.test_stem_collision_across_directories_is_removed`` in
``weld_canonical_node_ids_test.py``.

Same shape as ``discover_yaml_tool_script_coverage_test.py``: a strategy's
own unit tests cannot catch a source entry that was never configured, since
they call ``extract()`` directly and pass identically either way.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

_WORKFLOW_TYPE = "workflow"

# The file the originating gap was filed against.
_OVERLAY_WORKFLOW = "tools/publish_overlays/publish-pypi.yml"

# The bare-stem collision the widening makes reachable. Both must resolve,
# and both must be real files, or the regression this file guards would
# silently stop being exercised.
_COLLIDING_STEM_PAIR = (
    ".github/workflows/install-test.yml",
    "tools/publish_overlays/install-test.yml",
)


def _sources() -> list[dict]:
    """Parse the repo's checked-in ``.weld/discover.yaml`` source entries."""
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    return [entry for entry in sources if isinstance(entry, dict)]


def _workflow_entries() -> list[dict]:
    return [e for e in _sources() if e.get("type") == _WORKFLOW_TYPE]


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class DiscoverYamlWorkflowCoverageTest(unittest.TestCase):
    """Workflow source entries must cover every real workflow file."""

    def test_workflow_source_entries_exist(self) -> None:
        self.assertTrue(
            _workflow_entries(),
            "no type: workflow source entry in .weld/discover.yaml",
        )

    def test_publish_overlay_workflow_is_in_scope(self) -> None:
        self.assertTrue(
            (_REPO_ROOT / _OVERLAY_WORKFLOW).is_file(),
            "fixture path drifted; update _OVERLAY_WORKFLOW",
        )
        covered = in_scope_files(_workflow_entries(), [_OVERLAY_WORKFLOW])
        self.assertIn(
            _OVERLAY_WORKFLOW, covered,
            f"{_OVERLAY_WORKFLOW} is not covered by any workflow source "
            "entry, so it mints no workflow: node and can carry no invokes "
            "edge to the scripts its run: steps invoke",
        )

    def test_colliding_stem_pair_are_both_real_and_both_in_scope(self) -> None:
        for rel in _COLLIDING_STEM_PAIR:
            self.assertTrue(
                (_REPO_ROOT / rel).is_file(), f"fixture path drifted: {rel}"
            )
        covered = in_scope_files(_workflow_entries(), list(_COLLIDING_STEM_PAIR))
        self.assertEqual(set(_COLLIDING_STEM_PAIR), covered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

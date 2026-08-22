"""Regression test: weld's own shipped data is in discovery scope (bd q85a).

``weld/languages/*.yaml``, ``weld/templates/*`` and ``weld/docs/*.md`` ship in
the wheel and are named by ``//weld/languages:query_files``,
``//weld/templates`` and ``//weld/docs:docs``. The build system therefore knew
these files existed and the graph did not: ``wd impact
weld/languages/python.yaml`` answered node-not-found for a file the Tier-1
harness, discovery and the query surface all read, and "which targets and
tests consume this file" was unanswerable for weld's own product data.

ADR 0111 rules out the shortcut. A build target's ``srcs`` is a *referrer*,
not a second discovery scope, so minting nodes from the BUILD glob would put
files in ``_graph_anchors.graph_files_with_nodes`` that discovery never read
-- inflating ADR 0101 coverage with claims nothing backs. The fix has to be a
real discovery source per family, which is what this test pins.

Same family as ``discover_yaml_{python,bazel,tool_script}_coverage_test``, and
the same reason: a strategy's own unit tests pass identically whether or not
``discover.yaml`` ever invokes it. The expectation is derived from the tree
and from the BUILD filegroups rather than hard-coded, so the next language
pack or template fails this test instead of starting invisible.

Two properties beyond "is it covered":

* The node **type** per family is asserted, because that was the decision the
  issue was opened to make -- ``config`` for the data weld reads at runtime,
  ``doc`` for the documentation it ships -- and neither needed a new entry in
  ``VALID_NODE_TYPES``.
* The markdown entry's ``id_prefix`` must mirror its directory path. A
  referring strategy guesses the spelling from the path alone
  (``weld.strategies._target_ids.target_ids``) and never sees another entry's
  ``id_prefix``, so a prettier prefix mints nodes ``//weld/docs:docs`` cannot
  reach -- discovered, and still unanswerable for "which target ships this",
  which is the half of the gap that would have looked fixed.

The repo's ``.weld/discover.yaml`` is internal state and is absent from the
published source tree, so the suite skips cleanly when it is not present.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml
from weld.strategies._target_ids import target_ids

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

#: Each shipped-data family: the directory, the glob its BUILD filegroup uses,
#: and the node type the discovery entry must claim.
_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("weld/languages", "*.yaml", "config"),
    ("weld/docs", "*.md", "doc"),
    ("weld/templates", "*", "config"),
)

#: Already anchored by the Python trio, and deliberately not re-claimed: two
#: claimants on one file is the ambiguity the ``weld/tests`` exclude exists to
#: avoid.
_TEMPLATE_PYTHON = ("weld/templates/external_adapter.py",
                    "weld/templates/local_strategy.py")

#: Claimed by the bazel strategy, not by the shipped-data entries.
_BUILD_FILES = ("weld/templates/BUILD.bazel", "weld/languages/BUILD.bazel",
                "weld/docs/BUILD.bazel")

#: Out of scope on purpose: ``//weld/docs:docs`` is ``glob(["*.md"])``, so the
#: ADRs one level down are not part of the shipped filegroup. Widening would
#: put a second ADR corpus beside ``docs/adrs/``.
_NESTED_DOC = "weld/docs/adr/0001-plugin-strategy-architecture.md"


def _sources() -> list[dict]:
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    return [e for e in (data.get("sources") or []) if isinstance(e, dict)]


def _family_files(directory: str, pattern: str) -> set[str]:
    """Files in *directory* matching *pattern*, minus the ones others claim."""
    found = {
        p.relative_to(_REPO_ROOT).as_posix()
        for p in (_REPO_ROOT / directory).glob(pattern)
        if p.is_file() and not p.is_symlink()
    }
    return found - set(_TEMPLATE_PYTHON) - set(_BUILD_FILES)


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class ShippedDataCoverageTest(unittest.TestCase):
    """Every file weld ships as data must resolve to a discovery source."""

    def setUp(self) -> None:
        super().setUp()
        self.sources = _sources()

    def test_every_shipped_data_file_is_in_scope(self) -> None:
        for directory, pattern, _type in _FAMILIES:
            with self.subTest(directory=directory):
                expected = _family_files(directory, pattern)
                # Guards the guard: an empty universe makes the assertion
                # vacuous, which is the state this test exists to detect.
                self.assertTrue(
                    expected,
                    f"found no files under {directory}; the derivation is "
                    "broken or the checkout is incomplete",
                )
                missing = expected - in_scope_files(self.sources, sorted(expected))
                self.assertEqual(
                    missing, set(),
                    f"{sorted(missing)} ship in the wheel and are named in a "
                    "BUILD filegroup, but no discover.yaml entry resolves "
                    "them -- so 'which targets and tests consume this file' "
                    "is unanswerable for weld's own product data",
                )

    def test_each_family_is_claimed_under_the_decided_node_type(self) -> None:
        """The decision the issue was opened to make, pinned per family."""
        for directory, pattern, node_type in _FAMILIES:
            with self.subTest(directory=directory):
                sample = sorted(_family_files(directory, pattern))[0]
                claimants = [
                    e for e in self.sources
                    if in_scope_files([e], [sample])
                ]
                self.assertTrue(claimants, f"nothing claims {sample}")
                self.assertEqual(
                    {e.get("type") for e in claimants}, {node_type},
                    f"{sample} must be a '{node_type}' node: a language pack "
                    "and a scaffolding template are static data weld reads at "
                    "runtime, and weld/docs/*.md is documentation about weld",
                )

    def test_no_new_node_type_was_needed(self) -> None:
        """``config`` and ``doc`` are both already in the closed set."""
        from weld.contract import VALID_NODE_TYPES

        for _directory, _pattern, node_type in _FAMILIES:
            self.assertIn(node_type, VALID_NODE_TYPES)

    def test_the_doc_entry_id_prefix_mirrors_its_directory(self) -> None:
        """Otherwise ``//weld/docs:docs`` cannot reach the nodes it ships."""
        sample = "weld/docs/glossary.md"
        self.assertTrue((_REPO_ROOT / sample).is_file(),
                        "fixture path drifted; update the sample")
        entries = [e for e in self.sources if in_scope_files([e], [sample])]
        self.assertEqual(len(entries), 1)
        minted = f"{entries[0].get('id_prefix')}/{Path(sample).stem}"
        self.assertIn(
            minted, target_ids(sample),
            "the markdown entry mints an id no referring strategy can guess, "
            f"so //weld/docs:docs resolves nothing for {sample}; id_prefix "
            "must be 'doc:' + the directory path",
        )

    def test_the_python_templates_keep_their_single_claimant(self) -> None:
        """Two claimants on one file is what the excludes exist to prevent."""
        for path in _TEMPLATE_PYTHON:
            with self.subTest(path=path):
                self.assertTrue((_REPO_ROOT / path).is_file(),
                                "fixture path drifted; update _TEMPLATE_PYTHON")
                claimants = [
                    e.get("strategy") for e in self.sources
                    if in_scope_files([e], [path])
                ]
                self.assertNotIn(
                    "config_file", claimants,
                    f"{path} is already anchored by the Python trio; claiming "
                    "it as a config: node too gives one file two claimants",
                )

    def test_the_shipped_build_files_stay_with_the_bazel_strategy(self) -> None:
        for path in _BUILD_FILES:
            with self.subTest(path=path):
                self.assertTrue((_REPO_ROOT / path).is_file(),
                                "fixture path drifted; update _BUILD_FILES")
                claimants = [
                    e.get("strategy") for e in self.sources
                    if in_scope_files([e], [path])
                ]
                self.assertNotIn("config_file", claimants)
                self.assertIn("bazel", claimants)

    def test_the_nested_adr_corpus_stays_out_of_scope(self) -> None:
        """Non-recursive on purpose; the filegroup is ``glob(["*.md"])``."""
        self.assertTrue((_REPO_ROOT / _NESTED_DOC).is_file(),
                        "fixture path drifted; update _NESTED_DOC")
        self.assertEqual(
            in_scope_files(self.sources, [_NESTED_DOC]), set(),
            f"{_NESTED_DOC} is in scope; //weld/docs:docs does not ship it, "
            "and a second ADR corpus beside docs/adrs/ is a separate decision",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

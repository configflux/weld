"""Regression: file: IDs must be case-preserved on BOTH fresh and stateful discover.

Symptom that motivated this regression:
A user running tier-1 Java fixtures against pinned corpora (guava, spring-boot)
observed ``file:`` node IDs flipping between an all-lowercased form on a fresh
``wd discover`` and the case-preserved form on a stateful re-discover. The
inconsistency broke F1 gold-query fixtures (gold IDs minted against the second
shape no longer matched IDs emitted from the first shape) and surfaced as test
pollution against ``weld_csharp_test_framework_strategy_test`` whose
``test_contains_edge_to_test_file_is_emitted`` case expects exactly
``file:FooTests`` (not ``file:footests``).

The fix that landed in c67c7c8 routed :func:`weld._node_ids.file_id` through
:func:`canonical_slug_case_sensitive` so file IDs preserve on-disk case. Both
the fresh and stateful discover paths reach the same minter via the strategies'
``_make_node_id`` helpers, so the contract holds end-to-end -- but the bug
report did not include an integration check that pinned this invariant across
the fresh/stateful boundary, which is where the divergence originally surfaced.

This test closes that gap: it runs :func:`weld.discover.discover` twice (once
fresh, once stateful) against a fixture with mixed-case path segments shaped
after the guava ``com/google/common/collect/Lists.java`` layout and asserts
that the resulting ``file:*`` IDs are byte-identical, with case preserved on
every segment. A second case mutates a file between runs so the stateful path
exercises ``purge_stale_nodes`` plus the strategy re-run, then asserts the
case-preservation invariant still holds for modified and newly-added files.

If a future change reintroduces a case-folding step (for example, by routing
``file_id`` through ``canonical_slug`` or by intercepting node IDs through a
post-process pass that lowercases), this test fails with a concrete diff of
the offending IDs before any tier-1 fixture or downstream consumer breaks.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import discover  # noqa: E402


def _build_guava_shaped_fixture(root: Path) -> None:
    """Create a fixture that mirrors the guava layout (mixed-case path segments).

    Reproduces the exact path shape from the bug report:
    ``guava/src/com/google/common/collect/{Lists,Maps}.java``. The
    ``collect`` segment is lowercase (matching upstream) while the file
    stems are CapitalCase, so the fixture exercises both case classes
    in a single path. The Java strategy routes through tree-sitter,
    which is the production path for the pinned corpora that surfaced
    the bug. The strategy degrades gracefully when tree-sitter is not
    installed -- the test only asserts ID equality between two runs,
    so an empty result on both runs would trivially satisfy the
    invariant. To keep the assertion meaningful we additionally include
    a Python module via ``python_module`` (no tree-sitter dependency)
    so at least one strategy always emits ``file:*`` nodes regardless
    of the optional grammar installation.
    """
    # Java tree (production path for tier-1 java corpora; degrades to
    # empty if tree-sitter-java is not installed).
    java_src = root / "guava" / "src" / "com" / "google" / "common" / "collect"
    java_src.mkdir(parents=True)
    (java_src / "Lists.java").write_text(
        "package com.google.common.collect;\n"
        "public class Lists {\n"
        "  public static java.util.List<Object> newArrayList() { return null; }\n"
        "}\n",
        encoding="utf-8",
    )
    (java_src / "Maps.java").write_text(
        "package com.google.common.collect;\n"
        "public class Maps {\n"
        "  public static java.util.Map<Object,Object> newHashMap() { return null; }\n"
        "}\n",
        encoding="utf-8",
    )

    # Python tree (no optional deps; guarantees at least one file: node
    # so the equality assertion is non-vacuous).
    py_src = root / "src" / "MixedCase"
    py_src.mkdir(parents=True)
    (py_src / "__init__.py").write_text("", encoding="utf-8")
    (py_src / "PythonLikeFile.py").write_text(
        "class FooBar:\n    def baz(self):\n        return 1\n",
        encoding="utf-8",
    )

    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n"
        "  - strategy: java\n"
        "    glob: 'guava/src/**/*.java'\n"
        "    language: java\n"
        "  - strategy: python_module\n"
        "    glob: 'src/**/*.py'\n"
        "    type: file\n",
        encoding="utf-8",
    )


def _file_ids(graph: dict) -> list[str]:
    """Return the sorted list of ``file:*`` node IDs from *graph*."""
    return sorted(nid for nid in graph.get("nodes", {}) if nid.startswith("file:"))


class DiscoverFreshAndStatefulFileIdsAgreeTest(unittest.TestCase):
    """Fresh and stateful discover runs must produce identical file IDs."""

    def test_file_ids_match_across_fresh_and_stateful(self) -> None:
        with tempfile.TemporaryDirectory(prefix="case-pres-") as td:
            root = Path(td)
            _build_guava_shaped_fixture(root)

            # Run 1: fresh (no state file). Forces full discovery and
            # writes discovery-state.json + graph.json.
            graph_fresh = discover(root, incremental=False)
            fresh_ids = _file_ids(graph_fresh)

            # Run 2: stateful (reuses the state file written above).
            # When no files changed the incremental path keeps the
            # existing nodes verbatim; both fresh and stateful must
            # therefore agree on the case of every ``file:*`` ID.
            graph_stateful = discover(root, incremental=True)
            stateful_ids = _file_ids(graph_stateful)

            self.assertEqual(
                fresh_ids,
                stateful_ids,
                "file: node IDs must be byte-identical across fresh and "
                "stateful discover runs. "
                "Divergence indicates a case-folding regression on one of the "
                "minting paths.",
            )

    def test_file_ids_preserve_on_disk_case(self) -> None:
        """The IDs themselves must keep the on-disk case (vjxi.6).

        Without this assertion the equality test above would still pass
        in the (broken) world where BOTH paths lowercase consistently.
        The case-preservation contract pins ADR 0041 Layer 1 + c67c7c8.
        """
        with tempfile.TemporaryDirectory(prefix="case-pres-onpath-") as td:
            root = Path(td)
            _build_guava_shaped_fixture(root)
            graph = discover(root, incremental=False)
            ids = _file_ids(graph)
            # At least the Python module node must always emit (no
            # optional grammar required). It exercises mixed case in
            # both directory and stem segments.
            self.assertIn(
                "file:src/MixedCase/PythonLikeFile",
                ids,
                f"Expected case-preserved file ID for Python module; got: {ids}",
            )


class DiscoverStatefulAfterMutationsPreservesCaseTest(unittest.TestCase):
    """Stateful re-discover after edits must keep case for every file ID.

    Mutating a file between runs triggers the ``purge_stale_nodes`` plus
    strategy-re-run branch of the incremental path. That branch is where
    the original bug surfaced: a strategy that rebuilds the node ID from
    the source path could re-lowercase if it stopped routing through
    :func:`weld._node_ids.file_id`. Adding a new file in the same run
    exercises the ``added`` branch of the state diff.
    """

    def test_modified_and_new_files_keep_case(self) -> None:
        with tempfile.TemporaryDirectory(prefix="case-pres-mutate-") as td:
            root = Path(td)
            _build_guava_shaped_fixture(root)

            graph_fresh = discover(root, incremental=False)
            fresh_ids = set(_file_ids(graph_fresh))

            # Modify an existing Python module (triggers purge_stale +
            # strategy re-run for that file's source entry).
            py = root / "src" / "MixedCase" / "PythonLikeFile.py"
            py.write_text(
                "class FooBar:\n"
                "    def baz(self):\n        return 2\n"
                "    def added_method(self):\n        return 3\n",
                encoding="utf-8",
            )

            # Add a brand-new mixed-case file (exercises the ``added``
            # state-diff branch).
            new = root / "src" / "MixedCase" / "NewArrival.py"
            new.write_text(
                "def helper():\n    return 0\n",
                encoding="utf-8",
            )

            graph_stateful = discover(root, incremental=True)
            stateful_ids = set(_file_ids(graph_stateful))

            # Every fresh ID must still resolve under its case-preserved
            # spelling -- nothing should be lowercased on the second run.
            for nid in fresh_ids:
                self.assertIn(
                    nid,
                    stateful_ids,
                    "case-preserved fresh ID disappeared from stateful run "
                    f"(possible re-lowercasing): {nid}",
                )

            # The new file must mint a case-preserved ID, not its lower
            # form.
            self.assertIn(
                "file:src/MixedCase/NewArrival",
                stateful_ids,
                "added file did not mint a case-preserved file ID on the "
                f"incremental path. stateful_ids={sorted(stateful_ids)}",
            )
            self.assertNotIn(
                "file:src/mixedcase/newarrival",
                stateful_ids,
                "incremental discover lowercased a newly-added file's ID; "
                "the case-preserved form is the canonical one (vjxi.6).",
            )


if __name__ == "__main__":
    unittest.main()

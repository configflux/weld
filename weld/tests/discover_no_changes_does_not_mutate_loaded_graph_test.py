"""Regression: the incremental "no-changes" branch must not mutate the dict
loaded from ``graph.json`` in place.

When ``_discover_single_repo`` takes the fast path (no files dirty or deleted),
it reads the existing graph from disk via ``json.loads`` and then needs to
refresh ``meta.updated_at``/``meta.git_sha``. The function used to mutate that
loaded dict in place and return it, which means any caller that still holds a
reference to the originally-loaded object would silently observe the refreshed
fields -- a surprising side effect that also makes the function's return value
alias a value the caller may consider "the previous graph".

This test pins the invariant: the freshly-loaded dict produced by
``json.loads`` must remain byte-identical to the on-disk graph after
``_discover_single_repo`` returns. The returned graph is a separate object
with the refreshed fields applied.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import discover as discover_mod  # noqa: E402
from weld.discover import _discover_single_repo  # noqa: E402


def _build_fixture(root: Path) -> None:
    """Minimal fixture with one source file and one strategy.

    Any fixture that produces at least one node is sufficient; the test
    exercises the no-changes branch, not node content.
    """
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )


class DiscoverNoChangesDoesNotMutateLoadedGraphTest(unittest.TestCase):
    """Regression for the no-changes branch mutation bug."""

    def test_incremental_no_changes_does_not_mutate_loaded_graph(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nochg-mutate-") as td:
            root = Path(td)
            _build_fixture(root)

            # Seed: first run writes state file + graph.json so the
            # second run can hit the incremental code path.
            seed_graph = _discover_single_repo(root, incremental=False)
            # Simulate what the production caller does (e.g. the CLI's
            # ``wd discover`` which persists the graph after discovery).
            graph_path = root / ".weld" / "graph.json"
            # Freeze a distinctive ``updated_at`` on disk so that any
            # refresh on the loaded dict is unambiguously detectable in
            # the equality check below (without depending on sub-second
            # timing differences between the seed and the second run).
            frozen_seed = copy.deepcopy(seed_graph)
            frozen_seed["meta"]["updated_at"] = "2000-01-01T00:00:00+00:00"
            graph_path.write_text(json.dumps(frozen_seed), encoding="utf-8")

            # Snapshot of what ``graph.json`` looks like on disk going into
            # the second call. We will compare the dict that
            # ``json.loads`` produces against this snapshot AFTER the
            # function returns to detect in-place mutation.
            on_disk_before = json.loads(graph_path.read_text(encoding="utf-8"))

            # Spy on ``json.loads``: the single call inside
            # ``_discover_single_repo`` that loads ``graph.json`` produces
            # the dict we care about. We capture that dict so we can
            # verify the function did not mutate it.
            real_json_loads = json.loads
            captured: list[dict] = []

            def spy_loads(s, *args, **kwargs):
                result = real_json_loads(s, *args, **kwargs)
                # Only capture dict results that look like a graph
                # payload (the function also parses hash-state JSON elsewhere).
                if isinstance(result, dict) and "meta" in result and "nodes" in result:
                    captured.append(result)
                return result

            with mock.patch.object(discover_mod.json, "loads", side_effect=spy_loads):
                returned = _discover_single_repo(root, incremental=True)

            # Sanity: the function must have hit the no-changes branch,
            # which is the only place that currently reads + refreshes
            # the graph without re-running strategies. The returned dict
            # should carry a fresh ``updated_at`` that differs from the
            # snapshot (proving we are on the incremental-refresh path).
            self.assertIn("meta", returned)
            self.assertEqual(len(captured), 1,
                             "expected exactly one graph.json load on the "
                             "no-changes incremental path")

            loaded_during_call = captured[0]

            # The core invariant: the dict produced by ``json.loads`` must
            # equal the on-disk snapshot. If the function mutated it in
            # place (e.g., overwrote ``meta.updated_at`` or ``meta.git_sha``
            # on the loaded object), this will fail.
            self.assertEqual(
                loaded_during_call,
                on_disk_before,
                "_discover_single_repo mutated the dict returned by "
                "json.loads; the no-changes branch must not touch the "
                "loaded object and should work on a fresh copy instead.",
            )

            # And the returned graph must be a separate object from the
            # one json.loads produced -- otherwise the caller receives
            # the mutated dict.
            self.assertIsNot(
                returned,
                loaded_during_call,
                "_discover_single_repo returned the same object it got "
                "from json.loads; it should return a fresh copy so that "
                "the caller's view of the loaded bytes is preserved.",
            )

    def test_incremental_no_changes_refreshes_returned_meta(self) -> None:
        """Companion check: the fix must not silently skip the refresh.

        The whole point of the no-changes branch is to stamp a fresh
        ``updated_at`` on the returned graph. After the fix, the returned
        graph's ``meta.updated_at`` must differ from (or at least be
        present and newer than) the on-disk value; it is only the loaded
        dict that must stay pristine.
        """
        with tempfile.TemporaryDirectory(prefix="nochg-refresh-") as td:
            root = Path(td)
            _build_fixture(root)

            seed_graph = _discover_single_repo(root, incremental=False)
            graph_path = root / ".weld" / "graph.json"
            # Freeze a known old ``updated_at`` on disk so we can detect
            # whether the returned graph was refreshed.
            seed_graph_frozen = copy.deepcopy(seed_graph)
            seed_graph_frozen["meta"]["updated_at"] = "2000-01-01T00:00:00+00:00"
            graph_path.write_text(json.dumps(seed_graph_frozen), encoding="utf-8")

            returned = _discover_single_repo(root, incremental=True)

            self.assertIn("meta", returned)
            self.assertIn("updated_at", returned["meta"])
            self.assertNotEqual(
                returned["meta"]["updated_at"],
                "2000-01-01T00:00:00+00:00",
                "no-changes branch must refresh meta.updated_at on the "
                "returned graph",
            )


if __name__ == "__main__":
    unittest.main()

"""``children_status`` is bounded at a federated root (ADR 0082, bd hwwo).

The one *unmeasured, real* breach risk bd hwwo flagged: ``freshness`` costs a
near-constant ~90-130 B, but ``children_status`` (attached by
:func:`weld._mcp_read.attach_children_status` inside ``weld_query`` /
``weld_context`` / ``weld_path``) is one entry per registered child, so it is
unbounded in a workspace's child count. A fixed transport reserve cannot
bound an unbounded map -- it needs the same fit-and-report treatment as any
other droppable bucket (:func:`weld._read_budget.bound_dict_to_budget`).

This module proves both halves:

1. **Red without the cap.** :meth:`FederatedGraph.children_status` on a
   many-child workspace, measured uncapped, exceeds the *entire* read budget
   on its own -- independent of anything else in the envelope, so this is not
   a fixture-tuning artifact.
2. **Green with it.** The real MCP dispatch path -- ``weld_query`` and
   ``weld_context`` -- caps the map, reports the omitted count, and the total
   dispatched payload fits :data:`weld._read_budget.DEFAULT_READ_BUDGET_BYTES`
   regardless.

A small-workspace case pins the no-op path: with few enough children to fit
inside the reserve outright, nothing is omitted -- this is the same shape
:mod:`weld.tests.weld_mcp_federation_test` already pins for the four-sentinel
fixture, restated here as a boundary case of the same mechanism rather than a
duplicate of that file's coverage.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._mcp_dispatch import dispatch as _dispatch
from weld._read_budget import CHILDREN_STATUS_RESERVE_BYTES, DEFAULT_READ_BUDGET_BYTES, envelope_bytes
from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph
from weld.mcp_server import build_tools
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

# _mcp_dispatch.dispatch now takes the live tool registry as an explicit
# tools_provider parameter (ADR 0130 disposition #7); see
# weld_dispatched_budget_test.py's identical comment for the full rationale.
dispatch = partial(_dispatch, tools_provider=build_tools)

_TS = "2026-08-20T00:00:00+00:00"

#: All-``missing`` children (never created on disk) so a many-child fixture
#: costs one root ``git init`` and zero per-child subprocess calls -- fast
#: and deterministic, and it exercises the exact code path a present/corrupt
#: child would (``FederatedGraph.children_status`` classifies "missing"
#: before it ever touches git). See ``weld_mcp_federation_test`` for the
#: mixed-sentinel-state coverage this module deliberately does not repeat.
_MANY = 700


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
    )


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    (root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _write_graph(root: Path, nodes: dict) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps({
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
            "nodes": nodes, "edges": [],
        }),
        encoding="utf-8",
    )


def _build_workspace(root: Path, n_children: int) -> None:
    _init_repo(root)
    children = [
        ChildEntry(name=f"service-{i:04d}", path=f"service-{i:04d}")
        for i in range(n_children)
    ]
    _write_graph(root, {"entity:Seed": {"type": "entity", "label": "Seed", "props": {}}})
    dump_workspaces_yaml(
        WorkspaceConfig(children=children, cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml",
    )


#: Present children needed to reliably exceed CHILDREN_STATUS_RESERVE_BYTES
#: (ADR 0082 amendment 2026-08-20 measured ~20-35 "present" entries fill the
#: 3,840 B reserve on this repo's serialization) -- comfortably past that
#: with margin for CI variance.
_PRESENT_TO_FILL_RESERVE = 60


def _build_mixed_workspace(root: Path, n_present: int, corrupt_name: str) -> None:
    """*n_present* present children plus one corrupt child named *corrupt_name*.

    A ``.git`` marker is a bare directory rather than a real repo (no
    subprocess per child, matching ``_build_workspace``'s all-missing
    fixture's speed rationale) -- ``FederatedGraph``'s sentinel check only
    asks whether the path exists, and ``probe_child_status`` (bd sk3c)
    classifies presence from ``graph.json`` alone, never real git state.
    *corrupt_name* is the caller's job to make sort alphabetically last
    among every registered name; this only builds what it is told to.
    """
    _init_repo(root)
    children = []
    for i in range(n_present):
        name = f"service-{i:04d}"
        child_root = root / name
        (child_root / ".git").mkdir(parents=True)
        (child_root / ".weld").mkdir()
        (child_root / ".weld" / "graph.json").write_text(
            json.dumps({
                "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
                "nodes": {}, "edges": [],
            }),
            encoding="utf-8",
        )
        children.append(ChildEntry(name=name, path=name))
    corrupt_root = root / corrupt_name
    (corrupt_root / ".git").mkdir(parents=True)
    (corrupt_root / ".weld").mkdir()
    (corrupt_root / ".weld" / "graph.json").write_text("{bad json\n", encoding="utf-8")
    children.append(ChildEntry(name=corrupt_name, path=corrupt_name))

    _write_graph(root, {"entity:Seed": {"type": "entity", "label": "Seed", "props": {}}})
    dump_workspaces_yaml(
        WorkspaceConfig(children=children, cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml",
    )


class ManyChildrenBreachWithoutTheCapTest(unittest.TestCase):
    """The uncapped map alone can exceed the whole budget (the bug, isolated)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_workspace(self.root, _MANY)

    def test_raw_children_status_alone_exceeds_the_entire_budget(self) -> None:
        raw = FederatedGraph(self.root).children_status()
        raw_bytes = envelope_bytes({"children_status": raw})
        self.assertEqual(len(raw), _MANY)
        self.assertGreater(
            raw_bytes, DEFAULT_READ_BUDGET_BYTES,
            "fixture must reproduce the breach bd hwwo flagged as unmeasured, "
            "or this pins nothing",
        )


class ManyChildrenFitTheBudgetOnceDispatchedTest(unittest.TestCase):
    """The real dispatch path caps the map and stays inside the contract."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_workspace(self.root, _MANY)
        import os
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore(self) -> None:
        import os
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def test_weld_query_children_status_is_capped_and_fits(self) -> None:
        result = dispatch(
            "weld_query", {"term": "Seed", "limit": 20}, root=self.root,
        )
        self._assert_capped_and_fits(result)

    def test_weld_context_children_status_is_capped_and_fits(self) -> None:
        result = dispatch(
            "weld_context", {"node_id": "entity:Seed"}, root=self.root,
        )
        self._assert_capped_and_fits(result)

    def test_weld_path_children_status_is_capped_and_fits(self) -> None:
        # weld_path shares attach_children_status verbatim with query/context
        # (mcp_server.py); a trivial zero-length path is enough to prove the
        # same capping engages here too, not just on the two surfaces above.
        result = dispatch(
            "weld_path",
            {"from_id": "entity:Seed", "to_id": "entity:Seed"},
            root=self.root,
        )
        self._assert_capped_and_fits(result)

    def _assert_capped_and_fits(self, result: dict) -> None:
        kept = result["children_status"]
        omitted = result["children_status_omitted"]
        self.assertLess(len(kept), _MANY, "the cap must have dropped entries")
        self.assertEqual(
            len(kept) + omitted, _MANY,
            "no silent truncation: kept + omitted must account for every child",
        )
        self.assertGreater(omitted, 0)
        self.assertLessEqual(
            envelope_bytes({"children_status": kept}), CHILDREN_STATUS_RESERVE_BYTES,
        )
        self.assertIn("freshness", result)
        self.assertLessEqual(envelope_bytes(result), DEFAULT_READ_BUDGET_BYTES)
        # Deterministic prefix (ADR 0012): the survivors are the
        # alphabetically-first names, matching children_status()'s own order.
        self.assertEqual(
            list(kept), sorted(f"service-{i:04d}" for i in range(_MANY))[: len(kept)],
        )

    def test_deterministic_across_repeated_dispatch(self) -> None:
        first = dispatch("weld_query", {"term": "Seed", "limit": 20}, root=self.root)
        second = dispatch("weld_query", {"term": "Seed", "limit": 20}, root=self.root)
        first.pop("freshness")
        second.pop("freshness")
        self.assertEqual(first, second)


class FewChildrenAreNeverOmittedTest(unittest.TestCase):
    """The boundary case: a workspace small enough needs no capping at all."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_workspace(self.root, 3)
        import os
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore(self) -> None:
        import os
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def test_children_status_omitted_is_zero_and_always_present(self) -> None:
        result = dispatch(
            "weld_query", {"term": "Seed", "limit": 20}, root=self.root,
        )
        self.assertEqual(len(result["children_status"]), 3)
        self.assertEqual(result["children_status_omitted"], 0)


class PriorityOrderingSurvivesTheCapTest(unittest.TestCase):
    """bd sk3c: a corrupt child sorting alphabetically last must survive the
    cap -- the fixture the ADR 0082 amendment (2026-08-20) flagged as a gap.

    Plain alphabetical order (pre-sk3c) drops
    :func:`~weld._read_budget.bound_dict_to_budget`'s *tail*; a corrupt
    child named to sort after every ``service-NNNN`` present child would be
    exactly what gets dropped once enough present children fill the
    reserve. :meth:`~weld.federation.FederatedGraph.children_status` now
    sorts non-present states first (bd sk3c), so the corrupt entry is near
    the *front* of what ``bound_dict_to_budget`` sees and survives even
    though its name alone would place it last.
    """

    _CORRUPT_NAME = "zzz-corrupt-service"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_mixed_workspace(
            self.root, _PRESENT_TO_FILL_RESERVE, self._CORRUPT_NAME,
        )
        import os
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore(self) -> None:
        import os
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def test_fixture_actually_exercises_the_cap(self) -> None:
        # Sanity check the fixture is meaningful, not vacuously passing:
        # the uncapped map must exceed the reserve, and it must sort
        # alphabetically last among every registered name (the shape that
        # broke under plain alphabetical order).
        raw = FederatedGraph(self.root).children_status()
        self.assertGreater(
            envelope_bytes({"children_status": raw}), CHILDREN_STATUS_RESERVE_BYTES,
        )
        self.assertEqual(sorted(raw)[-1], self._CORRUPT_NAME)

    def test_corrupt_child_survives_the_cap_despite_sorting_last(self) -> None:
        result = dispatch(
            "weld_query", {"term": "Seed", "limit": 20}, root=self.root,
        )
        kept = result["children_status"]
        self.assertGreater(
            result["children_status_omitted"], 0, "fixture must exercise the cap",
        )
        self.assertIn(
            self._CORRUPT_NAME, kept,
            "a corrupt child sorting alphabetically last must not be silently "
            "dropped by the size cap",
        )
        self.assertEqual(kept[self._CORRUPT_NAME]["status"], "corrupt")
        # Priority-ordered: the corrupt entry leads (non-present class
        # sorts first), so it is not merely present but present *first*.
        self.assertEqual(next(iter(kept)), self._CORRUPT_NAME)

    def test_weld_context_and_weld_path_agree_with_weld_query(self) -> None:
        """CLI/MCP parity: every attach_children_status call site (bd sk3c) --
        weld_query, weld_context, weld_path -- shares one children_status()
        implementation, so all three must show the corrupt entry surviving
        in the same priority-first position."""
        query_kept = dispatch(
            "weld_query", {"term": "Seed", "limit": 20}, root=self.root,
        )["children_status"]
        context_kept = dispatch(
            "weld_context", {"node_id": "entity:Seed"}, root=self.root,
        )["children_status"]
        path_kept = dispatch(
            "weld_path",
            {"from_id": "entity:Seed", "to_id": "entity:Seed"},
            root=self.root,
        )["children_status"]
        for surface_name, kept in (
            ("weld_query", query_kept),
            ("weld_context", context_kept),
            ("weld_path", path_kept),
        ):
            self.assertIn(self._CORRUPT_NAME, kept, surface_name)
            self.assertEqual(next(iter(kept)), self._CORRUPT_NAME, surface_name)
        self.assertEqual(list(query_kept), list(context_kept))
        self.assertEqual(list(query_kept), list(path_kept))


if __name__ == "__main__":
    unittest.main()

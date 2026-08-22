"""Provenance-keyed edge purge keeps three more unstamped inbound edge kinds.

bd 57lra: ``concept_from_bd``'s ``relates_to`` edges and ``tool_script``'s /
``yaml_meta``'s ``invokes`` edges carried no ``props.provenance.file``. When
the edge's TARGET file was dirtied, ``purge_stale_nodes`` purged the target's
node into ``removed_ids`` in the same pass that later re-mints it; with no
provenance to judge these edges by, ``purge_edges_by_provenance`` fell
through to the conservative endpoint-membership floor and dropped them
immediately -- *before* the target's own strategy re-ran and put its node
back under the same id. Because each producer's own source (the bd issue
store, the referring shell script, the referring workflow YAML) stayed
clean, ``_merge_once``'s per-source dirty-intersect gate never re-ran it, so
the edge was never re-minted either. A fresh full discover of the identical
tree has no such gap, since every strategy re-runs unconditionally.

This is a different mechanism from ADR 0074's fourth amendment (bd znzu):
that widen-and-retry only rescues an edge that already *survived* the
initial purge by provenance and then dangled because its endpoint was never
re-minted. These edges never survived the initial purge at all -- they carry
no provenance to survive by -- so ``orphaned_producer_files`` never even
sees them.

The fix is the same opt-in ``test_peer`` (bd heum) and ``bazel`` (bd cpkp)
already took: each strategy stamps ``provenance.file`` naming the file whose
extraction produced the edge, never the target endpoint. All three land
together here because the reporting issue's own repro measured all three
losses from one dirtied file in one pass (six named edges on the real repo,
reduced to one shared minimal fixture): a target file owned by
``python_module``, cited by a dogfood-gap issue, invoked by a shell script,
and invoked by a CI workflow's ``run:`` step.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

#: The shared target every producer below points at, and its node id under
#: ``python_module`` (the strategy that owns and re-mints it when dirty).
_TARGET = "lib/thing.py"
_TARGET_NODE = "file:lib/thing"

#: The three clean producers, each in a source entry disjoint from the
#: target's own glob -- the whole defect requires that disjointness, since a
#: dirty target inside a producer's own glob would force a re-run that masks
#: the gap (see ``incremental_markdown_provenance_purge_test`` for the case
#: where that masking makes a predicted loss NOT reproduce).
#:
#: Deliberately a bare filename, not the real deployment's internal issue-
#: store directory -- the strategy reads whatever path ``source['path']``
#: names (see ``weld_concept_from_bd_strategy_test.py``'s
#: ``_TEST_ISSUES_REL``, which takes the same neutral approach), and the
#: production literal in a test fixture trips the publish-audit
#: danger-pattern scan for an internal-source-only reference.
_ISSUES = "issues.jsonl"
_CALLER = "tools/caller.sh"
_WORKFLOW = ".github/workflows/ci.yml"

_CONCEPT_EDGE = ("concept:widget-gap", "relates_to", _TARGET_NODE)
_TOOL_EDGE = ("tool:tools/caller", "invokes", _TARGET_NODE)
_WORKFLOW_EDGE = ("workflow:.github/workflows/ci", "invokes", _TARGET_NODE)
_INBOUND_EDGES = (_CONCEPT_EDGE, _TOOL_EDGE, _WORKFLOW_EDGE)

#: Producing file for each edge above, in the same order -- the direction
#: the provenance stamp must name (never the target endpoint).
_PRODUCER_BY_EDGE = {
    _CONCEPT_EDGE: _ISSUES,
    _TOOL_EDGE: _CALLER,
    _WORKFLOW_EDGE: _WORKFLOW,
}


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _strip_meta(graph: dict) -> dict:
    """Drop volatile + order-volatile meta; nodes/edges must match exactly."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def _edge_provenance(graph: dict, edge: tuple[str, str, str]) -> object:
    """Return the fixture edge's ``provenance`` prop, or a legible sentinel.

    The sentinel keeps a missing edge from failing this assertion with an
    ``AttributeError`` on ``None`` -- it fails the equality instead, with
    the edge identified.
    """
    from_id, etype, to_id = edge
    for e in graph.get("edges", []):
        if e.get("from") == from_id and e.get("type") == etype and e.get("to") == to_id:
            return (e.get("props") or {}).get("provenance")
    return "<no edge>"


def _write_fixture(
    root: Path,
    *,
    target_value: int = 1,
    delete_target: bool = False,
    issues_extra: bool = False,
    caller_extra: bool = False,
    workflow_extra: bool = False,
) -> None:
    """Lay down the shared target plus its three independent producers.

    Each producer lives under its own source entry (disjoint glob/path from
    ``lib/*.py``), so dirtying the target alone never puts a producer's own
    file in the dirty set -- the exact shape the issue's repro needs.
    """
    lib = root / "lib"
    lib.mkdir(exist_ok=True)
    target = lib / "thing.py"
    if delete_target:
        if target.exists():
            target.unlink()
    else:
        target.write_text(f"VALUE = {target_value}\n", encoding="utf-8")

    lines = [json.dumps({
        "id": "x-0001",
        "title": "widget gap",
        "status": "open",
        "labels": ["weld-dogfood-gap"],
        "description": f"See {_TARGET} for details.",
    })]
    if issues_extra:
        # A second, closed (filtered-out) issue: dirties the file's bytes
        # without changing what the strategy extracts, so this is a pure
        # "clean re-mint, identical output" edit.
        lines.append(json.dumps({
            "id": "x-0002",
            "title": "unrelated closed gap",
            "status": "closed",
            "labels": ["weld-dogfood-gap"],
            "description": "nothing to see",
        }))
    (root / _ISSUES).write_text("\n".join(lines) + "\n", encoding="utf-8")

    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    caller_body = f"#!/bin/bash\npython3 {_TARGET}\n"
    if caller_extra:
        caller_body += "# noop\n"
    (tools / "caller.sh").write_text(caller_body, encoding="utf-8")

    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    workflow_body = (
        "name: CI\n"
        "on:\n"
        "  push:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - run: python3 {_TARGET}\n"
    )
    if workflow_extra:
        workflow_body += "# noop\n"
    (workflows / "ci.yml").write_text(workflow_body, encoding="utf-8")

    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: lib/*.py\n    type: file\n    strategy: python_module\n"
        f'  - path: "{_ISSUES}"\n    type: concept\n'
        "    strategy: concept_from_bd\n"
        "  - glob: tools/*.sh\n    type: tool\n    strategy: tool_script\n"
        '  - glob: ".github/workflows/*.yml"\n    type: workflow\n'
        "    strategy: yaml_meta\n",
        encoding="utf-8",
    )


def _seed_then_edit(**kw: object) -> dict:
    """Full-discover the ``target_value=1`` state, apply the edit, refresh."""
    with tempfile.TemporaryDirectory(prefix="inbound-prov-inc-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_at(**kw: object) -> dict:
    """Full-discover a clean checkout of the post-edit state."""
    with tempfile.TemporaryDirectory(prefix="inbound-prov-full-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class DirtyTargetKeepsInboundEdgesTest(unittest.TestCase):
    """bd 57lra's own repro: dirty ONLY the shared target; producers stay clean."""

    def test_inbound_edges_survive_and_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit(target_value=2)
        g_full = _full_at(target_value=2)

        inc_edges = _edge_set(g_inc)
        for edge in _INBOUND_EDGES:
            self.assertIn(
                edge, inc_edges,
                f"inbound edge {edge} into the dirtied target was purged "
                "and never re-minted -- ADR 0074 provenance regression "
                "(the producer's own source stayed clean, so it never "
                "re-ran to re-mint it)",
            )
        self.assertEqual(inc_edges, _edge_set(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )

    def test_each_edge_names_its_producing_file_as_provenance(self) -> None:
        """Direction pin: provenance is always the producer, never the target.

        Stamping the target instead would be exactly as broken as stamping
        nothing -- the target is stale in precisely the case retention has
        to survive.
        """
        for label, graph in (("full", _full_at(target_value=2)),
                             ("incremental", _seed_then_edit(target_value=2))):
            for edge in _INBOUND_EDGES:
                with self.subTest(path=label, edge=edge):
                    self.assertEqual(
                        _edge_provenance(graph, edge),
                        {"file": _PRODUCER_BY_EDGE[edge]},
                        f"{edge} must name its producing file as "
                        "props.provenance.file",
                    )


class DirtyProducerMatchesFullTest(unittest.TestCase):
    """Editing a producer (target untouched) must still match full.

    The safety net for the fix: these edges are now purged by provenance
    instead of endpoint membership, so a dirty PRODUCER must still purge and
    re-mint correctly under the new rule. Each producer's own glob/path
    holds the dirty file, so it re-runs and puts back exactly one edge --
    unaffected by the fix, pinned so it cannot regress.
    """

    def test_every_producer_edit_matches_full(self) -> None:
        for kw in (
            {"issues_extra": True},
            {"caller_extra": True},
            {"workflow_extra": True},
        ):
            with self.subTest(**kw):
                g_inc = _seed_then_edit(**kw)
                g_full = _full_at(**kw)
                self.assertEqual(_edge_set(g_inc), _edge_set(g_full))
                self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))


class DeletedTargetDropsInboundEdgesTest(unittest.TestCase):
    """Provenance survival must not resurrect a genuinely dangling edge.

    A deleted target is not itself dirty content -- no producer's source
    changed either -- so provenance keeps all three edges through the purge
    even though the target node is gone; the post-process dangling-edge
    filter, not endpoint membership, has to be what drops them. A full
    discover never emits them at all, since the target no longer resolves.
    """

    def test_deleted_target_matches_full(self) -> None:
        g_inc = _seed_then_edit(delete_target=True)
        g_full = _full_at(delete_target=True)

        self.assertNotIn(
            _TARGET_NODE, g_inc.get("nodes", {}),
            "deleted target must not survive incrementally",
        )
        inc_edges = _edge_set(g_inc)
        for edge in _INBOUND_EDGES:
            self.assertNotIn(
                edge, inc_edges,
                "provenance survival must not resurrect an inbound edge "
                "whose target was genuinely deleted",
            )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleted-target incremental graph must match a full discover",
        )


if __name__ == "__main__":
    unittest.main()

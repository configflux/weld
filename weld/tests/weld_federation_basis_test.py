"""A federated root's freshness basis, and the verdicts that name theirs.

The unit half of field-eval finding M1 (ADR 0141 D1, bd lcq0c.3). The e2e
probe in ``weld_field_eval_v0250_e2e_test`` drives the evaluator's own
sequence through the real CLI; these cases pin the three mechanisms it rides
on, each of which can be wrong in a way that still lets that one sequence
pass:

* **Discovery feeds the basis.** A federated discover that publishes the root
  graph records the ``workspaces.yaml`` content it read, and records it only
  when it published -- a run whose graph went elsewhere has nothing to vouch
  for and must not stamp the root as though it had.
* **The gate stops asking a scope question about a path it can answer.**
  ``dirty_sources_diverge`` consulted ``discover.yaml`` unconditionally, so a
  root without one condemned every dirty path including a file the inventory
  held an exact hash for. Narrowing that is what makes the recorded basis
  usable at all; the conservative half -- an *unrecorded* dirty file with no
  sources to judge it -- must survive intact, and has its own case here.
* **A verdict carries a basis.** Where the gate still condemns on an
  inventory it does not have, the payload says so. Asserted through
  ``assert_stale_verdict_names_its_basis`` -- the same invariant the M1 probe
  applies -- rather than by matching the string, so this cannot pass by
  agreeing with itself about spelling.

The freshness direction that matters most is the one no repro exercises:
these must not buy freshness with a false-fresh. Every "reports fresh" case
below is paired with the edit that must still report stale, and the pair is
what the assertions are for.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._staleness import UNVOUCHED_SOURCES_REASON, compute_stale_info
from weld._staleness_coverage import coverage_stale
from weld._staleness_worktree import (
    dirty_sources_diverge,
    dirty_sources_diverge_detail,
)
from weld._stale_reasons import CONTENT_DIFFERS
from weld.contract import SCHEMA_VERSION
from weld.discover import discover
from weld.discovery_state import DiscoveryState, load_state, save_state
from weld.tests._federation_staleness_fixtures import (
    _git,
    _init_repo,
    _seed_root,
    _write_child_graph,
)
from weld.tests._staleness_invariants import assert_stale_verdict_names_its_basis
from weld.workspace import ChildEntry

#: The root's sole discovery input -- the file ``build_root_meta_graph``
#: names in ``meta.discovered_from`` and therefore the only content a
#: federated run can vouch for.
_REGISTRY = ".weld/workspaces.yaml"


def _child(root: Path, name: str) -> ChildEntry:
    """A committed, discovered child repo -- the shape ``_seed_root`` wants."""
    repo = _init_repo(root / name)
    _write_child_graph(repo)
    return ChildEntry(name=name, path=name)


class FederationBasisRecordingTest(unittest.TestCase):
    """What a federated discover leaves behind, and when it leaves it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "ws"
        self.root.mkdir(parents=True)
        self.children = [_child(self.root, "alpha")]
        _seed_root(self.root, self.children)

    def _stale(self) -> dict:
        graph_path = self.root / ".weld" / "graph.json"
        meta = json.loads(graph_path.read_text(encoding="utf-8"))["meta"]
        # ``git_sha`` is volatile meta and lives in the ADR 0065 sidecar, so
        # the real read path merges it back in; do the same rather than
        # asserting against a graph whose recorded SHA is missing.
        sidecar = self.root / ".weld" / "graph-meta.json"
        if sidecar.is_file():
            meta.update(json.loads(sidecar.read_text(encoding="utf-8")))
        return compute_stale_info(graph_path, meta)

    def _edit_registry(self, suffix: str = "\n# wired\n") -> None:
        registry = self.root / _REGISTRY
        registry.write_text(
            registry.read_text(encoding="utf-8") + suffix, encoding="utf-8"
        )

    def test_the_published_root_records_the_registry_it_read(self) -> None:
        state = load_state(self.root)
        self.assertIsNotNone(state, "a published federated root recorded nothing")
        self.assertEqual(
            sorted(state.files), [_REGISTRY],
            "the recorded basis is not the manifest the root declares",
        )
        # The hash is of the file, not of something that merely round-trips.
        from weld.discovery_state import compute_hash

        self.assertEqual(
            state.files[_REGISTRY], compute_hash(self.root / _REGISTRY)
        )

    def test_the_recorded_basis_vouches_for_the_graph_beside_it(self) -> None:
        """An inventory that names no graph is coverage doubt, not a fix.

        ``save_state_for_graph`` is reached with ``graph_published=True``
        here; get that wrong and the root swaps a nameless working-tree
        verdict for a nameless coverage one, which the M1 e2e probe would
        not tell apart from a fix.
        """
        self.assertFalse(
            coverage_stale(self.root),
            "the root reports coverage doubt against the basis it just wrote",
        )
        self.assertFalse(self._stale()["stale"], "a freshly discovered root is stale")

    def test_an_uncommitted_registry_edit_settles_on_rediscover(self) -> None:
        """M1's sequence: edit, discover, ask -- fresh, and by content.

        The edit is never committed, so ``git status`` reports the registry
        dirty for the whole test. That is the input, not a detail: dirtiness
        is a fact about HEAD that no discovery run can change, which is why
        the answer has to come from content the run recorded.
        """
        self._edit_registry()
        self.assertTrue(
            self._stale()["stale"], "an unrecorded registry edit reads fresh"
        )

        discover(self.root, incremental=False, write_root_graph=True)

        after = self._stale()
        assert_stale_verdict_names_its_basis(after, where="rediscovered")
        self.assertFalse(
            after["stale"],
            f"the root is stale immediately after the discover that reads "
            f"the file it blames: {after}",
        )

    def test_an_unrediscovered_registry_edit_is_stale_and_names_the_file(self) -> None:
        """The direction that must not be bought: no false-fresh.

        Recording a basis is only sound while the basis is still compared
        against. An edit the graph has not read must stay stale *and* name
        the registry, or the fix has traded an unactionable verdict for a
        wrong one.
        """
        self._edit_registry()

        info = self._stale()
        assert_stale_verdict_names_its_basis(info, where="registry edited")
        self.assertTrue(info["stale"], info)
        self.assertEqual(
            info["stale_sources"],
            [{"path": _REGISTRY, "reason": CONTENT_DIFFERS}],
            info,
        )

    def test_a_root_that_also_carries_discover_yaml_is_not_coverage_stale(self) -> None:
        """The dimension recording a basis could have re-opened M1 in.

        ``files_missing_from_inventory`` asks which files ``discover.yaml``
        resolves that no run ingested -- a single-repo question. A federated
        discover reads only the registry, so at a root carrying both files
        every source below it would answer "never ingested" against the
        one-entry inventory, and the root would be permanently coverage-stale
        with ``wd discover`` unable to help: M1 again, one signal over. The
        guard is invisible without an inventory, which is why it needs a case
        of its own rather than riding the others.
        """
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources:\n  - glob: 'src/**/*.py'\n    type: file\n    strategy: python\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "root sources")

        discover(self.root, incremental=False, write_root_graph=True)

        self.assertFalse(
            coverage_stale(self.root),
            "the federated root reports its own tree as never-ingested",
        )
        self.assertFalse(self._stale()["stale"], self._stale())

    def test_a_run_that_publishes_no_graph_records_nothing(self) -> None:
        """``--output`` elsewhere leaves the readable graph untouched.

        So it has nothing to vouch for, and stamping an inventory for it
        would mark the root stale (or worse, fresh) on the strength of a run
        no reader can see -- the same line ``mark_state_published`` draws on
        the single-repo path.
        """
        before = load_state(self.root)
        elsewhere = Path(self._tmp.name) / "elsewhere.json"

        discover(self.root, incremental=False, output=elsewhere)

        self.assertTrue(elsewhere.is_file(), "the run wrote no graph at all")
        after = load_state(self.root)
        self.assertIsNotNone(after)
        self.assertEqual(after.files, before.files)
        self.assertEqual(after.published_graph, before.published_graph)


class NoSourcesEscapeTest(unittest.TestCase):
    """``dirty_sources_diverge`` and its detail, at a root with no sources.

    Driven directly rather than through ``compute_stale_info``: the gate and
    the detail are the pair that must not disagree, and the composer's other
    three signals would mask which of them answered.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()
        self.tracked = self.root / "input.yaml"
        self.tracked.write_text("a: 1\n", encoding="utf-8")

    def _record(self, *rels: str) -> None:
        from weld.discovery_state import compute_hash

        save_state(
            self.root,
            DiscoveryState(
                files={rel: compute_hash(self.root / rel) for rel in rels},
                files_with_no_nodes=set(rels),
            ),
        )

    def test_a_recorded_unchanged_path_needs_no_scope_question(self) -> None:
        """The M1 escape: no ``discover.yaml``, and nothing to ask it.

        The inventory holds this path's hash and the hash still matches, so
        whether some source glob would resolve it has no bearing on the
        answer. Loading sources first is what made a federation root condemn
        its own registry.
        """
        self._record("input.yaml")

        self.assertFalse(dirty_sources_diverge(self.root, ["input.yaml"]))
        self.assertEqual(dirty_sources_diverge_detail(self.root, ["input.yaml"]), [])

    def test_a_recorded_changed_path_diverges_and_is_named(self) -> None:
        self._record("input.yaml")
        self.tracked.write_text("a: 2\n", encoding="utf-8")

        self.assertTrue(dirty_sources_diverge(self.root, ["input.yaml"]))
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["input.yaml"]),
            [{"path": "input.yaml", "reason": CONTENT_DIFFERS}],
        )

    def test_an_unrecorded_path_with_no_sources_stays_conservative(self) -> None:
        """The half that must not move: undecidable still reads stale.

        Something is dirty, on disk, and absent from the inventory, and with
        no configured sources nothing here can say whether discovery would
        read it. The gate condemns; the detail still reports every path it
        *can* speak for, instead of discarding those along with the one it
        cannot.
        """
        self._record("input.yaml")
        self.tracked.write_text("a: 2\n", encoding="utf-8")
        (self.root / "new.yaml").write_text("b: 1\n", encoding="utf-8")

        dirty = ["input.yaml", "new.yaml"]
        self.assertTrue(dirty_sources_diverge(self.root, dirty))
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, dirty),
            [{"path": "input.yaml", "reason": CONTENT_DIFFERS}],
        )


class VerdictNamesItsBasisTest(unittest.TestCase):
    """A stale verdict the working-tree gate produced always says why.

    The residual undecidable state -- a graph on disk whose inventory is
    absent entirely -- is reachable outside federation (an older graph, a
    Mode B clone whose gitignored sidecar never travelled), and it produced
    exactly M1's payload: stale, empty ``stale_sources``, every other field
    reassuring. Recording the federated basis removes the federated route
    into it; this is the guard for the rest.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _init_repo(Path(self._tmp.name))
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "sources")
        self.head = _git(self.root, "rev-parse", "HEAD")
        (self.root / ".weld").mkdir(exist_ok=True)

    def _info(self) -> dict:
        graph_path = self.root / ".weld" / "graph.json"
        meta = {
            "version": SCHEMA_VERSION,
            "git_sha": self.head,
            "discovered_from": ["src/"],
        }
        graph_path.write_text(
            json.dumps({"meta": meta, "nodes": {}, "edges": []}), encoding="utf-8"
        )
        return compute_stale_info(graph_path, meta)

    def test_a_gate_with_no_inventory_names_the_doubt_itself(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")

        info = self._info()

        self.assertTrue(info["stale"], info)
        # Still no file-level blame: there is no inventory to derive one from,
        # and ADR 0141 rules out minting a path to satisfy the letter.
        self.assertEqual(info["stale_sources"], [], info)
        self.assertEqual(info["reason"], UNVOUCHED_SOURCES_REASON, info)
        # The contract, from the product's own invariant rather than restated.
        assert_stale_verdict_names_its_basis(info, where="no inventory")

    def test_a_fresh_verdict_gains_no_reason_key(self) -> None:
        """The payload shape is unchanged everywhere it had a basis already.

        ``reason`` is absent on every state that can point at something, so a
        consumer branching on the key still meets only the states with
        nothing else to say (``seed_block_detail`` is one).
        """
        info = self._info()

        self.assertFalse(info["stale"], info)
        self.assertNotIn("reason", info, info)


if __name__ == "__main__":
    unittest.main()

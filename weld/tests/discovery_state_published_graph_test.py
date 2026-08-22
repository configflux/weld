"""The inventory names the graph it published, and checks it (bd wq9i).

``discovery-state.json`` records what a discovery run resolved. That is
evidence about the graph a reader loads only when the same run published it
**and** the body still on disk is the one it published. ADR 0101's second
amendment recorded the first half as a boolean; this pins the second, which a
boolean cannot express: it says *a* graph was published, never that this is
that graph.

The distinction is not academic. Two situations look identical to a boolean
and must resolve opposite ways:

* gate 5 lands a sibling's graph **bytes verbatim** beside the inventory that
  describes them -- a new inode, the same graph. Refusing it would make every
  fresh worktree pay the cold full discover seeding exists to avoid.
* gate 5 also *keeps* a state file already present at the destination, so a
  worktree that lost only its ``graph.json`` gets a **foreign** body landed
  beside a state that vouches for its own. Accepting that serves another
  checkout's content stamped with our HEAD.

Content is what separates them, so the token carries a digest; the stat pair
beside it is what keeps the common answer off a multi-megabyte read. These
tests pin the matrix directly -- the integration and end-to-end halves live in
``discover_unproven_state_forces_full_test`` and
``weld_worktree_seed_replaced_body_test``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import _discover_state_check as check_mod
from weld._discover_state_check import (
    mark_state_published,
    published_graph_token,
    save_state_for_graph,
    state_vouches_for_graph,
)
from weld.discovery_state import DiscoveryState, load_state, save_state

#: Two graph bodies of *equal* length. A size comparison cannot tell them
#: apart, which is precisely why the token carries a digest as well.
#:
#: Both anchor the ``a.py`` these tests' inventories record. That is what a
#: real published pair looks like, and stamping now requires it: an inventory
#: claiming node-bearing files the body does not anchor is refused outright
#: (bd qmbp), so a body anchoring *nothing* could only ever exercise that
#: refusal, never the token behaviour these tests are about. ``_FOREIGN``
#: below is the body that deliberately fails it.
_BODY = json.dumps(
    {"meta": {}, "nodes": {"file:aaa": {"props": {"file": "a.py"}}},
     "edges": []},
)
_TWIN = json.dumps(
    {"meta": {}, "nodes": {"file:zzz": {"props": {"file": "a.py"}}},
     "edges": []},
)
#: A body that anchors a different file entirely -- the ADR 0096 gate 5 shape
#: where a *foreign* graph lands beside an inventory that was already there.
_FOREIGN = json.dumps(
    {"meta": {}, "nodes": {"file:qqq": {"props": {"file": "other.py"}}},
     "edges": []},
)


class _GraphFixture(unittest.TestCase):
    """A root holding a canonical ``graph.json`` and nothing else."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="published-graph-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()
        self.graph = self.root / ".weld" / "graph.json"
        self.graph.write_text(_BODY, encoding="utf-8")

    def vouch(self, state: DiscoveryState | None = None) -> bool:
        if state is None:
            state = load_state(self.root)
        return state_vouches_for_graph(state, self.graph)

    def publish(self) -> DiscoveryState:
        """Record an inventory that names the graph currently on disk."""
        state = DiscoveryState(
            files={"a.py": "sha256:a"},
            published_graph=published_graph_token(self.graph),
        )
        save_state(self.root, state)
        return state

    def reland(self, body: str) -> None:
        """Replace the graph the way a copy does: a new file, a new inode."""
        self.graph.unlink()
        self.graph.write_text(body, encoding="utf-8")
        self.stamp_a_later_mtime()

    def stamp_a_later_mtime(self) -> None:
        """Move the graph's timestamp somewhere the recorded pin is not.

        Set rather than waited for. What these tests need is "the stat
        shortcut no longer matches", and sleeping until the filesystem's
        clock happens to tick is a flaky way to ask for it -- the granularity
        is the platform's business, not the assertion's.
        """
        stamp = self.graph.stat().st_mtime_ns + 1_000_000_000
        os.utime(self.graph, ns=(stamp, stamp))


class TokenTest(_GraphFixture):
    """What :func:`published_graph_token` records."""

    def test_token_carries_content_and_placement(self) -> None:
        token = published_graph_token(self.graph)
        self.assertEqual(sorted(token), ["mtime_ns", "sha256", "size"])
        self.assertEqual(token["size"], len(_BODY))
        self.assertTrue(token["sha256"].startswith("sha256:"))

    def test_an_unreadable_graph_yields_no_token(self) -> None:
        # Nothing to name, so nothing is claimed: the state vouches for no
        # graph and buys a refresh, which is the safe direction.
        self.assertIsNone(published_graph_token(self.root / ".weld" / "absent"))

    def test_a_graph_rewritten_mid_digest_yields_no_token(self) -> None:
        # A torn token would be the bug in miniature: its cheap half would
        # vouch for a body its digest never described. The interloping write
        # changes the *size*, so this asserts the guard rather than the
        # filesystem's timestamp granularity.
        real = check_mod.compute_hash

        def rewrite_then_hash(path: Path) -> str:
            digest = real(path)
            path.write_text(_TWIN + " " * 32, encoding="utf-8")
            return digest

        with mock.patch.object(check_mod, "compute_hash", rewrite_then_hash):
            self.assertIsNone(published_graph_token(self.graph))


class VouchingTest(_GraphFixture):
    """When an inventory may be used as the basis for the graph on disk."""

    def test_a_published_graph_vouches(self) -> None:
        self.publish()
        self.assertTrue(self.vouch())

    def test_no_state_vouches_for_nothing(self) -> None:
        self.assertFalse(self.vouch(None))

    def test_a_state_naming_no_graph_vouches_for_nothing(self) -> None:
        # The ``write_graph=False`` shape, and every state written before the
        # field existed.
        self.assertFalse(self.vouch(DiscoveryState(files={"a.py": "sha256:a"})))

    def test_identical_bytes_at_a_new_inode_still_vouch(self) -> None:
        # The legitimate seed. The stat pair has moved, so this answer costs a
        # digest -- once, after which the reconcile re-stamps the token.
        self.publish()
        self.reland(_BODY)
        self.assertTrue(self.vouch())

    def test_a_foreign_body_of_the_same_size_does_not_vouch(self) -> None:
        # The shape a stat-only token would wave through if the sizes happened
        # to match, and the shape no hash diff can see: the inventory still
        # describes the tree exactly.
        self.publish()
        self.reland(_TWIN)
        self.assertFalse(self.vouch())

    def test_a_foreign_body_of_a_different_size_does_not_vouch(self) -> None:
        self.publish()
        self.reland(json.dumps({"meta": {}, "nodes": {}, "edges": []}))
        self.assertFalse(self.vouch())

    def test_an_edited_body_in_place_does_not_vouch(self) -> None:
        # Not every replacement arrives by rename; an in-place write keeps the
        # inode and still changes the content. Same length on purpose, so the
        # digest is what has to catch it.
        state = self.publish()
        with self.graph.open("r+", encoding="utf-8") as handle:
            handle.write(_TWIN)
        self.stamp_a_later_mtime()
        self.assertFalse(self.vouch(state))

    def test_a_removed_graph_does_not_vouch(self) -> None:
        state = self.publish()
        self.graph.unlink()
        self.assertFalse(self.vouch(state))

    def test_a_partial_token_does_not_vouch(self) -> None:
        """Completeness is checked before the cheap shortcut, not after.

        The dangerous member of this set is the third: a correct
        ``(size, mtime_ns)`` and no digest at all. Answering it on the stat
        pair alone would restore exactly the placement-only claim this
        mechanism replaced -- and, worse, quietly: the state writer carries a
        pinning token forward untouched, so a graph left sitting still would
        keep the weak encoding alive indefinitely. Nothing here writes a
        partial token, so a partial one is a state that was truncated or
        edited, and doubt costs only a refresh.
        """
        good = published_graph_token(self.graph)
        for token in (
            "not-a-dict",
            {},
            {"size": good["size"], "mtime_ns": good["mtime_ns"]},
            {"sha256": good["sha256"], "size": good["size"]},
            {"sha256": 17, "size": good["size"], "mtime_ns": good["mtime_ns"]},
            {"sha256": good["sha256"], "size": "big",
             "mtime_ns": good["mtime_ns"]},
        ):
            with self.subTest(token=token):
                self.assertFalse(
                    self.vouch(DiscoveryState(files={"a.py": "x"},
                                              published_graph=token)),
                )

    def test_a_stale_placement_falls_through_to_the_digest(self) -> None:
        # The stat pair is a shortcut, never the claim: a token whose
        # placement no longer matches is still settled by content. This is
        # what keeps the legitimate seed incremental instead of merely
        # rejecting it cheaply.
        token = dict(published_graph_token(self.graph))
        token["mtime_ns"] = token["mtime_ns"] - 1
        state = DiscoveryState(files={"a.py": "x"}, published_graph=token)

        self.assertTrue(self.vouch(state))

        self.reland(_TWIN)
        self.assertFalse(self.vouch(state))


class StampingTest(_GraphFixture):
    """What the two producers write, and what they decline to write."""

    def test_a_publishing_run_names_its_graph(self) -> None:
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=True,
        )
        self.assertTrue(self.vouch())

    def test_a_withholding_run_names_nothing(self) -> None:
        # ``--output`` elsewhere, the library caller, or an interruption before
        # the graph lands: readers still hold the older body.
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=False,
        )
        self.assertIsNone(load_state(self.root).published_graph)
        self.assertFalse(self.vouch())

    def test_an_untouched_graph_is_not_re_hashed(self) -> None:
        # The no-change refresh is the hot path (bd 85tb.2 kept it off both a
        # re-serialize and a 14 MB rewrite); re-hashing the graph on every
        # state write would put a multi-megabyte read straight back into it.
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=True,
        )
        first = load_state(self.root).published_graph

        with mock.patch.object(
            check_mod, "compute_hash", wraps=check_mod.compute_hash,
        ) as spy:
            save_state_for_graph(
                self.root, {"a.py": "sha256:a"}, {"nodes": {}},
                graph_published=True,
            )

        self.assertEqual(
            spy.call_count, 0,
            "an unchanged graph still pins the recorded token, so the digest "
            "beside it is still that file's -- carry it forward",
        )
        self.assertEqual(load_state(self.root).published_graph, first)

    def test_mark_state_published_names_the_landed_graph(self) -> None:
        # The ``wd discover`` tail / ``wd warm`` shape: discovery wrote an
        # inventory that could not yet claim a readable graph, and the caller
        # lands the canonical copy itself.
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=False,
        )
        mark_state_published(self.root, self.graph)
        self.assertTrue(self.vouch())

    def test_mark_state_published_restamps_a_stale_claim(self) -> None:
        # The early return is "already vouches for *this* body", not "has
        # vouched for something" -- otherwise a state naming an earlier graph
        # would keep vouching for a body no longer there.
        self.publish()
        self.reland(_TWIN)
        self.assertFalse(self.vouch(), "precondition: the claim went stale")

        mark_state_published(self.root, self.graph)

        self.assertTrue(self.vouch())

    def test_a_foreign_body_is_not_re_stamped(self) -> None:
        # The other half of re-stamping: it may only name a body this
        # inventory actually describes. Re-stamping a graph that anchors
        # different files is how a foreign body acquires a vouch it never
        # earned -- the ADR 0096 gate 5 shape (bd wq9i), which now fails the
        # coverage audit instead of being blessed by the re-stamp (bd qmbp).
        before = self.publish().published_graph
        self.reland(_FOREIGN)
        self.assertFalse(self.vouch(), "precondition: the claim went stale")

        mark_state_published(self.root, self.graph)

        self.assertFalse(self.vouch())
        # Refusing writes nothing at all, so the superseded token stays put.
        # That is strictly the safer of the two outcomes: it names a body no
        # longer on disk, so it vouches for nothing either way, and declining
        # to touch the state keeps the refusal free of side effects.
        self.assertEqual(load_state(self.root).published_graph, before)

    def test_a_body_rewritten_mid_stamp_is_not_vouched_for(self) -> None:
        # The token must name the very bytes the coverage audit read. If the
        # body is replaced between the two, stamping would vouch for one
        # graph on another's coverage -- the mid-flight hazard
        # ``published_graph_token`` already guards with its paired stat, and
        # the reason the digest is re-checked after the audit (bd qmbp).
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=False,
        )

        def audit_then_swap(*args, **kwargs):
            self.reland(_FOREIGN)
            return True

        with mock.patch.object(
            check_mod, "inventory_describes_graph", side_effect=audit_then_swap,
        ):
            mark_state_published(self.root, self.graph)

        self.assertIsNone(load_state(self.root).published_graph)

    def test_a_graph_written_elsewhere_is_not_vouched_for(self) -> None:
        # ``--output /tmp/x.json`` leaves readers on the older body, which is
        # exactly the divergence the coverage probe exists to catch.
        elsewhere = self.root / "exported.json"
        elsewhere.write_text(_BODY, encoding="utf-8")
        save_state_for_graph(
            self.root, {"a.py": "sha256:a"}, {"nodes": {}},
            graph_published=False,
        )

        mark_state_published(self.root, elsewhere)

        self.assertIsNone(load_state(self.root).published_graph)


class SerializationTest(_GraphFixture):
    """The on-disk shape, including what older weld versions read."""

    def test_the_token_round_trips(self) -> None:
        token = self.publish().published_graph
        self.assertEqual(load_state(self.root).published_graph, token)

    def test_the_boolean_survives_as_a_compatibility_mirror(self) -> None:
        # An older weld gates its incremental basis on this boolean. Dropping
        # it would cost that reader a full re-discovery on every alternating
        # run -- but nothing in this version may consult it, because it is
        # exactly the lossy claim being retired.
        self.publish()
        raw = json.loads(
            (self.root / ".weld" / "discovery-state.json").read_text(
                encoding="utf-8",
            ),
        )
        self.assertTrue(raw["graph_published"])
        self.assertEqual(raw["published_graph"]["size"], len(_BODY))

    def test_the_mirror_alone_does_not_vouch(self) -> None:
        self.publish()
        path = self.root / ".weld" / "discovery-state.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("published_graph")
        path.write_text(json.dumps(raw), encoding="utf-8")

        self.assertTrue(raw["graph_published"], "fixture: the mirror stays")
        self.assertFalse(self.vouch())


if __name__ == "__main__":
    unittest.main()

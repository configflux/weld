"""A wildcard in a *directory* segment must resolve, end to end (bd uhxjc).

``weld.glob_match._walk_one`` took its non-``**`` branch for any pattern
without ``**`` and computed ``parent = (root / pattern).parent``. For
``apps/*/package.json`` that parent is the literal path ``<root>/apps/*``,
which is never a directory, so the guard returned and the glob matched
**nothing** -- the flat branch's version of the ``**`` defect the module
docstring records as bd t06t, and still live after it.

This is the system-level probe, run through the real ``python -m weld`` in a
subprocess against a real git repo, because the in-process layers each look
fine on their own. What a user actually meets is three compounding failures
that only a whole run shows:

1. ``wd discover`` writes an **empty** graph and exits 0. Silence, not a
   subset: there is no partial result to notice.
2. ``wd stale`` then reports ``coverage_stale`` for every file the glob
   named, reason ``in-scope file never ingested`` -- immediately after a
   clean, successful, full discovery. The ADR 0101 accounting matches the
   path list by regex (:func:`weld._staleness_coverage.in_scope_files`,
   which handles the shape correctly) while the walk that feeds the
   inventory returns nothing, so the two disagree in the one direction ADR
   0101 names as the expensive one: a file marked in scope that the state
   can never cover is permanent staleness and a refresh on **every read**,
   forever.
3. So the ADR 0017 signal is gone too. ``source_stale`` can only speak for
   files the inventory knows, and editing one of these never reaches it.
   The path is still *listed* -- as ``in-scope file never ingested``, which
   is why the case below asserts the **reason** rather than the presence: a
   presence check answers yes for the opposite reason and proves nothing.

Both wildcard shapes the issue names are exercised: one wildcard segment
(``apps/*/package.json``) and a wildcard in more than one
(``services/*/src/*.py``). A third entry with a literal directory part
(``*.md``) rides along as the control that the fast single-directory path
still resolves, and still does not recurse.

The unit-level cases for the same contract are
``weld_glob_segment_wildcard_test``; the never-over-report half is armed in
the shared ADR 0101 fixture (``weld/tests/_coverage_stale_lib.py``).
"""

from __future__ import annotations

import json
import unittest

from weld._stale_reasons import CONTENT_DIFFERS, NEVER_INGESTED
from weld.tests._cli_e2e_harness import CliRepoHarness

#: One wildcard segment, a wildcard in two segments, and the literal-directory
#: control -- in the order the config below declares them.
_APP_MANIFESTS = ("apps/a/package.json", "apps/b/package.json")
_SERVICE_SOURCES = ("services/x/src/main.py", "services/y/src/util.py")
_SEGMENT_GLOB_FILES = _APP_MANIFESTS + _SERVICE_SOURCES

TREE: dict[str, str] = {
    "apps/a/package.json": (
        '{"name": "app-a", "version": "1.0.0", "scripts": {"build": "tsc"}}\n'
    ),
    "apps/b/package.json": (
        '{"name": "app-b", "version": "1.0.0", "scripts": {"test": "jest"}}\n'
    ),
    "services/x/src/main.py": "def x_main():\n    return 1\n",
    "services/y/src/util.py": "def y_util():\n    return 2\n",
    # Control for the literal-directory fast path, and for its
    # non-recursion: only the root-level one is in scope.
    "top.md": "# top\n\n## section\n\nprose\n",
    "docs/deep/buried.md": "# buried\n\n## section\n\nprose\n",
}

CONFIG = """version: 1
sources:
  - glob: "apps/*/package.json"
    type: config
    strategy: manifest
  - glob: "services/*/src/*.py"
    type: file
    strategy: python_module
  - glob: "*.md"
    type: doc
    strategy: markdown
"""


class SegmentGlobDiscoveryTest(CliRepoHarness, unittest.TestCase):
    """One repo, one ``wd discover``, then the three consequences.

    The temp git repo, the pinned environment and the "is this the checkout
    under test?" assertion are ``weld.tests._cli_e2e_harness``'s -- shared with
    the sibling probe that asks the same question of the walker's *callers*
    (``weld_strategy_segment_glob_e2e_test``) rather than kept in a second copy.
    """

    graph: dict
    state: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(TREE, CONFIG)
        cls.discover = cls.wd("discover", "--output", ".weld/graph.json")
        cls.graph = cls.read_json(".weld/graph.json")
        cls.state = cls.read_json(".weld/discovery-state.json")

    # -- helpers ---------------------------------------------------------

    def _files_with_nodes(self) -> set[str]:
        """Every repo-relative path some emitted node names as its source."""
        return {
            (node.get("props") or {}).get("file")
            for node in self.graph.get("nodes", {}).values()
        } - {None}

    def _stale(self) -> dict:
        return json.loads(self.wd("stale", "--json").stdout)

    # -- the defect ------------------------------------------------------

    def test_discover_emits_a_node_for_every_segment_glob_file(self) -> None:
        """The headline: four files named by two globs, four files covered.

        Asserted through ``props.file`` rather than by node id so the claim is
        "discovery read this file", not "the manifest strategy spells its id
        this way" -- the id spelling is the strategy's business and may change
        without this contract changing.
        """
        covered = self._files_with_nodes()
        missing = [rel for rel in _SEGMENT_GLOB_FILES if rel not in covered]
        self.assertEqual(
            missing, [],
            f"`wd discover` emitted no node for {missing}; a wildcard in a "
            f"directory segment resolved to nothing. Covered: {sorted(covered)}",
        )

    def test_provenance_names_every_segment_glob_file(self) -> None:
        """ADR 0017: the graph must record which files it read.

        Separate from the node assertion above because a strategy that read a
        file and emitted nothing for it would still owe provenance -- and
        because this is the list the incremental basis and ``source_stale``
        are computed from.
        """
        discovered_from = set(self.graph.get("meta", {}).get(
            "discovered_from", []
        ))
        for rel in _SEGMENT_GLOB_FILES:
            with self.subTest(file=rel):
                self.assertIn(rel, discovered_from)

    def test_the_inventory_records_every_segment_glob_file(self) -> None:
        """``resolve_source_files``' answer, as the product recorded it.

        This is the ADR 0101 in-scope accounting: a file absent here is one
        no incremental run will re-read and no ``wd stale`` can speak for.
        """
        inventory = set(self.state.get("files", {}))
        for rel in _SEGMENT_GLOB_FILES:
            with self.subTest(file=rel):
                self.assertIn(rel, inventory)

    def test_a_freshly_discovered_repo_is_not_stale(self) -> None:
        """The compounding consequence, and the one a user actually meets.

        With the walk empty and the coverage matcher not, every file the two
        globs name reads as ``in-scope file never ingested`` the instant a
        clean full discovery finishes -- so the repo is stale forever and
        every read pays a refresh that cannot fix it.
        """
        stale = self._stale()
        self.assertEqual(
            stale.get("stale_sources"), [],
            "a repo reports stale sources immediately after a clean, "
            "successful `wd discover`",
        )
        self.assertFalse(stale.get("coverage_stale"), stale)
        self.assertFalse(stale.get("stale"), stale)

    def test_editing_a_segment_glob_file_is_noticed(self) -> None:
        """ADR 0017 ``source_stale`` must reach these files too.

        The *reason* is the assertion, not the path: before the fix this file
        was already named on every read, as ``NEVER_INGESTED`` -- so "is it
        listed?" answers yes for the opposite reason and proves nothing.
        Only ``CONTENT_DIFFERS`` means the inventory knew the file and
        noticed the edit. Both constants come from
        :mod:`weld._stale_reasons` rather than being spelled here twice.

        The only case that mutates the tree, so it restores the body on
        cleanup: the fixture is built once per class and its neighbours read
        the graph and inventory this run produced.
        """
        target = self.root / _APP_MANIFESTS[0]
        target.write_text(
            '{"name": "app-a", "version": "2.0.0", '
            '"scripts": {"build": "tsc -b"}}\n',
            encoding="utf-8",
        )
        self.addCleanup(target.write_text, TREE[_APP_MANIFESTS[0]], "utf-8")
        stale = self._stale()
        self.assertTrue(stale.get("source_stale"), stale)
        reasons = {
            entry.get("path"): entry.get("reason")
            for entry in stale.get("stale_sources", [])
        }
        self.assertEqual(reasons.get(_APP_MANIFESTS[0]), CONTENT_DIFFERS, stale)
        self.assertNotIn(NEVER_INGESTED, reasons.values(), stale)

    # -- controls --------------------------------------------------------

    def test_a_literal_directory_glob_still_resolves(self) -> None:
        """The fast single-directory path must cost nothing.

        ``*.md`` has no wildcard in its directory part, so it keeps taking
        the flat branch; if the fix widened that branch's condition too far
        this is what would notice.
        """
        self.assertIn("top.md", set(self.state.get("files", {})))

    def test_a_literal_directory_glob_still_does_not_recurse(self) -> None:
        """Control for the control: ``*.md`` must not become ``**/*.md``.

        Routing a pattern to the recursive walker is only correct when its
        regex still refuses to cross ``/``. Without this, a fix that simply
        sent every pattern down the ``**`` branch would look green above and
        have silently widened every single-directory glob in every config.
        """
        self.assertNotIn(
            "docs/deep/buried.md", set(self.state.get("files", {})),
        )

    def test_discover_reported_the_files_it_wrote(self) -> None:
        """Vacuity floor: an empty graph must not satisfy the assertions above.

        Cheap, and it is exactly the observation the bug report opens with --
        ``wd discover`` exiting 0 having written nothing. The exit code is
        already asserted by the runner (:meth:`_run` raises on non-zero), so
        what is left to pin is that a zero exit meant something.
        """
        self.assertGreaterEqual(len(self.graph.get("nodes", {})), 4)


if __name__ == "__main__":
    unittest.main()

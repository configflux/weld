"""Coverage accounting must see the files a brace glob names (bd 2z5no).

``weld._staleness_coverage.in_scope_files`` translated each entry's ``glob:``
with ``weld.glob_match._glob_pattern_to_regex`` and never called
``expand_braces``. That translator escapes ``{`` into a literal, so a brace
glob matched **nothing at all** there -- while ``walk_glob`` expands the group
first and matches everything it names. The two halves of ADR 0101 therefore
disagreed in the *quiet* direction: discovery read the files, the accounting
denied they were ever in scope.

That became reachable for every Node repository the moment ``wd init`` started
writing the dialect-family entries -- ``**/*.{ts,tsx}`` and
``**/*.{js,jsx,mjs,cjs}``. On a stock TypeScript or JavaScript checkout the
ADR 0101 probe believed none of those files was in scope, so
``files_missing_from_inventory`` could never fire for any of them: a ``.ts``
module added after the last ``wd discover`` was invisible to *every* freshness
signal, and ``wd stale`` kept answering ``stale: false`` while reads answered
"no such symbol" for code that was sitting in the tree.

This is the system-level probe, through the real ``python -m weld`` in a
subprocess, because the config under test is the one the **product generates**:
a hand-written fixture config would only prove what its author chose to spell.
So the fixture is a Node tree with no config at all, ``wd init`` writes the
entries, and every assertion below reads what that generated config produced.

The three moves, in order:

1. ``wd init`` writes brace globs -- the vacuity floor. If it ever stops, the
   cases below would pass while asserting nothing about braces.
2. ``wd discover`` ingests the dialect files, and the repo then reads clean.
   That is the over-report twin: widening the in-scope set is the expensive
   direction (ADR 0101 section 4), because a file the state can never cover is
   permanent staleness and a refresh on every read -- the shape bd uhxjc hit.
3. A new ``.ts`` (and a new ``.mjs``) added with no re-discover is reported as
   ``in-scope file never ingested``, and a file no entry names is not.

Grammars are deliberately not a dependency here. ``wd discover`` warns and
skips the ``tree_sitter`` strategy without them, but the *inventory* is what
this probe reads, and that records every file ``resolve_source_files``
resolved whether or not a strategy emitted a node for it. The subject is the
scope accounting, not the parse.

The unit-level cases for the same contract are in
``weld_coverage_scope_match_test``, over the shared ADR 0101 fixture.
"""

from __future__ import annotations

import json
import unittest

from weld._stale_reasons import NEVER_INGESTED
from weld.tests._cli_e2e_harness import CliRepoHarness

#: The dialect entries ``wd init`` writes for a TypeScript / JavaScript repo.
#: Asserted verbatim: they are the exact patterns the defect made inert, and
#: the second one is here so the probe covers a group whose alternatives are
#: more than two and whose matched file is not the first of them.
_TS_GLOB = "**/*.{ts,tsx}"
_JS_GLOB = "**/*.{js,jsx,mjs,cjs}"

#: Present at ``wd discover`` time, so the inventory covers them.
_INGESTED = ("src/app.ts", "src/widget.tsx", "src/legacy.js")

TREE: dict[str, str] = {
    "src/app.ts": "export const version = 1;\n",
    "src/widget.tsx": "export const Widget = () => null;\n",
    "src/legacy.js": "module.exports = { legacy: true };\n",
    "package.json": '{"name": "probe", "version": "1.0.0"}\n',
}


class CoverageBraceGlobTest(CliRepoHarness, unittest.TestCase):
    """One ``wd init``-configured Node repo, discovered once, then probed.

    The temp git repo, the pinned environment and the "is this the checkout
    under test?" assertion are ``weld.tests._cli_e2e_harness``'s. ``config`` is
    ``None`` because this probe's whole subject is the config the product
    generates, so it must be the thing that writes it.
    """

    config: str
    state: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(TREE, None)
        cls.wd("init")
        cls.config = (cls.root / ".weld" / "discover.yaml").read_text(
            encoding="utf-8"
        )
        cls.wd("discover", "--output", ".weld/graph.json")
        cls.state = cls.read_json(".weld/discovery-state.json")

    # -- helpers ---------------------------------------------------------

    def _stale(self) -> dict:
        return json.loads(self.wd("stale", "--json").stdout)

    def _add(self, rel: str, body: str) -> dict:
        """Write *rel* with no re-discovery, and report freshness with it there.

        Removed on cleanup: the fixture is built once per class and its
        neighbours read the inventory and the verdict that one run produced.
        """
        path = self.root / rel
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return self._stale()

    def _never_ingested(self, stale: dict) -> set[str]:
        return {
            entry.get("path")
            for entry in stale.get("stale_sources", [])
            if entry.get("reason") == NEVER_INGESTED
        }

    # -- vacuity floor ---------------------------------------------------

    def test_init_writes_the_dialect_family_brace_globs(self) -> None:
        """The premise: the generated config is what carries the brace groups.

        Without this the whole module could go green because ``wd init``
        stopped writing braces, having proved nothing about how they are
        accounted for.
        """
        for glob in (_TS_GLOB, _JS_GLOB):
            with self.subTest(glob=glob):
                self.assertIn(glob, self.config)

    def test_the_inventory_records_every_dialect_file(self) -> None:
        """The walk half, which always worked -- and the basis for the rest.

        A file absent from the inventory would read as never ingested for the
        opposite reason, so this is what makes the cases below mean what they
        say.
        """
        inventory = set(self.state.get("files", {}))
        for rel in _INGESTED:
            with self.subTest(file=rel):
                self.assertIn(rel, inventory)

    # -- the over-report twin --------------------------------------------

    def test_a_freshly_discovered_repo_is_not_stale(self) -> None:
        """Widening scope must not make a converged repo permanently stale.

        ADR 0101 section 4: over-reporting scope marks a file the state can
        never cover, which is staleness on every read forever. This is the
        direction that costs, so it is asserted on its own rather than left
        implied by the cases below.
        """
        stale = self._stale()
        self.assertFalse(stale.get("coverage_stale"), stale)
        self.assertFalse(stale.get("source_stale"), stale)
        self.assertFalse(stale.get("stale"), stale)
        self.assertEqual(stale.get("stale_sources"), [], stale)

    # -- the defect ------------------------------------------------------

    def test_a_new_typescript_file_is_reported_as_never_ingested(self) -> None:
        """The headline: ``**/*.{ts,tsx}`` puts a new ``.ts`` file in scope.

        The file is left uncommitted and untracked on purpose. That is the
        ADR 0101 blind spot in its cheapest form: the recorded SHA is still
        HEAD so there is no commit range to diff, the path is absent from
        ``meta.discovered_from`` so the working-tree probe never looks at it,
        and only the boundary snapshot -- which lists untracked, unignored
        files -- can see it at all.

        Both assertions are about *which* signal fired, not merely that one
        did. ``sha_behind`` staying false rules out the commit-range half, and
        the reason being ``NEVER_INGESTED`` rather than ``CONTENT_DIFFERS``
        rules out the working-tree half -- the same discriminator the sibling
        segment-glob probe uses, and for the same reason: a bare "is the path
        listed?" answers yes for either. ``source_stale`` is deliberately not
        asserted false: ``compute_stale_info`` raises it once coverage fires,
        so it is the composed verdict here rather than an ADR 0017 signal.
        """
        stale = self._add("src/added.ts", "export const added = 2;\n")
        self.assertTrue(stale.get("coverage_stale"), stale)
        self.assertIn("src/added.ts", self._never_ingested(stale))
        self.assertFalse(stale.get("sha_behind"), stale)

    def test_a_new_javascript_file_is_reported_as_never_ingested(self) -> None:
        """The second entry, and a non-leading alternative of its group.

        ``.mjs`` is the third of ``{js,jsx,mjs,cjs}``: a fix that expanded only
        the first alternative, or only the two-alternative form, passes the
        case above and fails here.
        """
        stale = self._add("src/added.mjs", "export const added = 3;\n")
        self.assertTrue(stale.get("coverage_stale"), stale)
        self.assertIn("src/added.mjs", self._never_ingested(stale))

    # -- control ---------------------------------------------------------

    def test_a_file_no_entry_names_is_not_reported(self) -> None:
        """The brace group expands to its alternatives, not to a wildcard.

        ``notes.txt`` is matched by nothing in the generated config, so a fix
        that treated ``{...}`` as "any suffix" -- or that dropped the
        extension check on the way to expanding -- would report it and turn
        every unrelated file into permanent staleness.
        """
        stale = self._add("src/notes.txt", "plain text, no strategy reads it\n")
        self.assertNotIn("src/notes.txt", self._never_ingested(stale))
        self.assertFalse(stale.get("coverage_stale"), stale)


if __name__ == "__main__":
    unittest.main()

"""Pin the ``.weld/.gitignore`` templates to the bookkeeping-path set.

The recurrence this stops (bd eqc4): weld gains a new ``.weld/`` sidecar,
the author remembers the gitignore templates -- omitting them makes
``git status`` visibly noisy -- and forgets
``weld._git_bookkeeping.WELD_BOOKKEEPING_PATHS``, which is invisible until
someone hits a repo that is ``source_stale`` on every read. It has now
happened four times: ``file-index-state.json`` (bd 85tb.2),
``auto-refresh.jsonl`` (bd keik), ``graph.write.lock`` (bd ds5r.4) and
``telemetry.jsonl`` (this issue).

The templates are the maintained inventory of "files weld writes and can
rebuild" -- that is literally what listing one means -- so every line in
them must also be excluded from source drift. Asserting that here turns the
invisible half of the omission into a build failure at the moment the
visible half is edited.

The converse does not hold and is not asserted: the set legitimately holds
paths no template lists, when the file is meant to be trackable rather than
ignored (``file-index.json`` in Mode A; ``.gitattributes``, ADR 0110, in
every mode). That is also the shape a *forgotten* template line takes --
this test cannot tell "trackable on purpose" apart from "omitted by
mistake", which is exactly why the mistake survived four rounds before
this test existed. bd lt96 closed one such gap
(``.enrichment-prompted``, ADR 0052: bookkeeping since its own incident, but
absent from both templates until that fix) by adding the line, not by
teaching this test the other direction.
"""

from __future__ import annotations

import unittest

from weld._git import _WELD_BOOKKEEPING_PATHS
from weld._git_bookkeeping import WELD_BOOKKEEPING_PATHS
from weld._gitignore_writer import (
    CONFIG_ONLY_GITIGNORE,
    IGNORE_ALL_GITIGNORE,
    TRACK_GRAPHS_GITIGNORE,
)


def _ignored_filenames(template: str) -> list[str]:
    """Return the literal filenames a managed template ignores.

    Comments, blanks, and negations (``!.gitignore``) are dropped. Glob
    lines are dropped too: they are a policy, not an inventory, and only
    ``IGNORE_ALL_GITIGNORE`` uses one.
    """
    return [
        line.strip()
        for line in template.splitlines()
        if line.strip()
        and not line.startswith("#")
        and not line.startswith("!")
        and "*" not in line
    ]


class GitignoreTemplateBookkeepingParityTest(unittest.TestCase):
    """Every managed-template line must be a known bookkeeping path."""

    def test_config_only_template_lines_are_all_bookkeeping(self) -> None:
        self._assert_all_bookkeeping(CONFIG_ONLY_GITIGNORE, "CONFIG_ONLY")

    def test_track_graphs_template_lines_are_all_bookkeeping(self) -> None:
        self._assert_all_bookkeeping(TRACK_GRAPHS_GITIGNORE, "TRACK_GRAPHS")

    def _assert_all_bookkeeping(self, template: str, label: str) -> None:
        names = _ignored_filenames(template)
        # Guards the parser itself: a template reformat that made every line
        # unparseable would otherwise leave this test passing vacuously.
        self.assertGreater(
            len(names), 10, f"{label} parsed to {len(names)} names -- parser broke"
        )
        missing = sorted(
            name for name in names
            if f".weld/{name}" not in WELD_BOOKKEEPING_PATHS
        )
        self.assertEqual(
            missing, [],
            f"{label}_GITIGNORE ignores {missing} because weld writes them, "
            "but weld._git_bookkeeping.WELD_BOOKKEEPING_PATHS does not list "
            "them. Any path weld writes must be excluded from source drift, "
            "or a checkout whose .weld/.gitignore predates the template line "
            "(they are skip-if-exists and never rewritten) reads weld's own "
            "output as user source and is source_stale on every read. Add "
            f"'.weld/<name>' for each of: {missing}",
        )

    def test_ignore_all_template_is_a_glob_not_an_inventory(self) -> None:
        # Pins why IGNORE_ALL is exempt above rather than silently skipped:
        # it blanket-ignores via '*' and names no sidecar to cross-check.
        self.assertEqual(_ignored_filenames(IGNORE_ALL_GITIGNORE), [])

    def test_git_reexport_is_the_same_object(self) -> None:
        # _git.py re-imports the set under its historical private name; the
        # rest of the codebase and these tests must not drift onto copies.
        self.assertIs(_WELD_BOOKKEEPING_PATHS, WELD_BOOKKEEPING_PATHS)

    def test_source_of_truth_config_is_not_bookkeeping(self) -> None:
        # The inclusion rule has a floor: user-authored config must keep
        # counting as drift. discover.yaml genuinely changes discovery
        # inputs, so a graph built before an edit really is out of date.
        for name in (
            "discover.yaml", "workspaces.yaml", "agents.yaml",
            "lint-rules.yaml", "cross_repo_overrides.yaml",
        ):
            self.assertNotIn(f".weld/{name}", WELD_BOOKKEEPING_PATHS)


if __name__ == "__main__":
    unittest.main()

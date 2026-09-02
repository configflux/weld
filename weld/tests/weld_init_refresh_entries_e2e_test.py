"""``wd init --refresh`` delivers a newly-wired *entry*, as a real-CLI probe.

``--refresh``'s unit of work was an unclaimed *language* (ADR 0135, ADR 0144).
A root config is not a language and neither is a framework source entry, so no
amount of language detection could ever emit one: when ``tsconfig.json`` joined
the root-config table, every existing TypeScript project kept a graph with no
``config:tsconfig_json`` node, and the only command that added the entry was
``wd init --force`` -- which discards the hand edits ``--refresh`` exists to
preserve.

Verified end to end before the fix: on the tree below ``wd init --refresh``
answered "discover.yaml is already current", returned ``wired=()``, bumped the
stamp, and left the config's ``config_file`` entry naming ``package.json``
alone. This module landed **red on purpose** (bd 5038-j5o5d) and was flipped by
the fix, not by editing it.

Five repos, because the second comparison has to add signal, add no noise, and
stay reversible:

* ``repro`` -- the issue's tree. The entry is appended, and the node it exists
  for reaches the graph: the assertion is ``config:tsconfig_json`` after
  ``wd discover``, not a string in a YAML file, because a wired entry that
  discovery does not read would satisfy the latter and none of the point.
* ``carried`` -- a config that already names ``tsconfig.json``. A no-op: the
  stamp may move, nothing is wired, and no second entry appears. An
  append-only merge that re-appends what is already there is a duplicate
  generator, not a remedy.
* ``removed`` -- the hard case. Refresh once (the entry lands), delete it by
  hand, refresh again. It must stay out: a diff against the live config alone
  cannot tell "never offered" from "offered and removed by hand", and
  resurrecting the second is how a merge stops being trustworthy.
* ``express`` -- the framework half of the same gap. Both dialect families are
  fully claimed, so the language comparison has nothing to say, and the repo
  still has an undetected-until-now express server in it.
* ``gin`` -- the one shape the two comparisons overlap on: a framework whose
  language is *also* unclaimed, which both passes have something to say about.
  It is wired once. Added in review of the fix, red before it.

It drives the real CLI in a subprocess because every surface the bug reached
is a command: the refresh merge, the config it writes, and the graph the next
``wd discover`` builds from it. An in-process call to ``refresh()`` would have
agreed with itself about what a config carries.

No grammar dependency: the entries under test are ``config_file`` and
``express``, neither of which parses with tree-sitter. ``wd discover`` warns
and skips the ``tree_sitter`` entry without the grammars and the ``config:``
nodes land regardless.
"""

from __future__ import annotations

import unittest

from weld.tests._cli_e2e_harness import CliRepoHarness

#: The issue's repro tree: one TypeScript source, and the two root configs a
#: Node project carries -- one of which the config below already names.
REPRO_TREE: dict[str, str] = {
    "src/a.ts": "export const a = 1;\n",
    "package.json": '{"name": "app", "private": true}\n',
    "tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
}

#: The config an existing project has: the TypeScript entry is wired and every
#: ``.ts`` file on disk is claimed, so the ADR 0144 language comparison is
#: silent and correct to be. The ``config_file`` entry lists ``package.json``
#: and nothing else -- exactly what ``wd init`` wrote before ``tsconfig.json``
#: joined the root-config table.
CARRIES_PACKAGE_ONLY = """\
# generated-by: weld 0.24.0
sources:
  # --- TypeScript sources (.ts, .tsx) ---
  - glob: "**/*.{ts,tsx}"
    type: file
    strategy: tree_sitter
    language: typescript
    emit_calls: true

  # --- Root configuration files ---
  - files: ["package.json"]
    type: config
    strategy: config_file
"""

#: The same config with the newer entry already in it. Nothing to deliver.
CARRIES_BOTH = CARRIES_PACKAGE_ONLY.replace(
    '- files: ["package.json"]', '- files: ["package.json", "tsconfig.json"]',
)

#: A Node service in both dialects with an express server in it. Both family
#: globs are wired, so every file on disk is claimed and the language
#: comparison reports nothing -- which is the whole point of this fixture.
EXPRESS_TREE: dict[str, str] = {
    "package.json": '{"name": "api", "private": true}\n',
    "src/server.ts": (
        'import express from "express";\n\nexport const app = express();\n'
        'app.get("/orders", (_req, res) => res.json([]));\n'
    ),
    "src/legacy.js": 'const express = require("express");\n',
}

CLAIMS_BOTH_DIALECTS = """\
# generated-by: weld 0.24.0
sources:
  - glob: "**/*.{ts,tsx}"
    type: file
    strategy: tree_sitter
    language: typescript
    emit_calls: true

  - glob: "**/*.{js,jsx,mjs,cjs}"
    type: file
    strategy: tree_sitter
    language: javascript
    emit_calls: true

  - files: ["package.json"]
    type: config
    strategy: config_file
"""

#: The node the whole issue is about. ``wd init`` has wired ``tsconfig.json``
#: since it joined the root-config table; a project that predates that release
#: never gets it, because refresh had no mechanism for a non-language entry.
TSCONFIG_NODE = "config:tsconfig_json"

#: The glob the express entry claims -- the union of both dialect families,
#: because the strategy reads a ``.ts`` server and a CommonJS ``.js`` module
#: alike (:data:`weld._init_framework_sources.TS_JS_FRAMEWORK_GLOB`).
EXPRESS_GLOB = "**/*.{ts,tsx,js,jsx,mjs,cjs}"


def _config_file_entry_count(config: str) -> int:
    """How many uncommented ``strategy: config_file`` entries *config* wires."""
    return sum(
        1 for line in config.splitlines()
        if line.strip() == "strategy: config_file"
    )


class RootConfigDeliveredTest(CliRepoHarness, unittest.TestCase):
    """The issue's repro: the entry is appended and its node reaches the graph."""

    before: str
    after: str
    refresh_output: str
    node_ids: set[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(REPRO_TREE, CARRIES_PACKAGE_ONLY)
        cls.before = cls.config_text()
        proc = cls.wd("init", "--refresh")
        cls.refresh_output = f"{proc.stdout}\n{proc.stderr}"
        cls.after = cls.config_text()
        cls.wd("discover", "--output", ".weld/graph.json")
        cls.node_ids = set(cls.read_json(".weld/graph.json").get("nodes", {}))

    @classmethod
    def config_text(cls) -> str:
        return (cls.root / ".weld" / "discover.yaml").read_text(encoding="utf-8")

    def test_the_starting_config_does_not_name_the_root_config(self) -> None:
        """The vacuity floor: the entry must genuinely be missing to begin with."""
        self.assertNotIn("tsconfig.json", self.before)

    def test_refresh_appends_the_root_config_entry(self) -> None:
        self.assertIn("tsconfig.json", self.after, self.after)

    def test_refresh_says_what_it_wired(self) -> None:
        """A merge the user cannot see is a merge they cannot review."""
        self.assertNotIn("already current", self.refresh_output)
        self.assertIn("tsconfig.json", self.refresh_output)

    def test_the_config_node_reaches_the_graph(self) -> None:
        """The outcome the issue is about, not the YAML that produces it."""
        self.assertIn(TSCONFIG_NODE, self.node_ids)

    def test_the_hand_written_entry_survives(self) -> None:
        self.assertIn('- glob: "**/*.{ts,tsx}"', self.after)
        self.assertIn("language: typescript", self.after)
        self.assertIn("package.json", self.after)

    def test_the_package_json_node_is_not_lost(self) -> None:
        """Append-only: the entry that was there keeps producing its node."""
        self.assertIn("config:package_json", self.node_ids)


class AlreadyCarriedIsNoOpTest(CliRepoHarness, unittest.TestCase):
    """A config that already names the entry gains nothing but a stamp."""

    after: str
    refresh_output: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(REPRO_TREE, CARRIES_BOTH)
        proc = cls.wd("init", "--refresh")
        cls.refresh_output = f"{proc.stdout}\n{proc.stderr}"
        cls.after = (cls.root / ".weld" / "discover.yaml").read_text(
            encoding="utf-8",
        )

    def test_no_second_config_file_entry_is_appended(self) -> None:
        self.assertEqual(_config_file_entry_count(self.after), 1, self.after)

    def test_the_root_config_is_named_exactly_once(self) -> None:
        self.assertEqual(self.after.count('"tsconfig.json"'), 1, self.after)

    def test_refresh_reports_nothing_wired(self) -> None:
        self.assertIn("already current", self.refresh_output)

    def test_the_stamp_is_refreshed(self) -> None:
        """The one thing a no-op refresh is still allowed to change."""
        self.assertNotIn("# generated-by: weld 0.24.0", self.after)
        self.assertIn("# generated-by: weld ", self.after)


class HandRemovedStaysOutTest(CliRepoHarness, unittest.TestCase):
    """An entry the user deleted is not resurrected by the next refresh.

    The sequence is the point: refresh once so the entry is genuinely one
    weld wrote, delete it the way a maintainer would, refresh again. Without
    a record of what weld previously wired, the second refresh sees only a
    config that lacks a detected entry -- indistinguishable from the first --
    and appends it right back.
    """

    after_first: str
    after_edit: str
    after_second: str
    second_output: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(REPRO_TREE, CARRIES_PACKAGE_ONLY)
        cls.wd("init", "--refresh")
        cls.after_first = cls._config()
        cls._write_config(cls._without_tsconfig(cls.after_first))
        cls.after_edit = cls._config()
        proc = cls.wd("init", "--refresh")
        cls.second_output = f"{proc.stdout}\n{proc.stderr}"
        cls.after_second = cls._config()

    @classmethod
    def _config(cls) -> str:
        return (cls.root / ".weld" / "discover.yaml").read_text(encoding="utf-8")

    @classmethod
    def _write_config(cls, text: str) -> None:
        (cls.root / ".weld" / "discover.yaml").write_text(text, encoding="utf-8")

    @staticmethod
    def _without_tsconfig(text: str) -> str:
        """Delete every *source-entry* line naming the root config.

        A maintainer deletes the entry; they do not edit weld's bookkeeping.
        So comment lines are left exactly as they are -- which is what makes
        this a hand removal rather than a reset.
        """
        return "".join(
            line for line in text.splitlines(keepends=True)
            if "tsconfig.json" not in line or line.lstrip().startswith("#")
        )

    def test_the_first_refresh_added_the_entry(self) -> None:
        """The premise. Without it the removal case proves nothing at all."""
        self.assertIn('"tsconfig.json"', self.after_first)

    def test_the_hand_edit_removed_it(self) -> None:
        self.assertNotIn('"tsconfig.json"', self.after_edit)

    def test_a_hand_removed_entry_is_not_re_added(self) -> None:
        self.assertNotIn('"tsconfig.json"', self.after_second, self.after_second)

    def test_the_second_refresh_reports_nothing_wired(self) -> None:
        self.assertIn("already current", self.second_output)


class FrameworkEntryDeliveredTest(CliRepoHarness, unittest.TestCase):
    """The framework half: a claimed language still has an unwired framework."""

    after: str
    refresh_output: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(EXPRESS_TREE, CLAIMS_BOTH_DIALECTS)
        proc = cls.wd("init", "--refresh")
        cls.refresh_output = f"{proc.stdout}\n{proc.stderr}"
        cls.after = (cls.root / ".weld" / "discover.yaml").read_text(
            encoding="utf-8",
        )

    def test_refresh_wires_the_express_entry(self) -> None:
        self.assertIn("strategy: express", self.after, self.after)

    def test_the_express_entry_claims_both_dialect_families(self) -> None:
        self.assertIn(f'- glob: "{EXPRESS_GLOB}"', self.after, self.after)

    def test_the_dialect_entries_are_not_duplicated(self) -> None:
        """The language comparison is silent here, and must stay silent."""
        self.assertEqual(self.after.count('strategy: tree_sitter'), 2, self.after)


class BothPassesOfferOneEntryTest(CliRepoHarness, unittest.TestCase):
    """A framework whose language is *also* unclaimed is wired once, not twice.

    Found in review of the change that added the entry comparison. The two
    passes overlap by construction on exactly this shape: the language pass
    emits a language's whole stack -- framework entries included -- and the
    entry pass emits framework entries whatever the language's claim status,
    which is the gap it exists for. A Go repo with gin in it and nothing
    claiming Go hits both, and the appended section carried two identical
    ``strategy: gin`` entries.

    Two entries on one glob is not merely untidy: it is the shape ADR 0103's
    node merge has to arbitrate, and it makes the config read as though the
    maintainer wired something twice on purpose.
    """

    after: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(
            {
                "go.mod": "module example.com/svc\n",
                "main.go": (
                    'package main\n\nimport (\n\t"github.com/gin-gonic/gin"'
                    "\n)\n\nfunc main() { r := gin.Default(); _ = r }\n"
                ),
            },
            "# generated-by: weld 0.20.0\nsources:\n"
            '  - glob: "docs/*.md"\n    type: doc\n    strategy: markdown\n',
        )
        cls.wd("init", "--refresh")
        cls.after = (cls.root / ".weld" / "discover.yaml").read_text(
            encoding="utf-8",
        )

    def test_the_unclaimed_language_is_wired(self) -> None:
        """The premise: both passes really do have something to say here."""
        self.assertIn("language: go", self.after, self.after)

    def test_the_framework_entry_is_wired_exactly_once(self) -> None:
        self.assertEqual(
            self.after.count("strategy: gin"), 1, self.after)

    def test_the_root_config_entry_is_wired_exactly_once(self) -> None:
        self.assertEqual(self.after.count('"go.mod"'), 1, self.after)


if __name__ == "__main__":
    unittest.main()

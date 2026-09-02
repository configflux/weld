"""Gap G1, as a probe through the real CLI: init must wire what it detects.

``wd init`` on the Node corpus printed that it found TypeScript *and*
JavaScript files and that it detected Express in ``services/api/src/legacy.js``
-- and then wrote a config that claimed neither the ``.tsx``/``.js`` dialects
nor express, and turned on no call evidence. Three-sevenths of the repo's
TypeScript and the whole of its JavaScript were invisible to the first
``wd discover`` a Node user ever ran (ADR 0142 D1, bd lrnx1.2).

The probe landed **red on purpose** and its marker was flipped by the fix, not
by this file (ADR 0142 D7); it is green from bd lrnx1.2 onward and its
assertions are the ones it was written with. It asserts the invariant, at both
ends of the same claim: the config init writes must *claim* every dialect it
counted and the framework it named, and the graph that follows must *hold*
those files. Either half alone is weaker than the pair -- a config with the
right globs and a discover that drops the files would satisfy the first, and an
accidental file node minted by some other strategy would satisfy the second.

Beside it is a pass-today assurance probe on detection itself. Detection is
the *input* the G1 fix consumes: if ``wd init`` stopped counting ``.tsx``
files or stopped recognising the express import, the fix above would be built
on sand and every other probe in this corpus would still be red for its own
unrelated reason. It is green today and must stay green.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._yaml import parse_yaml
from weld.strategies._glob_resolve import resolve_glob
from weld.tests._node_eval_corpus import MONOREPO_FILES
from weld.tests._node_eval_e2e_harness import (
    NodeEvalWorkspace,
    nodes_of_type,
    node_props,
)

#: The bd issue that owns G1's fix -- an issue-id suffix, the full ledger ids
#: being tracker-internal. The entry stays after the marker is flipped: it is
#: the ledger of who fixed G1, and the inventory guard reads it to check the
#: module against ADR 0142's own owner table.
_BD_FIXES = {"G1": "lrnx1.2"}

#: The three files a stock ``wd init`` left unclaimed before G1's fix: two
#: dialect siblings of a language it counted, and the JavaScript file it
#: detected a framework in. Their extensions are what G1 is about.
UNCLAIMED_DIALECT_FILES: tuple[str, ...] = (
    "apps/web/src/app/page.tsx",
    "apps/web/src/app/layout.tsx",
    "services/api/src/legacy.js",
)

#: What detection prints, and must keep printing. The counts are derived from
#: the corpus rather than retyped so that adding a file to the fixture cannot
#: leave a stale number asserting the wrong thing.
_TS_EXTENSIONS = (".ts", ".tsx")
_JS_EXTENSIONS = (".js",)
_EXPRESS_FILE = "services/api/src/legacy.js"

_WS: NodeEvalWorkspace | None = None
_TMP: tempfile.TemporaryDirectory | None = None
_INIT_OUTPUT: str = ""


def _count(extensions: tuple[str, ...]) -> int:
    return sum(
        1 for rel in MONOREPO_FILES if rel.endswith(extensions)
    )


def setUpModule() -> None:
    global _WS, _TMP, _INIT_OUTPUT
    _TMP = tempfile.TemporaryDirectory()
    _WS = NodeEvalWorkspace.monorepo(Path(_TMP.name))
    _INIT_OUTPUT = _WS.bootstrap_init().output
    # The graph half of G1's claim: what a Node user's very first
    # ``wd init && wd discover`` actually holds. Nothing is hand-wired here --
    # that is the point of this module, and the sibling modules that do wire
    # by hand say why they must.
    _WS.discover()


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def workspace() -> NodeEvalWorkspace:
    assert _WS is not None, "setUpModule did not run"
    return _WS


class InitWiringProbes(unittest.TestCase):
    ws: NodeEvalWorkspace

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()

    # -- what the generated config claims ---------------------------------

    def _claimed_files(self) -> set[str]:
        """Every repo file the generated ``discover.yaml`` actually claims.

        Resolved through the product's own globber rather than by reading the
        YAML as text: ``**/*.{ts,tsx}`` and two separate entries are the same
        claim, and a probe that pattern-matched strings would grade the
        spelling of the fix instead of its effect.
        """
        config = parse_yaml(self.ws.config_text())
        self.assertIsInstance(config, dict, "generated config is not a mapping")
        claimed: set[str] = set()
        for entry in config.get("sources") or []:
            if not isinstance(entry, dict):
                continue
            excludes = entry.get("exclude") or []
            if entry.get("glob"):
                claimed |= {
                    str(path.relative_to(self.ws.root))
                    for path in resolve_glob(self.ws.root, str(entry["glob"]), excludes)
                }
            for named in entry.get("files") or []:
                if (self.ws.root / str(named)).is_file():
                    claimed.add(str(named))
        return claimed

    def test_g1_init_wires_the_dialects_and_framework_it_detects(self) -> None:
        """A file init counted, or a framework it named, comes out wired.

        Four claims, and they are one claim seen from four sides: the dialect
        files are claimed by some source entry; express is wired because
        express was detected; the TypeScript entries emit call evidence, which
        is the raw material gap G2's resolution has to have (ADR 0142 D1); and
        the graph a plain ``wd discover`` then writes holds those files.

        Marker flipped by ``_BD_FIXES["G1"]``: the per-language source table
        (:mod:`weld._init_language_entries`) now writes one *family* glob per
        dialect family with ``emit_calls`` on, and express gained the framework
        adder its Go and Rust siblings always had
        (``weld._init_framework_sources._add_ts_js_framework_sources``). The
        assertions are unchanged -- what changed is the config underneath them.
        """
        config = parse_yaml(self.ws.config_text())
        sources = [e for e in (config.get("sources") or []) if isinstance(e, dict)]

        claimed = self._claimed_files()
        unclaimed = sorted(set(UNCLAIMED_DIALECT_FILES) - claimed)
        self.assertEqual(
            unclaimed, [],
            "`wd init` counted these files' languages and wired no source "
            f"entry that claims them:\n{self.ws.config_text()}",
        )

        strategies = {str(entry.get("strategy")) for entry in sources}
        self.assertIn(
            "express", strategies,
            "init detected Express and wired no express source entry; it has "
            f"{sorted(strategies)}",
        )

        ts_entries = [
            entry
            for entry in sources
            if str(entry.get("strategy")) == "tree_sitter"
            and str(entry.get("language")) == "typescript"
        ]
        self.assertTrue(ts_entries, "no TypeScript tree_sitter entry at all")
        self.assertTrue(
            all(str(entry.get("emit_calls")).lower() == "true" for entry in ts_entries),
            "the TypeScript entries init writes carry no emit_calls, so a "
            f"stock graph has no call evidence to resolve: {ts_entries}",
        )

        graph = self.ws.graph()
        held = {
            node_props(node).get("file")
            for node in nodes_of_type(graph, "file").values()
        }
        self.assertEqual(
            sorted(set(UNCLAIMED_DIALECT_FILES) - held), [],
            "a stock `wd init && wd discover` leaves these files out of the "
            f"graph entirely; it holds {sorted(f for f in held if f)}",
        )

    # -- pass-today assurance ---------------------------------------------

    def test_init_reports_every_dialect_and_the_framework_it_found(self) -> None:
        """Detection sees the whole repo -- the input the G1 fix consumes.

        Green today. It is asserted anyway because the finding is that init
        *detects and then discards*: if detection itself regressed, the probe
        above would still be red and would be blaming the wrong half.
        """
        output = _INIT_OUTPUT
        self.assertIn(
            f"Found {_count(_TS_EXTENSIONS)} Typescript files", output,
            f"init no longer counts every .ts/.tsx file:\n{output}",
        )
        self.assertIn(
            f"Found {_count(_JS_EXTENSIONS)} Javascript files", output,
            f"init no longer counts the .js file:\n{output}",
        )
        self.assertIn(
            f"Detected Express in {_EXPRESS_FILE}", output,
            f"init no longer detects the express require:\n{output}",
        )


if __name__ == "__main__":
    unittest.main()

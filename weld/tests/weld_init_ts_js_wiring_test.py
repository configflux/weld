"""``wd init`` wires the TypeScript/JavaScript it detects (ADR 0142 D1, gap G1).

``wd init`` counted a Next.js repo's ``.tsx`` files, counted its ``.js`` files,
named the Express it found in one of them -- and then wrote a config that
claimed none of the three. The dialect siblings fell through because the
per-language source table keyed a glob to *one* extension per language while
:data:`weld.init_detect.EXT_TO_LANG` counts four for the same two names; express
fell through because it was the one declared framework strategy (ADR 0071) with
no adder, and its rows in :data:`weld.init_detect.FRAMEWORK_PATTERNS` named
``python_module`` -- a Python strategy for a Node framework, which is how a
reader of that table could not see that nothing wired express at all.

``weld_node_eval_init_e2e_test`` is the system-level probe for the same claim,
driven through the real CLI on a realistic npm-workspaces monorepo. These are
its unit-level siblings, and they exist for the parts that probe cannot ask
cheaply: the shape a *TSX-only* repo gets, whether express stays out of a repo
that has none, the emission order the orchestrator merge depends on, and the
``--refresh`` side of the same table.

Claims are asserted by *resolving* the emitted globs against the tree, never by
matching their spelling: ``**/*.{ts,tsx}`` and two per-extension entries are the
same claim, and a test that graded the string would grade the shape of the fix
rather than its effect.
"""

from __future__ import annotations

import importlib
import io
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

from weld._unclaimed_sources import (
    detect_unclaimed_from_sources,
    detect_unclaimed_source_classes,
)
from weld._yaml import parse_yaml
from weld.init import init as init_run
from weld.init_detect import detect_frameworks, scan_files
from weld.strategies._glob_resolve import resolve_glob

#: A Next.js-shaped app: a default-exported page in ``.tsx`` beside a plain
#: ``.ts`` module. No express anywhere -- it is the negative case for the
#: framework entry as well as the positive one for the dialect family.
_NEXT_APP: dict[str, str] = {
    "package.json": '{"name": "web", "private": true}\n',
    "src/app/page.tsx": "export default function Home() {\n  return null;\n}\n",
    "src/lib/money.ts": 'export const CURRENCY = "USD";\n',
}

#: An express service written across the whole JavaScript dialect family: an
#: ES-module ``.ts`` server, a CommonJS ``.js`` module, a ``.jsx`` component and
#: build scripts in both module spellings ``node`` accepts.
_EXPRESS_SERVICE: dict[str, str] = {
    "package.json": '{"name": "api", "private": true}\n',
    "src/server.ts": (
        'import express from "express";\n\n'
        "export const app = express();\n\n"
        'app.get("/health", (_req, res) => res.json({ ok: true }));\n'
    ),
    "src/legacy.js": (
        'const express = require("express");\n\n'
        "const router = express.Router();\n\n"
        "module.exports = { router };\n"
    ),
    "src/widget.jsx": "export const Widget = () => null;\n",
    "scripts/build.mjs": "export const build = () => null;\n",
    "scripts/postinstall.cjs": "module.exports = () => null;\n",
}


def _lay_out(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


class _Init:
    """One ``wd init`` run: the tree it ran on, and the config it wrote."""

    def __init__(self, root: Path, raw: str) -> None:
        self.root = root
        self.raw = raw
        config = parse_yaml(raw)
        entries = config.get("sources") or [] if isinstance(config, dict) else []
        self.sources: list[dict] = [e for e in entries if isinstance(e, dict)]

    @property
    def strategies(self) -> set[str]:
        return {str(entry.get("strategy")) for entry in self.sources}

    def entries(self, strategy: str, language: str | None = None) -> list[dict]:
        return [
            entry
            for entry in self.sources
            if str(entry.get("strategy")) == strategy
            and (language is None or str(entry.get("language")) == language)
        ]

    def claimed_by(self, entries: list[dict]) -> set[str]:
        """Every repo file *entries* claim, resolved by the product's globber."""
        return {
            path.relative_to(self.root).as_posix()
            for entry in entries
            for path in resolve_glob(
                self.root, str(entry.get("glob", "")), entry.get("exclude") or [],
            )
        }


@contextmanager
def _initialised(files: dict[str, str]):
    """Lay *files* down in a tempdir, run ``wd init``, yield the :class:`_Init`.

    The tree stays alive for the body because every claim here is asserted by
    resolving a glob against it -- the files have to still be there.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _lay_out(root, files)
        out = root / ".weld" / "discover.yaml"
        with redirect_stderr(io.StringIO()):
            assert init_run(root, out, force=True), "wd init reported failure"
        yield _Init(root, out.read_text(encoding="utf-8"))


class DialectFamilyWiringTest(unittest.TestCase):
    """Every dialect ``wd init`` counted is claimed by an entry it wrote."""

    def test_the_typescript_entry_claims_both_of_its_dialects(self) -> None:
        with _initialised(_NEXT_APP) as init:
            entries = init.entries("tree_sitter", "typescript")
            self.assertTrue(entries, f"no TypeScript entry at all:\n{init.raw}")
            claimed = init.claimed_by(entries)
        self.assertEqual(
            sorted({"src/app/page.tsx", "src/lib/money.ts"} - claimed), [],
            "the TypeScript entry claims neither dialect it counted; it "
            f"claims {sorted(claimed)}",
        )

    def test_the_javascript_entry_claims_its_whole_family(self) -> None:
        """``.js``, ``.jsx``, ``.mjs`` and ``.cjs`` are all JavaScript.

        ``.mjs`` and ``.cjs`` are in the family glob without being in
        :data:`weld.init_detect.EXT_TO_LANG`: the counter only has to answer
        *is there JavaScript here*, which one ``.js`` file settles, while the
        glob has to claim every file that answer implies.
        """
        with _initialised(_EXPRESS_SERVICE) as init:
            entries = init.entries("tree_sitter", "javascript")
            self.assertTrue(entries, f"no JavaScript entry at all:\n{init.raw}")
            claimed = init.claimed_by(entries)
        expected = {
            "src/legacy.js", "src/widget.jsx",
            "scripts/build.mjs", "scripts/postinstall.cjs",
        }
        self.assertEqual(
            sorted(expected - claimed), [],
            f"the JavaScript entry claims only {sorted(claimed)}",
        )

    def test_a_tsx_only_repo_is_wired_at_all(self) -> None:
        """The narrowest shape of the finding: no ``.ts`` file to carry it.

        A Next.js app whose components are all ``.tsx`` used to init to a
        config with a ``**/*.ts`` entry that matched nothing -- wired, and
        empty. Every component invisible, no warning anywhere.
        """
        with _initialised({
            "package.json": '{"name": "ui"}\n',
            "src/App.tsx": "export default function App() {\n  return null;\n}\n",
        }) as init:
            entries = init.entries("tree_sitter", "typescript")
            self.assertTrue(entries, f"a .tsx repo wired no TypeScript:\n{init.raw}")
            self.assertIn("src/App.tsx", init.claimed_by(entries))

    def test_the_entry_comment_names_the_dialects_its_glob_claims(self) -> None:
        """The generated comment is read back out of the glob, not retyped."""
        with _initialised(_EXPRESS_SERVICE) as init:
            raw = init.raw
        self.assertIn("Typescript sources (.ts, .tsx)", raw)
        self.assertIn("Javascript sources (.js, .jsx, .mjs, .cjs)", raw)

    def test_both_family_entries_carry_call_evidence(self) -> None:
        """``emit_calls`` is what gap G2's resolution has to resolve.

        A stock TypeScript graph with no ``calls`` edges leaves ``wd callers``
        nothing to answer from, however good the resolver (ADR 0142 D1).
        """
        with _initialised(_EXPRESS_SERVICE) as init:
            entries = (
                init.entries("tree_sitter", "typescript")
                + init.entries("tree_sitter", "javascript")
            )
            self.assertEqual(len(entries), 2, init.raw)
            for entry in entries:
                self.assertIn(
                    str(entry.get("emit_calls")).lower(), ("true",),
                    f"no call evidence on {entry}",
                )


class ExpressWiringTest(unittest.TestCase):
    """A framework ``wd init`` names is a framework it wires."""

    def test_detected_express_is_wired_as_a_route_source(self) -> None:
        with _initialised(_EXPRESS_SERVICE) as init:
            entries = init.entries("express")
            self.assertTrue(
                entries,
                f"init detected express and wired none of it:\n{init.raw}",
            )
            claimed = init.claimed_by(entries)
            types = {str(entry.get("type")) for entry in entries}
        self.assertEqual(types, {"route"}, "express emits route nodes")
        self.assertEqual(
            sorted({"src/server.ts", "src/legacy.js"} - claimed), [],
            "the express entry misses a dialect express is registered in; it "
            f"claims {sorted(claimed)}",
        )

    def test_express_is_not_wired_where_it_is_not_detected(self) -> None:
        """Gated on detection, exactly like its gin and axum siblings."""
        with _initialised(_NEXT_APP) as init:
            self.assertNotIn("express", init.strategies, init.raw)

    def test_the_express_entry_precedes_the_tree_sitter_entries(self) -> None:
        """Emission order is the ADR 0071 merge contract, not cosmetics.

        The framework strategy emits a thin boundary-file placeholder for the
        file a route is registered in; the tree-sitter entry emits the
        canonical ``file:`` node for the same path. The later one wins the
        orchestrator merge, so the framework entry goes first.
        """
        with _initialised(_EXPRESS_SERVICE) as init:
            express_at = init.raw.find("strategy: express")
            tree_sitter_at = init.raw.find("strategy: tree_sitter")
        self.assertNotEqual(express_at, -1, "no express entry emitted")
        self.assertNotEqual(tree_sitter_at, -1, "no tree_sitter entry emitted")
        self.assertLess(
            express_at, tree_sitter_at,
            "express is emitted after tree_sitter, so its placeholder file "
            "node would win the merge over the canonical one",
        )

    def test_detection_names_the_strategy_that_extracts_express(self) -> None:
        """``FRAMEWORK_PATTERNS`` maps Express to a strategy that exists.

        It used to map to ``python_module``. Nothing read the field, so
        nothing broke -- it simply made the table state something false about
        a framework nothing wired, which is the shape of the whole finding.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, _EXPRESS_SERVICE)
            detected = {
                framework: strategy
                for framework, strategy, _path in detect_frameworks(
                    root, scan_files(root))
            }
        self.assertEqual(detected.get("Express"), "express", detected)
        # ... and the name it now gives resolves to a real bundled strategy,
        # the way ``weld._discover_strategies.load_strategy`` resolves one.
        module = importlib.import_module(f"weld.strategies.{detected['Express']}")
        self.assertTrue(
            callable(getattr(module, "extract", None)),
            f"{module.__name__} is not a strategy: it has no extract()",
        )

    def test_the_generated_catalog_documents_express(self) -> None:
        """The header lists the strategies a reader can wire by hand.

        express was absent from it while gin and axum were listed, so the one
        Node framework weld ships read as one it did not.
        """
        with _initialised(_EXPRESS_SERVICE) as init:
            raw = init.raw
        self.assertRegex(raw, r"#\s+express\s+—")


class JavaScriptIsAClaimableLanguageTest(unittest.TestCase):
    """``wd doctor`` / ``wd init --refresh`` can see an unwired JS repo.

    JavaScript was absent from
    :data:`weld._unclaimed_sources._CLAIMING_STRATEGIES`, so a repo whose
    ``.js`` files nothing wired was not merely unreported -- it was
    *unrefreshable*, because ``wd init --refresh`` wires exactly the languages
    that detector returns. The omission was invisible while ``wd init`` had no
    JavaScript entry to offer.
    """

    def test_javascript_with_nothing_wired_is_reported(self) -> None:
        # Since ADR 0144 the entries are what claims, so the non-claiming ones
        # carry a glob that *does* match the .js files: the strategy is why
        # they do not claim them, not the pattern.
        sources = [
            {"glob": "**/*", "type": "doc", "strategy": "markdown"},
            {"glob": "**/*", "type": "config", "strategy": "config_file"},
        ]
        unclaimed = detect_unclaimed_from_sources(
            sources, [f"src/mod{i}.js" for i in range(4)])
        self.assertEqual(
            [(item.language, item.file_count) for item in unclaimed],
            [("javascript", 4)],
        )

    def test_javascript_with_a_claiming_strategy_is_silent(self) -> None:
        for strategy in ("tree_sitter", "express"):
            with self.subTest(wired=strategy):
                entry = {
                    "glob": "**/*.js", "type": "file", "strategy": strategy,
                }
                self.assertEqual(
                    detect_unclaimed_from_sources(
                        [entry], [f"src/mod{i}.js" for i in range(4)]),
                    [],
                )

    def test_a_javascript_repo_on_disk_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, {
                "src/legacy.js": "module.exports = {};\n",
                ".weld/discover.yaml": (
                    "sources:\n"
                    '  - glob: "**/*.md"\n'
                    "    type: doc\n"
                    "    strategy: markdown\n"
                ),
            })
            unclaimed = detect_unclaimed_source_classes(root)
        self.assertEqual([item.language for item in unclaimed], ["javascript"])


if __name__ == "__main__":
    unittest.main()

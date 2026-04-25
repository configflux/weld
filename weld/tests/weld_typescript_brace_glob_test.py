"""Tests for brace-expansion glob support in typescript_exports strategy.

Context: the ``examples/04-monorepo-typescript`` discover.yaml uses glob
patterns of the form ``packages/ui/src/**/*.{ts,tsx}``. ``pathlib.Path.glob``
does not natively expand brace alternatives, so prior to the fix these
globs silently matched zero files and no file nodes were emitted for the
ui package or apps/web sources.

These tests verify that:

1. ``_resolve_glob`` expands brace alternatives and returns both .ts and
   .tsx files when the pattern is ``**/*.{ts,tsx}``.
2. ``extract`` emits file nodes for .tsx files when the glob uses brace
   expansion (regex fallback path, since tree-sitter TypeScript grammar
   is not a declared dependency).
3. Plain globs without braces still work (regression guard).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class BraceExpandResolveGlobTest(unittest.TestCase):
    """``_resolve_glob`` must honour brace alternatives."""

    def _make_monorepo(self, tmp: str) -> Path:
        root = Path(tmp)
        ui_src = root / "packages" / "ui" / "src"
        ui_src.mkdir(parents=True)
        (ui_src / "Button.tsx").write_text(
            "export function Button() { return null; }\n"
        )
        (ui_src / "Card.tsx").write_text(
            "export function Card() { return null; }\n"
        )
        (ui_src / "index.ts").write_text(
            'export * from "./Button";\nexport * from "./Card";\n'
        )
        return root

    def test_brace_glob_matches_both_ts_and_tsx(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_monorepo(td)
            matched, _dirs = typescript_exports._resolve_glob(
                root, "packages/ui/src/**/*.{ts,tsx}"
            )
        rels = sorted(p.relative_to(root).as_posix() for p in matched)
        self.assertIn("packages/ui/src/Button.tsx", rels)
        self.assertIn("packages/ui/src/Card.tsx", rels)
        self.assertIn("packages/ui/src/index.ts", rels)
        self.assertEqual(len(rels), 3)

    def test_brace_glob_deduplicates(self) -> None:
        """Repeated alternatives must not double-count a file."""
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_monorepo(td)
            matched, _dirs = typescript_exports._resolve_glob(
                root, "packages/ui/src/**/*.{ts,ts,tsx}"
            )
        rels = [p.relative_to(root).as_posix() for p in matched]
        self.assertEqual(sorted(rels), sorted(set(rels)))

    def test_plain_glob_without_braces_still_works(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_monorepo(td)
            matched, _dirs = typescript_exports._resolve_glob(
                root, "packages/ui/src/**/*.ts"
            )
        rels = sorted(p.relative_to(root).as_posix() for p in matched)
        self.assertEqual(rels, ["packages/ui/src/index.ts"])


class BraceGlobExtractionTest(unittest.TestCase):
    """End-to-end: ``extract`` emits file nodes for .tsx via brace glob.

    Uses the regex fallback (tree-sitter unavailable) because the repo
    does not pin ``tree-sitter-typescript`` and we want the test to be
    deterministic regardless of environment.
    """

    def _make_web_app(self, tmp: str) -> Path:
        root = Path(tmp)
        web_src = root / "apps" / "web" / "src"
        web_src.mkdir(parents=True)
        (web_src / "App.tsx").write_text(
            textwrap.dedent("""\
                export function App() { return null; }
                export const VERSION = "1.0";
            """)
        )
        (web_src / "layout.tsx").write_text(
            "export function Layout() { return null; }\n"
        )
        return root

    def test_extract_emits_tsx_nodes_via_brace_glob(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_web_app(td)
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root,
                    {
                        "glob": "apps/web/src/**/*.{ts,tsx}",
                        "id_prefix": "web",
                        "package": "pkg:web",
                    },
                    {},
                )
        files = sorted(
            node["props"]["file"] for node in result.nodes.values()
        )
        self.assertIn("apps/web/src/App.tsx", files)
        self.assertIn("apps/web/src/layout.tsx", files)
        # Every emitted node must reach the enclosing package.
        self.assertTrue(result.edges)
        for edge in result.edges:
            self.assertEqual(edge["from"], "pkg:web")


if __name__ == "__main__":
    unittest.main()

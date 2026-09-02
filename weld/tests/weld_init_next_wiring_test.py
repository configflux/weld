"""``wd init`` detects Next.js from its markers and wires it (ADR 0142 D4).

Every other framework ``wd init`` knows is found by reading an import line.
Next.js cannot be, and this file exists mostly to hold that difference still:
an app-router handler imports nothing from ``next``, so a repository can be a
whole Next.js application with no ``from "next"`` in it and an import scan
would report nothing to wire. Detection therefore keys on the two markers
``create-next-app`` writes -- a ``next.config.*`` and a ``next`` dependency --
and the tests below are the positive and negative cases for each.

The negative cases are the point as much as the positive ones. A marker check
that fires on ``next-auth``, on a ``"next"`` *script* name, or on a manifest
it could not parse would wire a strategy on no evidence, and detected-but-wrong
is worse than not detected: it puts a route source into a config the user then
has to un-wire.

``weld_init_ts_js_wiring_test`` is the sibling for the import-scanned half of
the same table (express, the dialect families, emission order), and
``weld_node_eval_init_e2e_test`` is the system-level probe that drives the
real CLI over a realistic npm-workspaces monorepo. Claims here are asserted by
*resolving* the emitted globs against the tree rather than by matching their
spelling, the same rule those two follow.
"""

from __future__ import annotations

import importlib
import io
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

from weld._init_next_markers import NEXT_FRAMEWORK, detect_next_framework
from weld._unclaimed_sources import detect_unclaimed_from_sources
from weld._yaml import parse_yaml
from weld.init import init as init_run
from weld.init_detect import detect_frameworks, scan_files
from weld.strategies._glob_resolve import resolve_glob

#: A Next.js app-router application: both markers, a handler module, a page,
#: and a plain module beside them. The handler imports nothing from ``next``,
#: which is the whole reason the marker scan exists.
_NEXT_APP: dict[str, str] = {
    "package.json": (
        '{"name": "web", "private": true,\n'
        ' "dependencies": {"next": "15.0.3", "react": "18.3.1"}}\n'
    ),
    "next.config.mjs": "export default { reactStrictMode: true };\n",
    "src/app/page.tsx": "export default function Home() {\n  return null;\n}\n",
    "src/app/api/orders/route.ts": (
        "export async function GET(): Promise<Response> {\n"
        "  return Response.json({});\n}\n"
    ),
    "src/lib/money.ts": 'export const CURRENCY = "USD";\n',
}

#: The same repository with both markers removed: a TypeScript app that has a
#: directory called ``app`` and is not Next.js. The negative case for wiring.
_PLAIN_TS_APP: dict[str, str] = {
    "package.json": (
        '{"name": "web", "private": true,\n'
        ' "dependencies": {"react": "18.3.1"}}\n'
    ),
    "src/app/page.tsx": "export default function Home() {\n  return null;\n}\n",
    "src/lib/money.ts": 'export const CURRENCY = "USD";\n',
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

    def entries(self, strategy: str) -> list[dict]:
        return [
            entry for entry in self.sources
            if str(entry.get("strategy")) == strategy
        ]

    def index_of(self, strategy: str) -> int:
        """Position of the first *strategy* entry, or ``-1`` when unwired."""
        for index, entry in enumerate(self.sources):
            if str(entry.get("strategy")) == strategy:
                return index
        return -1

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
    """Lay *files* down in a tempdir, run ``wd init``, yield the :class:`_Init`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _lay_out(root, files)
        out = root / ".weld" / "discover.yaml"
        with redirect_stderr(io.StringIO()):
            assert init_run(root, out, force=True), "wd init reported failure"
        yield _Init(root, out.read_text(encoding="utf-8"))


@contextmanager
def _detected(files: dict[str, str]):
    """Lay *files* down and yield ``detect_next_framework``'s answer."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _lay_out(root, files)
        yield detect_next_framework(root, scan_files(root))


class NextMarkerDetectionTest(unittest.TestCase):
    """Which repositories are Next.js repositories, and which are not."""

    def test_next_config_is_a_marker(self) -> None:
        with _detected({"next.config.mjs": "export default {};\n"}) as hits:
            self.assertEqual(
                hits, [(NEXT_FRAMEWORK, "next", "next.config.mjs")],
            )

    def test_every_next_config_extension_is_a_marker(self) -> None:
        """Next loads its config from six extensions; all six count."""
        for name in (
            "next.config.js", "next.config.mjs", "next.config.cjs",
            "next.config.ts", "next.config.mts", "next.config.cts",
        ):
            with self.subTest(config=name):
                with _detected({name: "export default {};\n"}) as hits:
                    self.assertTrue(hits, f"{name} did not fire the marker")

    def test_a_next_dependency_is_a_marker(self) -> None:
        with _detected(
            {"package.json": '{"dependencies": {"next": "15.0.3"}}\n'},
        ) as hits:
            self.assertEqual(
                hits, [(NEXT_FRAMEWORK, "next", "package.json")],
            )

    def test_a_dev_dependency_is_a_marker_too(self) -> None:
        """An app only built in CI legitimately keeps ``next`` in devDeps."""
        with _detected(
            {"package.json": '{"devDependencies": {"next": "15.0.3"}}\n'},
        ) as hits:
            self.assertTrue(hits)

    def test_a_workspace_child_manifest_is_found(self) -> None:
        """The marker is rarely at the root: a monorepo puts it under apps/."""
        with _detected({
            "package.json": '{"workspaces": ["apps/*"]}\n',
            "apps/web/package.json": '{"dependencies": {"next": "15.0.3"}}\n',
        }) as hits:
            self.assertEqual(
                [hit[2] for hit in hits], ["apps/web/package.json"],
            )

    def test_a_repo_with_neither_marker_is_not_next(self) -> None:
        with _detected(_PLAIN_TS_APP) as hits:
            self.assertEqual(hits, [])

    def test_a_longer_package_name_is_not_the_marker(self) -> None:
        """``next-auth`` / ``next-themes`` are other packages entirely."""
        with _detected({
            "package.json": (
                '{"dependencies": {"next-auth": "5.0.0", '
                '"next-themes": "0.3.0"}}\n'
            ),
        }) as hits:
            self.assertEqual(hits, [])

    def test_a_next_script_is_not_the_marker(self) -> None:
        """Only the dependency tables are read, not ``scripts`` or ``name``.

        A repository whose build script happens to be called ``next`` -- or
        that is itself *named* ``next`` -- is not thereby a Next.js app.
        """
        with _detected({
            "package.json": '{"name": "next", "scripts": {"next": "echo"}}\n',
        }) as hits:
            self.assertEqual(hits, [])

    def test_an_unparseable_manifest_is_not_a_marker(self) -> None:
        """A broken file is no evidence; wiring on it would be a guess."""
        with _detected({"package.json": "{not json at all\n"}) as hits:
            self.assertEqual(hits, [])

    def test_detection_is_deterministic_across_runs(self) -> None:
        """Two markers in one tree still report one, stable path (ADR 0012)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, {
                "apps/web/package.json": '{"dependencies": {"next": "15"}}\n',
                "apps/admin/package.json": '{"dependencies": {"next": "15"}}\n',
            })
            files = scan_files(root)
            answers = {
                tuple(detect_next_framework(root, files)) for _ in range(3)
            }
        self.assertEqual(len(answers), 1, answers)


class NextJoinsTheFrameworkTableTest(unittest.TestCase):
    """The marker scan is folded into ``detect_frameworks``' own answer."""

    def test_detect_frameworks_reports_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lay_out(root, _NEXT_APP)
            detected = {
                fw: strategy
                for fw, strategy, _path in detect_frameworks(root, scan_files(root))
            }
        self.assertEqual(detected.get(NEXT_FRAMEWORK), "next", detected)

    def test_the_named_strategy_exists_and_is_loadable(self) -> None:
        """Detected-but-unwirable is the failure mode ADR 0142 D1 recorded.

        Resolved the way :func:`weld._discover_strategies.load_strategy`
        resolves one, so a framework row naming a module that is not a
        strategy fails here rather than at the user's first ``wd discover``.
        """
        module = importlib.import_module("weld.strategies.next")
        self.assertTrue(
            callable(getattr(module, "extract", None)),
            f"{module.__name__} is not a strategy: it has no extract()",
        )


class NextWiringTest(unittest.TestCase):
    """A framework ``wd init`` names is a framework it wires."""

    def test_detected_next_is_wired_as_a_route_source(self) -> None:
        with _initialised(_NEXT_APP) as init:
            entries = init.entries("next")
            self.assertTrue(
                entries,
                f"init detected Next.js and wired none of it:\n{init.raw}",
            )
            claimed = init.claimed_by(entries)
            types = {str(entry.get("type")) for entry in entries}
        self.assertEqual(types, {"route"}, "next emits route nodes")
        self.assertEqual(
            sorted({"src/app/api/orders/route.ts", "src/app/page.tsx"} - claimed),
            [],
            "the next entry misses an app-router file; it claims "
            f"{sorted(claimed)}",
        )

    def test_next_is_not_wired_where_it_is_not_detected(self) -> None:
        """Gated on detection, exactly like its express / gin / axum siblings.

        The fixture has a ``src/app/page.tsx``, so this also pins the ADR 0071
        gate contract (bd lrnx1.7): what wires a framework strategy is the
        *marker*, never a directory name. A React app with an ``app`` folder
        is not a Next.js app.
        """
        with _initialised(_PLAIN_TS_APP) as init:
            self.assertNotIn("next", init.strategies, init.raw)

    def test_the_next_entry_precedes_the_tree_sitter_entries(self) -> None:
        """Emission order is the ADR 0071 merge contract, not cosmetics.

        The route strategy emits a thin boundary-file placeholder for the file
        a route is declared in; the tree-sitter entry emits the canonical
        ``file:`` node for the same path. The later entry wins the
        orchestrator merge, so the framework entry has to come first or the
        placeholder replaces the real file node.
        """
        with _initialised(_NEXT_APP) as init:
            next_at = init.index_of("next")
            tree_sitter_at = init.index_of("tree_sitter")
            raw = init.raw
        self.assertNotEqual(next_at, -1, raw)
        self.assertNotEqual(tree_sitter_at, -1, raw)
        self.assertLess(next_at, tree_sitter_at, raw)

    def test_the_generated_catalog_documents_next(self) -> None:
        """The header lists the strategies a reader can wire by hand."""
        with _initialised(_NEXT_APP) as init:
            raw = init.raw
        self.assertRegex(raw, r"#\s+next\s+—")

    def test_next_claims_the_typescript_source_class(self) -> None:
        """``wd doctor`` must not call a next-wired repo unclaimed.

        ``wd init --refresh`` wires exactly the languages the unclaimed
        detector returns, so a strategy missing from its table makes a repo
        that *is* wired look like one that needs re-wiring. Since ADR 0144 a
        claim is a matched file, so the entry carries the glob that matches
        the app's sources rather than only the strategy name.
        """
        for language, ext in (("typescript", ".ts"), ("javascript", ".js")):
            with self.subTest(language=language):
                entry = {
                    "glob": f"**/*{ext}", "type": "file", "strategy": "next",
                }
                paths = [f"app/mod{i}{ext}" for i in range(4)]
                self.assertEqual(
                    detect_unclaimed_from_sources([entry], paths), [],
                )


if __name__ == "__main__":
    unittest.main()

"""Tests for the ``weld`` adapter dependency gate in the public benchmark.

ADR 0059 honest-losing posture: when the tree-sitter Python bindings are
not installed, the underlying ``tree_sitter`` strategy silently no-ops
on ImportError (intentional graceful degradation -- see
``weld/strategies/tree_sitter.py:46``). The bench then scored 0 against
``answer_files`` and reported the row as ``status='ok'`` with F1=0.00,
which dishonestly classifies "feature didn't load" as "feature ran and
missed".

The fix mirrors the three-gate cascade already in
:mod:`weld.bench.adapters.weld_libclang`: when tree-sitter is missing
AND the task targets a language that requires it (inferred from the
answer-file extensions), the adapter reports ``status='unavailable'``
so the row is excluded from per-family aggregates rather than
masquerading as a quality miss.

These tests cover the new precondition surface; the existing
``WeldAdapterTest`` in ``weld_public_bench_adapters_test.py`` continues
to cover the present/Python path.

The :class:`EnsureGraphBootstrapsConfigTest` block at the bottom covers
a separate root cause: ``_ensure_graph`` previously called
``weld.discover.discover`` directly without first generating a
``.weld/discover.yaml``. On a fresh nlohmann/json clone with no
``.weld/`` tree, ``discover.py`` defaulted to ``sources=[]`` and minted
zero nodes, so every C++ bench row scored F1=0.00 regardless of
extractor quality. The fix bootstraps a default config via
:func:`weld.init.init` before invoking discovery.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bench._public_runner import PublicTask  # noqa: E402
from weld.bench.adapters import weld as weld_adapter  # noqa: E402


def _task(family: str = "navigation", **overrides) -> PublicTask:
    """Build a task with cpp answer-files by default (the bug class)."""
    defaults = {
        "repo_id": "njson",
        "id": "njson-nav-01",
        "family": family,
        "prompt": "x",
        "term": "basic_json",
        "symbol": "basic_json",
        "answer_files": ("single_include/nlohmann/json.hpp",),
    }
    defaults.update(overrides)
    return PublicTask(**defaults)


class LanguageInferenceTest(unittest.TestCase):
    """Unit tests for the answer-file -> language(s) inference helper."""

    def test_cpp_extensions(self) -> None:
        # The C++ family the bug originally triggered on -- all common
        # header / source extensions must map to ``cpp``.
        for ext in (".hpp", ".cpp", ".h", ".cc", ".cxx", ".hxx"):
            with self.subTest(ext=ext):
                langs = weld_adapter._tree_sitter_languages_for_task(
                    _task(answer_files=(f"include/foo{ext}",)),
                )
                self.assertIn("cpp", langs)

    def test_python_does_not_require_tree_sitter(self) -> None:
        # Python uses the ``python_module`` strategy, NOT tree-sitter,
        # so a python-only task must not appear in the required set.
        langs = weld_adapter._tree_sitter_languages_for_task(
            _task(answer_files=("src/flask/app.py",)),
        )
        self.assertNotIn("python", langs)
        # In fact, the required-tree-sitter set is empty.
        self.assertEqual(langs, set())

    def test_csharp_go_java_rust_typescript(self) -> None:
        for ext, lang in (
            (".cs", "csharp"),
            (".go", "go"),
            (".java", "java"),
            (".rs", "rust"),
            (".ts", "typescript"),
            (".tsx", "typescript"),
        ):
            with self.subTest(ext=ext, lang=lang):
                langs = weld_adapter._tree_sitter_languages_for_task(
                    _task(answer_files=(f"src/foo{ext}",)),
                )
                self.assertIn(lang, langs)

    def test_unknown_extension_returns_empty(self) -> None:
        # An answer file with no recognised extension (e.g. plain text
        # config) must NOT mark the task as tree-sitter-required, so the
        # adapter stays runnable on Python-only / mixed paths.
        langs = weld_adapter._tree_sitter_languages_for_task(
            _task(answer_files=("docs/README.md",)),
        )
        self.assertEqual(langs, set())

    def test_empty_answer_files_returns_empty(self) -> None:
        langs = weld_adapter._tree_sitter_languages_for_task(
            _task(answer_files=()),
        )
        self.assertEqual(langs, set())

    def test_mixed_python_and_cpp_only_returns_cpp(self) -> None:
        # If a task has answer files in multiple languages, only the
        # ones that actually need tree-sitter appear in the set.
        langs = weld_adapter._tree_sitter_languages_for_task(
            _task(
                answer_files=(
                    "tools/foo.py",
                    "include/bar.hpp",
                ),
            ),
        )
        self.assertEqual(langs, {"cpp"})


class TreeSitterUnavailableGateTest(unittest.TestCase):
    """When tree-sitter is missing and the task targets it -> ``unavailable``."""

    def test_cpp_task_reports_unavailable_when_tree_sitter_missing(self) -> None:
        # The original bug: a C++ task scored F1=0.00 because the
        # tree_sitter strategy silently returned 0 nodes. The adapter
        # must instead report ``unavailable`` so per-family aggregates
        # exclude the row (ADR 0059).
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=False,
            ):
                result = weld_adapter.run(
                    _task(), Path(repo_root),
                )
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.files, [])
            self.assertEqual(result.tokens, 0)
            self.assertIn("tree-sitter", result.error.lower())

    def test_python_task_runs_when_tree_sitter_missing(self) -> None:
        # Python tasks must not be gated on tree-sitter -- the
        # ``python_module`` strategy handles them. Even when the
        # tree-sitter probe fails the adapter must continue and produce
        # an ``ok``-or-``degraded`` envelope (i.e. NOT ``unavailable``).
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=False,
            ):
                result = weld_adapter.run(
                    _task(
                        repo_id="flask",
                        id="flask-nav-01",
                        answer_files=("src/flask/app.py",),
                    ),
                    Path(repo_root),
                )
            self.assertIn(result.status, ("ok", "degraded"))

    def test_unknown_extensions_run_when_tree_sitter_missing(self) -> None:
        # Tasks with answer files that don't map to any tree-sitter
        # language must not be gated -- continue the existing path.
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=False,
            ):
                result = weld_adapter.run(
                    _task(answer_files=("docs/README.md",)),
                    Path(repo_root),
                )
            self.assertIn(result.status, ("ok", "degraded"))

    @unittest.skipUnless(
        weld_adapter._is_tree_sitter_available(),
        "tree-sitter not installed; patched availability cannot stand in for the real module",
    )
    def test_tree_sitter_present_runs_normally(self) -> None:
        # When tree-sitter IS importable, the adapter does NOT short-
        # circuit -- it goes through ``_ensure_graph`` like before.
        # Skip when tree-sitter is genuinely absent: patching the probe
        # to True does not make the underlying module importable, and
        # downstream discover/file_index paths return ``unavailable``
        # for an empty tempdir, defeating the assertion.
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=True,
            ):
                result = weld_adapter.run(_task(), Path(repo_root))
            self.assertNotEqual(result.status, "unavailable")

    def test_csharp_task_reports_unavailable_when_tree_sitter_missing(self) -> None:
        # Same bug class as cpp but for C# answer files.
        with tempfile.TemporaryDirectory() as repo_root:
            with patch.object(
                weld_adapter, "_is_tree_sitter_available",
                return_value=False,
            ):
                result = weld_adapter.run(
                    _task(
                        repo_id="eshop",
                        id="eshop-nav-01",
                        answer_files=("src/Catalog.API/Program.cs",),
                    ),
                    Path(repo_root),
                )
            self.assertEqual(result.status, "unavailable")
            self.assertIn("tree-sitter", result.error.lower())


class TreeSitterAvailabilityProbeTest(unittest.TestCase):
    """The real probe must return a bool and not raise."""

    def test_probe_returns_bool(self) -> None:
        # Whatever the env, the function must return a bool; ImportError
        # must NOT propagate.
        result = weld_adapter._is_tree_sitter_available()
        self.assertIsInstance(result, bool)


class EnsureGraphBootstrapsConfigTest(unittest.TestCase):
    """``_ensure_graph`` must run ``wd init`` when no discover.yaml exists.

    Root cause for cpp F1=0.00 (see module docstring): without a
    ``.weld/discover.yaml`` :func:`weld.discover.discover` defaults to
    ``sources=[]`` and emits zero nodes. The bench tempdir for a fresh
    nlohmann/json clone has no ``.weld/`` tree, so every cpp bench row
    scored 0 even though tree-sitter was installed. ``_ensure_graph``
    must therefore bootstrap the default config (in-process equivalent
    of ``wd init``) before invoking discovery.

    Hermetic: writes a tiny cpp file inside a tempdir; the only side
    effects are the ``.weld/`` subdirectory inside that tempdir.
    """

    def _read_graph_node_count(self, repo_root: Path) -> int:
        graph_path = repo_root / ".weld" / "graph.json"
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
        nodes = payload.get("nodes") or {}
        return len(nodes)

    @unittest.skipUnless(
        weld_adapter._is_tree_sitter_available(),
        "tree-sitter not installed; cpp node extraction cannot run",
    )
    def test_fresh_cpp_tempdir_yields_nonempty_graph(self) -> None:
        # The exact bug class: a fresh tempdir with cpp source files but
        # no .weld/ tree. Before the fix, _ensure_graph called discover()
        # with sources=[] and the resulting graph had 0 nodes. After the
        # fix, the bootstrap step generates a discover.yaml whose
        # tree-sitter cpp glob picks up the file and mints at least one
        # file node. Skipped when tree-sitter is genuinely absent: the
        # cpp strategy depends on tree_sitter_cpp grammar.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "include").mkdir()
            (root / "include" / "json.hpp").write_text(
                "#pragma once\nnamespace nlohmann { class json {}; }\n",
                encoding="utf-8",
            )
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "#include \"json.hpp\"\nint main() { return 0; }\n",
                encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(
                ok, "_ensure_graph must report success when discovery runs",
            )
            # The bootstrap step must have written a discover.yaml inside
            # the tempdir (containment check).
            self.assertTrue(
                (root / ".weld" / "discover.yaml").exists(),
                "_ensure_graph must bootstrap .weld/discover.yaml",
            )
            # The graph must contain at least one node -- the cpp file
            # nodes from the tree-sitter source entries (or the
            # python_module entry on the .py-free tempdir, etc.).
            node_count = self._read_graph_node_count(root)
            self.assertGreater(
                node_count, 0,
                "graph.json must mint > 0 nodes after bootstrap; "
                "got 0 (regression from sources=[] default)",
            )

    def test_existing_graph_short_circuits_without_bootstrap(self) -> None:
        # If a graph already exists, _ensure_graph must NOT regenerate
        # it (and must NOT write a discover.yaml). Idempotency guard so
        # repeated calls in the bench loop are cheap.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True)
            sentinel = {
                "meta": {"version": "1", "schema_version": 1},
                "nodes": {"file:sentinel": {
                    "type": "file", "label": "sentinel",
                    "props": {"file": "sentinel"},
                }},
                "edges": [],
            }
            (root / ".weld" / "graph.json").write_text(
                json.dumps(sentinel), encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # Sentinel preserved -> short-circuit confirmed.
            payload = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )
            self.assertIn("file:sentinel", payload["nodes"])
            # No discover.yaml should have been written -- the short-
            # circuit happens before the bootstrap step.
            self.assertFalse(
                (root / ".weld" / "discover.yaml").exists(),
                "short-circuit must not bootstrap discover.yaml",
            )

    def test_bootstrap_idempotent_when_discover_yaml_exists(self) -> None:
        # If discover.yaml already exists (e.g. user pre-configured the
        # tempdir), _ensure_graph must not overwrite it -- it must reuse
        # the existing config and proceed straight to discovery.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "int main() { return 0; }\n", encoding="utf-8",
            )
            (root / ".weld").mkdir()
            user_yaml = "sources:\n  # user-curated\n"
            (root / ".weld" / "discover.yaml").write_text(
                user_yaml, encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # User's discover.yaml must be preserved verbatim.
            self.assertEqual(
                (root / ".weld" / "discover.yaml").read_text(encoding="utf-8"),
                user_yaml,
            )

    def test_bootstrap_writes_only_inside_repo_root(self) -> None:
        # Containment guard: bootstrap must only ever write into
        # ``repo_root/.weld/``, never elsewhere on the filesystem (the
        # bench tempdir is the trust boundary).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text(
                "int main() { return 0; }\n", encoding="utf-8",
            )
            ok = weld_adapter._ensure_graph(root)
            self.assertTrue(ok)
            # Every file inside the tempdir must live under root/.
            # Snapshot the tree and check no escape.
            for path in root.rglob("*"):
                self.assertTrue(
                    str(path.resolve()).startswith(str(root.resolve())),
                    f"bootstrap escaped the tempdir: {path}",
                )


if __name__ == "__main__":
    unittest.main()

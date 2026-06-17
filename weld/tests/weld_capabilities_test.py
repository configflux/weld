"""Tests for ``weld.capabilities`` -- runtime capability matrix (Layer B).

Registry shape, ``EXPECTED_STRATEGIES`` enforcement, runtime derivation
+ determinism, impact-envelope integration, ``detect_missing``, and the
``wd capabilities`` CLI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)

from weld._capabilities_registry import (  # noqa: E402
    EXPECTED_STRATEGIES,
    FRAMEWORK_EVIDENCE,
    LANGUAGE_EVIDENCE,
    MISSING_FRAMEWORK_PATTERNS,
    STRATEGY_CAPABILITIES,
    StrategyCapability,
)
from weld.capabilities import (  # noqa: E402
    compute_capabilities,
    detect_missing,
    list_disk_strategies,
)
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _missing_pattern_framework_overlap(
    registry: dict[str, StrategyCapability],
    missing: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
) -> set[str]:
    """Frameworks declared by both ``registry`` and ``missing``.

    Iterates :meth:`StrategyCapability.frameworks_set` so multi-framework
    entries (``cap.frameworks``) are scanned alongside single-framework
    entries (``cap.framework``). Returns the intersection so the real
    invariant test and the regression test for that invariant can share
    the same logic.
    """
    known: set[str] = set()
    for cap in registry.values():
        known.update(cap.frameworks_set())
    return known & set(missing)


# ---------------------------------------------------------------------------
# Registry-shape and enforcement tests
# ---------------------------------------------------------------------------


class RegistryShapeTest(unittest.TestCase):
    def test_each_entry_declares_language_or_framework(self) -> None:
        for stem, cap in STRATEGY_CAPABILITIES.items():
            self.assertTrue(
                cap.language is not None
                or cap.languages
                or cap.framework is not None
                or cap.frameworks
                or cap.evidence,
                f"{stem}: must declare language or framework or generic evidence",
            )

    def test_multi_lang_excludes_single_language(self) -> None:
        """``languages`` and ``language`` are mutually exclusive (ADR 0046)."""
        for stem, cap in STRATEGY_CAPABILITIES.items():
            if cap.languages:
                self.assertIsNone(
                    cap.language,
                    f"{stem}: cannot set both ``language`` and ``languages``",
                )

    def test_evidence_subset_of_allowed(self) -> None:
        allowed = LANGUAGE_EVIDENCE | FRAMEWORK_EVIDENCE
        for stem, cap in STRATEGY_CAPABILITIES.items():
            self.assertTrue(
                cap.evidence <= allowed,
                f"{stem}: evidence {cap.evidence - allowed} not in {allowed}",
            )

    def test_expected_strategies_match_disk(self) -> None:
        repo_root = Path(_repo_root)
        on_disk = list_disk_strategies(repo_root)
        self.assertEqual(
            EXPECTED_STRATEGIES,
            on_disk,
            "Registry / disk drift detected. Update STRATEGY_CAPABILITIES "
            f"(only_in_registry={sorted(EXPECTED_STRATEGIES - on_disk)}, "
            f"only_on_disk={sorted(on_disk - EXPECTED_STRATEGIES)}).",
        )

    def test_missing_patterns_disjoint_from_known_frameworks(self) -> None:
        """A framework already supported should not also be in the missing list.

        Iterates :meth:`StrategyCapability.frameworks_set` so multi-framework
        entries (e.g. ``manifest`` -> ``{npm, make}``, ``deploy_surface``
        -> ``{k8s, helm, terraform}``) are also checked. A future entry like
        ``_multi_fw(('helm', 'terraform', ...))`` paired with a stale
        ``MISSING_FRAMEWORK_PATTERNS['helm']`` must fail loudly here.
        """
        overlap = _missing_pattern_framework_overlap(
            STRATEGY_CAPABILITIES, MISSING_FRAMEWORK_PATTERNS,
        )
        self.assertEqual(
            overlap, set(),
            f"{sorted(overlap)} appear in both STRATEGY_CAPABILITIES "
            "(via cap.frameworks_set()) and MISSING_FRAMEWORK_PATTERNS",
        )

    def test_disjoint_invariant_catches_multi_fw_overlap(self) -> None:
        """The disjoint check must flag a multi-framework overlap.

        Regression guard: if the invariant only scanned ``cap.framework``
        and missed ``cap.frameworks``, a future ``_multi_fw`` entry that
        overlaps with ``MISSING_FRAMEWORK_PATTERNS`` would silently slip
        through. Construct a synthetic broken registry and assert the
        helper used by the real test would catch it.
        """
        from weld._capabilities_registry import _multi_fw

        broken_registry = {
            "synthetic_multi": _multi_fw(
                ("helm", "terraform", "k8s"), ("nodes_emitted",),
            ),
        }
        broken_missing = {
            "helm": ((), ("Chart.yaml",)),
        }
        overlap = _missing_pattern_framework_overlap(
            broken_registry, broken_missing,
        )
        self.assertIn("helm", overlap)


# ---------------------------------------------------------------------------
# compute_capabilities -- runtime derivation + determinism
# ---------------------------------------------------------------------------


def _make_repo(
    nodes: dict[str, dict] | None = None,
    *,
    yaml_strategies: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-05-03T00:00:00+00:00",
                },
                "nodes": nodes or {},
                "edges": [],
            },
        ),
        encoding="utf-8",
    )
    if yaml_strategies is not None:
        sources = "\n".join(
            f"  - glob: '*'\n    type: file\n    strategy: {s}"
            for s in yaml_strategies
        )
        (root / ".weld" / "discover.yaml").write_text(
            f"sources:\n{sources}\n", encoding="utf-8",
        )
    for relpath, body in (extra_files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


class ComputeCapabilitiesTest(unittest.TestCase):
    def test_python_evidence_when_py_files_present_and_strategies_wired(
        self,
    ) -> None:
        nodes = {
            "file:weld/graph.py": {
                "type": "file",
                "props": {"file": "weld/graph.py"},
            },
            "symbol:py:weld.graph:Graph.query": {
                "type": "symbol",
                "props": {
                    "file": "weld/graph.py",
                    "language": "python",
                },
            },
        }
        root = _make_repo(
            nodes,
            yaml_strategies=["python_module", "python_callgraph"],
        )
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("python", result["languages"])
        py = result["languages"]["python"]
        self.assertTrue(py["file"])
        self.assertTrue(py["module"])
        self.assertTrue(py["imports"])
        self.assertTrue(py["symbols"])
        self.assertTrue(py["calls"])
        # Tests evidence requires test_peer to be wired -- not in this fixture.
        self.assertFalse(py["tests"])

    def test_python_flags_false_when_no_py_files_in_graph(self) -> None:
        # Strategies wired but graph empty -> language present, all False.
        root = _make_repo(
            {},
            yaml_strategies=["python_module", "python_callgraph"],
        )
        graph_data = {"meta": {}, "nodes": {}, "edges": []}
        result = compute_capabilities(graph_data, root)
        self.assertIn("python", result["languages"])
        py = result["languages"]["python"]
        self.assertFalse(any(py.values()))

    def test_unwired_strategies_cannot_set_flags_true(self) -> None:
        # Graph has TS file, but only Python strategies are wired.
        nodes = {
            "file:src/index.ts": {
                "type": "file",
                "props": {"file": "src/index.ts"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["python_module"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("typescript", result["languages"])
        ts = result["languages"]["typescript"]
        self.assertFalse(any(ts.values()))

    def test_determinism_byte_identical_repeated_calls(self) -> None:
        nodes = {
            "file:weld/graph.py": {
                "type": "file",
                "props": {"file": "weld/graph.py"},
            },
            "file:Dockerfile": {
                "type": "file",
                "props": {"file": "Dockerfile"},
            },
        }
        root = _make_repo(
            nodes, yaml_strategies=["python_module", "dockerfile"],
        )
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        first = json.dumps(
            compute_capabilities(graph_data, root), sort_keys=True,
        )
        second = json.dumps(
            compute_capabilities(graph_data, root), sort_keys=True,
        )
        self.assertEqual(first, second)
        # Sanity: framework row populated.
        self.assertIn("dockerfile", json.loads(first)["frameworks"])

    def test_no_discover_yaml_falls_back_to_full_registry(self) -> None:
        # Empty config (no discover.yaml) -> all registered strategies
        # treated as active, but graph still empty so nothing reaches True.
        root = _make_repo({})
        graph_data = {"meta": {}, "nodes": {}, "edges": []}
        result = compute_capabilities(graph_data, root)
        # Must report some languages and some frameworks even with empty graph.
        self.assertIn("python", result["languages"])
        self.assertIn("dockerfile", result["frameworks"])
        # All flags False when graph carries no matching files.
        self.assertFalse(any(result["languages"]["python"].values()))


# ---------------------------------------------------------------------------
# Impact envelope integration
# ---------------------------------------------------------------------------


class ImpactEnvelopeIntegrationTest(unittest.TestCase):
    def test_capabilities_present_in_envelope(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        nodes = {
            "file:weld/graph.py": {
                "type": "file",
                "props": {"file": "weld/graph.py"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["python_module"])
        graph = Graph(root)
        graph.load()
        result = impact(graph, target="file:weld/graph.py", depth=1)
        self.assertIn("capabilities", result)
        caps = result["capabilities"]
        self.assertIn("languages", caps)
        self.assertIn("frameworks", caps)
        self.assertIn("python", caps["languages"])

    def test_capabilities_unchanged_for_seeds_input_path(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        nodes = {
            "file:weld/graph.py": {
                "type": "file",
                "props": {"file": "weld/graph.py"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["python_module"])
        graph = Graph(root)
        graph.load()
        result = impact(
            graph,
            seeds=["file:weld/graph.py"],
            input_paths=["weld/graph.py"],
            seed_kind="files",
            target_input=["weld/graph.py"],
            depth=1,
        )
        self.assertIn("capabilities", result)


# ---------------------------------------------------------------------------
# detect_missing
# ---------------------------------------------------------------------------


class DetectMissingTest(unittest.TestCase):
    def test_pom_detected_when_csproj_supported(self) -> None:
        # Post-ADR-0056 Wave 1: ``.csproj`` is owned by ``csharp_project``
        # so ``dotnet`` no longer appears in missing. ``pom.xml`` remains
        # uncovered until a future ADR adds Maven support. The supported
        # framework path is still asserted (python never appears in
        # missing).
        root = _make_repo(
            {},
            extra_files={
                "App/App.csproj": "<Project></Project>\n",
                "service/pom.xml": "<project></project>\n",
                "weld/graph.py": "# noop\n",
            },
        )
        missing = detect_missing(root)
        self.assertNotIn("dotnet", missing)
        self.assertIn("maven", missing)
        # python is supported -> never appears in missing list.
        self.assertNotIn("python", missing)

    def test_skip_dirs_pruned(self) -> None:
        root = _make_repo(
            {},
            extra_files={
                "node_modules/foo/Cargo.toml": "[package]\n",
            },
        )
        missing = detect_missing(root)
        self.assertNotIn("cargo", missing)

    def test_returns_sorted_unique(self) -> None:
        root = _make_repo(
            {},
            extra_files={
                "a/Cargo.toml": "x",
                "b/Cargo.toml": "x",
                "go.mod": "module m\n",
            },
        )
        missing = detect_missing(root)
        self.assertEqual(missing, sorted(missing))
        self.assertEqual(len(missing), len(set(missing)))
        self.assertIn("cargo", missing)
        self.assertIn("go_modules", missing)


if __name__ == "__main__":
    unittest.main()

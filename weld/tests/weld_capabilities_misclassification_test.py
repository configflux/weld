"""Regression tests for the manifest/cmake/make misclassification fix.

Split from :mod:`weld.tests.weld_capabilities_test` so that file stays
under the 400-line cap. Covers:

- ``StrategyCapability.frameworks`` mutual-exclusion with ``framework``.
- The basename-overlap dimension of
  ``test_missing_patterns_disjoint_from_known_frameworks`` -- a basename
  emitted by an already-wired strategy must never reappear in
  :data:`MISSING_FRAMEWORK_PATTERNS`.
- ``manifest`` correctly attributing to ``npm`` vs ``make`` based on
  the actual file present in the graph (Makefile-only must not flip
  ``npm: nodes_emitted=true``).
- ``detect_missing`` not double-counting basenames already handled by a
  wired strategy (Makefile -> manifest, CMakeLists.txt -> ros2_cmake).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld._capabilities_registry import (  # noqa: E402
    MISSING_FRAMEWORK_PATTERNS,
    STRATEGY_CAPABILITIES,
)
from weld.capabilities import (  # noqa: E402
    compute_capabilities,
    detect_missing,
)
from weld.contract import SCHEMA_VERSION  # noqa: E402


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


class MultiFrameworkRegistryShapeTest(unittest.TestCase):
    def test_multi_framework_excludes_single_framework(self) -> None:
        """``frameworks`` and ``framework`` are mutually exclusive."""
        for stem, cap in STRATEGY_CAPABILITIES.items():
            if cap.frameworks:
                self.assertIsNone(
                    cap.framework,
                    f"{stem}: cannot set both ``framework`` and ``frameworks``",
                )

    def test_missing_patterns_disjoint_from_known_basenames(self) -> None:
        """A basename owned by a wired strategy must not also appear in --missing.

        Strengthens ``test_missing_patterns_disjoint_from_known_frameworks``
        in the sibling file: that test only catches name-level overlap;
        this one catches basename-level overlap (the regression source
        for cmake/make/Makefile/CMakeLists.txt double-counting).
        """
        known_basenames: set[str] = set()
        for cap in STRATEGY_CAPABILITIES.values():
            known_basenames.update(cap.file_basenames)
        for fw, (_exts, basenames) in MISSING_FRAMEWORK_PATTERNS.items():
            overlap = set(basenames) & known_basenames
            self.assertFalse(
                overlap,
                f"MISSING_FRAMEWORK_PATTERNS[{fw!r}] basenames "
                f"{sorted(overlap)} are already emitted by a wired "
                "strategy in STRATEGY_CAPABILITIES (would double-count "
                "in --missing).",
            )


class ManifestMultiFrameworkComputeTest(unittest.TestCase):
    def test_manifest_makefile_only_reports_make_not_npm(self) -> None:
        """Makefile-only repo with manifest wired -> make=true, npm=false.

        Regression for capabilities/manifest misclassification: manifest
        processes both ``package.json`` and ``Makefile``/``GNUmakefile``,
        so its capability must attribute to both ``npm`` and ``make`` --
        otherwise a Makefile-only repo would falsely report ``npm``.
        """
        nodes = {
            "file:Makefile": {
                "type": "file",
                "props": {"file": "Makefile"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["manifest"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("make", result["frameworks"])
        self.assertIn("npm", result["frameworks"])
        self.assertTrue(result["frameworks"]["make"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["npm"]["nodes_emitted"])

    def test_manifest_gnumakefile_only_reports_make_not_npm(self) -> None:
        """``GNUmakefile`` is also recognised as ``make``."""
        nodes = {
            "file:GNUmakefile": {
                "type": "file",
                "props": {"file": "GNUmakefile"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["manifest"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertTrue(result["frameworks"]["make"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["npm"]["nodes_emitted"])

    def test_manifest_package_json_only_reports_npm_not_make(self) -> None:
        """package.json-only repo with manifest wired -> npm=true, make=false."""
        nodes = {
            "file:package.json": {
                "type": "file",
                "props": {"file": "package.json"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["manifest"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertTrue(result["frameworks"]["npm"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["make"]["nodes_emitted"])

    def test_manifest_both_files_present_reports_both_true(self) -> None:
        """Mixed repo: both npm and make should report nodes_emitted=true."""
        nodes = {
            "file:package.json": {
                "type": "file",
                "props": {"file": "package.json"},
            },
            "file:Makefile": {
                "type": "file",
                "props": {"file": "Makefile"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["manifest"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertTrue(result["frameworks"]["npm"]["nodes_emitted"])
        self.assertTrue(result["frameworks"]["make"]["nodes_emitted"])


class DeploySurfaceMultiFrameworkComputeTest(unittest.TestCase):
    """Mirror of ``ManifestMultiFrameworkComputeTest`` for ``deploy_surface``.

    ``deploy_surface`` processes ``Chart.yaml`` (helm), ``*.tf``
    (terraform), and k8s manifests, so its capability declares all three
    frameworks. The per-framework split in
    :data:`MULTI_FRAMEWORK_FILES` ensures a Chart.yaml-only repo does
    not flip ``k8s: nodes_emitted=true`` (the bug this issue addresses).
    """

    def test_deploy_surface_chart_yaml_only_reports_helm_not_k8s(
        self,
    ) -> None:
        """Chart.yaml-only repo with deploy_surface wired -> helm=true, k8s/terraform=false."""
        nodes = {
            "file:chart/Chart.yaml": {
                "type": "file",
                "props": {"file": "chart/Chart.yaml"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["deploy_surface"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("helm", result["frameworks"])
        self.assertIn("k8s", result["frameworks"])
        self.assertIn("terraform", result["frameworks"])
        self.assertTrue(result["frameworks"]["helm"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["k8s"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["terraform"]["nodes_emitted"])

    def test_deploy_surface_tf_only_reports_terraform_not_k8s(
        self,
    ) -> None:
        """``.tf``-only repo with deploy_surface wired -> terraform=true, k8s/helm=false."""
        nodes = {
            "file:infra/main.tf": {
                "type": "file",
                "props": {"file": "infra/main.tf"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["deploy_surface"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertTrue(result["frameworks"]["terraform"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["k8s"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["helm"]["nodes_emitted"])

    def test_deploy_surface_helm_and_terraform_present_reports_both(
        self,
    ) -> None:
        """Mixed repo: helm + terraform both report nodes_emitted=true."""
        nodes = {
            "file:chart/Chart.yaml": {
                "type": "file",
                "props": {"file": "chart/Chart.yaml"},
            },
            "file:infra/main.tf": {
                "type": "file",
                "props": {"file": "infra/main.tf"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["deploy_surface"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertTrue(result["frameworks"]["helm"]["nodes_emitted"])
        self.assertTrue(result["frameworks"]["terraform"]["nodes_emitted"])
        self.assertFalse(result["frameworks"]["k8s"]["nodes_emitted"])


class DetectMissingNoDoubleCountTest(unittest.TestCase):
    def test_makefile_not_in_missing_when_manifest_handles_it(self) -> None:
        """``Makefile`` must not appear as a missing ``make`` framework.

        ``manifest`` handles ``Makefile``/``GNUmakefile``, so the file
        must not also surface in ``--missing`` (would be duplicate
        signal). Removing ``make`` from
        :data:`MISSING_FRAMEWORK_PATTERNS` enforces this statically.
        """
        root = _make_repo(
            {},
            extra_files={"Makefile": "all:\n\techo hi\n"},
        )
        missing = detect_missing(root)
        self.assertNotIn("make", missing)

    def test_cmakelists_not_in_missing_when_ros2_cmake_handles_it(
        self,
    ) -> None:
        """``CMakeLists.txt`` must not appear as a missing ``cmake`` framework.

        ``ros2_cmake`` handles ``CMakeLists.txt``, so the file must not
        also be reported as a missing ``cmake`` framework. Removing
        ``cmake`` from :data:`MISSING_FRAMEWORK_PATTERNS` enforces this
        statically.
        """
        root = _make_repo(
            {},
            extra_files={"pkg/CMakeLists.txt": "project(p)\n"},
        )
        missing = detect_missing(root)
        self.assertNotIn("cmake", missing)

    def test_chart_yaml_not_in_missing_when_deploy_surface_handles_it(
        self,
    ) -> None:
        """``Chart.yaml`` must not appear as a missing ``helm`` framework.

        ``deploy_surface`` already lists ``Chart.yaml`` as a basename
        (and now declares ``helm`` as one of its frameworks), so
        ``helm`` must not also be reported as a missing framework.
        """
        root = _make_repo(
            {},
            extra_files={"chart/Chart.yaml": "name: x\nversion: 0.1.0\n"},
        )
        missing = detect_missing(root)
        self.assertNotIn("helm", missing)

    def test_tf_not_in_missing_when_deploy_surface_handles_it(
        self,
    ) -> None:
        """``.tf`` files must not appear as a missing ``terraform`` framework.

        ``deploy_surface`` already lists ``.tf`` as a file extension
        (and now declares ``terraform`` as one of its frameworks), so
        ``terraform`` must not also be reported as a missing framework.
        Removing ``terraform`` from :data:`MISSING_FRAMEWORK_PATTERNS`
        enforces this statically.
        """
        root = _make_repo(
            {},
            extra_files={"infra/main.tf": 'resource "x" {}\n'},
        )
        missing = detect_missing(root)
        self.assertNotIn("terraform", missing)


if __name__ == "__main__":
    unittest.main()

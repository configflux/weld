"""Bazel ``load()`` modelling asserted against this repository's own tree.

Split from ``weld_bazel_loads_test`` because the two need different inputs.
That suite is hermetic -- it writes the tree each case needs into a temp dir.
This one reads the host repo's BUILD and ``.bzl`` files, which bazel does not
see as inputs, so it carries the ``external`` tag for the same reason
``discover_yaml_bazel_coverage_test`` does: a BUILD-file edit must not replay a
cached PASS.

The reported gaps were all measured here -- ``weld/runtime_srcs.bzl`` invisible
(bd 73xa, bd rh3l), 208 macro-declared py_tests missing (bd akwh),
``weld_examples_test`` running against ``examples/`` with nothing in the graph
saying so (bd oj3m) -- so this is where they are pinned closed.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from weld.strategies.bazel import extract

_REPO_ROOT = Path(__file__).resolve().parents[2]

class RealRepoLoadTest(unittest.TestCase):
    """Against this repository, where the reported gaps were measured.

    ``bazel query`` is the ground truth for the "zero invented targets"
    asymmetry, but shelling out to bazel from inside a bazel test is not
    acceptable -- so the standing hermetic guard is the weaker, cheaper claim
    that catches the same defect: a target weld places in the wrong package
    cannot have its name in that package's own sources.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # The published source tree carries the package but not the host
        # repo's own BUILD files; skip cleanly there rather than fail.
        if not (_REPO_ROOT / "weld" / "runtime_srcs.bzl").is_file():
            raise unittest.SkipTest("host repo tree not present")
        cls.result = extract(
            _REPO_ROOT,
            {"glob": "**/BUILD.bazel", "exclude": ["**/fixtures/**"]},
            {},
        )

    def _tracked(self, rel: str) -> bool:
        return (_REPO_ROOT / rel).is_file()

    def test_macro_declared_targets_are_modelled(self) -> None:
        """bd akwh: 208 of //weld/tests' py_tests come from .bzl macros."""
        self.assertIn("test-target://weld/tests:weld_examples_test", self.result.nodes)
        self.assertIn("test-target://weld/tests:weld_edge_types_test", self.result.nodes)

    def test_parameterized_macro_declared_targets_are_modelled(self) -> None:
        """bd iysm: bench_py_test(name=, srcs=, deps=...) -- 22 keyword-argument
        call sites in weld/tests/bench/BUILD.bazel, invisible before ADR 0123.
        weld_public_bench_weld_adapter_test is the wheel-carrying target ADR
        0121's own issue named as a measured case (bd vgmu)."""
        node = self.result.nodes.get(
            "test-target://weld/tests/bench:weld_public_bench_weld_adapter_test"
        )
        self.assertIsNotNone(node)
        self.assertEqual(node["props"]["rule"], "py_test")

        deps = {
            e["to"] for e in self.result.edges
            if e["from"]
            == "test-target://weld/tests/bench:weld_public_bench_weld_adapter_test"
            and e["type"] == "depends_on"
        }
        # The macro-expanded deps list resolves in-repo build targets...
        self.assertIn("build-target://weld:runtime", deps)
        self.assertIn("build-target://weld/bench:bench_lib", deps)
        # ...and, composed with ADR 0121 unmodified, the external wheels too.
        self.assertIn("external-dep:pypi:tree_sitter", deps)
        self.assertIn("external-dep:pypi:tree_sitter_cpp", deps)

        srcs = {
            e["to"] for e in self.result.edges
            if e["from"]
            == "test-target://weld/tests/bench:weld_public_bench_weld_adapter_test"
            and e["type"] == "contains" and e["to"].startswith("file:")
        }
        self.assertEqual(
            srcs, {"file:weld/tests/bench/weld_public_bench_weld_adapter_test"}
        )

    def test_every_bench_py_test_call_site_is_modelled(self) -> None:
        """All 22 -- not just the one acceptance-criteria target."""
        expected = {
            "weld_bench_quality_test", "weld_bench_tasks_test",
            "weld_bench_tasks_cli_test", "weld_federation_benchmark_test",
            "weld_federation_memory_probe_test", "weld_federation_query_test",
            "weld_federation_eager_test", "synthetic_large_repo_test",
            "weld_public_bench_test", "weld_public_bench_setup_test",
            "weld_public_bench_skipped_test", "weld_public_bench_adapters_test",
            "weld_public_bench_cli_test", "weld_public_bench_libclang_setup_test",
            "weld_public_bench_libclang_materialize_test",
            "weld_public_bench_libclang_adapter_test",
            "bench_grammar_precondition_test",
            "weld_public_bench_weld_adapter_test",
            "weld_public_bench_weld_adapter_graph_test",
            "weld_public_bench_weld_adapter_fallback_test",
            "weld_public_bench_libclang_report_test",
            "weld_public_bench_libclang_integration_test",
        }
        self.assertEqual(len(expected), 22)
        for name in expected:
            self.assertIn(f"test-target://weld/tests/bench:{name}", self.result.nodes, name)

    def test_runtime_srcs_manifest_is_a_node(self) -> None:
        """bd 73xa: the file was always findable; the relationship was not."""
        node = self.result.nodes.get("file:weld/runtime_srcs")
        self.assertIsNotNone(node)
        self.assertEqual(node["props"]["file"], "weld/runtime_srcs.bzl")

    def test_runtime_target_contains_the_files_its_manifest_lists(self) -> None:
        contains = {
            e["to"] for e in self.result.edges
            if e["from"] == "build-target://weld:runtime" and e["type"] == "contains"
        }
        self.assertIn("file:weld/graph", contains)
        self.assertIn("file:weld/discover", contains)
        self.assertGreater(len(contains), 100)

    def test_examples_test_names_the_directory_it_runs_against(self) -> None:
        """bd oj3m, answered with existing vocabulary."""
        deps = {
            e["to"] for e in self.result.edges
            if e["from"] == "test-target://weld/tests:weld_examples_test"
            and e["type"] == "depends_on"
        }
        self.assertIn("build-target://examples:example_files", deps)

    def test_every_bzl_node_is_a_real_file(self) -> None:
        for nid, node in self.result.nodes.items():
            if node.get("props", {}).get("language") != "starlark":
                continue
            self.assertTrue(self._tracked(node["props"]["file"]), nid)

    def test_no_target_is_invented_into_a_package(self) -> None:
        """Wrong-package attribution is the failure mode bd akwh named.

        A macro emits into the package that calls it. If attribution ever
        keyed off the ``.bzl``'s own location instead, the target would land
        in a package whose sources never mention its name -- which is exactly
        what this asserts cannot happen.
        """
        sources: dict[str, str] = {}
        for nid, node in self.result.nodes.items():
            if node["type"] not in ("build-target", "test-target"):
                continue
            build_rel = node["props"]["file"]
            pkg = Path(build_rel).parent
            if build_rel not in sources:
                text = (_REPO_ROOT / build_rel).read_text(encoding="utf-8")
                for bzl in sorted((_REPO_ROOT / pkg).glob("*.bzl")):
                    text += bzl.read_text(encoding="utf-8")
                sources[build_rel] = text
            name = node["props"]["bazel_label"].rpartition(":")[2]
            self.assertIn(f'"{name}"', sources[build_rel], nid)


class BazelQueryParityTest(unittest.TestCase):
    """The real asymmetry check, run only where a bazel binary is available.

    Always skipped under ``bazel test`` -- nesting a bazel invocation inside a
    bazel action is not something this suite should do, and ``TEST_TMPDIR`` is
    how the runner announces itself. Kept in-tree because it is the check bd
    akwh named as its acceptance test, and a reviewer running

        python3 -m unittest weld.tests.weld_bazel_loads_repo_test

    by hand should not have to reconstruct it. The standing automated guard for
    the same property is ``test_no_target_is_invented_into_a_package`` above.
    """

    def test_emitted_targets_match_bazel_query(self) -> None:
        if os.environ.get("TEST_TMPDIR"):
            self.skipTest("refusing to run bazel inside a bazel action")
        if not (_REPO_ROOT / "weld" / "runtime_srcs.bzl").is_file():
            self.skipTest("host repo tree not present")
        try:
            proc = subprocess.run(
                ["bazel", "query", "kind(py_test, //weld/tests:*)"],
                cwd=_REPO_ROOT, capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.skipTest(f"bazel unavailable: {exc}")
        if proc.returncode != 0:
            self.skipTest("bazel query failed")
        truth = {ln.strip() for ln in proc.stdout.splitlines() if ln.startswith("//")}
        result = extract(
            _REPO_ROOT, {"glob": "**/BUILD.bazel", "exclude": ["**/fixtures/**"]}, {}
        )
        emitted = {
            n["props"]["bazel_label"] for n in result.nodes.values()
            if n["type"] == "test-target"
            and n["props"]["rule"] == "py_test"
            and n["props"]["bazel_label"].startswith("//weld/tests:")
        }
        self.assertEqual(emitted - truth, set(), "weld emitted targets bazel lacks")
        self.assertEqual(truth - emitted, set(), "weld missed targets bazel has")


if __name__ == "__main__":
    unittest.main()

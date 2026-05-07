"""CLI integration tests for ``wd impact`` (Layer A multi-seed wiring).

Locks the four mutually-exclusive seed inputs, the staleness gate, the
determinism regression, and the MCP smoke through the unchanged
``weld_impact`` helper. Helper-level unit tests live in
``weld_impact_test.py``; shared fixtures live in ``_impact_test_helpers``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.tests._impact_test_helpers import (
    ensure_repo_root_on_syspath,
    git,
    make_git_repo,
    new_root,
    run_cli,
    write_graph,
)

ensure_repo_root_on_syspath()


class CliMutualExclusionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = new_root()

    def test_no_seed_input_errors(self) -> None:
        code, _, stderr = run_cli(["--root", str(self.root)])
        self.assertNotEqual(code, 0)
        self.assertIn("required", stderr)

    def test_target_and_files_are_mutually_exclusive(self) -> None:
        code, _, stderr = run_cli([
            "weld/graph.py",
            "--files",
            "weld/graph.py",
            "--root",
            str(self.root),
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("mutually exclusive", stderr)

    def test_files_resolves_seeds_and_emits_unresolved(self) -> None:
        code, stdout, _ = run_cli([
            "--files",
            "weld/graph.py",
            "missing/file.py",
            "--json",
            "--root",
            str(self.root),
        ])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["target"]["kind"], "files")
        self.assertIn("file:weld/graph.py", result["target"]["resolved_nodes"])
        self.assertEqual(
            result["warnings"]["out_of_scope_inputs"], ["missing/file.py"],
        )


class FromDiffAndWorkingTreeCliTest(unittest.TestCase):
    """``--from-diff`` and ``--working-tree`` against a real tiny git repo."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        make_git_repo(self.root)
        write_graph(self.root)
        # Modify a file already known to the graph so ``git diff`` and
        # ``git status --porcelain`` both surface it.
        (self.root / "weld").mkdir(exist_ok=True)
        (self.root / "weld" / "graph.py").write_text(
            "# changed\n", encoding="utf-8",
        )
        git(["add", "weld/graph.py"], cwd=self.root)
        git(["commit", "-m", "touch graph.py"], cwd=self.root)
        # Untracked + out-of-scope. ``weird_name`` exercises porcelain edges (non-ASCII + literal ``" -> "``).
        (self.root / "novel.py").write_text("# novel\n", encoding="utf-8")
        self.weird_name = "weird -> héllo.txt"
        (self.root / self.weird_name).write_text("x\n", encoding="utf-8")

    def test_from_diff_reads_changed_files(self) -> None:
        code, stdout, _ = run_cli([
            "--from-diff",
            "HEAD~1",
            "--allow-stale",
            "--json",
            "--root",
            str(self.root),
        ])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["target"]["kind"], "from-diff")
        self.assertIn("file:weld/graph.py", result["target"]["resolved_nodes"])

    def test_working_tree_reads_porcelain(self) -> None:
        code, stdout, _ = run_cli([
            "--working-tree",
            "--allow-stale",
            "--json",
            "--root",
            str(self.root),
        ])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result["target"]["kind"], "working-tree")
        self.assertIn("novel.py", result["warnings"]["out_of_scope_inputs"])
        self.assertIn(self.weird_name, result["warnings"]["out_of_scope_inputs"])


class NotAGitRepoErrorTest(unittest.TestCase):
    """``--from-diff`` and ``--working-tree`` against a non-git ``--root``.

    The plan for bd-...-ezp1 said to gate behind a clear "not a git
    repository" error. This locks the dedicated branch so the user sees
    a flag-aware message instead of git's raw ``fatal: not a git
    repository`` stderr.
    """

    def setUp(self) -> None:
        # ``mkdtemp`` is a plain dir (no ``make_git_repo``) -- the guard
        # must fire before any git shell-out per the class docstring.
        self.root = Path(tempfile.mkdtemp())
        write_graph(self.root)

    def test_from_diff_against_non_git_root_emits_dedicated_error(self) -> None:
        code, _, stderr = run_cli([
            "--from-diff",
            "HEAD",
            "--allow-stale",
            "--root",
            str(self.root),
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--from-diff", stderr)
        self.assertIn("requires", stderr)
        self.assertIn("git repository", stderr)
        self.assertIn(str(self.root), stderr)
        # Verbatim git stderr must not leak through any more.
        self.assertNotIn("fatal: not a git repository", stderr)
        self.assertNotIn("git diff failed", stderr)

    def test_working_tree_against_non_git_root_emits_dedicated_error(self) -> None:
        code, _, stderr = run_cli([
            "--working-tree",
            "--allow-stale",
            "--root",
            str(self.root),
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--working-tree", stderr)
        self.assertIn("requires", stderr)
        self.assertIn("git repository", stderr)
        self.assertIn(str(self.root), stderr)
        self.assertNotIn("fatal: not a git repository", stderr)
        self.assertNotIn("git status failed", stderr)


class FromDiffOptionInjectionTest(unittest.TestCase):
    """``--from-diff <ref>`` must not let ``ref`` be parsed as a git option.

    Without hardening, ``--upload-pack=evil`` would surface git's
    multi-page ``usage: git diff`` banner instead of a clear weld
    error. Not a true RCE (the user owns their own invocation) but a
    confusing failure mode the issue calls out.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        make_git_repo(self.root)
        write_graph(self.root)

    def test_leading_dash_ref_is_not_parsed_as_option(self) -> None:
        # ``--from-diff=<value>`` form routes the leading-dash payload
        # to the parser argument instead of being a standalone flag.
        code, _, stderr = run_cli([
            "--from-diff=--upload-pack=evil",
            "--allow-stale", "--root", str(self.root),
        ])
        self.assertNotEqual(code, 0)
        self.assertNotIn("invalid option", stderr)
        self.assertNotIn("usage: git diff", stderr)
        self.assertIn("--upload-pack=evil", stderr)

    def test_helper_rejects_leading_dash_ref(self) -> None:
        # Direct call bypasses argparse, locking the helper contract.
        from weld.impact_cli import _git_diff_files

        with self.assertRaises(SystemExit) as ctx:
            _git_diff_files(self.root, "--upload-pack=evil")
        msg = str(ctx.exception.code)
        self.assertNotIn("invalid option", msg)
        self.assertNotIn("usage: git diff", msg)
        self.assertIn("--upload-pack=evil", msg)


class StaleGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        make_git_repo(self.root)
        write_graph(self.root, sha="0" * 40)
        (self.root / "weld").mkdir(exist_ok=True)
        (self.root / "weld" / "graph.py").write_text(
            "# init\n", encoding="utf-8",
        )
        git(["add", "weld/graph.py"], cwd=self.root)
        git(["commit", "-m", "graph.py"], cwd=self.root)

    def test_stale_gate_blocks_without_allow_stale(self) -> None:
        code, _, stderr = run_cli([
            "weld/graph.py",
            "--root",
            str(self.root),
        ])
        self.assertEqual(code, 2)
        self.assertIn("stale", stderr)

    def test_allow_stale_records_warning_and_proceeds(self) -> None:
        code, stdout, _ = run_cli([
            "weld/graph.py",
            "--allow-stale",
            "--json",
            "--root",
            str(self.root),
        ])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertTrue(result["warnings"]["stale_graph"])


class DeterminismRegressionTest(unittest.TestCase):
    """Run twice -- byte-identical envelope is a non-negotiable contract."""

    def setUp(self) -> None:
        self.root = new_root()

    def test_helper_two_invocations_byte_identical(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import impact

        graph_a = Graph(self.root)
        graph_a.load()
        graph_b = Graph(self.root)
        graph_b.load()
        result_a = impact(graph_a, target="weld/graph.py", depth=3)
        result_b = impact(graph_b, target="weld/graph.py", depth=3)
        ja = json.dumps(result_a, sort_keys=True, ensure_ascii=True)
        jb = json.dumps(result_b, sort_keys=True, ensure_ascii=True)
        self.assertEqual(ja, jb)

    def test_cli_json_two_invocations_byte_identical(self) -> None:
        code1, out1, _ = run_cli([
            "weld/graph.py", "--json", "--root", str(self.root),
        ])
        code2, out2, _ = run_cli([
            "weld/graph.py", "--json", "--root", str(self.root),
        ])
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(out1, out2)


class McpSmokeTest(unittest.TestCase):
    """The MCP helper picks up the new envelope fields automatically."""

    def setUp(self) -> None:
        self.root = new_root()

    def test_weld_impact_helper_returns_v2_envelope(self) -> None:
        from weld.mcp_helpers import weld_impact

        result = weld_impact(
            "symbol:py:weld.graph:Graph.query",
            depth=2,
            root=self.root,
        )
        self.assertEqual(result["impact_version"], 2)
        self.assertIn("tests", result["affected_surfaces"])
        self.assertIsInstance(result["warnings"], dict)
        for key in (
            "unresolved_callsites",
            "speculative_edges",
            "stale_graph",
            "out_of_scope_inputs",
            "low_capability_inputs",
            "messages",
        ):
            self.assertIn(key, result["warnings"])


class ShimReExportsTest(unittest.TestCase):
    def test_legacy_module_path_still_works(self) -> None:
        # ``weld.impact`` must still expose ``main``, ``impact``,
        # ``IMPACT_VERSION`` and ``format_human`` for backwards compat.
        from weld import impact as legacy

        self.assertTrue(callable(legacy.main))
        self.assertTrue(callable(legacy.impact))
        self.assertTrue(callable(legacy.format_human))
        self.assertEqual(legacy.IMPACT_VERSION, 2)


class LayerASeedAndWarningsUnitTest(unittest.TestCase):
    """Helper-level checks for Layer A: seed resolution + structured warnings.

    These would naturally live in ``weld_impact_test.py`` but the legacy
    file already sits at the line-count cap; landing them here keeps both
    files under the 400-line policy.
    """

    def setUp(self) -> None:
        self.root = new_root()

    def test_resolve_paths_partitions_known_and_unknown(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import _resolve_paths_to_seeds

        graph = Graph(self.root)
        graph.load()
        seeds, unresolved = _resolve_paths_to_seeds(
            graph,
            ["weld/graph.py", "missing/file.py", "weld/utils.py"],
        )
        self.assertIn("file:weld/graph.py", seeds)
        self.assertIn("symbol:py:weld.graph:Graph.query", seeds)
        self.assertIn("file:weld/utils.py", seeds)
        self.assertEqual(unresolved, ["missing/file.py"])
        self.assertEqual(seeds, sorted(seeds))

    def test_tests_bucket_populated_for_role_test_files(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import impact

        graph = Graph(self.root)
        graph.load()
        result = impact(graph, target="symbol:py:weld.graph:Graph.query", depth=2)
        ids = {entry["id"] for entry in result["affected_surfaces"]["tests"]}
        self.assertIn("file:weld/tests/weld_graph_test.py", ids)
        for entry in result["affected_surfaces"]["tests"]:
            # Strictly graph-shaped -- no Bazel/pytest fields.
            self.assertEqual(set(entry.keys()), {"id", "type", "file", "hop"})

    def test_low_capability_inputs_flag_isolated_files(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import impact

        graph = Graph(self.root)
        graph.load()
        result = impact(
            graph,
            seeds=["file:weld/utils.py"],
            depth=2,
            input_paths=["weld/utils.py"],
        )
        self.assertEqual(
            result["warnings"]["low_capability_inputs"], ["weld/utils.py"],
        )

    def test_unresolved_inputs_flow_into_envelope(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import impact

        graph = Graph(self.root)
        graph.load()
        result = impact(
            graph,
            seeds=[],
            unresolved_inputs=["foo.tf", "bar.csproj"],
            seed_kind="files",
            target_input=["foo.tf", "bar.csproj"],
        )
        self.assertEqual(
            result["target"]["unresolved_inputs"], ["foo.tf", "bar.csproj"],
        )
        self.assertEqual(
            result["warnings"]["out_of_scope_inputs"], ["foo.tf", "bar.csproj"],
        )

    def test_impact_requires_target_xor_seeds(self) -> None:
        from weld.graph import Graph
        from weld.impact_core import impact

        graph = Graph(self.root)
        graph.load()
        with self.assertRaises(ValueError):
            impact(graph)
        with self.assertRaises(ValueError):
            impact(
                graph,
                target="weld/graph.py",
                seeds=["file:weld/graph.py"],
            )


if __name__ == "__main__":
    unittest.main()

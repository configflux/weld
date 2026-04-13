"""Tests for the cortex-bench harness (project-1bt).

These tests build a tiny synthetic repo (with both a graph.json and a few
text files containing the search term) and assert that:

  - Prompts load from the YAML fixture format.
  - The bytes/4 tokenizer fallback works deterministically.
  - The grep baseline returns text proportional to file content.
  - Both ``cortex_cli_baseline`` and ``cortex_mcp_baseline`` produce non-empty
    JSON for each prompt category.
  - ``run_bench`` returns one ``BenchResult`` per prompt with non-negative
    token counts.
  - ``render_report`` produces the documented markdown shape with a
    summary section.
  - The ``cortex bench`` CLI subcommand writes a report file at the requested
    path.

The tests do NOT depend on tiktoken being installed.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure cortex package is importable from the repo root.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.bench.runner import (  # noqa: E402
    BenchResult,
    Prompt,
    count_tokens,
    grep_baseline,
    cortex_cli_baseline,
    cortex_mcp_baseline,
    load_prompts,
    render_report,
    run_bench,
)
from cortex.cli import main as cli_main  # noqa: E402
from cortex.contract import SCHEMA_VERSION  # noqa: E402

_TS = "2026-04-06T12:00:00+00:00"

def _setup_repo(term: str = "Store") -> str:
    """Create a temp repo dir with .cortex/graph.json + a few text files."""
    tmpdir = tempfile.mkdtemp()
    cortex_dir = os.path.join(tmpdir, ".cortex")
    os.makedirs(cortex_dir)
    nodes = {
        "entity:Store": {
            "type": "entity",
            "label": "Store",
            "props": {"file": "src/store.py", "description": "Store entity."},
        },
        "module:src/use_store.py": {
            "type": "module",
            "label": "use_store",
            "props": {"file": "src/use_store.py", "description": "Uses Store."},
        },
        "doc:docs/store.md": {
            "type": "doc",
            "label": "store doc",
            "props": {
                "file": "docs/store.md",
                "description": "Store overview.",
                "authority": "canonical",
            },
        },
    }
    edges = [
        {
            "from": "module:src/use_store.py",
            "to": "entity:Store",
            "type": "depends_on",
            "props": {},
        }
    ]
    graph = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "abc123"},
        "nodes": nodes,
        "edges": edges,
    }
    with open(os.path.join(cortex_dir, "graph.json"), "w") as f:
        json.dump(graph, f)
    # Minimal file index so cortex_find/references calls don't crash.
    with open(os.path.join(cortex_dir, "file-index.json"), "w") as f:
        json.dump(
            {
                "meta": {"version": 1, "updated_at": _TS},
                "files": {
                    "src/store.py": ["src", "store", "Store"],
                    "src/use_store.py": ["src", "use_store", "Store"],
                },
            },
            f,
        )
    src = os.path.join(tmpdir, "src")
    os.makedirs(src)
    for name in ("store.py", "use_store.py", "unrelated.py"):
        with open(os.path.join(src, name), "w") as f:
            if "store" in name:
                f.write(
                    "# header\n"
                    f"# mentions {term} repeatedly\n"
                    f"class {term}:\n"
                    "    pass\n"
                    + ("# filler line\n" * 50)
                )
            else:
                f.write("# nothing relevant\n" + ("# filler\n" * 30))
    docs = os.path.join(tmpdir, "docs")
    os.makedirs(docs)
    with open(os.path.join(docs, "store.md"), "w") as f:
        f.write(f"# {term}\n\nThe {term} entity is the core of the system.\n")
    return tmpdir

_FIXTURE_PROMPTS = """\
prompts:
  - id: q01
    prompt: "Where is Store?"
    category: navigation
    term: Store
  - id: q02
    prompt: "What depends on Store?"
    category: dependency
    term: Store
  - id: q03
    prompt: "Who calls Store?"
    category: callgraph
    term: Store
    symbol: Store
"""

class TokenizerTest(unittest.TestCase):
    def test_count_tokens_handles_empty(self) -> None:
        self.assertEqual(count_tokens(""), 0)

    def test_count_tokens_is_positive(self) -> None:
        self.assertGreater(count_tokens("hello world"), 0)

    def test_bytes_fallback_is_deterministic(self) -> None:
        # Whether tiktoken is installed or not, two equal strings must
        # have equal counts.
        a = "abcdefghij" * 10
        self.assertEqual(count_tokens(a), count_tokens(a))

class PromptLoaderTest(unittest.TestCase):
    def test_load_prompts_from_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as f:
            f.write(_FIXTURE_PROMPTS)
            path = Path(f.name)
        try:
            prompts = load_prompts(path)
        finally:
            path.unlink()
        self.assertEqual(len(prompts), 3)
        self.assertEqual(prompts[0].id, "q01")
        self.assertEqual(prompts[0].category, "navigation")
        self.assertEqual(prompts[2].symbol, "Store")

    def test_real_fixture_loads(self) -> None:
        # The checked-in fixture must parse and contain at least one
        # prompt per required category.
        path = Path(_repo_root) / "cortex" / "tests" / "bench" / "prompts.yaml"
        prompts = load_prompts(path)
        cats = {p.category for p in prompts}
        self.assertIn("navigation", cats)
        self.assertIn("dependency", cats)
        self.assertIn("callgraph", cats)
        self.assertGreaterEqual(len(prompts), 10)

class GrepBaselineTest(unittest.TestCase):
    def test_grep_baseline_finds_matching_files(self) -> None:
        root = Path(_setup_repo("Store"))
        prompt = Prompt(
            id="q",
            prompt="Where is Store?",
            category="navigation",
            term="Store",
        )
        text = grep_baseline(prompt, root)
        # Both store.py and use_store.py contain the term; unrelated.py
        # does not.
        self.assertIn("store.py", text)
        self.assertIn("use_store.py", text)
        self.assertNotIn("unrelated.py", text)
        self.assertGreater(count_tokens(text), 0)

class CortexBaselinesTest(unittest.TestCase):
    def test_cortex_cli_baseline_returns_json_for_each_category(self) -> None:
        root = Path(_setup_repo("Store"))
        for cat in ("navigation", "dependency", "callgraph"):
            prompt = Prompt(
                id="q",
                prompt="Store?",
                category=cat,
                term="Store",
                symbol="Store" if cat == "callgraph" else None,
            )
            out = cortex_cli_baseline(prompt, root)
            # Must be valid JSON and non-empty.
            parsed = json.loads(out)
            self.assertIsInstance(parsed, dict)
            self.assertGreater(count_tokens(out), 0)

    def test_cortex_mcp_baseline_returns_json_for_each_category(self) -> None:
        root = Path(_setup_repo("Store"))
        for cat in ("navigation", "dependency", "callgraph"):
            prompt = Prompt(
                id="q",
                prompt="Store?",
                category=cat,
                term="Store",
                symbol="Store" if cat == "callgraph" else None,
            )
            out = cortex_mcp_baseline(prompt, root)
            parsed = json.loads(out)
            self.assertIsInstance(parsed, dict)
            self.assertGreater(count_tokens(out), 0)

class RunBenchTest(unittest.TestCase):
    def test_run_bench_returns_one_result_per_prompt(self) -> None:
        root = Path(_setup_repo("Store"))
        prompts = [
            Prompt(id="a", prompt="x", category="navigation", term="Store"),
            Prompt(id="b", prompt="x", category="dependency", term="Store"),
        ]
        results = run_bench(prompts, root)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIsInstance(r, BenchResult)
            self.assertGreaterEqual(r.grep_tokens, 0)
            self.assertGreaterEqual(r.cli_tokens, 0)
            self.assertGreaterEqual(r.mcp_tokens, 0)

    def test_render_report_includes_summary(self) -> None:
        root = Path(_setup_repo("Store"))
        prompts = [
            Prompt(id="a", prompt="x", category="navigation", term="Store"),
            Prompt(id="b", prompt="x", category="dependency", term="Store"),
        ]
        report = render_report(run_bench(prompts, root))
        self.assertIn("# Cortex retrieval benchmark", report)
        self.assertIn("| id ", report)
        self.assertIn("## Summary", report)
        self.assertIn("cortex CLI vs grep", report)
        self.assertIn("cortex MCP vs grep", report)
        # Per-category breakdown should appear when categories vary.
        self.assertIn("By category", report)

    def test_reduction_handles_zero_grep_baseline(self) -> None:
        r = BenchResult(
            prompt=Prompt(
                id="x", prompt="x", category="navigation", term="x"
            ),
            grep_tokens=0,
            cli_tokens=10,
            mcp_tokens=10,
        )
        self.assertIsNone(r.reduction_cli)
        self.assertIsNone(r.reduction_mcp)

class CliEntrypointTest(unittest.TestCase):
    def test_cortex_bench_writes_report_file(self) -> None:
        root = Path(_setup_repo("Store"))
        prompts_path = root / "prompts.yaml"
        prompts_path.write_text(_FIXTURE_PROMPTS, encoding="utf-8")
        out_path = root / "report.md"
        rc = cli_main(
            [
                "bench",
                "--root",
                str(root),
                "--prompts",
                str(prompts_path),
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("# Cortex retrieval benchmark", text)
        self.assertIn("| q01 | navigation", text)

    def test_cortex_bench_print_mode(self) -> None:
        root = Path(_setup_repo("Store"))
        prompts_path = root / "prompts.yaml"
        prompts_path.write_text(_FIXTURE_PROMPTS, encoding="utf-8")
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_main(
                [
                    "bench",
                    "--root",
                    str(root),
                    "--prompts",
                    str(prompts_path),
                    "--print",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Cortex retrieval benchmark", buf.getvalue())

if __name__ == "__main__":
    unittest.main()

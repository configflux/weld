"""Tests for the copyable template files shipped with weld.

Validates that:
  - the project-local strategy template exists, loads, and produces valid output
  - the external adapter template exists, runs, and produces valid output
  - both templates pass the shared fragment contract validation
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies._helpers import StrategyResult  # noqa: E402

# -- Paths to templates under test -------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_LOCAL_STRATEGY_TEMPLATE = _TEMPLATES_DIR / "local_strategy.py"
_EXTERNAL_ADAPTER_TEMPLATE = _TEMPLATES_DIR / "external_adapter.py"

# -- Local strategy template tests -------------------------------------------

class LocalStrategyTemplateExistsTest(unittest.TestCase):
    """The project-local strategy template file must exist."""

    def test_file_exists(self) -> None:
        self.assertTrue(
            _LOCAL_STRATEGY_TEMPLATE.is_file(),
            f"Template not found: {_LOCAL_STRATEGY_TEMPLATE}",
        )

class LocalStrategyTemplateLoadableTest(unittest.TestCase):
    """The template must load as a Python module and expose extract()."""

    def test_has_extract_function(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "template_local_strategy", _LOCAL_STRATEGY_TEMPLATE
        )
        self.assertIsNotNone(spec)
        assert spec is not None  # for type narrowing
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(
            callable(getattr(mod, "extract", None)),
            "Template must define an extract() function",
        )

class LocalStrategyTemplateOutputTest(unittest.TestCase):
    """Calling extract() must return a valid StrategyResult / fragment."""

    def test_extract_returns_strategy_result(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "template_local_strategy", _LOCAL_STRATEGY_TEMPLATE
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create a dummy file so the template has something to find
            (root / "example.txt").write_text("hello\n", encoding="utf-8")
            source = {"glob": "*.txt"}
            context: dict = {}
            result = mod.extract(root, source, context)
        self.assertIsInstance(result, StrategyResult)
        self.assertIsInstance(result.nodes, dict)
        self.assertIsInstance(result.edges, list)
        self.assertIsInstance(result.discovered_from, list)

    def test_extract_output_validates(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "template_local_strategy", _LOCAL_STRATEGY_TEMPLATE
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "example.txt").write_text("hello\n", encoding="utf-8")
            source = {"glob": "*.txt"}
            context: dict = {}
            result = mod.extract(root, source, context)
        fragment = {
            "nodes": result.nodes,
            "edges": result.edges,
            "discovered_from": result.discovered_from,
        }
        errs = validate_fragment(
            fragment,
            source_label="template:local_strategy",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"Validation errors: {errs}")

# -- External adapter template tests -----------------------------------------

class ExternalAdapterTemplateExistsTest(unittest.TestCase):
    """The external adapter template file must exist."""

    def test_file_exists(self) -> None:
        self.assertTrue(
            _EXTERNAL_ADAPTER_TEMPLATE.is_file(),
            f"Template not found: {_EXTERNAL_ADAPTER_TEMPLATE}",
        )

class ExternalAdapterTemplateRunnableTest(unittest.TestCase):
    """The adapter template must be executable and emit valid JSON."""

    def test_runs_and_emits_json(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_EXTERNAL_ADAPTER_TEMPLATE)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"Adapter exited {proc.returncode}: {proc.stderr}",
        )
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, dict)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)

    def test_output_validates_as_fragment(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_EXTERNAL_ADAPTER_TEMPLATE)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        data = json.loads(proc.stdout)
        errs = validate_fragment(
            data,
            source_label="template:external_adapter",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"Validation errors: {errs}")

class ExternalAdapterIntegrationTest(unittest.TestCase):
    """The adapter template works through the external_json pathway."""

    def test_runs_via_external_json_runner(self) -> None:
        from weld.discover import _run_external_json

        source = {
            "strategy": "external_json",
            "command": f"{sys.executable} {_EXTERNAL_ADAPTER_TEMPLATE}",
        }
        result = _run_external_json(Path("/tmp"), source)
        self.assertIsInstance(result.nodes, dict)
        # The template should produce at least one node
        self.assertGreater(len(result.nodes), 0)

# -- Template documentation references ---------------------------------------

# -- Markdown template existence tests ----------------------------------------

class MarkdownTemplateExistsTest(unittest.TestCase):
    """Bundled markdown templates must exist and be non-empty."""

    def test_weld_readme_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_readme.md").is_file())

    def test_weld_readme_not_empty(self) -> None:
        content = (_TEMPLATES_DIR / "weld_readme.md").read_text(encoding="utf-8")
        self.assertGreater(len(content.strip()), 100)

    def test_weld_cmd_claude_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_cmd_claude.md").is_file())

    def test_weld_cmd_claude_not_empty(self) -> None:
        content = (_TEMPLATES_DIR / "weld_cmd_claude.md").read_text(encoding="utf-8")
        self.assertGreater(len(content.strip()), 100)

    def test_weld_skill_codex_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_skill_codex.md").is_file())

    def test_weld_skill_codex_not_empty(self) -> None:
        content = (_TEMPLATES_DIR / "weld_skill_codex.md").read_text(encoding="utf-8")
        self.assertGreater(len(content.strip()), 100)

    def test_codex_mcp_config_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "codex_mcp_config.toml").is_file())

    def test_codex_mcp_config_not_empty(self) -> None:
        content = (_TEMPLATES_DIR / "codex_mcp_config.toml").read_text(encoding="utf-8")
        self.assertGreater(len(content.strip()), 50)

    def test_agent_templates_include_manual_enrichment_guidance(self) -> None:
        for template_name in (
            "weld_cmd_claude.md",
            "weld_skill_codex.md",
            "weld_skill_copilot.md",
        ):
            with self.subTest(template=template_name):
                content = (_TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
                self.assertIn("wd add-node", content)
                self.assertIn('"provider": "manual"', content)
                self.assertIn('"model": "agent-reviewed"', content)

# -- Template documentation references ---------------------------------------

class TemplateDocReferencesTest(unittest.TestCase):
    """Docs must point to the template files."""

    def test_cookbook_references_templates(self) -> None:
        cookbook = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "strategy-cookbook.md"
        )
        text = cookbook.read_text(encoding="utf-8")
        self.assertIn(
            "wd scaffold local-strategy", text,
            "strategy-cookbook.md must reference local strategy scaffolding",
        )
        self.assertIn(
            "wd scaffold external-adapter", text,
            "strategy-cookbook.md must reference external adapter scaffolding",
        )

    def test_onboarding_references_templates(self) -> None:
        onboarding = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "onboarding.md"
        )
        text = onboarding.read_text(encoding="utf-8")
        self.assertIn(
            "wd scaffold", text,
            "onboarding.md must reference weld scaffolding",
        )

if __name__ == "__main__":
    unittest.main()

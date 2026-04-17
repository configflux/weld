"""Source-contract tests for ``wd init`` discover.yaml generation.

The bundled ``compose``, ``dockerfile``, and ``yaml_meta`` strategies
read ``source["glob"]`` and will crash with ``KeyError: 'glob'`` when fed
a ``files:`` entry. Only ``config_file`` accepts ``files:``. The
generator must respect that split so ``wd discover`` does not crash (and
does not silently under-discover) when a child config is auto-written.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._yaml import parse_yaml  # noqa: E402
from weld.init import generate_yaml, init  # noqa: E402
from weld.init_detect import find_python_glob_roots, scan_files  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Strategies whose runtime contract requires ``glob``.
_GLOB_STRATEGIES = frozenset({"compose", "dockerfile", "yaml_meta"})
# Strategies that legitimately accept ``files``.
_FILES_STRATEGIES = frozenset({"config_file"})


def _init_fixture(name: str) -> dict:
    fixture_dir = _FIXTURES / name
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".weld" / "discover.yaml"
        success = init(fixture_dir, out, force=True)
        assert success, f"wd init failed for {name}"
        return parse_yaml(out.read_text(encoding="utf-8"))


class GeneratorSourceContractTest(unittest.TestCase):
    """Generator emits ``glob:`` for glob-only strategies, never ``files:``."""

    def test_compose_entry_uses_glob_not_files(self) -> None:
        yaml_text = generate_yaml(
            languages={}, frameworks=[],
            dockerfiles=[], compose_files=["docker-compose.yml"],
            ci_files=[], claude_agents=[], claude_commands=[],
            doc_dirs=[], python_globs=[], root_configs=[],
        )
        data = parse_yaml(yaml_text)
        compose = [s for s in data.get("sources", [])
                   if s.get("strategy") == "compose"]
        self.assertEqual(len(compose), 1, yaml_text)
        self.assertIn("glob", compose[0],
                      f"compose entry must use glob, got {compose[0]}")
        self.assertNotIn("files", compose[0],
                         f"compose entry must not use files, got {compose[0]}")
        self.assertEqual(compose[0]["glob"], "docker-compose.yml")

    def test_single_dockerfile_uses_glob_not_files(self) -> None:
        yaml_text = generate_yaml(
            languages={}, frameworks=[],
            dockerfiles=["Dockerfile"], compose_files=[],
            ci_files=[], claude_agents=[], claude_commands=[],
            doc_dirs=[], python_globs=[], root_configs=[],
        )
        data = parse_yaml(yaml_text)
        df_sources = [s for s in data.get("sources", [])
                      if s.get("strategy") == "dockerfile"]
        self.assertEqual(len(df_sources), 1, yaml_text)
        self.assertIn("glob", df_sources[0],
                      f"dockerfile entry must use glob, got {df_sources[0]}")
        self.assertNotIn("files", df_sources[0],
                         f"dockerfile entry must not use files, "
                         f"got {df_sources[0]}")
        self.assertEqual(df_sources[0]["glob"], "Dockerfile")

    def test_multiple_dockerfiles_each_use_glob(self) -> None:
        yaml_text = generate_yaml(
            languages={}, frameworks=[],
            dockerfiles=["docker/api.Dockerfile", "Dockerfile"],
            compose_files=[], ci_files=[],
            claude_agents=[], claude_commands=[],
            doc_dirs=[], python_globs=[], root_configs=[],
        )
        data = parse_yaml(yaml_text)
        df_sources = [s for s in data.get("sources", [])
                      if s.get("strategy") == "dockerfile"]
        self.assertGreaterEqual(len(df_sources), 2, yaml_text)
        for src in df_sources:
            self.assertIn("glob", src,
                          f"dockerfile entry must use glob, got {src}")
            self.assertNotIn("files", src,
                             f"dockerfile entry must not use files, got {src}")


class FixtureSourceContractTest(unittest.TestCase):
    """Every fixture's generated config respects the strategy contract."""

    def _assert_source_shape(self, sources: list[dict], label: str) -> None:
        for i, src in enumerate(sources):
            strat = src.get("strategy")
            if strat in _GLOB_STRATEGIES:
                self.assertIn(
                    "glob", src,
                    f"{label}: source[{i}] strategy={strat!r} requires "
                    f"'glob' but got {sorted(src.keys())}",
                )
                self.assertNotIn(
                    "files", src,
                    f"{label}: source[{i}] strategy={strat!r} must not "
                    f"use 'files'",
                )
            elif strat in _FILES_STRATEGIES and "files" in src:
                self.assertIsInstance(
                    src["files"], list,
                    f"{label}: source[{i}] files must be a list",
                )

    def test_all_fixtures_respect_strategy_source_contract(self) -> None:
        for name in ("python_bazel", "typescript_node", "cpp_clang",
                     "csharp_project", "legacy_onboarding",
                     "ros2_workspace"):
            data = _init_fixture(name)
            self._assert_source_shape(data.get("sources", []), name)


class PythonRootLevelDiscoveryTest(unittest.TestCase):
    """Python repos with only root-level ``*.py`` files must not silently
    under-discover. Previously ``find_python_glob_roots`` skipped root and
    produced an empty Python source list."""

    def test_root_level_python_files_produce_glob(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("x = 1\n", encoding="utf-8")
            (root / "helper.py").write_text("y = 2\n", encoding="utf-8")
            files = scan_files(root)
            globs = find_python_glob_roots(root, files)
            self.assertTrue(
                any(g.endswith("*.py") for g in globs),
                f"Expected a Python glob for root-level files, got {globs}",
            )


if __name__ == "__main__":
    unittest.main()

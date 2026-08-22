"""Tests for the examples/ directory.

Validates that:
  - All expected example files exist
  - discover.yaml files are valid YAML with a sources list
  - The custom strategy loads and produces valid StrategyResult output
  - wd discover runs successfully against each example
  - No internal/private references leak into public-facing content
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld._yaml import parse_yaml  # noqa: E402
from weld.contract import validate_fragment  # noqa: E402
from weld.strategies._helpers import StrategyResult  # noqa: E402

_EXAMPLES_DIR = _repo_root / "examples"
_FASTAPI_DIR = _EXAMPLES_DIR / "01-python-fastapi"
_CUSTOM_DIR = _EXAMPLES_DIR / "02-custom-strategy"

# Patterns that must never appear in public-facing example content.
# Constructed indirectly so THIS file itself does not trigger the public
# danger-pattern scanner.
_PRIVATE_PATTERNS = [
    re.compile(r"\b" + "weld" + "-" + "internal" + r"\b", re.IGNORECASE),
    re.compile(r"\b" + "tilbuds" + "radar" + r"\b", re.IGNORECASE),
    re.compile(r"\b" + "gastown" + "hall" + r"\b", re.IGNORECASE),
    re.compile(r"\bbd-\w+\b"),
    re.compile(r"\b" + "weld" + "-" + "internal" + r"-\w+\b"),
]


class ExamplesDirectoryStructureTest(unittest.TestCase):
    """All expected example files must exist."""

    def test_top_level_readme_exists(self) -> None:
        self.assertTrue((_EXAMPLES_DIR / "README.md").is_file())

    def test_fastapi_readme_exists(self) -> None:
        self.assertTrue((_FASTAPI_DIR / "README.md").is_file())

    def test_fastapi_discover_yaml_exists(self) -> None:
        self.assertTrue((_FASTAPI_DIR / ".weld" / "discover.yaml").is_file())

    def test_fastapi_app_exists(self) -> None:
        self.assertTrue((_FASTAPI_DIR / "app.py").is_file())

    def test_fastapi_models_exists(self) -> None:
        self.assertTrue((_FASTAPI_DIR / "models.py").is_file())

    def test_custom_strategy_readme_exists(self) -> None:
        self.assertTrue((_CUSTOM_DIR / "README.md").is_file())

    def test_custom_strategy_discover_yaml_exists(self) -> None:
        self.assertTrue((_CUSTOM_DIR / ".weld" / "discover.yaml").is_file())

    def test_custom_strategy_plugin_exists(self) -> None:
        self.assertTrue(
            (_CUSTOM_DIR / ".weld" / "strategies" / "todo_comment.py").is_file()
        )

    def test_custom_strategy_sample_exists(self) -> None:
        self.assertTrue((_CUSTOM_DIR / "sample.py").is_file())


class DiscoverYamlValidityTest(unittest.TestCase):
    """discover.yaml files must be valid YAML with a sources list."""

    def _load_yaml(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        data = parse_yaml(text)
        self.assertIsInstance(data, dict, f"{path} must be a YAML mapping")
        return data

    def test_fastapi_discover_yaml_has_sources(self) -> None:
        data = self._load_yaml(_FASTAPI_DIR / ".weld" / "discover.yaml")
        self.assertIn("sources", data)
        self.assertIsInstance(data["sources"], list)
        self.assertGreater(len(data["sources"]), 0)

    def test_custom_strategy_discover_yaml_has_sources(self) -> None:
        data = self._load_yaml(_CUSTOM_DIR / ".weld" / "discover.yaml")
        self.assertIn("sources", data)
        self.assertIsInstance(data["sources"], list)
        self.assertGreater(len(data["sources"]), 0)

    def test_fastapi_sources_have_required_keys(self) -> None:
        data = self._load_yaml(_FASTAPI_DIR / ".weld" / "discover.yaml")
        for entry in data["sources"]:
            self.assertIn("strategy", entry)

    def test_custom_sources_reference_todo_strategy(self) -> None:
        data = self._load_yaml(_CUSTOM_DIR / ".weld" / "discover.yaml")
        strategies = [s["strategy"] for s in data["sources"]]
        self.assertIn("todo_comment", strategies)


class CustomStrategyLoadTest(unittest.TestCase):
    """The custom strategy must load and produce valid output."""

    def _load_strategy(self):
        strategy_path = (
            _CUSTOM_DIR / ".weld" / "strategies" / "todo_comment.py"
        )
        spec = importlib.util.spec_from_file_location(
            "todo_comment", strategy_path
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_has_extract_function(self) -> None:
        mod = self._load_strategy()
        self.assertTrue(
            callable(getattr(mod, "extract", None)),
            "Custom strategy must define an extract() function",
        )

    def test_extract_returns_strategy_result(self) -> None:
        mod = self._load_strategy()
        result = mod.extract(_CUSTOM_DIR, {"glob": "*.py"}, {})
        self.assertIsInstance(result, StrategyResult)
        self.assertIsInstance(result.nodes, dict)
        self.assertIsInstance(result.edges, list)
        self.assertIsInstance(result.discovered_from, list)

    def test_extract_finds_todo_comments(self) -> None:
        mod = self._load_strategy()
        result = mod.extract(_CUSTOM_DIR, {"glob": "*.py"}, {})
        self.assertGreater(
            len(result.nodes), 0,
            "Strategy should find at least one TODO/FIXME in sample.py",
        )
        for node_id, node in result.nodes.items():
            self.assertEqual(node["type"], "concept")
            self.assertIn("file", node["props"])
            self.assertIn("line", node["props"])
            self.assertIn("kind", node["props"])
            self.assertIn(node["props"]["kind"], ("TODO", "FIXME"))

    def test_extract_output_validates_as_fragment(self) -> None:
        mod = self._load_strategy()
        result = mod.extract(_CUSTOM_DIR, {"glob": "*.py"}, {})
        fragment = {
            "nodes": result.nodes,
            "edges": result.edges,
            "discovered_from": result.discovered_from,
        }
        errs = validate_fragment(
            fragment,
            source_label="example:todo_comment",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"Validation errors: {errs}")

    def test_empty_glob_returns_empty(self) -> None:
        mod = self._load_strategy()
        result = mod.extract(_CUSTOM_DIR, {"glob": ""}, {})
        self.assertEqual(len(result.nodes), 0)
        self.assertEqual(len(result.edges), 0)


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    """Map each file under *root* to (size, mtime_ns, sha256).

    mtime is part of the fingerprint on purpose. The sibling pip-wheel
    pollution guard compares content only, and that made it structurally
    blind: ``weld/__init__.py`` is empty by ADR 0099, so a producer that
    rewrote it byte-identically passed a SHA check while still bumping
    the inode's timestamps -- which is exactly what Bazel's
    concurrent-modification guard keys on (bd w3n6). Content equality is
    not enough to prove a tree was left alone.
    """
    out: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, digest)
    return out


class DiscoverIntegrationTest(unittest.TestCase):
    """wd discover must run successfully against each example."""

    def _run_discover(self, example_dir: Path) -> dict:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(_repo_root)
        # Discover writes .weld/discovery-state.json (and friends) into
        # its working directory. Run it against a scratch copy, never the
        # live example: examples/BUILD.bazel exposes the tree as
        # glob(["**/*"]), so anything written here is a declared Bazel
        # action input, and rewriting one mid-run bumps its ctime and
        # trips Bazel's concurrent-modification guard -- which then skips
        # disk-cache upload for every affected action (bd w3n6). The
        # sibling weld_examples_discover_golden_test.py already copies
        # for the same reason.
        with tempfile.TemporaryDirectory(prefix="weld-example-") as tmp:
            scratch = Path(tmp) / example_dir.name
            shutil.copytree(example_dir, scratch)
            return self._discover_in(scratch, example_dir.name, env)

    def _discover_in(self, workdir: Path, label: str, env: dict) -> dict:
        proc = subprocess.run(
            [sys.executable, "-m", "weld", "discover"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"wd discover failed in {label}: {proc.stderr}",
        )
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, dict)
        return data

    def test_fastapi_discover_produces_nodes(self) -> None:
        data = self._run_discover(_FASTAPI_DIR)
        self.assertIn("nodes", data)
        self.assertGreater(
            len(data["nodes"]), 0,
            "FastAPI example should produce at least one node",
        )

    def test_custom_strategy_discover_produces_nodes(self) -> None:
        data = self._run_discover(_CUSTOM_DIR)
        self.assertIn("nodes", data)
        self.assertGreater(
            len(data["nodes"]), 0,
            "Custom strategy example should produce at least one node",
        )

    def test_discover_does_not_write_into_live_example_tree(self) -> None:
        """Running discover must leave the live examples/ tree untouched.

        Regression guard for bd w3n6: this suite used to run discover with
        cwd set to the live example directory, which wrote
        examples/01-python-fastapi/.weld/discovery-state.json into tracked
        source on every gate. Because examples/ is globbed into a Bazel
        filegroup, that write landed on a declared action input mid-build
        and Bazel responded by skipping disk-cache upload for hundreds of
        targets ("Skipping uploading outputs because of concurrent
        modifications"). The .gitignore entry for discovery-state.json
        hid the git-visible half of the symptom but left the producer.
        """
        before = _tree_fingerprint(_EXAMPLES_DIR)
        self._run_discover(_FASTAPI_DIR)
        self._run_discover(_CUSTOM_DIR)
        after = _tree_fingerprint(_EXAMPLES_DIR)

        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(
            name for name in set(before) & set(after)
            if before[name] != after[name]
        )
        self.assertEqual(
            (added, removed, changed), ([], [], []),
            "discover mutated the live examples/ tree -- it must run "
            f"against a scratch copy.\n  added:   {added}\n"
            f"  removed: {removed}\n  changed: {changed}",
        )

    def test_custom_strategy_discover_finds_todos(self) -> None:
        data = self._run_discover(_CUSTOM_DIR)
        concept_nodes = [
            n for n in data["nodes"].values()
            if n.get("type") == "concept"
        ]
        self.assertGreater(
            len(concept_nodes), 0,
            "Custom strategy should produce concept-type nodes for TODOs",
        )


class NoPrivateReferencesTest(unittest.TestCase):
    """Example files must not contain internal/private references."""

    def _scan_file(self, path: Path) -> list[str]:
        """Return list of private pattern matches found in a file."""
        findings = []
        text = path.read_text(encoding="utf-8")
        for pattern in _PRIVATE_PATTERNS:
            matches = pattern.findall(text)
            for match in matches:
                findings.append(f"{path.name}: found private ref '{match}'")
        return findings

    def _scan_directory(self, directory: Path) -> list[str]:
        """Scan all text files in a directory tree for private refs."""
        findings = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            try:
                findings.extend(self._scan_file(path))
            except UnicodeDecodeError:
                continue
        return findings

    def test_no_private_refs_in_examples(self) -> None:
        findings = self._scan_directory(_EXAMPLES_DIR)
        self.assertEqual(
            findings, [],
            "Private references found in examples:\n"
            + "\n".join(findings),
        )


if __name__ == "__main__":
    unittest.main()

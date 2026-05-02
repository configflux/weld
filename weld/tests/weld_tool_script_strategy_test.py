"""Tests for the tool_script discovery strategy.

The strategy walks files matching ``glob`` and emits one ``tool:<stem>``
node per file. The language label is derived from the file suffix
(``.py`` -> ``python``, ``.sh`` -> ``bash``) and falls back to the
shebang line for extensionless or atypically-named scripts.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.tool_script import extract


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestToolScriptEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

    def test_directory_with_only_subdirs_returns_empty(self) -> None:
        # is_file() filter must keep directories out of the node map.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools" / "nested").mkdir(parents=True)
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(result.nodes, {})


class TestToolScriptHappyPath(unittest.TestCase):
    """Suffix-based language detection covers the common case."""

    def test_python_suffix_yields_python_tool_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "audit.py", "print('hi')\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:audit", result.nodes)
            node = result.nodes["tool:audit"]
            self.assertEqual(node["type"], "tool")
            self.assertEqual(node["label"], "audit.py")
            props = node["props"]
            self.assertEqual(props["file"], "tools/audit.py")
            self.assertEqual(props["lang"], "python")
            self.assertEqual(props["source_strategy"], "tool_script")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["script"])

    def test_shell_suffix_yields_bash_lang(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "release.sh", "#!/usr/bin/env bash\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:release", result.nodes)
            self.assertEqual(
                result.nodes["tool:release"]["props"]["lang"], "bash"
            )


class TestToolScriptShebangFallback(unittest.TestCase):
    """Without a recognized suffix, the shebang line decides the language."""

    def test_shebang_python_on_extensionless_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(
                root / "tools" / "runner",
                "#!/usr/bin/env python3\nprint('hi')\n",
            )
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:runner", result.nodes)
            self.assertEqual(
                result.nodes["tool:runner"]["props"]["lang"], "python"
            )

    def test_unknown_suffix_without_recognizable_shebang_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No suffix the strategy recognises and no language hint in
            # the first line: lang must be reported as 'unknown' rather
            # than guessed.
            _touch(root / "tools" / "mystery", "echo nothing helpful\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:mystery", result.nodes)
            self.assertEqual(
                result.nodes["tool:mystery"]["props"]["lang"], "unknown"
            )

    def test_dotted_stem_is_normalized_for_node_id(self) -> None:
        # Dots in the stem are not safe in node ids; the strategy must
        # collapse them to underscores so the id remains stable across
        # tools that don't tolerate dotted ids.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "build.helper.py", "x = 1\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:build_helper", result.nodes)


if __name__ == "__main__":
    unittest.main()

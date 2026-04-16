"""In-memory determinism regression for ``weld/file_index.py::build_file_index``.

ADR 0012 scopes the determinism contract to ``graph.json``, but
``file-index.json`` is a sibling artifact and ``build_file_index``
produces the canonical in-memory representation that feeds both
``save_file_index`` and any future in-process consumer (brief, CLI
search, MCP tools). AST visit order is deterministic within a single
Python run but is an implementation detail of the parser, so the
in-memory token lists must themselves be emitted in canonical sorted
order rather than AST visit / insertion order.

This test builds two tiny temporary git repositories with identical
content, invokes ``build_file_index`` on each, and asserts:

1. The two resulting mappings are element-equal (two-run stability on
   independent fresh inputs).
2. Per-file token lists are sorted lexicographically so downstream
   consumers that bypass ``save_file_index`` still see canonical order.

Mirrors the black-box style of
``weld/tests/weld_file_index_determinism_test.py`` but exercises
``build_file_index`` directly.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.file_index import build_file_index  # noqa: E402


_SAMPLE_PY = '''\
"""Module for determinism regression fixture."""

from os import path as _renamed_path
from collections import OrderedDict, defaultdict

__all__ = ["Zebra", "alpha_fn", "mango_fn"]


class Zebra:
    pass


class Alpha:
    pass


def mango_fn():
    return 1


def alpha_fn():
    return 0


def _private_helper():
    return None
'''

_SAMPLE_MD = """\
# Zeta Topic

Intro text.

## Alpha Topic

More text.

## Mango Topic
"""

_SAMPLE_YAML = """\
zeta: 1
alpha: 2
mango: 3
"""


def _seed_repo(root: Path) -> None:
    """Create a tiny git-tracked source tree under *root*."""
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text(_SAMPLE_PY, encoding="utf-8")
    (root / "pkg" / "readme.md").write_text(_SAMPLE_MD, encoding="utf-8")
    (root / "pkg" / "conf.yaml").write_text(_SAMPLE_YAML, encoding="utf-8")

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env={**env})
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env={**env})
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=root,
        check=True,
        env={**env},
    )


class BuildFileIndexDeterminismTest(unittest.TestCase):
    """``build_file_index`` must return a canonical, stable mapping."""

    def test_two_builds_element_equal(self) -> None:
        """Two independent ``build_file_index`` runs on identical input
        trees must return element-equal mappings (same keys, same
        per-file token sequences in the same order).
        """
        with tempfile.TemporaryDirectory() as tda, \
                tempfile.TemporaryDirectory() as tdb:
            root_a = Path(tda)
            root_b = Path(tdb)
            _seed_repo(root_a)
            _seed_repo(root_b)

            index_a = build_file_index(root_a)
            index_b = build_file_index(root_b)

        self.assertEqual(
            sorted(index_a.keys()),
            sorted(index_b.keys()),
            "build_file_index must see the same file set across runs.",
        )
        for path in index_a:
            self.assertEqual(
                index_a[path],
                index_b[path],
                f"token list for {path!r} diverged between runs: "
                f"{index_a[path]!r} vs {index_b[path]!r}",
            )

    def test_tokens_sorted_within_each_file_entry(self) -> None:
        """Per-file token lists must be emitted in sorted order so any
        in-memory consumer (brief, find, MCP) sees canonical order
        without having to re-sort defensively.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_repo(root)
            index = build_file_index(root)

        self.assertTrue(index, "fixture repo should produce a non-empty index")
        for path, tokens in index.items():
            self.assertEqual(
                tokens,
                sorted(tokens),
                f"tokens for {path!r} are not sorted: {tokens!r}. "
                f"Fix: sort the per-file token list inside build_file_index.",
            )


if __name__ == "__main__":
    unittest.main()

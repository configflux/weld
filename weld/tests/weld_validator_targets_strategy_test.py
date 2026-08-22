"""Tests for the validator_targets discovery strategy.

The strategy scans validator modules (lints, checkers, gates) for the
repo-relative path literals they name and emits ``validates`` edges to
those files. The cases below pin the three things that decide whether the
edges are trustworthy: what counts as a governed path, what is refused at
the repository boundary, and when a node may be minted rather than only
referenced.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES, ROLE_VALUES
from weld.strategies.validator_targets import (
    _MAX_GLOB_EXPANSION,
    extract,
)


def _write(root: Path, rel: str, text: str) -> Path:
    """Create *root/rel* with *text*, making parent directories."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source(**overrides: object) -> dict:
    """Return a source entry for the ``tools/`` glob with *overrides*."""
    source: dict = {"glob": "tools/*.py", "type": "file"}
    source.update(overrides)
    return source


def _edge_targets(edges: list[dict], edge_from: str) -> set[str]:
    """Return the ``to`` ids of *edges* originating at *edge_from*."""
    return {e["to"] for e in edges if e["from"] == edge_from}


class ValidatorTargetsExtractTest(unittest.TestCase):
    """End-to-end extract() behaviour over a temporary worktree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_path_literal_becomes_validates_edge(self) -> None:
        """A module constant naming a real file yields a validates edge."""
        _write(self.root, "pkg/guard.py", "VALUE = 1\n")
        _write(
            self.root,
            "tools/lint_guard.py",
            'GUARD_PATH = "pkg/guard.py"\n',
        )
        result = extract(self.root, _source(), {})
        self.assertIn(
            {
                "from": "file:tools/lint_guard",
                "to": "file:pkg/guard",
                "type": "validates",
                "props": {
                    "source_strategy": "validator_targets",
                    "authority": "derived",
                    "confidence": "inferred",
                    "provenance": {"file": "tools/lint_guard.py"},
                },
            },
            result.edges,
        )

    def test_edge_names_the_validator_as_its_producing_file(self) -> None:
        """ADR 0074 (sixth amendment): provenance is the validator, never
        the governed target -- a governed target routinely lives in a
        disjoint source entry (see
        incremental_inbound_edge_provenance_purge_test.py for the
        incremental-purge contract this stamp exists to keep). Stamping the
        target instead would be exactly as broken as stamping nothing.
        """
        _write(self.root, "pkg/guard.py", "VALUE = 1\n")
        _write(
            self.root,
            "tools/lint_guard.py",
            'GUARD_PATH = "pkg/guard.py"\n',
        )
        result = extract(self.root, _source(), {})
        edges = [e for e in result.edges if e["to"] == "file:pkg/guard"]
        self.assertEqual(1, len(edges))
        self.assertEqual(
            {"file": "tools/lint_guard.py"}, edges[0]["props"]["provenance"],
        )

    def test_docstring_path_is_harvested(self) -> None:
        """Prose in a docstring names governed files as often as a constant.

        This is the reported case: the only place the lint stated which
        file must stay import-free was its own docstring.
        """
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")
        _write(
            self.root,
            "tools/lint_prose.py",
            '"""Requires ``pkg/mod.py`` to stay import-free."""\n',
        )
        result = extract(self.root, _source(), {})
        self.assertIn(
            "file:pkg/mod",
            _edge_targets(result.edges, "file:tools/lint_prose"),
        )

    def test_export_less_init_is_minted_as_stub(self) -> None:
        """An export-less __init__.py gets a node so the edge can resolve.

        ``python_module`` declines to anchor it (ADR 0041), which is
        exactly why the governing lint was unreachable from it.
        """
        _write(self.root, "pkg/__init__.py", "")
        _write(
            self.root,
            "tools/lint_init.py",
            'INIT = "pkg/__init__.py"\n',
        )
        result = extract(self.root, _source(), {})
        node = result.nodes.get("file:pkg/__init__")
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual("file", node["type"])
        self.assertEqual("pkg/__init__.py", node["props"]["file"])
        # ``inferred`` is load-bearing: ADR 0103's claim_supersedes veto is
        # what stops this stub overwriting a real definite node.
        self.assertEqual("inferred", node["props"]["confidence"])
        # A minted node must satisfy the graph contract or `wd validate`
        # rejects the whole graph -- the first draft used a role that is not
        # in ROLE_VALUES and did exactly that.
        self.assertTrue(set(node["props"]["roles"]) <= ROLE_VALUES)
        self.assertIn(node["props"]["confidence"], CONFIDENCE_VALUES)
        self.assertIn(
            "file:pkg/__init__",
            _edge_targets(result.edges, "file:tools/lint_init"),
        )

    def test_anchoring_module_is_never_minted(self) -> None:
        """A target another strategy would anchor is referenced, not minted.

        Discovery scope stays a config decision: naming a file in a lint
        must not drag it into the graph from outside the configured globs.
        """
        _write(self.root, "pkg/real.py", "def handler():\n    return 1\n")
        _write(self.root, "tools/lint_real.py", 'P = "pkg/real.py"\n')
        result = extract(self.root, _source(), {})
        self.assertEqual({}, result.nodes)
        self.assertIn(
            "file:pkg/real",
            _edge_targets(result.edges, "file:tools/lint_real"),
        )

    def test_unparseable_target_is_never_minted(self) -> None:
        """A .py target weld cannot read gets no node of its own.

        ``python_module`` skips unparseable source, so a stub for it would
        be a graph node no query can say anything true about.
        """
        _write(self.root, "pkg/__init__.py", "def (:\n")
        _write(self.root, "tools/lint_bad_init.py", 'P = "pkg/__init__.py"\n')
        result = extract(self.root, _source(), {})
        self.assertEqual({}, result.nodes)

    def test_recursive_glob_pattern_is_scanned(self) -> None:
        """A ``**`` source glob finds validators, and reports the files it read.

        Provenance is per-file since bd od2a: the parent directory this used
        to report becomes ``"./"`` for a match at the repo root, which makes
        every path in the repository count as tracked source.
        """
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")
        _write(self.root, "tools/nested/lint_deep.py", 'P = "pkg/mod.py"\n')
        result = extract(self.root, _source(glob="tools/**/*.py"), {})
        self.assertIn(
            "file:pkg/mod",
            _edge_targets(result.edges, "file:tools/nested/lint_deep"),
        )
        self.assertEqual(
            ["tools/nested/lint_deep.py"], result.discovered_from,
        )

    def test_glob_literal_expands_to_each_file(self) -> None:
        """A bounded glob literal yields one edge per matched file."""
        _write(self.root, "pkg/a.py", "def a():\n    return 1\n")
        _write(self.root, "pkg/b.py", "def b():\n    return 1\n")
        _write(self.root, "tools/lint_glob.py", 'PATTERN = "pkg/*.py"\n')
        result = extract(self.root, _source(), {})
        targets = _edge_targets(result.edges, "file:tools/lint_glob")
        self.assertIn("file:pkg/a", targets)
        self.assertIn("file:pkg/b", targets)

    def test_oversized_glob_is_dropped_whole(self) -> None:
        """Extension-wide governance produces no per-file edges."""
        for index in range(_MAX_GLOB_EXPANSION + 5):
            _write(self.root, f"pkg/mod{index}.py", "def f():\n    return 1\n")
        _write(self.root, "tools/lint_wide.py", 'PATTERN = "pkg/*.py"\n')
        result = extract(self.root, _source(), {})
        self.assertEqual(
            set(), _edge_targets(result.edges, "file:tools/lint_wide"),
        )

    def test_shell_literal_does_not_target_same_stem_module(self) -> None:
        """End-to-end guard for the same-stem extension collision.

        ``bin/run.sh`` and ``bin/run.py`` collapse to one ``file:`` ID, so a
        shell literal must never be offered that spelling.
        """
        _write(self.root, "bin/run.py", "def main():\n    return 1\n")
        _write(self.root, "bin/run.sh", "#!/bin/sh\nexit 0\n")
        _write(self.root, "tools/lint_sh.py", 'SCRIPT = "bin/run.sh"\n')
        result = extract(self.root, _source(), {})
        self.assertNotIn(
            "file:bin/run",
            _edge_targets(result.edges, "file:tools/lint_sh"),
        )

    def test_self_reference_is_skipped(self) -> None:
        """A validator naming itself does not validate itself."""
        _write(
            self.root,
            "tools/lint_self.py",
            'SELF = "tools/lint_self.py"\n',
        )
        result = extract(self.root, _source(), {})
        self.assertEqual([], result.edges)

    def test_include_names_filters_non_validators(self) -> None:
        """Only modules matching include_names may claim governance."""
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")
        _write(self.root, "tools/lint_yes.py", 'P = "pkg/mod.py"\n')
        _write(self.root, "tools/helper_no.py", 'P = "pkg/mod.py"\n')
        result = extract(self.root, _source(include_names=["lint_*.py"]), {})
        froms = {e["from"] for e in result.edges}
        self.assertEqual({"file:tools/lint_yes"}, froms)

    def test_exclude_drops_test_peers(self) -> None:
        """The exclude list keeps a validator's own test peer out."""
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")
        _write(self.root, "tools/lint_x.py", 'P = "pkg/mod.py"\n')
        _write(self.root, "tools/lint_x_test.py", 'P = "pkg/mod.py"\n')
        result = extract(
            self.root, _source(exclude=["tools/*_test.py"]), {},
        )
        self.assertEqual(
            {"file:tools/lint_x"}, {e["from"] for e in result.edges},
        )

    def test_unparseable_module_is_skipped(self) -> None:
        """A syntax error in one validator does not fail discovery."""
        _write(self.root, "pkg/mod.py", "def f():\n    return 1\n")
        _write(self.root, "tools/lint_broken.py", "def (:\n")
        _write(self.root, "tools/lint_ok.py", 'P = "pkg/mod.py"\n')
        result = extract(self.root, _source(), {})
        self.assertEqual(
            {"file:tools/lint_ok"}, {e["from"] for e in result.edges},
        )

    def test_missing_glob_parent_returns_empty(self) -> None:
        """An unconfigured tree yields an empty result, not an error."""
        result = extract(self.root, _source(), {})
        self.assertEqual(({}, [], []), tuple(result))

    def test_missing_glob_key_returns_empty(self) -> None:
        """A source entry with no glob yields an empty result."""
        result = extract(self.root, {"type": "file"}, {})
        self.assertEqual(({}, [], []), tuple(result))

    def test_output_is_deterministic(self) -> None:
        """Two runs over the same tree produce identical edge ordering."""
        for name in ("c", "a", "b"):
            _write(self.root, f"pkg/{name}.py", "def f():\n    return 1\n")
        _write(
            self.root,
            "tools/lint_order.py",
            'P = ("pkg/c.py", "pkg/a.py", "pkg/b.py")\n',
        )
        first = extract(self.root, _source(), {}).edges
        second = extract(self.root, _source(), {}).edges
        self.assertEqual(first, second)
        self.assertEqual(
            ["file:pkg/a", "file:pkg/b", "file:pkg/c"],
            [e["to"] for e in first if e["to"].startswith("file:")],
        )


if __name__ == "__main__":
    unittest.main()

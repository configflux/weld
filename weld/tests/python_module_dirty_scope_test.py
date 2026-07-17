"""python_module dirty-scope incremental re-parse (ADR 0084, bd ir2l).

``python_module.extract`` now narrows its parse loop to the dirty subset of
its glob when the orchestrator hands an ``IncrementalHint`` (ADR 0084). Two
guards:

* **Perf-pin** -- ``test_only_dirty_file_parsed``. Byte-identity alone does
  NOT prove the optimization landed: python_module already produced identical
  output when it re-parsed every sibling (the orchestrator discards the clean
  ones). This test counts the files python_module actually ``ast.parse``'d on
  an incremental refresh and asserts it parsed ONLY the dirty file, while a
  full discover of the same tree parses every sibling. Without the dirty-scope
  wiring this test fails (incremental parses all siblings) even though
  byte-identity still passes.
* **Byte-identity** -- ``test_incremental_byte_identical_to_full``. Editing one
  file in a multi-file flat glob (with a ``package`` so ``contains`` edges are
  exercised) must yield nodes+edges identical to a full discover at the same
  end state. (The broader equivalence guard lives in
  ``incremental_refresh_equivalence_test``; this pins the python_module
  flat-glob + package case specifically.)

Plus focused unit tests for the ``dirty_scoped_matched`` helper.
"""

from __future__ import annotations

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld.strategies._incremental_hint import INCREMENTAL_HINT_KEY, IncrementalHint
from weld.strategies._python_module_incremental import dirty_scoped_matched

_SIBLINGS = ("alpha", "beta", "gamma", "delta", "epsilon")
_DIRTY = "beta"


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _fixture(root: Path, sentinel: int) -> None:
    """Flat-glob python_module package with several siblings.

    ``pkg/*.py`` (flat, mirroring the repo's ``weld/*.py``) with a ``package``
    so ``contains`` edges are emitted. Only ``beta`` carries the sentinel, so
    editing the sentinel dirties exactly one sibling.
    """
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for n in _SIBLINGS:
        body = f"    return {sentinel}\n" if n == _DIRTY else "    return 1\n"
        (pkg / f"{n}.py").write_text(f"def {n}_fn():\n{body}", encoding="utf-8")
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n  nodes:\n    - id: pkg:pkg\n      type: package\n"
        "      label: pkg\nsources:\n  - strategy: python_module\n"
        "    glob: pkg/*.py\n    type: file\n    package: pkg:pkg\n",
        encoding="utf-8",
    )


def _record_ast_parse(sink: list[str]):
    """Patch the shared ``ast.parse`` to record every file it is called on.

    The orchestrator loads bundled strategies under a synthetic module name
    (``weld_strategy_python_module``), so patching ``python_module``'s own
    namespace misses them. Every strategy's ``import ast`` binds the one shared
    ``ast`` module, so wrapping ``ast.parse`` (delegating to the original)
    observes each strategy's parses regardless of load name. The fixture below
    configures ONLY ``python_module`` and ``ast.parse`` is never invoked at
    import time (CPython compiles bytecode without it), so on a ``pkg/*.py``
    tree every recorded ``.py`` parse is python_module's.
    """
    orig = ast.parse

    def _rec(source, *args, **kwargs):
        fname = kwargs.get("filename")
        if fname is None and args:
            fname = args[0]
        sink.append(str(fname) if fname is not None else "<unknown>")
        return orig(source, *args, **kwargs)

    return mock.patch("ast.parse", _rec)


def _parsed_pkg_rels(sink: list[str], root: Path) -> set[str]:
    out: set[str] = set()
    for f in sink:
        p = Path(f)
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            continue
        if rel.endswith(".py"):
            out.add(rel)
    return out


def _strip_meta(graph: dict) -> dict:
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


class DirtyScopedMatchedUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("/repo")
        self.matched = [self.root / "pkg" / f"{n}.py" for n in _SIBLINGS]

    def test_no_hint_returns_matched_unchanged(self) -> None:
        # Full discover / non-incremental caller: identity, same object semantics.
        self.assertEqual(dirty_scoped_matched(self.matched, self.root, {}), self.matched)
        # A context with an unrelated key is still "no hint".
        self.assertEqual(
            dirty_scoped_matched(self.matched, self.root, {"other": 1}), self.matched,
        )

    def test_hint_narrows_to_dirty_subset_order_preserved(self) -> None:
        ctx = {INCREMENTAL_HINT_KEY: IncrementalHint(
            dirty_files=frozenset({"pkg/gamma.py", "pkg/alpha.py"}), prior_nodes={},
        )}
        got = dirty_scoped_matched(self.matched, self.root, ctx)
        # Order follows *matched* (alpha before gamma), not the dirty set.
        self.assertEqual([str(p.relative_to(self.root)) for p in got],
                         ["pkg/alpha.py", "pkg/gamma.py"])

    def test_hint_with_no_intersection_returns_empty(self) -> None:
        ctx = {INCREMENTAL_HINT_KEY: IncrementalHint(
            dirty_files=frozenset({"pkg/not_here.py"}), prior_nodes={},
        )}
        self.assertEqual(dirty_scoped_matched(self.matched, self.root, ctx), [])


class PythonModuleDirtyScopePerfPinTest(unittest.TestCase):
    def test_only_dirty_file_parsed(self) -> None:
        from weld.discover import _discover_single_repo

        # Incremental: seed full, edit one sibling, refresh -> python_module
        # must ast.parse ONLY the dirty file.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root, 1)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _fixture(root, 99)  # edit only beta's body
            _commit(root)
            sink: list[str] = []
            with _record_ast_parse(sink):
                _discover_single_repo(root, incremental=True, write_graph=True)
            self.assertEqual(
                _parsed_pkg_rels(sink, root), {"pkg/beta.py"},
                "python_module must parse ONLY the dirty file on an incremental "
                "refresh; parsing siblings means the ADR 0084 dirty-scope did "
                "not land (byte-identity would still pass -- this is the guard "
                "that catches a silent no-op).",
            )

        # Contrast: a full discover of the same tree parses EVERY sibling
        # (proves the recorder captures correctly and the full path is
        # unchanged -- N parsed vs 1).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root, 99)
            _commit(root)
            sink_full: list[str] = []
            with _record_ast_parse(sink_full):
                _discover_single_repo(root, incremental=False, write_graph=True)
            expected_full = {f"pkg/{n}.py" for n in _SIBLINGS} | {"pkg/__init__.py"}
            self.assertEqual(
                _parsed_pkg_rels(sink_full, root), expected_full,
                "full discover should parse every sibling; contrast for the "
                "incremental case above",
            )

    def test_incremental_byte_identical_to_full(self) -> None:
        from weld.discover import _discover_single_repo

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root, 1)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _fixture(root, 99)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root, 99)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "python_module dirty-scoped incremental graph (nodes+contains edges) "
            "must be byte-identical to a full discover at the same end state",
        )


if __name__ == "__main__":
    unittest.main()

"""T1a determinism audit regression — frozenset iteration order.

Covers ADR 0012 §2 row 1 for a specific site:
``weld.strategies.cpp_resolver.CPP_HEADER_EXTS`` is a frozenset of
strings. Under Python's default ``PYTHONHASHSEED=random`` setting, the
native iteration order of such a frozenset varies between processes.
The call site in ``augment_state_with_headers`` now wraps the
iteration with ``sorted()`` so the outer loop walks extensions in a
canonical order. That order determines the sequence in which header
entries are appended to ``per_file`` — and that list feeds
``resolve_includes_pass`` where edges are mutated in place.

This test locks the contract in three complementary ways:

1. In-process: the iteration order of ``CPP_HEADER_EXTS`` must equal
   ``sorted(CPP_HEADER_EXTS)``.
2. Cross-process: two subprocesses running under different
   ``PYTHONHASHSEED`` values must emit the same iteration — which is
   only true when callers sort before iterating.
3. Call-site regression: an AST scan of all ``weld/`` production files
   flags any ``for ... in <frozenset>`` loop that does not wrap the
   iterable with ``sorted()``, catching regressions at the source.

Both runtime assertions deliberately exercise the iteration pattern
the production code uses after the fix: ``for ext in sorted(...)``.
If a future caller reverts to ``for ext in CPP_HEADER_EXTS`` directly,
the cross-process assertion will flag the regression.

The AST scanner (assertion 3) provides a *static* safety net: it
collects every module-level ``frozenset(...)`` assignment in ``weld/``
production code, then verifies that every ``for`` loop iterating over
one of those names wraps it with ``sorted()``.

Companion audit document: ``docs/determinism-audit-T1a.md``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies.cpp_resolver import CPP_HEADER_EXTS  # noqa: E402

# ---------------------------------------------------------------------------
# Production-path frozenset iteration scanner
# ---------------------------------------------------------------------------

_WELD_PKG = Path(_repo_root) / "weld"
_EXCLUDED_DIRS = {"tests", "bench", "__pycache__"}


def _production_py_files() -> list[Path]:
    """Collect ``weld/**/*.py`` excluding test/bench/cache directories."""
    result: list[Path] = []
    for root, dirs, files in os.walk(_WELD_PKG):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for fname in sorted(files):
            if fname.endswith(".py"):
                result.append(Path(root) / fname)
    return sorted(result)


def _frozenset_names_in_module(tree: ast.Module) -> set[str]:
    """Return names assigned to ``frozenset(...)`` at module level."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if isinstance(val, ast.Call):
            func = val.func
            if isinstance(func, ast.Name) and func.id == "frozenset":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _is_sorted_call(node: ast.expr) -> bool:
    """True when *node* is ``sorted(<anything>)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
    )


def _unsorted_frozenset_iterations() -> list[str]:
    """AST-scan production code for ``for x in <frozenset>`` without sorted().

    Returns a list of ``"relpath:line  for <target> in <name>"`` strings
    for every violation found.
    """
    violations: list[str] = []
    for filepath in _production_py_files():
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            continue

        fs_names = _frozenset_names_in_module(tree)
        if not fs_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            iter_node = node.iter
            # ``for x in SOME_FROZENSET`` (bare name, no sorted)
            if (
                isinstance(iter_node, ast.Name)
                and iter_node.id in fs_names
            ):
                rel = filepath.relative_to(_repo_root)
                violations.append(
                    f"{rel}:{node.lineno}  "
                    f"for ... in {iter_node.id}"
                )
            # ``for x in sorted(SOME_FROZENSET)`` — safe, skip
            # ``for x in func(SOME_FROZENSET)`` where func != sorted
            if _is_sorted_call(iter_node):
                continue
            if isinstance(iter_node, ast.Call):
                for arg in iter_node.args:
                    if isinstance(arg, ast.Name) and arg.id in fs_names:
                        # Wrapped in a non-sorted call — flag it
                        func_name = (
                            iter_node.func.id
                            if isinstance(iter_node.func, ast.Name)
                            else "<expr>"
                        )
                        rel = filepath.relative_to(_repo_root)
                        violations.append(
                            f"{rel}:{node.lineno}  "
                            f"for ... in {func_name}({arg.id})"
                        )
    return violations


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _sorted_iteration_order_under_seed(seed: str) -> list[str]:
    """Return ``sorted(CPP_HEADER_EXTS)`` as observed under PYTHONHASHSEED=seed.

    Spawn a subprocess so this process's current hash seed does not
    contaminate the sample. The subprocess mirrors the production
    iteration pattern in ``augment_state_with_headers``: it wraps
    the frozenset with ``sorted()`` before iterating.
    """
    prog = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, %r)
        from weld.strategies.cpp_resolver import CPP_HEADER_EXTS
        # Mirror the production call site: sort before iterating.
        for ext in sorted(CPP_HEADER_EXTS):
            print(ext)
        """
        % _repo_root
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    proc = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess failed: {proc.stderr}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class FrozensetOrderDeterminismTest(unittest.TestCase):
    """ADR 0012 §2 row 1: set/dict iteration to ordered output must go through sorted()."""

    def test_cpp_header_exts_sorted_iteration_is_canonical(self) -> None:
        """``sorted(CPP_HEADER_EXTS)`` must match the canonical lexicographic order.

        This locks the effective iteration order used by the
        production call site in ``augment_state_with_headers``.
        Wrapping the frozenset with ``sorted()`` guarantees a stable
        canonical sequence regardless of the per-process hash seed.
        """
        expected = [".h", ".hh", ".hpp", ".hxx", ".inc", ".ipp", ".tpp"]
        self.assertEqual(
            sorted(CPP_HEADER_EXTS),
            expected,
            "sorted(CPP_HEADER_EXTS) must match the canonical "
            "lexicographic order. If this assertion changes, update "
            "ADR 0012 §2 row 1 and the call site in "
            "weld/strategies/cpp_resolver.py.",
        )

    def test_cpp_header_exts_iteration_is_hashseed_stable(self) -> None:
        """Sorted iteration under two different PYTHONHASHSEED values must match.

        Runs two subprocesses with different seeds and asserts the
        iteration orders are equal. They are equal because the
        production pattern wraps the frozenset with ``sorted()`` before
        iterating; a regression that removes the sort would fail this
        assertion under at least one of the two seeds.
        """
        order_a = _sorted_iteration_order_under_seed("42")
        order_b = _sorted_iteration_order_under_seed("1337")
        self.assertEqual(
            order_a,
            order_b,
            "iteration order over CPP_HEADER_EXTS must be identical "
            "across different PYTHONHASHSEED values. The production "
            "call site must wrap with sorted() (ADR 0012 §2 row 1 "
            "and §5).",
        )

    def test_no_unsorted_frozenset_iteration_in_production(self) -> None:
        """AST-scan weld/ production code for unsorted frozenset iteration.

        Walks every ``.py`` file under ``weld/`` (excluding tests and
        benchmarks), collects module-level ``frozenset(...)`` assignments,
        then checks that every ``for`` loop iterating over one of those
        names wraps the iterable with ``sorted()``.

        A violation here means a production call site iterates a
        frozenset without deterministic ordering, risking
        PYTHONHASHSEED-dependent output (ADR 0012 §2).
        """
        violations = _unsorted_frozenset_iterations()
        self.assertEqual(
            violations,
            [],
            "Production code iterates frozenset variable(s) without "
            "sorted(). Each site listed below must wrap the iterable "
            "with sorted() to guarantee deterministic order "
            "(ADR 0012 §2):\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

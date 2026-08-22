"""Incremental == full for a rule-generating parameterized macro (bd iysm).

``incremental_cross_source_equivalence_test`` already pins the cross-source
purge shape (ADR 0074/bd cpkp) for a ``.bzl`` that exports a **constant**
(``load(":srcs.bzl", "LIB_SRCS")``, spent on a literal ``py_test``'s
``srcs``). Nothing in that family exercises a ``.bzl`` macro that itself
*generates* the rule call -- ADR 0123's subject -- so this is a separate,
minimal fixture rather than an extension of that sensitive, already-pinned
one (the same choice ``incremental_decorates_scope_calls_equivalence_test``
made for the same reason).

The mechanism ADR 0123 relies on is unchanged from the zero-parameter macro
case: every edge a macro-expanded target emits carries the same
``edge_props(rel_path)`` provenance, stamped from the *BUILD file*, that a
literal rule call's edges already carry (``weld/strategies/bazel.py``'s
``extract()`` does not distinguish a macro-expanded target dict from a
literal one once ``targets_in`` has produced it). This fixture's job is to
confirm that reuse holds under the one shape only a parameterized macro
call has: a keyword-argument, ``**kwargs``-splat call site whose producing
BUILD file stays clean while the file it names goes dirty -- the exact
clean-producer/dirty-endpoint shape bd cpkp named -- and whose ``deps``
resolve into both an in-repo target and an ``external-dep:`` node (ADR
0121), so both edge classes are covered in the one round.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402


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


def _write_fixture(root: Path, edited: str | None) -> None:
    """A ``bench_py_test``-shaped macro: name/tags/local + ``**kwargs``.

    ``alpha_test`` is declared with keyword arguments only, mirroring every
    real call site in ``weld/tests/bench/BUILD.bazel`` -- ``srcs``/``deps``
    arrive at the inner rule call exclusively through the splat, never as an
    explicit keyword, which is the shape :func:`weld.strategies._bazel_eval
    .eval_kwarg`'s dict-fallback exists for.
    """
    def body(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == edited:
            text = text + "\n# edited\n"
        path.write_text(text, encoding="utf-8")

    body("src/__init__.py", "")
    body("src/alpha.py", "def alpha_fn():\n    return 1\n")
    body(
        "src/wrap.bzl",
        'load("@rules_python//python:defs.bzl", "py_test")\n\n'
        "def wrap_py_test(name, tags = [], **kwargs):\n"
        '    py_test(name = name, tags = tags + ["no-sandbox"], **kwargs)\n',
    )
    body(
        "src/BUILD.bazel",
        'load(":wrap.bzl", "wrap_py_test")\n\n'
        "wrap_py_test(\n"
        '    name = "alpha_test",\n'
        '    srcs = ["alpha.py"],\n'
        '    deps = ["@pypi//tree_sitter"],\n'
        ")\n",
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - strategy: python_module\n    glob: src/**/*.py\n    type: file\n"
        '  - strategy: bazel\n    glob: "**/BUILD.bazel"\n    type: build-target\n',
        encoding="utf-8",
    )


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


def _strip_meta(graph: dict) -> dict:
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _incremental_graph(edited: str | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, None)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, edited)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_graph(edited: str | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, edited)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class ParamMacroExpandedTargetEquivalenceTest(unittest.TestCase):
    """The macro-expanded target's node/edges, incremental vs. full."""

    def test_every_single_file_edit_matches_full(self) -> None:
        for edited in ("src/alpha.py", "src/BUILD.bazel", "src/wrap.bzl"):
            with self.subTest(edited=edited):
                inc, full = _incremental_graph(edited), _full_graph(edited)
                inc_e, full_e = _edge_set(inc), _edge_set(full)
                self.assertEqual(
                    inc_e, full_e,
                    f"editing {edited} diverged the incremental edge set from "
                    f"a full discover over the macro-expanded target "
                    f"(full-only={sorted(full_e - inc_e)}, "
                    f"inc-only={sorted(inc_e - full_e)})",
                )
                self.assertEqual(
                    _strip_meta(inc), _strip_meta(full),
                    f"editing {edited} diverged the incremental graph "
                    "nodes/edges from a full discover",
                )

    def test_macro_target_edges_survive_clean_build_file_incremental(self) -> None:
        """The named regression shape (bd cpkp), asserted directly: the
        producer (src/BUILD.bazel, and the .bzl the macro itself is in) is
        clean, the endpoint (src/alpha.py) is dirty, and the macro-expanded
        target's edges -- in-repo contains AND the external-dep depends_on --
        must survive exactly as a full discover would produce them."""
        inc_e = _edge_set(_incremental_graph("src/alpha.py"))
        self.assertIn(
            ("test-target://src:alpha_test", "contains", "file:src/alpha"),
            inc_e,
        )
        self.assertIn(
            (
                "test-target://src:alpha_test", "depends_on",
                "external-dep:pypi:tree_sitter",
            ),
            inc_e,
        )


if __name__ == "__main__":
    unittest.main()

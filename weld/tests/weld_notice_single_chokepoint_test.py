"""Static guard: exactly one stderr chokepoint for ``[weld] ...`` notices.

Companion to :mod:`weld.tests.weld_notice_test` (which pins the sink) and
:mod:`weld.tests.weld_json_stdout_purity_test` (which drives the end-to-end
``--json`` purity contract). This test AST-scans the shipped ``weld`` package and
asserts that no source file emits a general ``[weld] ...`` line by writing to a
stream directly -- neither ``print(..., file=sys.stderr)`` nor
``<stream>.write("[weld] ...")``: every such operational notice must route
through :func:`weld._notice.emit`, so the stdout-purity invariant has a single
enforcement point.

Deliberately *not* flagged (these are correct as raw prints):

* ``[weld watch] ...`` -- a distinct namespace; ``emit`` would double-prefix it
  (``emit`` only writes a message as-is when it starts with the exact ``[weld]``
  token, and ``[weld watch]`` does not).
* Non-``[weld]`` lines (progress text, raw exception strings, the discover
  success summary) -- ``emit`` would inject a ``[weld]`` prefix and change the
  observable text.

The scan runs over the package as materialised next to ``weld.__init__`` -- the
Bazel runfiles copy under sandboxed test runs, or the real source tree under a
local ``python -m unittest`` / ``pytest`` run. A minimum-file-count assertion
guards against a false green if the package sources are ever absent from the
runfiles tree.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import weld

_PACKAGE_DIR = Path(weld.__file__).resolve().parent
_MIN_SCANNED_FILES = 30  # runtime alone ships well over 100 modules


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        rel = path.relative_to(_PACKAGE_DIR)
        parts = rel.parts
        if "build" in parts or "tests" in parts:
            continue
        if path.name.endswith("_test.py"):
            continue
        files.append(path)
    return files


def _literal_prefix(node: ast.AST) -> str | None:
    """Best-effort static leading text of a ``print`` first argument."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_prefix(node.left)
    return None


def _is_stderr_print(call: ast.Call) -> bool:
    """``print(..., file=sys.stderr)``."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "print"):
        return False
    for kw in call.keywords:
        if kw.arg == "file":
            value = kw.value
            return (
                isinstance(value, ast.Attribute)
                and value.attr == "stderr"
                and isinstance(value.value, ast.Name)
                and value.value.id == "sys"
            )
    return False


def _is_stream_write(call: ast.Call) -> bool:
    """Any ``<stream>.write(...)`` call.

    A ``[weld] ...`` line written this way bypasses :func:`weld._notice.emit`
    exactly as a bare ``print(..., file=sys.stderr)`` would. The sink itself
    (``weld/_notice.py``) writes a *variable*, never a ``[weld]``-prefixed
    literal, so it is not caught here.
    """
    return isinstance(call.func, ast.Attribute) and call.func.attr == "write"


def _emit_bypass_first_arg(call: ast.Call) -> ast.expr | None:
    """Return the message argument if *call* is a raw notice sink, else None."""
    if _is_stderr_print(call) or _is_stream_write(call):
        return call.args[0] if call.args else None
    return None


class NoticeSingleChokepointTest(unittest.TestCase):
    def test_no_general_weld_stderr_prints_bypass_emit(self) -> None:
        sources = _iter_source_files()
        self.assertGreaterEqual(
            len(sources),
            _MIN_SCANNED_FILES,
            f"Only found {len(sources)} weld source files under "
            f"{_PACKAGE_DIR}; the package sources appear to be missing from "
            "the test environment, which would make this guard a false green.",
        )

        offenders: list[str] = []
        for path in sources:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                arg = _emit_bypass_first_arg(node)
                if arg is None:
                    continue
                prefix = _literal_prefix(arg)
                if prefix is not None and prefix.startswith("[weld] "):
                    rel = path.relative_to(_PACKAGE_DIR.parent)
                    offenders.append(f"{rel}:{node.lineno}")

        self.assertEqual(
            offenders,
            [],
            "Every general '[weld] ...' notice must route through "
            "weld._notice.emit, not print(..., file=sys.stderr), so the "
            "stdout-purity invariant keeps a single chokepoint. Offending "
            "sites:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()

"""No strategy may build ``discovered_from`` out of a file's parent directory.

``weld_strategy_file_provenance_test`` drives twenty-two strategies and
asserts what they record. It cannot cover the twenty-third: a strategy added
tomorrow, or one this repo cannot drive without an optional dependency
(``tree_sitter`` needs its grammars). Both former directory forms were
reintroduced repeatedly precisely because each looked locally harmless --
``csharp_package`` even shipped a private ``!= "./"`` filter, which is this
bug being handled one strategy at a time instead of once.

So the shape is refused structurally, by reading the source rather than by
running it: an expression that reaches ``.parent`` must not flow into a
provenance collection. The check is AST-based on purpose -- a text scan would
trip over every docstring in this repo that *describes* the defect, including
the ones written to explain the fix.

The rule this enforces is :class:`weld.strategies._helpers.StrategyResult`'s:
``discovered_from`` is the files a strategy read, never node identity. The one
legitimate directory entry (``python_package``, whose node *is* the directory)
goes through :func:`weld.strategies._helpers.directory_provenance`, which
degenerates to the member files at the repo root -- so it needs no ``.parent``
here and is not an exception to the rule.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import weld.strategies

_STRATEGY_DIR = pathlib.Path(weld.strategies.__file__).parent

#: Method names that push a value into a provenance collection.
_SINKS = frozenset({"append", "extend", "add", "update"})


def _is_provenance_sink(call: ast.Call) -> bool:
    """True when *call* writes into a ``discovered_*`` collection."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _SINKS:
        return False
    target = func.value
    if not isinstance(target, ast.Name):
        return False
    return "discovered" in target.id


def _reaches_parent(node: ast.AST) -> bool:
    """True when *node* contains a ``.parent`` attribute access."""
    return any(
        isinstance(sub, ast.Attribute) and sub.attr == "parent"
        for sub in ast.walk(node)
    )


class TestNoDirectoryDerivedProvenance(unittest.TestCase):
    """A ``.parent`` expression must never reach ``discovered_from``."""

    def test_no_strategy_appends_a_parent_directory(self) -> None:
        offenders: list[str] = []
        for path in sorted(_STRATEGY_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_provenance_sink(node):
                    continue
                if any(_reaches_parent(arg) for arg in node.args):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "these strategies derive discovered_from from a directory rather "
            "than from the files they read; at the repo root that entry "
            'becomes "./", which makes every path in the repository count as '
            "tracked source. Record the file, or use "
            "_helpers.directory_provenance when the node IS the directory: "
            + ", ".join(offenders),
        )

    def test_the_check_can_actually_see_the_defect(self) -> None:
        """The guard above is only worth its line count if it would fire.

        A structural assertion that passes because it matches nothing is the
        failure mode to worry about here, so the detector is run against the
        exact expression the fourteen converted strategies used to contain.
        """
        offending = ast.parse(
            "discovered_from.append(py.parent.relative_to(root).as_posix() + '/')",
        )
        found = [
            node for node in ast.walk(offending)
            if isinstance(node, ast.Call)
            and _is_provenance_sink(node)
            and any(_reaches_parent(arg) for arg in node.args)
        ]
        self.assertEqual(len(found), 1)

    def test_the_check_does_not_fire_on_the_repaired_shape(self) -> None:
        clean = ast.parse(
            "discovered_from.extend(file_provenance(root, [py]))\n"
            "discovered_from.append(rel.as_posix())\n",
        )
        found = [
            node for node in ast.walk(clean)
            if isinstance(node, ast.Call)
            and _is_provenance_sink(node)
            and any(_reaches_parent(arg) for arg in node.args)
        ]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

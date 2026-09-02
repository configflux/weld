"""A strategy may name the glob's parent, but must not resolve with it.

ADR 0112 deleted the ``(root / pattern).parent`` plus ``is_dir()`` guard from
every strategy that had one, and its Verification section checked the result
with ``grep -l _resolve_glob weld/strategies/``. Four strategies matched
neither that grep nor the ``walk_glob`` call-site survey beside it, because
they inlined the same resolve under a name of their own -- ``routers_dir``
(``fastapi``), ``contracts_dir`` (``pydantic``), ``parent`` (``compose``,
``events_config``). So they kept the guard, and everything ADR 0112 § 1 records
as fixed stayed broken in them for another three weeks (bd b9xgd).

An inventory built from the names the old code used cannot see a copy that
chose a different name. This is the check that can: it asks the AST for the
*expression*, ``(root / <anything>).parent``, which every copy had regardless
of what it assigned the result to.

The rule is deliberately not "no strategy may write that expression". The
expression has a legitimate use -- as a **label** derived from the pattern,
never as the resolve. ``sqlalchemy`` keeps ``domain_dir`` to name modules with,
and ``fastapi`` keeps a per-file equivalent to find the app module beside a
routers directory. What separates a label from a resolve is what the strategy
does *next*: a module that names the parent and also calls the shared resolver
is using it as a label, because the resolver is where its files come from. A
module that names the parent and calls no resolver has nowhere else for its
files to come from, which is the defect.

One strategy is exempt by shape rather than by exception, and it is named
below with its reason: ``worker_stage`` never resolves a file glob at all.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import weld.strategies

_STRATEGY_DIR = pathlib.Path(weld.strategies.__file__).parent

#: The two entry points of ``weld.strategies._glob_resolve``. A module that
#: calls neither has no shared resolve to be using the parent alongside.
_RESOLVERS = frozenset({"resolve_glob", "resolve_glob_with_provenance"})

#: ``worker_stage`` walks the glob's parent with ``Path.iterdir`` looking for
#: stage subdirectories and never uses ``Path(pattern).name``: there is no file
#: set for the shared resolver to return, so migrating it would change what a
#: stage *is* rather than how a glob resolves. Exempt by shape, and the only
#: entry -- a second one is a prompt to re-read this docstring, not to append.
_DIRECTORY_SHAPED = frozenset({"worker_stage.py"})


def _names_the_glob_parent(node: ast.AST) -> bool:
    """True for ``(root / <expr>).parent`` -- the expression every copy had."""
    if not (isinstance(node, ast.Attribute) and node.attr == "parent"):
        return False
    inner = node.value
    return (
        isinstance(inner, ast.BinOp)
        and isinstance(inner.op, ast.Div)
        and isinstance(inner.left, ast.Name)
        and inner.left.id == "root"
    )


def _calls_a_shared_resolver(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _RESOLVERS
        for node in ast.walk(tree)
    )


def _offenders_in(source: str, filename: str = "<test>") -> list[str]:
    """Lines naming the glob parent in a module that resolves no glob."""
    tree = ast.parse(source, filename=filename)
    if _calls_a_shared_resolver(tree):
        return []
    return [
        f"{filename}:{node.lineno}"
        for node in ast.walk(tree)
        if _names_the_glob_parent(node)
    ]


class TestGlobParentIsALabelNotAResolve(unittest.TestCase):
    """Every strategy that names the glob's parent also resolves through ADR 0112."""

    def test_no_strategy_resolves_through_the_glob_parent(self) -> None:
        offenders: list[str] = []
        for path in sorted(_STRATEGY_DIR.glob("*.py")):
            if path.name in _DIRECTORY_SHAPED:
                continue
            offenders.extend(
                _offenders_in(path.read_text(encoding="utf-8"), path.name)
            )
        self.assertEqual(
            offenders, [],
            "these strategies derive a directory from the glob pattern and "
            "call no shared resolver, so the parent *is* their resolve -- the "
            "ADR 0112 copy, which emits nothing for a `**` glob or a wildcard "
            "directory segment (fastapi, pydantic) or the wrong set entirely "
            "(compose, events_config). Resolve with "
            "weld.strategies._glob_resolve.resolve_glob: " + ", ".join(offenders),
        )

    def test_the_check_can_actually_see_the_defect(self) -> None:
        """A structural assertion that matches nothing is the failure mode.

        Both historical shapes, verbatim: the early return and the root
        fallback. Neither module calls a resolver, which is the whole point.
        """
        early_return = (
            "def extract(root, source, context):\n"
            "    pattern = source['glob']\n"
            "    routers_dir = (root / pattern).parent\n"
            "    if not routers_dir.is_dir():\n"
            "        return None\n"
        )
        root_fallback = (
            "def extract(root, source, context):\n"
            "    pattern = source['glob']\n"
            "    parent = (root / pattern).parent\n"
            "    if not parent.is_dir():\n"
            "        parent = root\n"
        )
        self.assertEqual(len(_offenders_in(early_return)), 1)
        self.assertEqual(len(_offenders_in(root_fallback)), 1)

    def test_the_parent_is_allowed_beside_a_real_resolve(self) -> None:
        """``sqlalchemy``'s shape: the parent as a label, the resolver for files.

        Without this the check would be a blanket ban, and the two strategies
        that legitimately need a pattern-derived label would have to smuggle it
        past a lint rather than say what they mean.
        """
        labelled = (
            "def extract(root, source, context):\n"
            "    pattern = source['glob']\n"
            "    domain_dir = (root / pattern).parent\n"
            "    for py in resolve_glob(root, pattern, []):\n"
            "        yield module_name(py, domain_dir)\n"
        )
        self.assertEqual(_offenders_in(labelled), [])

    def test_the_exempt_strategy_is_still_the_shape_it_claims(self) -> None:
        """``worker_stage`` is exempt for a reason, so the reason is asserted.

        If it ever grows a real file resolve, the exemption stops describing it
        and this fails rather than silently covering a fifth copy.
        """
        source = (_STRATEGY_DIR / "worker_stage.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(
            _calls_a_shared_resolver(tree),
            "worker_stage now resolves a glob; drop it from _DIRECTORY_SHAPED",
        )
        self.assertIn(
            "iterdir", source,
            "worker_stage no longer walks a directory; re-read the exemption",
        )


if __name__ == "__main__":
    unittest.main()

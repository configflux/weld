"""No strategy may spell a repo-relative path with the platform separator.

Node *ids* are POSIX everywhere (ADR 0041). ``props.file``, ``props.dir``,
``props.declared_in`` and ``discovered_from`` were left to whichever strategy
wrote them, and roughly half wrote ``str(x.relative_to(root))`` -- which is
separator-native. On POSIX the two agree byte for byte, so the divergence is
invisible on every platform this repo's CI runs; off POSIX the same file is
``file:weld/foo`` by id and ``weld\\foo.py`` by prop, and every consumer that
joins a prop back to an id silently stops matching (bd 64r2).

The canonical construction helper already exists and is already used from this
package: :func:`weld._rel_path.rel_to_root`, which
``_incremental_hint.dirty_matched`` was moved onto by bd v552. ADR 0112 swept
the remaining ~40 sites onto it rather than minting a second helper in the
strategy layer -- giving the canonical form two definitions is the failure the
consolidation exists to stop.

This is a *structural* pin, for the same reason
``strategy_provenance_shape_test`` is: the sweep is only worth its diff if a
strategy added tomorrow cannot quietly reintroduce the native form.
:func:`weld._discover_postprocess._canonicalize_path_props` (bd 244j) would
absorb the offender at the emit boundary and nothing would ever fail, so
without this test the rule is unenforceable by construction.

Text scanning is not an option -- this repo's docstrings quote the defective
expression to explain the fix, including the paragraph above.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import weld.strategies

_STRATEGY_DIR = pathlib.Path(weld.strategies.__file__).parent


def _is_native_rel_path(node: ast.AST) -> bool:
    """True for ``str(<expr>.relative_to(<expr>))`` -- the native spelling."""
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Name) and node.func.id == "str"):
        return False
    if len(node.args) != 1:
        return False
    inner = node.args[0]
    return (
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "relative_to"
    )


def _offenders_in(source: str, filename: str = "<test>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    return [
        f"{filename}:{node.lineno}"
        for node in ast.walk(tree)
        if _is_native_rel_path(node)
    ]


class TestNoNativeSeparatorPathProps(unittest.TestCase):
    """``str(x.relative_to(root))`` must not appear in weld/strategies."""

    def test_no_strategy_builds_a_native_separator_relative_path(self) -> None:
        offenders: list[str] = []
        for path in sorted(_STRATEGY_DIR.glob("*.py")):
            offenders.extend(
                _offenders_in(path.read_text(encoding="utf-8"), path.name)
            )
        self.assertEqual(
            offenders, [],
            "these sites spell a repo-relative path with the platform "
            "separator, so off POSIX their props disagree with the POSIX node "
            "ids that address the same file. Use "
            "weld._rel_path.rel_to_root(path, root) instead: "
            + ", ".join(offenders),
        )

    def test_the_check_can_actually_see_the_defect(self) -> None:
        """A structural assertion that matches nothing is the failure mode.

        Run the detector against the exact expression the swept sites held.
        """
        self.assertEqual(
            len(_offenders_in("rel_path = str(py.relative_to(root))")), 1,
        )
        self.assertEqual(
            len(_offenders_in("yield str(p.relative_to(root.resolve())), tree")),
            1,
        )

    def test_the_check_does_not_fire_on_the_repaired_shape(self) -> None:
        self.assertEqual(
            _offenders_in(
                "rel_path = rel_to_root(py, root)\n"
                "rel = py.relative_to(root)\n"
                "posix = py.relative_to(root).as_posix()\n"
                "name = str(py)\n"
            ),
            [],
        )

    def test_the_canonical_helper_is_what_the_strategies_import(self) -> None:
        """Guard against a second definition of the canonical form.

        The whole point of routing through ``weld._rel_path`` is that the rule
        has one home. A strategy-layer ``rel_posix`` re-implementation would
        pass the check above while recreating the drift.
        """
        from weld._rel_path import rel_to_root

        redefiners: list[str] = []
        for path in sorted(_STRATEGY_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    node.name in {"rel_posix", "rel_to_root", "canonical_rel_path"}
                ):
                    redefiners.append(f"{path.name}:{node.name}")
        self.assertEqual(
            redefiners, [],
            "the canonical repo-relative form is defined in weld/_rel_path.py "
            "and nowhere else; import it: " + ", ".join(redefiners),
        )
        self.assertEqual(rel_to_root.__module__, "weld._rel_path")


if __name__ == "__main__":
    unittest.main()

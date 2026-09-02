"""No route boundary placeholder may outrank a real file node (bd iurvv).

Four strategies mint a thin ``file:`` placeholder for the boundary file they
found a route in, so their diagnostic ``exposes`` edge cannot dangle when no
tree-sitter entry is wired beside them. A placeholder that states no
``props.confidence`` is unrankable: :func:`weld._discover_node_merge.claim_supersedes`
vetoes a write only when **both** sides state a comparable confidence, so an
unrankable stub falls through to last-writer-wins and replaces the definite,
evidence-bearing node another source entry already walked.

The system-level proof that this costs a user their handler files' evidence is
``weld_route_boundary_placeholder_e2e_test``. This is the structural half, and
it exists for a different failure: a *fifth* copy of the payload. The sites are
**discovered** from ``weld/strategies`` rather than listed here, so a new route
strategy that mints its own placeholder is policed the day it lands rather than
the day someone remembers this file. Listing them would make this guard exactly
as complete as the memory of whoever last edited it -- which is how four copies
of one defect came to exist.

Every assertion is phrased in terms of the production rule, never in terms of
the literal ``"inferred"``: what has to hold is that the orchestrator refuses
the placeholder over a definite claim and accepts the definite claim over the
placeholder, in both orders. A test that asserted the string would pass on a
confidence value ``claim_supersedes`` cannot rank, which is the bug.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import unittest
from pathlib import Path

import weld.strategies
from weld._discover_node_merge import claim_supersedes
from weld.contract import CONFIDENCE_VALUES

#: The name every boundary-file placeholder builder goes by. One name across
#: express / next / axum / gin today, which is what makes the sweep below a
#: sweep rather than a list.
BUILDER_NAME = "boundary_file_node"

#: A stand-in for a real tree-sitter file node: the props that pass stamps
#: which the merge rule actually consults. Not the whole node -- ``exports``
#: and friends are the *stake*, not the input to the decision.
DEFINITE_FILE_NODE: dict = {
    "type": "file",
    "label": "server",
    "props": {
        "file": "api/server.ts",
        "authority": "derived",
        "confidence": "definite",
        "roles": ["implementation"],
    },
}


def _strategies_dir() -> Path:
    return Path(weld.strategies.__file__).resolve().parent


def builder_modules() -> list[str]:
    """Every ``weld.strategies`` module that *defines* a placeholder builder.

    Found by parsing each module's source rather than by importing all of
    them: the question is "where is this function defined", which the AST
    answers exactly, and a re-export would answer it wrongly.
    """
    found: list[str] = []
    for path in sorted(_strategies_dir().glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable source
            continue
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == BUILDER_NAME
            ):
                found.append(f"weld.strategies.{path.stem}")
                break
    return found


def call_builder(module_name: str) -> dict:
    """Call *module_name*'s placeholder builder with a plausible path.

    The builders differ in signature -- the shared TypeScript one is
    parameterised on the calling strategy, the axum and gin copies are not --
    so required keyword-only parameters are filled from the signature instead
    of being spelled per module here, which would put the same list this file
    refuses to keep back into it.
    """
    builder = getattr(importlib.import_module(module_name), BUILDER_NAME)
    kwargs = {
        name: "probe"
        for name, parameter in inspect.signature(builder).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
    }
    return builder("api/server.ts", **kwargs)


class BoundaryPlaceholderSitesTest(unittest.TestCase):
    """The sweep is not vacuous, and it covers the four known strategies."""

    def test_the_sweep_finds_every_known_placeholder_site(self) -> None:
        modules = set(builder_modules())
        self.assertLessEqual(
            {
                "weld.strategies._ts_route_helpers",
                "weld.strategies._express_routes_helpers",
                "weld.strategies._axum_routes_helpers",
                "weld.strategies._gin_routes_helpers",
            },
            modules,
            "a known placeholder site stopped being found; the sweep below "
            "would then police fewer sites while still passing",
        )


class BoundaryPlaceholderRankTest(unittest.TestCase):
    """Every site, against the real orchestrator rule."""

    def test_every_placeholder_states_a_known_confidence(self) -> None:
        for module_name in builder_modules():
            with self.subTest(module=module_name):
                props = call_builder(module_name).get("props") or {}
                self.assertIn(
                    props.get("confidence"), CONFIDENCE_VALUES,
                    "a placeholder stating no confidence in the contract "
                    "vocabulary is unrankable, and an unrankable claim wins "
                    "by write order",
                )

    def test_a_placeholder_never_takes_the_id_from_a_definite_node(self) -> None:
        for module_name in builder_modules():
            with self.subTest(module=module_name):
                placeholder = call_builder(module_name)
                self.assertFalse(
                    claim_supersedes(DEFINITE_FILE_NODE, placeholder),
                    "this placeholder still overwrites the canonical file "
                    "node when its source entry is declared later",
                )

    def test_a_definite_node_always_takes_the_id_from_a_placeholder(self) -> None:
        """The other order, so the outcome does not depend on entry order."""
        for module_name in builder_modules():
            with self.subTest(module=module_name):
                placeholder = call_builder(module_name)
                self.assertTrue(
                    claim_supersedes(placeholder, DEFINITE_FILE_NODE),
                )

    def test_a_placeholder_is_still_accepted_on_an_unclaimed_id(self) -> None:
        """The control: the placeholder must still be the node when it is
        the only claim, or the ``exposes`` edge it exists for dangles."""
        for module_name in builder_modules():
            with self.subTest(module=module_name):
                placeholder = call_builder(module_name)
                self.assertTrue(claim_supersedes(None, placeholder))
                self.assertEqual(placeholder.get("type"), "file")
                self.assertEqual(
                    (placeholder.get("props") or {}).get("file"),
                    "api/server.ts",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

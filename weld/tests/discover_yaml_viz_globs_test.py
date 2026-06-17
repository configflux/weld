"""Regression test: .weld/discover.yaml covers the weld.viz subpackage.

Closes the dogfood gap where queries for ``VizApi``, ``make_server``,
``_handler_for``, etc. returned empty because ``.weld/discover.yaml``
had no source entry for ``weld/viz/*.py``. Sibling subpackages
(``weld/strategies/*.py``, plus ``weld/*.py`` and ``tools/*.py``) are
each covered by the canonical Python trio -- ``python_module``,
``python_callgraph``, ``python_package`` -- and this test pins that the
``weld.viz`` subpackage has the same coverage.

The test is config-level on purpose: it parses the repo's checked-in
``.weld/discover.yaml`` and asserts the three source entries are
present. We do not invoke ``wd discover`` here -- that's slow, and the
existing discovery-integration tests already exercise the
``python_module`` / ``python_callgraph`` / ``python_package`` strategies
against fixtures. The contract this test pins is the *configuration*
contract: if someone removes the viz entries by accident, this test
fails fast and points at the dogfood-gap regression class.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld._yaml import parse_yaml  # noqa: E402

# The canonical Python source trio every Python subpackage in this repo
# must be configured with. Mirrors ``weld/*.py``, ``weld/strategies/*.py``
# and ``tools/*.py`` entries already present in ``.weld/discover.yaml``.
_VIZ_GLOB = "weld/viz/*.py"
_EXPECTED_STRATEGIES = ("python_module", "python_callgraph", "python_package")


def _load_discover_yaml() -> dict:
    """Parse the repo's checked-in ``.weld/discover.yaml``."""
    path = _REPO_ROOT / ".weld" / "discover.yaml"
    return parse_yaml(path.read_text(encoding="utf-8"))


class DiscoverYamlVizGlobsTest(unittest.TestCase):
    """Config-level regression for the weld.viz dogfood gap."""

    def test_viz_glob_has_three_source_entries(self) -> None:
        # Pins: exactly three source entries reference ``weld/viz/*.py``.
        # If a fourth entry is added later (e.g. a new strategy), this
        # number bumps; if one is removed, the dogfood gap reopens and
        # this test fails fast.
        data = _load_discover_yaml()
        sources = data.get("sources", [])
        viz_entries = [
            entry for entry in sources
            if entry.get("glob") == _VIZ_GLOB
        ]
        self.assertEqual(
            len(viz_entries), 3,
            "expected 3 source entries for weld/viz/*.py "
            "(python_module + python_callgraph + python_package); "
            f"found {len(viz_entries)}: {viz_entries!r}",
        )

    def test_viz_glob_uses_canonical_python_trio(self) -> None:
        # Pins: the three viz entries use exactly the canonical strategy
        # trio. This is the strategies set that produces symbol nodes
        # (callgraph), file nodes (module), and the package node with
        # contains edges to those files (package).
        data = _load_discover_yaml()
        sources = data.get("sources", [])
        strategies = {
            entry.get("strategy") for entry in sources
            if entry.get("glob") == _VIZ_GLOB
        }
        self.assertEqual(
            strategies,
            set(_EXPECTED_STRATEGIES),
            "weld/viz/*.py source entries must use the canonical "
            "Python trio "
            f"({sorted(_EXPECTED_STRATEGIES)}); "
            f"got {sorted(strategies)}",
        )

    def test_viz_glob_matches_strategies_subpackage_pattern(self) -> None:
        # The dogfood-gap motivation was that weld/strategies/*.py was
        # already configured this way and weld/viz/*.py was not. Pin
        # symmetry between the two subpackages so a future config
        # refactor cannot drop one without dropping the other -- both
        # subpackages must keep the same strategy coverage.
        data = _load_discover_yaml()
        sources = data.get("sources", [])

        def _strategies_for(glob: str) -> set[str]:
            return {
                entry.get("strategy") for entry in sources
                if entry.get("glob") == glob
            }

        viz_strategies = _strategies_for(_VIZ_GLOB)
        strat_strategies = _strategies_for("weld/strategies/*.py")
        self.assertEqual(
            viz_strategies,
            strat_strategies,
            "weld/viz/*.py and weld/strategies/*.py must have the "
            "same strategy set so both subpackages stay discoverable. "
            f"viz={sorted(viz_strategies)} "
            f"strategies={sorted(strat_strategies)}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

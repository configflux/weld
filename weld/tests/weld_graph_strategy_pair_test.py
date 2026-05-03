"""Tests for ``weld._graph_strategy_pair`` (ADR 0041 Layer 3, rule 3).

Covers ``strategy-pair-consistency``: declared strategy pairs must
visit the same file set on the current tree or list each divergence in
``pair_asymmetry_allowlist`` with a reason. Tests use temp directories
populated with tiny fixture trees so they exercise the prune-aware
walker the rule shares with the strategies themselves.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


class StrategyPairConsistencyTest(unittest.TestCase):
    """Paired strategies must visit the same file set or carry an
    explicit ``pair_asymmetry_allowlist`` entry with a reason."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        # Lay out a tiny tree with one ``_underscore`` file that one
        # member of the pair would skip and the other would not.
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("# a\n")
        (self.root / "pkg" / "_b.py").write_text("# b\n")
        (self.root / ".weld").mkdir()

    def _yaml(self) -> str:
        return (
            "sources:\n"
            "  - glob: pkg/*.py\n"
            "    type: file\n"
            "    strategy: alpha\n"
            "  - glob: pkg/*.py\n"
            "    type: symbol\n"
            "    strategy: beta\n"
            "    exclude: ['_*.py']\n"
            "strategy_pairs:\n"
            "  - name: alpha+beta\n"
            "    members: [alpha, beta]\n"
        )

    def test_violates_when_one_member_skips_a_file_the_other_visits(
        self,
    ) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        (self.root / ".weld" / "discover.yaml").write_text(self._yaml())

        violations = list(check_strategy_pair_consistency(self.root))
        # ``_b.py`` is visible to alpha but excluded by beta. Expect at
        # least one violation that names the file and the pair.
        self.assertTrue(violations)
        self.assertTrue(
            all(v.rule == "strategy-pair-consistency" for v in violations)
        )
        self.assertTrue(
            any("_b.py" in v.message for v in violations),
            msg=(
                "expected mention of _b.py in violation messages: "
                f"{[v.message for v in violations]}"
            ),
        )
        self.assertTrue(
            any("alpha+beta" in v.message for v in violations),
            msg="expected violation to name the pair 'alpha+beta'",
        )

    def test_passes_when_allowlist_covers_the_difference(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        yaml = self._yaml() + (
            "pair_asymmetry_allowlist:\n"
            "  alpha+beta:\n"
            "    - path: pkg/_b.py\n"
            "      member_skipping: beta\n"
            "      reason: 'private helper module intentionally skipped'\n"
        )
        (self.root / ".weld" / "discover.yaml").write_text(yaml)

        self.assertEqual(
            list(check_strategy_pair_consistency(self.root)), []
        )

    def test_passes_when_no_strategy_pairs_declared(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # discover.yaml without ``strategy_pairs`` -- rule has nothing
        # to check against.
        (self.root / ".weld" / "discover.yaml").write_text("sources: []\n")
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root)), []
        )

    def test_passes_when_pair_members_visit_the_same_set(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        yaml = (
            "sources:\n"
            "  - glob: pkg/*.py\n"
            "    type: file\n"
            "    strategy: alpha\n"
            "  - glob: pkg/*.py\n"
            "    type: symbol\n"
            "    strategy: beta\n"
            "strategy_pairs:\n"
            "  - name: alpha+beta\n"
            "    members: [alpha, beta]\n"
        )
        (self.root / ".weld" / "discover.yaml").write_text(yaml)
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root)), [],
        )

    def test_passes_when_no_discover_yaml_present(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # No discover.yaml at all -- rule no-ops cleanly.
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root)), []
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for ``weld._graph_strategy_pair`` (ADR 0041 Layer 3, rule 3).

Covers ``strategy-pair-consistency`` in both of its halves:

* the *declared* half -- pair members must resolve the same file set
  from ``.weld/discover.yaml`` or list each divergence in
  ``pair_asymmetry_allowlist`` with a reason;
* the *emitted* half (bd sf36) -- each member's emitted file anchors
  must stay inside the file set that member declared, so a strategy
  that ignores its own ``exclude:`` at runtime is caught even when the
  two members' config is byte-identical.

Tests use temp directories populated with tiny fixture trees so they
exercise the prune-aware walker the rule shares with the strategies
themselves.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path



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


def _node(strategy: str, anchor: str | None) -> dict:
    """Minimal graph node carrying discovery provenance."""
    props: dict = {"source_strategy": strategy}
    if anchor is not None:
        props["file"] = anchor
    return {"label": "n", "type": "symbol", "props": props}


class StrategyPairEmissionTest(unittest.TestCase):
    """A member's emitted anchors must stay inside its declared set.

    Regression coverage for bd sf36: the declared-set comparison alone
    re-derives both members' file sets from the same config, so a pair
    whose members declare identical ``glob`` + ``exclude`` compares
    clean no matter what either strategy did at runtime -- which is
    exactly how bd 3abf (python_callgraph resolving its glob with no
    excludes, ~10.4k spurious nodes) stayed green.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("# a\n")
        (self.root / "pkg" / "_b.py").write_text("# b\n")
        (self.root / ".weld").mkdir()
        # Both members declare the identical glob + exclude -- the
        # configuration the trio in this repo's discover.yaml requires,
        # and the one the declared-set half cannot distinguish.
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources:\n"
            "  - glob: pkg/*.py\n"
            "    type: file\n"
            "    strategy: alpha\n"
            "    exclude: ['_*.py']\n"
            "  - glob: pkg/*.py\n"
            "    type: symbol\n"
            "    strategy: beta\n"
            "    exclude: ['_*.py']\n"
            "strategy_pairs:\n"
            "  - name: alpha+beta\n"
            "    members: [alpha, beta]\n"
        )

    def test_flags_anchor_outside_the_members_declared_set(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        nodes = {
            "file:pkg/a": _node("alpha", "pkg/a.py"),
            "symbol:pkg.a:f": _node("beta", "pkg/a.py"),
            # beta ignored its own exclude and parsed the skipped file.
            "symbol:pkg._b:g": _node("beta", "pkg/_b.py"),
        }
        violations = list(
            check_strategy_pair_consistency(self.root, nodes)
        )
        self.assertTrue(
            violations, msg="expected the emitted over-reach to be flagged"
        )
        self.assertTrue(
            all(v.rule == "strategy-pair-consistency" for v in violations)
        )
        self.assertTrue(
            any(
                "beta" in v.message and "pkg/_b.py" in v.message
                for v in violations
            ),
            msg=(
                "expected a violation naming beta and pkg/_b.py: "
                f"{[v.message for v in violations]}"
            ),
        )

    def test_passes_when_emitted_anchors_are_a_strict_subset(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # The legitimate asymmetry: a symbol-emitting member produces
        # nothing for a file with no definitions, so its anchor set is a
        # strict subset of its partner's. That must stay clean.
        (self.root / "pkg" / "c.py").write_text("# no defs\n")
        nodes = {
            "file:pkg/a": _node("alpha", "pkg/a.py"),
            "file:pkg/c": _node("alpha", "pkg/c.py"),
            "symbol:pkg.a:f": _node("beta", "pkg/a.py"),
        }
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root, nodes)), []
        )

    def test_config_only_call_still_supported(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # Called without node provenance the rule keeps its original
        # declared-set-only behaviour.
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root)), []
        )

    def test_ignores_nodes_without_provenance_or_anchor(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        nodes = {
            "package:beta": _node("beta", None),
            "symbol:external": {"label": "x", "type": "symbol", "props": {}},
            "malformed": {"label": "x", "type": "symbol"},
        }
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root, nodes)), []
        )

    def test_skips_members_declared_without_a_glob(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # A ``path:``-declared member reads one manifest and may emit
        # nodes about entirely different files (concept_from_bd does),
        # so it carries no declared *walk* set to contain it.
        (self.root / "manifest.txt").write_text("x\n")
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources:\n"
            "  - glob: pkg/*.py\n"
            "    type: file\n"
            "    strategy: alpha\n"
            "  - path: manifest.txt\n"
            "    type: concept\n"
            "    strategy: gamma\n"
            "strategy_pairs:\n"
            "  - name: alpha+gamma\n"
            "    members: [alpha, gamma]\n"
        )
        nodes = {
            "concept:x": _node("gamma", "pkg/elsewhere.py"),
            "file:pkg/a": _node("alpha", "pkg/a.py"),
            "file:pkg/_b": _node("alpha", "pkg/_b.py"),
        }
        violations = list(
            check_strategy_pair_consistency(self.root, nodes)
        )
        # The declared half still reports alpha's files as missing from
        # gamma (gamma declares no glob at all) -- that is the
        # pre-existing rule and not what this test pins. What must not
        # appear is an emission violation for gamma's own anchor.
        self.assertEqual(
            [v for v in violations if "pkg/elsewhere.py" in v.message],
            [],
            msg=str([v.message for v in violations]),
        )

    def test_allowlist_does_not_exempt_emitted_over_reach(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # ``pair_asymmetry_allowlist`` records an intentional *skip*.
        # Emitting a node for a file the member declared it would skip
        # is the opposite direction and must not inherit the exemption.
        with open(
            self.root / ".weld" / "discover.yaml", "a", encoding="utf-8"
        ) as handle:
            handle.write(
                "pair_asymmetry_allowlist:\n"
                "  alpha+beta:\n"
                "    - path: pkg/_b.py\n"
                "      member_skipping: beta\n"
                "      reason: 'private helper intentionally skipped'\n"
            )
        nodes = {"symbol:pkg._b:g": _node("beta", "pkg/_b.py")}
        violations = list(
            check_strategy_pair_consistency(self.root, nodes)
        )
        self.assertTrue(
            any("pkg/_b.py" in v.message for v in violations),
            msg=(
                "allow-listed skips must not exempt emitted over-reach: "
                f"{[v.message for v in violations]}"
            ),
        )

    def test_windows_style_anchor_compares_against_posix_declaration(
        self,
    ) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # Most strategies build props.file as
        # ``str(path.relative_to(root))`` -- backslash-separated on
        # Windows -- while declared sets are always as_posix(). Without
        # normalisation every anchor on that platform would read as
        # out-of-declared and the rule would be a false-positive storm.
        nodes = {"file:pkg/a": _node("alpha", "pkg\\a.py")}
        self.assertEqual(
            list(check_strategy_pair_consistency(self.root, nodes)), []
        )

    def test_reports_each_offending_anchor_once(self) -> None:
        from weld._graph_closure_invariants import (
            check_strategy_pair_consistency,
        )

        # Many nodes may share one anchor (a module's symbols); the
        # violation is per file, not per node.
        nodes = {
            "symbol:pkg._b:g": _node("beta", "pkg/_b.py"),
            "symbol:pkg._b:h": _node("beta", "pkg/_b.py"),
            "symbol:pkg._b:i": _node("beta", "pkg/_b.py"),
        }
        violations = [
            v
            for v in check_strategy_pair_consistency(self.root, nodes)
            if "pkg/_b.py" in v.message
        ]
        self.assertEqual(len(violations), 1, msg=str(violations))


if __name__ == "__main__":
    unittest.main()

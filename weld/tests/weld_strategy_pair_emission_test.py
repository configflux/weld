"""End-to-end proof that the pair rule observes runtime strategy behaviour.

The unit tests in ``weld_graph_strategy_pair_test.py`` hand the rule a
hand-built node mapping. This file closes the loop the way bd 3abf
actually failed: it runs *real* discovery over a fixture tree with a
project-local strategy (ADR 0024) whose glob resolution ignores the
``exclude:`` list it was handed, then lints the graph that run produced.

The two strategies in the fixture declare byte-identical ``glob`` and
``exclude`` -- the configuration the Python trio in this repo's
``discover.yaml`` requires -- so the declared-set half of the rule
compares them clean by construction. Only the emitted half can tell the
buggy strategy from the fixed one, which is the whole point of bd sf36:
before it, a strategy could ignore its excludes and ship ~10.4k spurious
nodes with the lint reporting nothing.

The buggy resolution reproduced here is the exact 3abf shape: pass the
glob to the walker with no excludes and rely on a per-file check that
never sees directory-form patterns.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._graph_closure_invariants import check_strategy_pair_consistency
from weld.discover import discover

_DISCOVER_YAML = textwrap.dedent(
    """
    sources:
      - glob: "pkg/**/*.py"
        type: file
        strategy: fixture_anchors
        exclude:
          - "pkg/tests/**"
      - glob: "pkg/**/*.py"
        type: symbol
        strategy: fixture_symbols
        exclude:
          - "pkg/tests/**"
    strategy_pairs:
      - name: fixture
        members: [fixture_anchors, fixture_symbols]
    topology: {}
    """
).lstrip()

#: Anchor half of the pair: resolves its glob *with* the excludes, the
#: way every strategy is supposed to (bd eerc / bd 9gdq swept all 37).
_ANCHOR_STRATEGY = textwrap.dedent(
    '''
    """Fixture strategy: one file node per matched module."""

    from pathlib import Path

    from weld.glob_match import walk_glob
    from weld.strategies._helpers import StrategyResult


    def extract(root: Path, source: dict, context: dict) -> StrategyResult:
        nodes: dict[str, dict] = {}
        pattern = source.get("glob", "")
        excludes = source.get("exclude", []) or []
        matched = walk_glob(root, pattern, excludes=excludes)
        discovered_from = []
        for path in matched:
            rel = path.relative_to(root).as_posix()
            discovered_from.append(rel)
            nodes["file:" + rel] = {
                "type": "file",
                "label": path.name,
                "props": {
                    "file": rel,
                    "source_strategy": "fixture_anchors",
                    "confidence": "definite",
                },
            }
        return StrategyResult(nodes, [], discovered_from)
    '''
).lstrip()

#: Symbol half. ``{walk}`` is substituted with either the honouring or
#: the ignoring call so the only difference between the two runs is the
#: strategy's runtime treatment of its own declared excludes.
_SYMBOL_STRATEGY = textwrap.dedent(
    '''
    """Fixture strategy: one symbol node per matched module."""

    from pathlib import Path

    from weld.glob_match import walk_glob
    from weld.strategies._helpers import StrategyResult


    def extract(root: Path, source: dict, context: dict) -> StrategyResult:
        nodes: dict[str, dict] = {{}}
        pattern = source.get("glob", "")
        excludes = source.get("exclude", []) or []
        matched = {walk}
        discovered_from = []
        for path in matched:
            rel = path.relative_to(root).as_posix()
            discovered_from.append(rel)
            nodes["symbol:py:" + rel + ":main"] = {{
                "type": "symbol",
                "label": "main",
                "props": {{
                    "file": rel,
                    "source_strategy": "fixture_symbols",
                    "confidence": "definite",
                }},
            }}
        return StrategyResult(nodes, [], discovered_from)
    '''
).lstrip()

_HONOURS_EXCLUDES = "walk_glob(root, pattern, excludes=excludes)"
#: The bd 3abf regression: the walker never learns about the excludes.
_IGNORES_EXCLUDES = "walk_glob(root, pattern)"

_PROD = "pkg/prod.py"
_TEST_MATERIAL = "pkg/tests/helper_test.py"


class StrategyPairEmissionEndToEndTest(unittest.TestCase):
    """Real discovery + real lint over a strategy mutated in-test."""

    def _build_root(self, tmp: Path, walk_call: str) -> Path:
        for rel in (_PROD, _TEST_MATERIAL):
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def main():\n    return 1\n", encoding="utf-8")
        weld_dir = tmp / ".weld"
        (weld_dir / "strategies").mkdir(parents=True)
        (weld_dir / "discover.yaml").write_text(
            _DISCOVER_YAML, encoding="utf-8"
        )
        (weld_dir / "strategies" / "fixture_anchors.py").write_text(
            _ANCHOR_STRATEGY, encoding="utf-8"
        )
        (weld_dir / "strategies" / "fixture_symbols.py").write_text(
            _SYMBOL_STRATEGY.format(walk=walk_call), encoding="utf-8"
        )
        return tmp

    def _run(self, walk_call: str) -> tuple[dict, list, list]:
        """Return ``(graph, violations, declared_only_violations)``.

        The third element is the rule run without node provenance --
        i.e. exactly what it could see before bd sf36.
        """
        with TemporaryDirectory() as name:
            root = self._build_root(Path(name), walk_call)
            graph = discover(root, incremental=False, with_sqlite=False)
            violations = list(
                check_strategy_pair_consistency(root, graph["nodes"])
            )
            declared_only = list(check_strategy_pair_consistency(root))
            return graph, violations, declared_only

    def test_strategy_ignoring_its_excludes_is_flagged(self) -> None:
        graph, violations, declared_only = self._run(_IGNORES_EXCLUDES)

        # The blind spot this test exists to close: both members declare
        # the same glob + exclude, so re-deriving their file sets from
        # config compares clean however the strategies behaved.
        self.assertEqual(
            declared_only,
            [],
            "fixture no longer isolates the emitted half -- the declared "
            "half now fires on its own, so a green emitted assertion "
            f"would prove nothing: {[v.message for v in declared_only]}",
        )

        # Precondition: the mutated strategy really did leak the excluded
        # file into the graph. Without this the test could pass green on
        # a fixture that never reproduced the bug.
        leaked = [
            node_id
            for node_id, node in graph["nodes"].items()
            if (node.get("props") or {}).get("file") == _TEST_MATERIAL
            and (node.get("props") or {}).get("source_strategy")
            == "fixture_symbols"
        ]
        self.assertTrue(
            leaked,
            "fixture did not reproduce the bug: no symbol node was "
            "emitted for the excluded file",
        )

        offending = [v for v in violations if _TEST_MATERIAL in v.message]
        self.assertTrue(
            offending,
            "strategy-pair-consistency must flag a strategy that emitted "
            f"a node for its own excluded file; got: "
            f"{[v.message for v in violations]}",
        )
        self.assertTrue(
            all(v.rule == "strategy-pair-consistency" for v in offending)
        )
        self.assertTrue(
            all("fixture_symbols" in v.message for v in offending),
            f"violation must name the offending member: {offending}",
        )

    def test_strategy_honouring_its_excludes_is_clean(self) -> None:
        graph, violations, _ = self._run(_HONOURS_EXCLUDES)

        anchors = {
            (node.get("props") or {}).get("file")
            for node in graph["nodes"].values()
            if (node.get("props") or {}).get("source_strategy")
            in ("fixture_anchors", "fixture_symbols")
        }
        self.assertIn(_PROD, anchors)
        self.assertNotIn(_TEST_MATERIAL, anchors)
        self.assertEqual(
            violations, [], msg=str([v.message for v in violations])
        )


if __name__ == "__main__":
    unittest.main()

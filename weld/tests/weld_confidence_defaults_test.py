"""Tests for the strategy -> default-confidence map (ADR 0050).

Asserts the static map ships every strategy that emits edges, that the
classification matches the per-class taxonomy from ADR 0050 (tree-sitter
/ AST / build-system parsers = definite, heuristic / filename matchers =
inferred, LLM / cross-repo guesses = speculative), and that the lookup
helper safely falls back to ``"speculative"`` for unknown names.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._confidence_defaults import (  # noqa: E402
    STRATEGY_DEFAULT_CONFIDENCE,
    classify_strategy,
)
from weld.contract import CONFIDENCE_VALUES  # noqa: E402


class ConfidenceDefaultsMapTest(unittest.TestCase):
    """The static ``STRATEGY_DEFAULT_CONFIDENCE`` map shape."""

    def test_every_value_is_a_known_confidence(self) -> None:
        bad = {
            name: value for name, value in STRATEGY_DEFAULT_CONFIDENCE.items()
            if value not in CONFIDENCE_VALUES
        }
        self.assertEqual(
            bad, {},
            f"STRATEGY_DEFAULT_CONFIDENCE contains values outside "
            f"CONFIDENCE_VALUES={sorted(CONFIDENCE_VALUES)}: {bad}",
        )

    def test_every_key_is_a_nonempty_string(self) -> None:
        bad = [
            name for name in STRATEGY_DEFAULT_CONFIDENCE
            if not isinstance(name, str) or not name
        ]
        self.assertEqual(bad, [])

    def test_definite_class_is_populated(self) -> None:
        # Per ADR 0050 the definite bucket is the largest: tree-sitter /
        # AST / build-system parsers. The map must include at least the
        # canonical core: tree_sitter, bazel, fastapi, grpc_proto.
        for canonical_definite in (
            "tree_sitter", "bazel", "fastapi", "grpc_proto",
            "python_callgraph", "graph_closure",
        ):
            self.assertEqual(
                STRATEGY_DEFAULT_CONFIDENCE[canonical_definite], "definite",
                f"strategy {canonical_definite!r} must default to definite",
            )

    def test_inferred_class_is_populated(self) -> None:
        # Heuristic strategies that may over-include must default to
        # inferred, not definite. Prevent a refactor from silently
        # promoting a guess to a fact.
        for canonical_inferred in (
            "test_peer", "events_bindings", "grpc_bindings",
            "ros2_topology", "post_processing",
        ):
            self.assertEqual(
                STRATEGY_DEFAULT_CONFIDENCE[canonical_inferred], "inferred",
                f"strategy {canonical_inferred!r} must default to inferred",
            )

    def test_speculative_class_is_populated(self) -> None:
        # LLM enrichment and pure name-match cross-repo resolvers default
        # to speculative. Under-specifying these is the single most
        # damaging failure mode this ADR targets.
        for canonical_speculative in (
            "anthropic_enrichment", "openai_enrichment", "ollama_enrichment",
            "copilot_cli_enrichment", "package_import_resolver",
        ):
            self.assertEqual(
                STRATEGY_DEFAULT_CONFIDENCE[canonical_speculative],
                "speculative",
                f"strategy {canonical_speculative!r} must default to "
                f"speculative",
            )


class ClassifyStrategyTest(unittest.TestCase):
    """``classify_strategy`` lookup with safe fallback."""

    def test_known_strategy_returns_mapped_value(self) -> None:
        self.assertEqual(classify_strategy("tree_sitter"), "definite")
        self.assertEqual(classify_strategy("test_peer"), "inferred")
        self.assertEqual(
            classify_strategy("anthropic_enrichment"), "speculative",
        )

    def test_unknown_strategy_defaults_to_speculative(self) -> None:
        # Per ADR 0050: "Strategies not in the map default to speculative
        # -- forcing the issue." The fallback is intentional friction.
        self.assertEqual(classify_strategy("not_a_real_strategy"), "speculative")

    def test_empty_or_none_strategy_defaults_to_speculative(self) -> None:
        self.assertEqual(classify_strategy(""), "speculative")
        self.assertEqual(classify_strategy(None), "speculative")

    def test_strategy_with_explicit_override_takes_precedence(self) -> None:
        # The function accepts an optional ``default`` override so callers
        # that have additional context (for example a strategy type already
        # validated as deterministic) can set a different floor. The
        # override is used only when the strategy is unknown; a known
        # strategy still returns its mapped value.
        self.assertEqual(
            classify_strategy("not_a_real_strategy", default="inferred"),
            "inferred",
        )
        self.assertEqual(
            classify_strategy("tree_sitter", default="inferred"),
            "definite",  # explicit map wins over override
        )


if __name__ == "__main__":
    unittest.main()

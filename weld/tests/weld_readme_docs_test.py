"""Regression guard: root README.md documents bootstrap + prime surface.

Asserts that the committed root ``README.md`` carries literal mentions of the
v0.3.0 bootstrap opt-out flags, the copilot instruction file path, and the
federation append behavior so that future edits cannot silently remove them.

This is a substring-presence test, not a prose check -- future README
rewrites that still carry the documented surface in different phrasing will
continue to pass.
"""

from __future__ import annotations

import unittest
from pathlib import Path


def _repo_root() -> Path:
    """Resolve the repository root from this test file location."""
    return Path(__file__).resolve().parents[2]


class ReadmeBootstrapDocsTest(unittest.TestCase):
    """Root README.md must document the v0.3.0 bootstrap / prime surface."""

    def setUp(self) -> None:
        self.path = _repo_root() / "README.md"
        self.assertTrue(self.path.exists(), f"Missing root README: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")

    def test_bootstrap_opt_out_flags_documented(self) -> None:
        """The agent-first onboarding section must mention all three opt-out flags."""
        for flag in ("--no-mcp", "--no-enrich", "--cli-only"):
            self.assertIn(
                flag,
                self.text,
                f"README.md must document the `{flag}` bootstrap flag",
            )

    def test_copilot_bootstrap_mentions_instructions_file(self) -> None:
        """README must state that copilot bootstrap writes the instructions file."""
        self.assertIn(
            "weld.instructions.md",
            self.text,
            "README.md must mention weld.instructions.md (copilot instruction surface)",
        )

    def test_federation_append_behavior_documented(self) -> None:
        """README must describe bootstrap appending federation guidance when workspaces.yaml is present."""
        # Both markers must appear and together in the same paragraph context.
        self.assertIn(
            "workspaces.yaml",
            self.text,
            "README.md must reference workspaces.yaml",
        )
        # Find a region where bootstrap + workspaces.yaml co-occur.
        lowered = self.text.lower()
        wy_index = lowered.find("workspaces.yaml")
        while wy_index != -1:
            window = lowered[max(0, wy_index - 400): wy_index + 400]
            if "bootstrap" in window and (
                "federation" in window or "workspace status" in window
            ):
                return
            wy_index = lowered.find("workspaces.yaml", wy_index + 1)
        self.fail(
            "README.md must describe bootstrap appending federation guidance "
            "when workspaces.yaml is present",
        )

    def test_prime_surface_matrix_documented(self) -> None:
        """The CLI table row for ``wd prime`` must mention the surface matrix."""
        # Accept either phrasing so mild editorial tweaks don't fail the test.
        lowered = self.text.lower()
        self.assertTrue(
            "surface matrix" in lowered or "agent surface" in lowered,
            "README.md must describe the per-framework agent surface matrix "
            "printed by `wd prime`",
        )

    def test_trust_and_install_stance_documented(self) -> None:
        """Root README must document trusted discovery and source-first install."""
        self.assertIn("repositories you trust", self.text)
        self.assertIn("external_json", self.text)
        self.assertIn("source/Git-first", self.text)
        self.assertIn("Python 3.10 through 3.13", self.text)


if __name__ == "__main__":
    unittest.main()

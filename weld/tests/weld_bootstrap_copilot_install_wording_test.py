"""Bug-2: Copilot skill templates must ship the end-user install path.

The bundled Copilot skill templates land verbatim in downstream consumer
repos (no substitution). Previously they referenced the contributor workflow
``pip install -e ./weld    # from the monorepo root``, which is nonsense for
end users. They must instead point at the README-canonical install
``uv tool install configflux-weld``.

These tests pin both the static template content and the bootstrap output so
the wording cannot regress to contributor-only instructions.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.bootstrap import bootstrap

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_COPILOT_TEMPLATES = (
    "weld_skill_copilot.md",
    "weld_skill_copilot.cli.md",
)


class CopilotSkillInstallWordingTest(unittest.TestCase):
    def _read_template(self, name: str) -> str:
        path = _TEMPLATES_DIR / name
        self.assertTrue(path.is_file(), f"missing template: {path}")
        return path.read_text(encoding="utf-8")

    def test_templates_have_no_contributor_install_wording(self) -> None:
        for name in _COPILOT_TEMPLATES:
            content = self._read_template(name)
            self.assertNotIn(
                "pip install -e",
                content,
                f"{name}: contributor 'pip install -e' wording must be removed",
            )
            self.assertNotIn(
                "monorepo root",
                content,
                f"{name}: contributor 'monorepo root' wording must be removed",
            )

    def test_templates_use_readme_canonical_install(self) -> None:
        for name in _COPILOT_TEMPLATES:
            content = self._read_template(name)
            self.assertIn(
                "uv tool install configflux-weld",
                content,
                f"{name}: must use the README-canonical install command",
            )

    def test_templates_pointer_at_readme_alternatives(self) -> None:
        """Mini-spec requires a one-line 'alternatives in README' pointer."""
        for name in _COPILOT_TEMPLATES:
            content = self._read_template(name)
            lowered = content.lower()
            self.assertIn(
                "readme",
                lowered,
                f"{name}: must reference the README for install alternatives",
            )
            self.assertIn(
                "alternatives",
                lowered,
                f"{name}: must mention install alternatives pointer",
            )

    def test_bootstrapped_copilot_skill_uses_canonical_install(self) -> None:
        """End-to-end: ``wd bootstrap copilot`` output ships the new wording."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            self.assertIn("uv tool install configflux-weld", content)
            self.assertNotIn("pip install -e", content)
            self.assertNotIn("monorepo root", content)


if __name__ == "__main__":
    unittest.main()

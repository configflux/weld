"""Tests for the copilot-cli probe in :mod:`weld._doctor_optional`.

The doctor's optional-dependency section must surface copilot-cli the
same way it surfaces Python provider modules, but the probe path differs:
copilot-cli is a standalone binary on ``PATH`` (or
``WELD_COPILOT_BINARY``), not an importable module. The install hint
must point at the GitHub docs URL, never a ``pip install`` line.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from weld._doctor_optional import check_optional_deps
from weld.doctor import CheckResult


def _all_python_present(mod_name: str) -> bool:
    return True


def _no_python_present(mod_name: str) -> bool:
    return False


class CopilotPresentTest(unittest.TestCase):
    """When the binary resolves on PATH, copilot-cli appears in ``present``."""

    def test_present_summary_includes_copilot_cli(self):
        with patch(
            "weld._doctor_optional._module_available",
            side_effect=_all_python_present,
        ), patch(
            "weld._doctor_optional.shutil.which",
            return_value="/usr/local/bin/copilot",
        ):
            results = check_optional_deps(CheckResult)
        present = [
            r for r in results if "optional deps present" in r.message
        ]
        self.assertTrue(present)
        self.assertIn("copilot-cli", present[0].message)
        # No note for copilot when present.
        notes = [
            r for r in results
            if r.level == "note"
            and getattr(r, "note_id", None) == "optional-copilot-cli-missing"
        ]
        self.assertFalse(notes)


class CopilotMissingTest(unittest.TestCase):
    """When the binary is absent, doctor emits a note with the github hint."""

    def test_missing_emits_note_with_github_hint(self):
        with patch(
            "weld._doctor_optional._module_available",
            side_effect=_all_python_present,
        ), patch(
            "weld._doctor_optional.shutil.which",
            return_value=None,
        ):
            results = check_optional_deps(CheckResult)
        copilot_notes = [
            r for r in results
            if r.level == "note"
            and getattr(r, "note_id", None) == "optional-copilot-cli-missing"
        ]
        self.assertEqual(len(copilot_notes), 1)
        msg = copilot_notes[0].message
        self.assertIn("copilot-cli", msg)
        self.assertIn(
            "https://docs.github.com/en/copilot",
            msg,
            f"expected github docs URL in copilot hint, got: {msg!r}",
        )
        self.assertNotIn(
            "pip install",
            msg,
            "copilot-cli is not a Python module; hint must not say pip install",
        )

    def test_missing_summary_includes_copilot_cli(self):
        with patch(
            "weld._doctor_optional._module_available",
            side_effect=_no_python_present,
        ), patch(
            "weld._doctor_optional.shutil.which",
            return_value=None,
        ):
            results = check_optional_deps(CheckResult)
        missing_summary = [
            r for r in results
            if "optional deps missing" in r.message
        ]
        self.assertTrue(missing_summary)
        self.assertIn("copilot-cli", missing_summary[0].message)


class CopilotEnvHonoursOverrideTest(unittest.TestCase):
    """``WELD_COPILOT_BINARY`` must drive the resolution name."""

    def test_env_var_passes_to_which(self):
        seen: list[str] = []

        def fake_which(name: str) -> str | None:
            seen.append(name)
            # Pretend the override path resolves.
            return "/opt/cust/copilot-bin"

        env = {"WELD_COPILOT_BINARY": "/opt/cust/copilot-bin"}
        with patch.dict("os.environ", env, clear=False), patch(
            "weld._doctor_optional._module_available",
            side_effect=_all_python_present,
        ), patch(
            "weld._doctor_optional.shutil.which",
            side_effect=fake_which,
        ):
            results = check_optional_deps(CheckResult)
        # The probe should call shutil.which with the env override (or its
        # basename) rather than the default ``copilot``.
        self.assertTrue(seen, "shutil.which was never called for copilot")
        # Accept either the full override or its basename: doctor MAY pass
        # whatever ``CopilotCliProvider`` would. We just need it to NOT be
        # the default literal when the env override is set.
        self.assertNotEqual(
            seen[0],
            "copilot",
            "WELD_COPILOT_BINARY override was ignored",
        )
        present = [
            r for r in results if "optional deps present" in r.message
        ]
        self.assertTrue(present)
        self.assertIn("copilot-cli", present[0].message)


if __name__ == "__main__":
    unittest.main()

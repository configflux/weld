"""``wd doctor`` must judge the ``mcp`` SDK by version, not by import.

The ``mcp`` extra is pinned to ``mcp>=2`` because the stdio server drives an
API that arrived in 2.0. A bare ``import mcp`` therefore proves nothing: a
stale 1.x SDK imports fine and still cannot run the server. Doctor used to
count it as present, so the one command whose job is to tell you whether your
setup works told you it did, right up until the server refused to start.

These tests pin the three states apart and, just as importantly, pin what each
one may *not* say:

* absent -> install the extra, and never an upgrade hint;
* installed but pre-2.0 -> upgrade, and never "not installed" (that wording
  sends someone who has the SDK to install it again);
* installed and current -> present, as before.

The fourth state is a version that cannot be read at all. It is reported as
usable on purpose: doctor is advisory, the stdio server keeps its own hard
guard, and a probe that cannot see a version has not observed an old one.

Every case is simulated -- module availability and the version accessor are
both patched -- so the assertions hold on a host with mcp 1.x installed, one
with 2.x, and one with none. That is not hypothetical: this was written on a
host carrying mcp 1.27.2, and CI installs mcp>=2.

The simulated versions below carry a PEP 440 local segment. PyPI refuses to
publish those, and no local build would pick this label, so no host can report
one -- an assertion that finds a simulated version in doctor's output has
therefore proved the patch reached the code under test rather than agreeing
with the host by luck. That distinction is not academic: these cases first
simulated a plain "1.27.2" and passed only because the authoring host happened
to carry exactly that release, while the version doctor quoted back had been
the host's own all along.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from weld import _mcp_stdio
from weld._doctor_optional import check_optional_deps
from weld._mcp_sdk import (
    HANDLER_API,
    REQUIRED_SPEC,
    UPGRADE_COMMAND,
    provides_handler_api,
    version_supported,
)
from weld.doctor import CheckResult


_PRE_2_VERSION = "1.27.2+weld-test-only"
_SUPPORTED_VERSION = "2.1.0+weld-test-only"


def _optional_results(version: str | None, *, mcp_installed: bool = True) -> list:
    """Run the optional-deps check against a simulated ``mcp`` install.

    Only ``mcp`` is made available so the other probes cannot colour the
    summaries; ``copilot`` is forced off the PATH for the same reason.

    Patching the accessor on :mod:`weld._mcp_sdk` -- the module that defines
    it -- is what makes one target enough. Every reader reaches it through
    that module, so both the classification and the version quoted back in
    the warning see this simulation. Patching a name in a *consuming*
    module's namespace would cover only that module's copy, which is how the
    warning's version once slipped through and reported the host's SDK.
    """
    with patch(
        "weld._doctor_optional._module_available",
        side_effect=lambda mod: mcp_installed and mod == "mcp",
    ), patch(
        "weld._doctor_optional.shutil.which", return_value=None,
    ), patch(
        "weld._mcp_sdk.installed_version", return_value=version,
    ):
        return check_optional_deps(CheckResult)


def _messages(results: list, fragment: str) -> list[str]:
    return [r.message for r in results if fragment in r.message]


def _levels(results: list, level: str) -> list:
    return [r for r in results if r.level == level]


class PreTwoSdkTest(unittest.TestCase):
    """An SDK the stdio server cannot drive is not a satisfied dependency."""

    def setUp(self) -> None:
        self.results = _optional_results(_PRE_2_VERSION)

    def test_not_counted_as_present(self) -> None:
        # The regression: `import mcp` succeeded, so doctor said the extra was
        # satisfied while `wd mcp serve` exited 2.
        present = _messages(self.results, "optional deps present")

        self.assertFalse(
            [m for m in present if "mcp SDK" in m],
            f"a pre-2.0 SDK must not be summarised as present: {present}",
        )

    def test_warns_with_the_version_and_the_upgrade_command(self) -> None:
        warns = _levels(self.results, "warn")

        self.assertEqual(len(warns), 1, f"expected one warn, got {warns}")
        message = warns[0].message
        self.assertIn("mcp SDK", message)
        self.assertIn(
            _PRE_2_VERSION,
            message,
            "the warning must quote the simulated version; any other value "
            "means the message read the host's own SDK instead of the patch",
        )
        self.assertIn(REQUIRED_SPEC, message)
        self.assertIn(UPGRADE_COMMAND, message)
        self.assertEqual(warns[0].section, "Optional")

    def test_never_claims_the_sdk_is_missing(self) -> None:
        # Telling someone who installed the SDK to install it is the exact
        # failure the stdio server's stderr branching was split to avoid; the
        # doctor line has to hold the same line.
        for message in _messages(self.results, "mcp SDK"):
            self.assertNotIn("not installed", message)
            self.assertNotIn("configflux-weld[mcp]", message)

        missing_summary = _messages(self.results, "optional deps missing")
        self.assertTrue(missing_summary, "other deps are still missing here")
        self.assertNotIn("mcp SDK", missing_summary[0])

        self.assertFalse(
            [
                r for r in self.results
                if getattr(r, "note_id", None) == "optional-mcp-missing"
            ],
            "a pre-2.0 SDK must not emit the install-the-extra note",
        )

    def test_stays_non_fatal(self) -> None:
        # Optional deps never raise doctor's exit code, and a degraded one is
        # still an optional one.
        self.assertFalse(_levels(self.results, "fail"))


class SupportedSdkTest(unittest.TestCase):
    """A current SDK reports exactly as it did before the version check."""

    def setUp(self) -> None:
        self.results = _optional_results(_SUPPORTED_VERSION)

    def test_counted_as_present(self) -> None:
        present = _messages(self.results, "optional deps present")

        self.assertTrue(present)
        self.assertIn("mcp SDK", present[0])

    def test_emits_no_warning(self) -> None:
        self.assertFalse(_levels(self.results, "warn"))


class AbsentSdkTest(unittest.TestCase):
    """Absence keeps pointing at the extra, never at an upgrade."""

    def setUp(self) -> None:
        # A stale version is deliberately visible here: metadata can outlive
        # the package. Absence must win over it, or an uninstalled SDK would
        # be reported as one needing an upgrade.
        self.results = _optional_results(_PRE_2_VERSION, mcp_installed=False)

    def test_emits_the_install_note(self) -> None:
        notes = [
            r for r in self.results
            if getattr(r, "note_id", None) == "optional-mcp-missing"
        ]

        self.assertEqual(len(notes), 1, f"expected one note, got {notes}")
        self.assertIn("not installed", notes[0].message)
        self.assertIn("configflux-weld[mcp]", notes[0].message)

    def test_emits_no_upgrade_hint(self) -> None:
        self.assertFalse(_levels(self.results, "warn"))
        self.assertFalse(_messages(self.results, UPGRADE_COMMAND))

    def test_named_in_the_missing_summary(self) -> None:
        missing_summary = _messages(self.results, "optional deps missing")

        self.assertTrue(missing_summary)
        self.assertIn("mcp SDK", missing_summary[0])


class UnreadableVersionTest(unittest.TestCase):
    """An unknown version is not evidence of an old one."""

    def test_treated_as_usable(self) -> None:
        results = _optional_results(None)

        present = _messages(results, "optional deps present")
        self.assertTrue(present)
        self.assertIn("mcp SDK", present[0])
        self.assertFalse(
            _levels(results, "warn"),
            "an unreadable version must not nag a working install to upgrade",
        )


class VersionClassifierTest(unittest.TestCase):
    """The floor is a major-version comparison, not a string match."""

    def test_classifies_versions(self) -> None:
        cases = (
            ("1.27.2", False),
            ("1.0", False),
            ("0.9.0", False),
            ("2.0.0", True),
            ("2.0.0b1", True),  # pre-releases of a supported major count
            ("10.0.0", True),   # lexical comparison would fail this one
            (None, True),
            ("", True),
            ("not-a-version", True),
        )
        for version, expected in cases:
            with self.subTest(version=version):
                self.assertEqual(version_supported(version), expected)


class SurfaceConsistencyTest(unittest.TestCase):
    """Doctor and the stdio server must tell one story about one SDK."""

    def test_both_surfaces_name_the_same_requirement(self) -> None:
        # A reader who sees the doctor line and then the server's refusal
        # must be pointed at the same version floor by both.
        doctor_warn = _levels(_optional_results(_PRE_2_VERSION), "warn")[0]

        self.assertIn(REQUIRED_SPEC, doctor_warn.message)
        self.assertIn(REQUIRED_SPEC, _mcp_stdio._UNUSABLE_HINT)

    def test_handler_probe_detects_the_registration_api(self) -> None:
        # The server's feature probe and the doctor's version floor are two
        # readings of one requirement; this pins the reading the server uses.
        class _Pre2Server:
            pass

        class _Sdk2Server:
            def add_request_handler(self) -> None:
                pass

        self.assertEqual(HANDLER_API, "add_request_handler")
        self.assertFalse(provides_handler_api(_Pre2Server))
        self.assertTrue(provides_handler_api(_Sdk2Server))


if __name__ == "__main__":
    unittest.main()

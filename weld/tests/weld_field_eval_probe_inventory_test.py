"""The guard on the field-eval probe corpus: nine probes, each accounted for.

``weld_field_eval_e2e_test`` is red on purpose -- every one of its nine probes
is an expected failure until the finding it reproduces is fixed. That makes it
the easiest file in the repo to "fix" the wrong way: delete a probe, rename it
out of the pattern, or decorate it with ``skip``, and the suite goes green
having stopped asking the question. The 0.23.1 corpus never silenced anything
either; it simply never asked, and nine defects shipped.

So the inventory is asserted rather than trusted. This file imports the probe
module and inspects it: exactly one probe per finding N1-N9, none skipped, and
every probe still expected to fail naming both the finding and the bd issue
that owns its fix. It costs no subprocess and no fixture -- importing the probe
module does not run its ``setUpModule``, so this stays a millisecond-scale
structural check.
"""

from __future__ import annotations

import re
import unittest

from weld.tests import weld_field_eval_e2e_test as probes
from weld.tests._field_eval_e2e_harness import finding_marker

_FINDINGS = [f"n{i}" for i in range(1, 10)]


class ProbeInventoryTest(unittest.TestCase):
    _PROBE_CLASSES = (probes.CrossRepoResolverProbes, probes.DiscoveryAndReadProbes)

    def _probes(self) -> dict[str, tuple[type, str]]:
        found: dict[str, tuple[type, str]] = {}
        for case in self._PROBE_CLASSES:
            for name in dir(case):
                match = re.fullmatch(r"test_(n[1-9])_\w+", name)
                if match is None:
                    continue
                self.assertNotIn(
                    match.group(1), found, f"two probes claim {match.group(1)}"
                )
                found[match.group(1)] = (case, name)
        return found

    def test_every_finding_has_exactly_one_probe(self) -> None:
        self.assertEqual(sorted(self._probes()), _FINDINGS)

    def test_no_probe_is_skipped(self) -> None:
        for finding, (case, name) in sorted(self._probes().items()):
            method = getattr(case, name)
            self.assertFalse(
                getattr(method, "__unittest_skip__", False),
                f"{finding} is skipped; a finding is fixed or it is not",
            )

    def test_every_expected_failure_names_its_finding(self) -> None:
        for finding, (case, name) in sorted(self._probes().items()):
            method = getattr(case, name)
            if not getattr(method, "__unittest_expecting_failure__", False):
                continue  # flipped by its fix task -- nothing left to record
            marker = finding_marker(case, name)
            self.assertIsNotNone(
                marker, f"{name} expects failure without naming a finding"
            )
            self.assertEqual(marker[0].lower(), finding, marker)
            self.assertIn(probes._BD, marker[1], marker)
            self.assertTrue(marker[2].strip(), f"{name} records an empty reason")

    def test_no_test_in_the_module_is_silenced(self) -> None:
        """Every test here, numbered probe or not, is skip-free and accounted for.

        A finding can be fixed in parts. N4's closure half landed -- so its
        probe is a live assertion now -- while the strategy half that still
        duplicates one symbol did not, and that half stays red under its own
        issue. Such a test claims no ``n<i>`` slot, so the inventory above
        never sees it, and it would be the easiest thing in the file to skip
        or delete. It is held to the same contract instead: not skipped, and
        if it expects failure, naming an owning issue and a reason where a
        reader meets them.
        """
        for case in self._PROBE_CLASSES:
            for name in dir(case):
                if not name.startswith("test_"):
                    continue
                method = getattr(case, name)
                self.assertFalse(
                    getattr(method, "__unittest_skip__", False),
                    f"{name} is skipped; a defect is fixed or it is not",
                )
                if not getattr(method, "__unittest_expecting_failure__", False):
                    continue
                marker = finding_marker(case, name)
                self.assertIsNotNone(
                    marker, f"{name} expects failure without naming a finding"
                )
                self.assertTrue(
                    marker[1].strip(), f"{name} names no issue that owns its fix"
                )
                self.assertTrue(marker[2].strip(), f"{name} records an empty reason")
                self.assertIn("EXPECTED FAILURE", method.__doc__ or "", name)

    def test_the_marker_reaches_the_rendered_docstring(self) -> None:
        # The reason has to be visible where a reader meets the failure -- in
        # `-v` output and in the test's own docstring -- not only in an
        # attribute this file happens to know how to read.
        for finding, (case, name) in sorted(self._probes().items()):
            method = getattr(case, name)
            if not getattr(method, "__unittest_expecting_failure__", False):
                continue
            self.assertIn("EXPECTED FAILURE", method.__doc__ or "", finding)


if __name__ == "__main__":
    unittest.main()

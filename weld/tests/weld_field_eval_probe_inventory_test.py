"""The guard on the field-eval probe corpora: every probe accounted for.

``weld_field_eval_e2e_test`` and ``weld_field_eval_v0250_e2e_test`` are red on
purpose -- their probes are expected failures until the finding each
reproduces is fixed. That makes them the easiest files in the repo to "fix"
the wrong way: delete a probe, rename it out of the pattern, or decorate it
with ``skip``, and the suite goes green having stopped asking the question. The
0.23.1 corpus never silenced anything either; it simply never asked, and nine
defects shipped.

So the inventory is asserted rather than trusted. This file imports both probe
modules and inspects them: exactly one probe per finding (N1-N9, M1-M4), none
skipped, and every probe still expected to fail naming both the finding and
the bd issue that owns its fix. It costs no subprocess and no fixture --
importing a probe module does not run its ``setUpModule`` -- so this stays a
millisecond-scale structural check.

The contract each probe is held to is ADR 0141 D5 and docs/testing-hygiene.md
"Fixing a field finding": a finding lands as a red probe carrying its id and
its owning issue, and the fix flips the marker rather than the file going
quiet.

One parameterized suite, not two copies: a round differs only by which module
to read, which findings to expect, and which bd issues may own a fix. Round
two's nine all belong to one epic and are pinned against its id; round three's
four do not -- M2's owner is the symbol-identity issue round two deferred --
so that round declares its owners as a set and every marker must name one of
them. Copying the file per round is how the second copy comes to police less
than the first.
"""

from __future__ import annotations

import re
import unittest
from types import ModuleType
from typing import NamedTuple

from weld.tests import weld_field_eval_e2e_test as n_probes
from weld.tests import weld_field_eval_v0250_e2e_test as m_probes
from weld.tests._probe_markers import finding_marker


class Round(NamedTuple):
    """One evaluation round's probe file, and what it owes."""

    label: str
    module: ModuleType
    findings: tuple[str, ...]
    #: bd issue-id suffixes a marker in this round may name. A probe whose
    #: marker names anything else is pointing its reader at the wrong ledger.
    owners: frozenset

    def classes(self) -> tuple[type, ...]:
        """Every ``TestCase`` the round's own module defines.

        Derived rather than listed. A class named here by hand is a class that
        can be *left out* by hand -- and an unlisted class is exactly where a
        probe could be parked and quietly skipped, which is the one thing this
        file exists to prevent. Restricted to classes the module itself
        defines, so an imported helper case cannot join a round it is not in.
        """
        return tuple(
            obj
            for obj in vars(self.module).values()
            if isinstance(obj, type)
            and issubclass(obj, unittest.TestCase)
            and obj.__module__ == self.module.__name__
        )

    def pattern(self) -> re.Pattern:
        return re.compile(rf"test_({'|'.join(self.findings)})_\w+")


_ROUNDS = (
    Round(
        label="v0.24.0",
        module=n_probes,
        findings=tuple(f"n{i}" for i in range(1, 10)),
        owners=frozenset({n_probes._BD}),
    ),
    Round(
        label="v0.25.0",
        module=m_probes,
        findings=tuple(f"m{i}" for i in range(1, 5)),
        owners=frozenset(m_probes._BD_FIXES.values()),
    ),
)


class ProbeInventoryTest(unittest.TestCase):
    def _probes(self, round_: Round) -> dict[str, tuple[type, str]]:
        pattern = round_.pattern()
        found: dict[str, tuple[type, str]] = {}
        for case in round_.classes():
            for name in dir(case):
                match = pattern.fullmatch(name)
                if match is None:
                    continue
                self.assertNotIn(
                    match.group(1),
                    found,
                    f"{round_.label}: two probes claim {match.group(1)}",
                )
                found[match.group(1)] = (case, name)
        return found

    def test_every_finding_has_exactly_one_probe(self) -> None:
        for round_ in _ROUNDS:
            self.assertEqual(
                sorted(self._probes(round_)), sorted(round_.findings), round_.label
            )

    def test_no_probe_is_skipped(self) -> None:
        for round_ in _ROUNDS:
            for finding, (case, name) in sorted(self._probes(round_).items()):
                method = getattr(case, name)
                self.assertFalse(
                    getattr(method, "__unittest_skip__", False),
                    f"{finding} is skipped; a finding is fixed or it is not",
                )

    def test_every_expected_failure_names_its_finding(self) -> None:
        for round_ in _ROUNDS:
            for finding, (case, name) in sorted(self._probes(round_).items()):
                method = getattr(case, name)
                if not getattr(method, "__unittest_expecting_failure__", False):
                    continue  # flipped by its fix task -- nothing left to record
                marker = finding_marker(case, name)
                self.assertIsNotNone(
                    marker, f"{name} expects failure without naming a finding"
                )
                self.assertEqual(marker[0].lower(), finding, marker)
                self.assertTrue(
                    any(owner in marker[1] for owner in round_.owners),
                    f"{name} names {marker[1]!r}, which owns no fix in "
                    f"{round_.label}: {sorted(round_.owners)}",
                )
                self.assertTrue(marker[2].strip(), f"{name} records an empty reason")

    def test_no_test_in_either_module_is_silenced(self) -> None:
        """Every test in either file, numbered probe or not, is accounted for.

        A finding can be fixed in parts, and each part lands on its own issue.
        N4's closure half landed first and its probe became a live assertion;
        the strategy half that duplicated one symbol stayed red beside it,
        under its own issue, until ADR 0143 closed it too. Such a test claims
        no finding slot, so the inventory above never sees it, and it would be
        the easiest thing in either file to skip or delete. It is held to the
        same contract instead: not skipped, and if it expects failure, naming
        an owning issue and a reason where a reader meets them.
        """
        for round_ in _ROUNDS:
            for case in round_.classes():
                for name in dir(case):
                    if not name.startswith("test_"):
                        continue
                    self._assert_not_silenced(case, name)

    def _assert_not_silenced(self, case: type, name: str) -> None:
        method = getattr(case, name)
        self.assertFalse(
            getattr(method, "__unittest_skip__", False),
            f"{name} is skipped; a defect is fixed or it is not",
        )
        if not getattr(method, "__unittest_expecting_failure__", False):
            return
        marker = finding_marker(case, name)
        self.assertIsNotNone(marker, f"{name} expects failure without naming a finding")
        self.assertTrue(marker[1].strip(), f"{name} names no issue that owns its fix")
        self.assertTrue(marker[2].strip(), f"{name} records an empty reason")
        self.assertIn("EXPECTED FAILURE", method.__doc__ or "", name)

    def test_the_marker_reaches_the_rendered_docstring(self) -> None:
        # The reason has to be visible where a reader meets the failure -- in
        # `-v` output and in the test's own docstring -- not only in an
        # attribute this file happens to know how to read.
        for round_ in _ROUNDS:
            for finding, (case, name) in sorted(self._probes(round_).items()):
                method = getattr(case, name)
                if not getattr(method, "__unittest_expecting_failure__", False):
                    continue
                self.assertIn("EXPECTED FAILURE", method.__doc__ or "", finding)

    def test_the_round_table_is_not_vacuous(self) -> None:
        """Guard the guard: a mistyped module or class polices nothing.

        Every assertion above iterates ``_ROUNDS`` and then iterates what it
        finds. An empty class tuple, an empty finding list, or an owner set
        with nothing in it makes all of them pass without reading a probe --
        the one failure mode a table-driven guard has that a hand-written one
        does not.
        """
        self.assertEqual(len(_ROUNDS), 2, "a round was dropped from the table")
        for round_ in _ROUNDS:
            self.assertGreaterEqual(
                len(round_.classes()), 2,
                f"{round_.label}: the class scan found almost nothing -- "
                f"every assertion above would iterate an empty list",
            )
            self.assertTrue(round_.findings, f"{round_.label}: no findings")
            self.assertTrue(round_.owners, f"{round_.label}: no fix owners")
            self.assertTrue(
                all(owner.strip() for owner in round_.owners),
                f"{round_.label}: an empty bd issue is not an owner",
            )


if __name__ == "__main__":
    unittest.main()

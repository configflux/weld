"""The guard on the Node/Next.js probe corpus: every gap accounted for.

The three ``weld_node_eval_*_e2e_test`` modules are red on purpose -- one
expected failure per gap, until the fix that owns it lands. That makes them
the easiest files in the repo to "fix" the wrong way: delete a probe, rename
it out of the pattern, or decorate it with ``skip``, and the suite goes green
having stopped asking the question. Nothing about ADR 0142's eight gaps was
hidden; they simply lived on routes no test entered, and a silenced probe puts
one back there.

So the inventory is asserted rather than trusted. This file imports all three
probe modules and inspects them: exactly one probe per gap G1-G8, none
skipped, and every probe still expected to fail naming both its gap and the bd
issue that owns its fix. Importing a probe module does not run its
``setUpModule``, so this costs no subprocess and no fixture and stays a
millisecond-scale structural check.

It is a near-twin of ``weld_field_eval_probe_inventory_test`` and stays a
separate file on purpose: that one's table is *evaluation rounds*, each with
its own bundle provenance and owner set, and folding a readiness corpus into
it would make the round vocabulary mean two different things. What the two
share is the marker mechanism itself, which is why that lives in
:mod:`weld.tests._probe_markers` rather than in either harness.
"""

from __future__ import annotations

import re
import unittest
from types import ModuleType
from typing import NamedTuple

from weld.tests import weld_node_eval_init_e2e_test as init_probes
from weld.tests import weld_node_eval_polyrepo_e2e_test as polyrepo_probes
from weld.tests import weld_node_eval_resolution_e2e_test as resolution_probes
from weld.tests import weld_node_eval_symbols_e2e_test as symbols_probes
from weld.tests._probe_markers import finding_marker

#: Every module that carries a gap probe, and the gaps it is expected to
#: carry. Stated per module rather than as one flat list so a probe that
#: wandered into the wrong module -- where its fixture's bootstrap does not
#: hold -- is a failure here rather than a mystery there.
_MODULES: tuple[tuple[ModuleType, tuple[str, ...]], ...] = (
    (init_probes, ("g1",)),
    (resolution_probes, ("g2", "g3", "g7")),
    (symbols_probes, ("g4", "g5", "g6")),
    (polyrepo_probes, ("g8",)),
)

#: Every gap ADR 0142 records, and the bd issue-id suffix that owns its fix.
#: Not derived from the probe modules' own ``_BD_FIXES`` tables, and that is
#: the point: an owner set derived from the table it checks is self-consistent
#: and therefore vacuous. QA proved it -- pointing a module's ``_BD_FIXES``
#: entry at an issue that owns nothing was the one mutation an earlier version
#: of this guard did not catch, because the marker and the permitted owners
#: were the same value. This is the independent statement, and comparing the
#: two is what catches a gap dropped, or an owner drifted, on either side.
#:
#: G4 and G5 share an owner: default exports and barrels are one task because
#: they are one extraction pass.
_GAP_OWNERS: dict[str, str] = {
    "g1": "lrnx1.2",
    "g2": "lrnx1.3",
    "g3": "lrnx1.4",
    "g4": "lrnx1.5",
    "g5": "lrnx1.5",
    "g6": "lrnx1.6",
    "g7": "lrnx1.7",
    "g8": "lrnx1.8",
}

_ALL_GAPS: tuple[str, ...] = tuple(sorted(_GAP_OWNERS))


class Corpus(NamedTuple):
    """One probe module and the gaps it carries."""

    module: ModuleType
    findings: tuple[str, ...]

    @property
    def owners(self) -> frozenset:
        """The bd issue-id suffixes a marker in this module may name."""
        return frozenset(_GAP_OWNERS[gap] for gap in self.findings)

    @property
    def label(self) -> str:
        return self.module.__name__.rsplit(".", 1)[-1]

    def classes(self) -> tuple[type, ...]:
        """Every ``TestCase`` the module itself defines.

        Derived rather than listed. A class named here by hand is a class that
        can be *left out* by hand -- and an unlisted class is exactly where a
        probe could be parked and quietly skipped, which is the one thing this
        file exists to prevent.
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


_CORPORA: tuple[Corpus, ...] = tuple(
    Corpus(module, findings) for module, findings in _MODULES
)


class NodeEvalProbeInventoryTest(unittest.TestCase):
    def _probes(self, corpus: Corpus) -> dict[str, tuple[type, str]]:
        pattern = corpus.pattern()
        found: dict[str, tuple[type, str]] = {}
        for case in corpus.classes():
            for name in dir(case):
                match = pattern.fullmatch(name)
                if match is None:
                    continue
                self.assertNotIn(
                    match.group(1),
                    found,
                    f"{corpus.label}: two probes claim {match.group(1)}",
                )
                found[match.group(1)] = (case, name)
        return found

    def test_every_gap_has_exactly_one_probe(self) -> None:
        seen: list[str] = []
        for corpus in _CORPORA:
            probes = self._probes(corpus)
            self.assertEqual(
                sorted(probes), sorted(corpus.findings), corpus.label
            )
            seen.extend(probes)
        self.assertEqual(
            sorted(seen), sorted(_ALL_GAPS),
            "the corpus does not carry exactly one probe per ADR 0142 gap",
        )

    def test_no_probe_is_skipped(self) -> None:
        for corpus in _CORPORA:
            for gap, (case, name) in sorted(self._probes(corpus).items()):
                self.assertFalse(
                    getattr(getattr(case, name), "__unittest_skip__", False),
                    f"{gap} is skipped; a gap is fixed or it is not",
                )

    def test_every_expected_failure_names_its_gap_and_owner(self) -> None:
        for corpus in _CORPORA:
            for gap, (case, name) in sorted(self._probes(corpus).items()):
                method = getattr(case, name)
                if not getattr(method, "__unittest_expecting_failure__", False):
                    continue  # flipped by its fix task -- nothing left to record
                marker = finding_marker(case, name)
                self.assertIsNotNone(
                    marker, f"{name} expects failure without naming a gap"
                )
                self.assertEqual(marker[0].lower(), gap, marker)
                self.assertIn(
                    _GAP_OWNERS[gap], marker[1],
                    f"{name} names {marker[1]!r}; ADR 0142 gives {gap} to "
                    f"{_GAP_OWNERS[gap]}",
                )
                self.assertTrue(marker[2].strip(), f"{name} records an empty reason")
                self.assertIn("EXPECTED FAILURE", method.__doc__ or "", gap)

    def test_each_module_declares_the_owners_the_adr_gives_it(self) -> None:
        """A module's own ``_BD_FIXES`` table agrees with :data:`_GAP_OWNERS`.

        The probes read their marker's issue out of that per-module table, so
        without this the check above would only ever compare a value against
        itself. Two statements of the same fact, compared -- which is the only
        arrangement in which either can be wrong out loud.
        """
        for corpus in _CORPORA:
            self.assertEqual(
                dict(corpus.module._BD_FIXES),
                {gap.upper(): _GAP_OWNERS[gap] for gap in corpus.findings},
                f"{corpus.label}: its _BD_FIXES table has drifted from ADR 0142",
            )

    def test_no_test_in_any_probe_module_is_silenced(self) -> None:
        """Every test in these files, numbered probe or not, is accounted for.

        The pass-today assurance probes claim no gap slot, so the inventory
        above never sees them -- which would make them the easiest tests here
        to skip or delete, and they are the corpus's whole answer to "did a
        fix cost us coverage". They are held to the same contract instead: not
        skipped, and if one ever expects failure, naming an owning issue and a
        reason where a reader meets them.
        """
        for corpus in _CORPORA:
            for case in corpus.classes():
                for name in dir(case):
                    if name.startswith("test_"):
                        self._assert_not_silenced(case, name)

    def _assert_not_silenced(self, case: type, name: str) -> None:
        method = getattr(case, name)
        self.assertFalse(
            getattr(method, "__unittest_skip__", False),
            f"{name} is skipped; a gap is fixed or it is not",
        )
        if not getattr(method, "__unittest_expecting_failure__", False):
            return
        marker = finding_marker(case, name)
        self.assertIsNotNone(marker, f"{name} expects failure without naming a gap")
        self.assertTrue(marker[1].strip(), f"{name} names no issue that owns its fix")
        self.assertTrue(marker[2].strip(), f"{name} records an empty reason")
        self.assertIn("EXPECTED FAILURE", method.__doc__ or "", name)

    def test_every_module_carries_a_pass_today_assurance_probe(self) -> None:
        """ADR 0142 D7's other half: red probes and green ones, per module.

        The gap probes say what is broken; the assurance probes say what must
        not break while it is being fixed. A module that lost its green ones
        would keep passing this file's other checks while quietly becoming a
        bug list with no regression armour behind it.
        """
        for corpus in _CORPORA:
            numbered = {name for _case, name in self._probes(corpus).values()}
            assurance = [
                name
                for case in corpus.classes()
                for name in dir(case)
                if name.startswith("test_") and name not in numbered
            ]
            self.assertTrue(
                assurance,
                f"{corpus.label} has gap probes and no pass-today assurance "
                "probe beside them",
            )

    def test_the_corpus_table_is_not_vacuous(self) -> None:
        """Guard the guard: a mistyped module or class polices nothing.

        Every assertion above iterates ``_CORPORA`` and then iterates what it
        finds. An empty class tuple, an empty finding list, or an owner set
        with nothing in it makes all of them pass without reading a probe --
        the one failure mode a table-driven guard has that a hand-written one
        does not.
        """
        self.assertEqual(len(_CORPORA), 4, "a probe module left the table")
        self.assertEqual(len(_ALL_GAPS), 8, "ADR 0142 records eight gaps")
        for corpus in _CORPORA:
            self.assertTrue(
                corpus.classes(),
                f"{corpus.label}: the class scan found nothing -- every "
                "assertion above would iterate an empty list",
            )
            self.assertTrue(corpus.findings, f"{corpus.label}: no gaps")
            self.assertTrue(corpus.owners, f"{corpus.label}: no fix owners")
            self.assertTrue(
                all(owner.strip() for owner in corpus.owners),
                f"{corpus.label}: an empty bd issue is not an owner",
            )


if __name__ == "__main__":
    unittest.main()

"""Shared table-driven harness for strategy ``exclude:`` regression batteries.

Three waves of the same defect (bd 3abf, eerc, 9gdq) needed the same
shape of proof: build a real fixture tree, call a strategy's real
``extract()``, and ask whether anything derived from the excluded file
survived. The harness lives here so each wave's battery is only its
fixture bodies plus its case table, and so every wave shares one
definition of "leaked".

Two mixins are exported because the two glob shapes have different
contracts:

* :class:`ExcludeFormBatteryMixin` -- for strategies whose glob may
  contain ``**``. All three exclude forms (``pkg/tests``, ``tests``,
  ``pkg/tests/**``) must prune, because ``walk_glob`` prunes matching
  directories during descent.
* :class:`CaseRunnerMixin` -- the fixture/extract/scan plumbing on its
  own, for batteries that assert a narrower contract (see
  ``weld_strategy_exclude_flat_glob_test``: a single-directory glob has
  no subtree to prune, so only the ``<dir>/**`` form is reachable).

Both mixins are plain mixins, not ``TestCase`` subclasses, so unittest
does not collect them standalone -- mix into ``unittest.TestCase`` and
set ``CASES``.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Directory that every case places its droppable file under.
EXCLUDED_DIR = "pkg/tests"


@dataclass(frozen=True)
class Case:
    """One strategy's exclude contract, exercised end to end."""

    name: str
    module: Any
    glob: str
    keep_rel: str
    keep_body: str
    drop_rel: str
    drop_body: str
    #: Token proving the kept file's artifact survived. Matched
    #: case-insensitively -- C# fixtures capitalise their identifiers
    #: (``ZzkeepContext``) while their paths do not.
    keep_marker: str
    #: Tokens proving the excluded file leaked. Baseline must show at
    #: least one; every exclude form must show none.
    drop_markers: tuple[str, ...] = ("zzdrop", EXCLUDED_DIR)
    #: Extra ``source:`` keys the strategy requires (e.g. ``language``).
    extra_source: dict = field(default_factory=dict)


class CaseRunnerMixin:
    """Fixture construction and result scanning for a :class:`Case`."""

    def _build(self, case: Case) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)  # type: ignore[attr-defined]
        for rel, body in (
            (case.keep_rel, case.keep_body),
            (case.drop_rel, case.drop_body),
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        return root

    def _blob(self, case: Case, excludes: list[str] | None) -> str:
        """Everything the strategy emitted, flattened for token scanning.

        The strategies mint wholly different node types and id schemes,
        so the uniform question this asks is the contract question: did
        *anything* derived from the excluded file survive anywhere in
        the result -- nodes, edges, or ``discovered_from`` provenance?
        Lower-cased so one marker matches both a C# fixture's
        capitalised identifiers and its lower-case path.
        """
        root = self._build(case)
        source: dict = {"glob": case.glob, **case.extra_source}
        if excludes is not None:
            source["exclude"] = excludes
        result = case.module.extract(root, source, {})
        return (
            repr(result.nodes) + repr(result.edges) + repr(result.discovered_from)
        ).lower()

    def _assert_pruned(self, case: Case, excludes: list[str]) -> None:
        blob = self._blob(case, excludes)
        for marker in case.drop_markers:
            self.assertNotIn(  # type: ignore[attr-defined]
                marker.lower(), blob,
                f"{case.name}: exclude {excludes!r} leaked {marker!r}",
            )
        self.assertIn(  # type: ignore[attr-defined]
            case.keep_marker.lower(), blob,
            f"{case.name}: exclude {excludes!r} over-pruned the kept file",
        )

    def _assert_baseline_is_live(self, case: Case) -> None:
        """Control: without excludes the droppable file is really emitted.

        Without this, an inert fixture (unparsed language, unmatched
        glob) would make every pruning assertion pass vacuously.
        """
        blob = self._blob(case, None)
        self.assertTrue(  # type: ignore[attr-defined]
            any(m.lower() in blob for m in case.drop_markers),
            f"{case.name}: fixture is inert -- baseline emitted "
            f"none of {case.drop_markers!r}",
        )


class ExcludeFormBatteryMixin(CaseRunnerMixin):
    """Every exclude form must prune, for globs that can contain ``**``."""

    #: Set by the concrete TestCase.
    CASES: tuple[Case, ...] = ()

    def test_baseline_emits_from_the_excludable_subtree(self) -> None:
        """Control: with no excludes the droppable file is really emitted."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self._assert_baseline_is_live(case)
                self.assertIn(  # type: ignore[attr-defined]
                    case.keep_marker.lower(), self._blob(case, None),
                )

    def test_segmented_directory_form_prunes_subtree(self) -> None:
        """``pkg/tests`` must exclude everything under ``pkg/tests/``."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self._assert_pruned(case, [EXCLUDED_DIR])

    def test_bare_directory_name_prunes_at_any_depth(self) -> None:
        """``tests`` must exclude a ``tests/`` dir nested any depth down."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self._assert_pruned(case, ["tests"])

    def test_subtree_form_still_honoured(self) -> None:
        """The ``<dir>/**`` form must keep working (it mostly always did)."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self._assert_pruned(case, [f"{EXCLUDED_DIR}/**"])

    def test_multiple_directory_forms_compose(self) -> None:
        """Several directory-form patterns in one list all take effect."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self._assert_pruned(case, ["vendor", EXCLUDED_DIR, "build"])

    def test_no_exclude_key_is_not_treated_as_an_exclude(self) -> None:
        """A source entry with no ``exclude:`` prunes nothing."""
        for case in self.CASES:
            with self.subTest(strategy=case.name):  # type: ignore[attr-defined]
                self.assertEqual(  # type: ignore[attr-defined]
                    self._blob(case, None), self._blob(case, []),
                )

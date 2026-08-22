"""Extend the ``weld_runtime_srcs_claim_test`` shape (bd tnz3) to subpackages.

That test covers ``weld/BUILD.bazel`` top-level only, by design (see its own
docstring). This module pins the identical invariant for the three
subpackages confirmed, while investigating bd tnz3's follow-up (bd 5038-kpu1),
to carry the same hazard shape: a hand-enumerated, non-glob ``srcs`` list in a
shared ``py_library``/``py_binary``, with no automated on-disk-vs-declared
check --

* ``weld/strategies/BUILD.bazel`` (``:helpers``, ``:strategies``)
* ``weld/cross_repo/BUILD.bazel`` (``:cross_repo``)
* ``weld/bench/BUILD.bazel`` (``:bench_primitives``, ``:public_bench_lib``,
  ``:bench_lib``, ``:synthetic_large_repo``, ``:bench``)

``weld/tests/BUILD.bazel`` is NOT covered: it declares one ``py_test`` per
file rather than a shared library ``srcs`` list, and a missing declaration
there is already caught by a different, existing mechanism
(``//tools:lint_test_wiring``).

One parameterized module, not three near-duplicates: the claims / on-disk-diff
/ double-claim-pin logic below is identical across all three packages -- it
differs only by which BUILD file to read, one subpackage-glob wrinkle
(``weld/bench/adapters/`` is a real Python subpackage, unlike
``weld/bench/fixtures/``, which holds ``.py`` files only as benchmark corpus
*data* and is deliberately excluded -- see ``weld/bench/BUILD.bazel``), and
the pinned double-claim dict per package (empty for all three today). Three
copies would just triplicate the same assertions with the package name
find-and-replaced.

None of the three BUILD files use a macro call or a glob for a claiming
rule's ``srcs`` (checked by reading each in full before writing this test) --
so there is nothing here to expand or explicitly skip, unlike
``weld/tests/BUILD.bazel``'s comprehension-declared suite.

ADR 0108 is what makes this cheap: ``_bazel_loads.load_module`` resolves a
BUILD file's ``load()``ed namespace from source, so the declared list is
readable without a bazel invocation and this runs in the fast hermetic lane.
None of the three BUILD files here actually use ``load()`` today (unlike
``weld/BUILD.bazel``'s ``RUNTIME_SRCS``), but resolving it anyway costs
nothing and keeps this test correct if one of them ever extracts its inline
list to a ``.bzl`` constant the way ``weld/BUILD.bazel`` already has.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import NamedTuple

from weld.strategies._bazel_loads import resolve_build_loads
from weld.strategies._bazel_starlark import (
    module_bindings,
    parse_module,
    targets_in,
)

#: The rules that can claim a ``.py`` source in these packages.
_CLAIMING_RULES = ("py_library", "py_binary", "py_test")


class PackageSpec(NamedTuple):
    """One subpackage's BUILD file plus the invariants pinned for it."""

    #: Repo-relative package directory, e.g. ``"weld/strategies"``.
    pkg_dir: str
    #: Subdirectories that are real Python subpackages of this package (they
    #: hold an ``__init__.py``) and so are claimed with a ``<subdir>/`` prefix
    #: in the owning BUILD file, e.g. ``("adapters",)`` for ``weld/bench``.
    #: Empty for a package with no such subdirectory.
    subdirs: tuple[str, ...]
    #: Deliberate double-claims: ``.py`` name (with any ``subdirs`` prefix) to
    #: the sorted list of target names that legitimately both claim it. Empty
    #: today for all three packages -- pinned anyway so a *future* overlap is
    #: a decision someone made rather than drift nobody noticed, exactly like
    #: ``weld_runtime_srcs_claim_test``'s pin for ``weld/BUILD.bazel``.
    expected_doubles: dict[str, list[str]]
    #: "Guard the guard" floor: the real claimed-file count is well above
    #: this, so a load()/parse regression that made every claim resolve empty
    #: fails loudly here instead of making the real assertions pass
    #: vacuously.
    min_claimed: int


#: One entry per in-scope subpackage. Counts measured against the tree at
#: authoring time: strategies claims 147 files (helpers=4, strategies=143),
#: cross_repo claims 9, bench claims 18 (12 top-level + 6 under adapters/).
PACKAGE_SPECS: tuple[PackageSpec, ...] = (
    PackageSpec("weld/strategies", (), {}, min_claimed=100),
    PackageSpec("weld/cross_repo", (), {}, min_claimed=5),
    PackageSpec("weld/bench", ("adapters",), {}, min_claimed=10),
)


def _repo_root() -> Path:
    """Return the runfiles root holding these packages' staged declarations.

    Each ``<pkg>/BUILD.bazel`` and ``<pkg>/all_python_sources`` filegroup
    arrives through *data* -- the latter a glob filegroup, deliberately,
    because staging the *declared* sources instead would make this test
    enumerate the very set it is checking against and pass vacuously.
    """
    return Path(__file__).resolve().parents[2]


def _reader(root: Path):
    def read(rel_path: str) -> str | None:
        candidate = root / rel_path
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return None
    return read


def _targets_for(root: Path, pkg_dir: str) -> list[dict]:
    """Parse ``<pkg_dir>/BUILD.bazel`` into target dicts with ``load()`` resolved.

    Mirrors the order the ``bazel`` strategy uses: loads bind first, then the
    BUILD file's own module-level assignments fold over them, then targets
    are evaluated.
    """
    read = _reader(root)
    text = read(f"{pkg_dir}/BUILD.bazel")
    assert text is not None, f"{pkg_dir}/BUILD.bazel is unreadable"
    tree = parse_module(text)
    assert tree is not None, f"{pkg_dir}/BUILD.bazel did not parse"
    loaded = resolve_build_loads(tree, pkg_dir, read, {})
    env = module_bindings(tree, loaded.bindings)
    targets = targets_in(tree, _CLAIMING_RULES, env, loaded.origins)
    assert targets is not None, f"{pkg_dir}/BUILD.bazel targets did not evaluate"
    return targets


def _claims(targets: list[dict]) -> dict[str, list[str]]:
    claims: dict[str, list[str]] = {}
    for target in targets:
        for src in target["srcs"]:
            if src.endswith(".py"):
                claims.setdefault(src, []).append(target["name"])
    return claims


def _on_disk(root: Path, spec: PackageSpec) -> set[str]:
    pkg = root / spec.pkg_dir
    names = {path.name for path in pkg.glob("*.py")}
    for subdir in spec.subdirs:
        names |= {f"{subdir}/{path.name}" for path in (pkg / subdir).glob("*.py")}
    return names


class SubpackageSrcsClaimTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = _repo_root()
        cls.targets = {
            spec.pkg_dir: _targets_for(cls.root, spec.pkg_dir)
            for spec in PACKAGE_SPECS
        }

    def test_the_manifest_actually_resolved(self) -> None:
        """Guard the guard: an unresolved srcs list would pass vacuously.

        If ``load()`` resolution (or the AST evaluator itself) regressed for
        one of these packages, its targets would look empty or their ``srcs``
        would resolve to nothing, every module in that package would look
        unclaimed, and the real assertion below would fail loudly -- but a
        *future* refactor that also relaxed that assertion could turn this
        file into a no-op for that package. Pinning a healthy claimed-file
        count per package keeps that honest.
        """
        for spec in PACKAGE_SPECS:
            with self.subTest(pkg=spec.pkg_dir):
                targets = self.targets[spec.pkg_dir]
                self.assertGreaterEqual(
                    len(targets), 1, f"{spec.pkg_dir}/BUILD.bazel: no claiming target found"
                )
                claimed = len(_claims(targets))
                self.assertGreater(
                    claimed, spec.min_claimed,
                    f"{spec.pkg_dir}: only {claimed} .py files claimed across "
                    f"{[t['name'] for t in targets]} -- load() resolution broke",
                )

    def test_every_module_is_claimed_by_at_least_one_target(self) -> None:
        """The bd 73xa shape: on disk, imported at runtime, in no target."""
        for spec in PACKAGE_SPECS:
            with self.subTest(pkg=spec.pkg_dir):
                claims = _claims(self.targets[spec.pkg_dir])
                unclaimed = sorted(_on_disk(self.root, spec) - set(claims))
                self.assertEqual(
                    unclaimed, [],
                    f"{unclaimed} present on disk under {spec.pkg_dir}/ but "
                    f"claimed by no target in {spec.pkg_dir}/BUILD.bazel -- "
                    "this is the bd 73xa ModuleNotFoundError shape, which a "
                    "green `bazel test` does not catch because the module is "
                    "simply never imported. Add it to the srcs list of the "
                    "target that owns it",
                )

    def test_double_claims_stay_the_deliberate_ones(self) -> None:
        """Overlap is legal here; an *unrecorded* overlap is what to catch.

        All three packages pin an empty dict today -- adding an entry is
        fine, it just has to be a choice someone made and recorded here,
        exactly like ``weld_runtime_srcs_claim_test``'s pin for
        ``weld/BUILD.bazel``.
        """
        for spec in PACKAGE_SPECS:
            with self.subTest(pkg=spec.pkg_dir):
                claims = _claims(self.targets[spec.pkg_dir])
                actual = {
                    src: sorted(names)
                    for src, names in claims.items() if len(names) > 1
                }
                self.assertEqual(
                    actual, spec.expected_doubles,
                    f"{spec.pkg_dir}/BUILD.bazel: double-claimed .py files "
                    f"changed from the pinned set {spec.expected_doubles} to "
                    f"{actual}. If this is a deliberate new narrow target, "
                    "update PACKAGE_SPECS in this test; if not, remove the "
                    "accidental second declaration",
                )

    def test_no_target_claims_a_module_that_does_not_exist(self) -> None:
        """The other direction: a stale manifest entry for a deleted module."""
        for spec in PACKAGE_SPECS:
            with self.subTest(pkg=spec.pkg_dir):
                claims = _claims(self.targets[spec.pkg_dir])
                phantom = sorted(set(claims) - _on_disk(self.root, spec))
                self.assertEqual(
                    phantom, [],
                    f"{spec.pkg_dir}/BUILD.bazel declares {phantom}, which is "
                    f"not on disk under {spec.pkg_dir}/ -- a stale srcs entry",
                )


if __name__ == "__main__":
    unittest.main()

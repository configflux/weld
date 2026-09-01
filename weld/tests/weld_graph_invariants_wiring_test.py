"""Every module ``:graph_invariants_lib`` owns is claimed once, not copied around.

bd 5038-hdutn / ADR 0139: the shared graph invariants used to ride in the
``srcs`` of every field-eval target, on the recorded premise that they were
"test data, not a surface anything imports outside these targets". The
test-quality program falsified that -- the golden families and the purge family
need the same helpers -- and the alternative to a ``py_library`` is copying a
300-line module into a dozen more ``srcs`` lists, which is how a shared
invariant silently forks into a dozen slightly different ones.

The promotion alone is a convention. This is the mechanism: exactly one rule in
``//weld/tests`` may claim a module that library owns, and it must be the
library. A future consumer that pastes the filename into its ``srcs`` instead of
adding ``deps = [":graph_invariants_lib"]`` fails here, in the fast loop, naming
both targets -- rather than compiling a second private copy that drifts from the
first without anything noticing.

bd 5038-dgqgr widened "a module that library owns" from one named file to the
library's whole ``srcs`` list, read off the same scan. The guard shipped
policing ``_graph_invariants.py`` alone, and the library has since taken on
three more shared modules -- ``_contract_markers.py`` (bd 5038-fprr2, since
grown by bd 5038-hkb8x to the size the paragraph above argues about, and
imported by three modules rather than one) and the two golden helpers of bd
5038-ipa1e -- each of which sat in exactly the conventionally-protected state
this guard exists to end. Deriving the policed set is exact rather than merely
generous: every member of that ``srcs`` list is by construction a module the
library owns, and there is no member of it for which a second private copy would
be acceptable. The fifth is then covered the day it lands rather than the day
someone remembers this file.

Derivation brings a way to pass vacuously that a named constant did not have --
an owner renamed, deleted, or emptied derives no modules and polices nothing.
:data:`_MUST_BE_OWNED` is the floor under it; the two scan floors below are the
older ones and are unchanged.

Scope is ``srcs`` only. ``data`` is deliberately not policed: the field-eval
bundle ``sh_test`` legitimately stages fixture *files* it reads off disk rather
than imports, and folding that spelling in would flag a pattern the package
already uses correctly.

Resolution goes through weld's own Starlark reader (ADR 0108's
``resolve_build_loads`` plus ADR 0123's macro-aware ``targets_in``), not a text
scan of the BUILD files: ``tools/lint_test_wiring`` records that a BUILD-text
variant was tried once and returned a false positive on prose. Reading the
declaration structurally also resolves ``field_eval_tests.bzl``'s
``_E2E_FIXTURE`` constant to its member list, which a text scan of the ``srcs =``
line cannot see at all -- and it is what makes the derivation above free, since
the scan already yields every target's ``srcs``.

Both halves of the package are scanned, because ``targets_in`` on
``BUILD.bazel`` does *not* expand a macro call: the BUILD file yields its own
directly-declared targets, and each ``weld/tests/*.bzl`` yields the ones its
macro body declares. Missing either half would leave the guard blind to
whichever half a future copy lands in -- so both are floored below, and a parse
regression that emptied either one fails loudly instead of passing vacuously.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from weld.strategies._bazel_loads import resolve_build_loads
from weld.strategies._bazel_starlark import (
    module_bindings,
    parse_module,
    targets_in,
)

#: The package whose wiring is pinned, repo-relative.
_PKG_DIR = "weld/tests"

#: The label consumers depend on, and the owner of every module policed here.
#: Pinned by name: a rename is a decision that has to update every consumer, not
#: something a claim scan should absorb.
_OWNER = "graph_invariants_lib"

#: Modules the owner must still declare. This is *not* the policed set -- that
#: is derived from the owner's ``srcs``, so a new shared module is covered on
#: arrival. It is the floor under that derivation (bd 5038-dgqgr), naming the
#: module the guard shipped for and the one that motivated widening it.
#:
#: Two entries rather than four: the golden helpers of bd 5038-ipa1e are policed
#: by derivation like everything else, but naming them here would additionally
#: freeze their *home*, and later splitting them into their own library is a
#: legitimate decision this guard has no business blocking.
_MUST_BE_OWNED = ("_graph_invariants.py", "_contract_markers.py")

#: Rules that can claim a ``.py`` source (mirrors ``weld_subpackage_srcs_claim_test``).
_CLAIMING_RULES = ("py_library", "py_binary", "py_test")

#: "Guard the guard" floors, well below the real counts (~496 and ~259 at the
#: time of writing). A load()/parse regression that made either scan resolve
#: empty must fail here rather than make the claim assertions vacuous.
_MIN_BUILD_TARGETS = 400
_MIN_MACRO_TARGETS = 60


def _repo_root() -> Path:
    """Repo root of the runfiles tree this test executes from."""
    return Path(__file__).resolve().parents[2]


def _reader(root: Path):
    def read(rel_path: str) -> str | None:
        try:
            return (root / rel_path).read_text(encoding="utf-8")
        except OSError:
            return None

    return read


def _build_targets(root: Path) -> list[dict]:
    """Targets declared directly in ``weld/tests/BUILD.bazel``."""
    read = _reader(root)
    text = read(f"{_PKG_DIR}/BUILD.bazel")
    assert text is not None, f"{_PKG_DIR}/BUILD.bazel is unreadable"
    tree = parse_module(text)
    assert tree is not None, f"{_PKG_DIR}/BUILD.bazel did not parse"
    loaded = resolve_build_loads(tree, _PKG_DIR, read, {})
    env = module_bindings(tree, loaded.bindings)
    targets = targets_in(tree, _CLAIMING_RULES, env, loaded.origins)
    assert targets is not None, f"{_PKG_DIR}/BUILD.bazel targets did not evaluate"
    return targets


def _macro_targets(root: Path) -> dict[str, list[dict]]:
    """Targets each ``weld/tests/*.bzl`` macro body declares, keyed by filename."""
    per_file: dict[str, list[dict]] = {}
    for path in sorted((root / _PKG_DIR).glob("*.bzl")):
        tree = parse_module(path.read_text(encoding="utf-8"))
        assert tree is not None, f"{path.name} did not parse"
        env = module_bindings(tree, {})
        declared: list[dict] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef):
                declared.extend(targets_in(stmt, _CLAIMING_RULES, env) or [])
        per_file[path.name] = declared
    return per_file


def _claimants(targets: list[dict], module: str) -> list[dict]:
    return [t for t in targets if module in (t.get("srcs") or [])]


class GraphInvariantsHasOneOwnerTest(unittest.TestCase):
    """Each module in ``:graph_invariants_lib``'s srcs is claimed by it alone."""

    @classmethod
    def setUpClass(cls) -> None:
        root = _repo_root()
        cls.build_targets = _build_targets(root)
        cls.macro_targets = _macro_targets(root)
        cls.all_targets = list(cls.build_targets)
        for declared in cls.macro_targets.values():
            cls.all_targets.extend(declared)
        cls.owners = [t for t in cls.all_targets if t.get("name") == _OWNER]
        # The policed set: whatever the owner declares, never a list restated here.
        cls.shared_modules = sorted(
            {m for owner in cls.owners for m in (owner.get("srcs") or [])}
        )

    def test_scan_reaches_both_halves_of_the_package(self) -> None:
        """Neither half may resolve empty, or the claim check means nothing."""
        self.assertGreaterEqual(
            len(self.build_targets), _MIN_BUILD_TARGETS,
            "BUILD.bazel claim scan collapsed -- the load()/parse path regressed",
        )
        macro_total = sum(len(v) for v in self.macro_targets.values())
        self.assertGreaterEqual(
            macro_total, _MIN_MACRO_TARGETS,
            "weld/tests/*.bzl macro-body scan collapsed: "
            f"{ {k: len(v) for k, v in self.macro_targets.items()} }",
        )
        self.assertTrue(
            self.macro_targets.get("field_eval_tests.bzl"),
            "field_eval_tests.bzl declared no targets -- macro bodies stopped resolving",
        )

    def test_the_owner_is_a_single_library(self) -> None:
        """One ``py_library`` answers to the name (bd 5038-hdutn).

        A ``py_test`` spelled the same way would claim its own srcs exactly once
        and satisfy every count below, while leaving nothing for a consumer to
        ``deps`` on.
        """
        self.assertEqual(
            [owner["rule"] for owner in self.owners], ["py_library"],
            f"//{_PKG_DIR}:{_OWNER} must be exactly one py_library so consumers "
            "can dep on it; the scan resolved rules "
            f"{[owner['rule'] for owner in self.owners]}",
        )

    def test_the_owner_still_declares_every_named_module(self) -> None:
        """The floor under the derivation (bd 5038-dgqgr).

        The policed set is whatever the owner declares, so a module quietly
        dropped from its ``srcs`` would stop being policed with nothing failing:
        every claim case below still passes, over a shorter list.
        """
        missing = [m for m in _MUST_BE_OWNED if m not in self.shared_modules]
        self.assertEqual(
            missing, [],
            f"//{_PKG_DIR}:{_OWNER} no longer declares {missing}; it declares "
            f"{self.shared_modules}. The claim scan polices exactly this srcs "
            "list, so a module that leaves it stops being policed silently: "
            "re-add it, or move this guard to wherever the module now lives.",
        )

    def test_exactly_one_target_claims_each_shared_module(self) -> None:
        """No module the owner declares may be claimed a second time.

        bd 5038-hdutn for ``_graph_invariants.py``, bd 5038-dgqgr for the rest
        of the library's ``srcs``.
        """
        for module in self.shared_modules:
            with self.subTest(module=module):
                claimants = _claimants(self.all_targets, module)
                self.assertEqual(
                    [c["name"] for c in claimants], [_OWNER],
                    f"{module} must be claimed by //{_PKG_DIR}:{_OWNER} alone. "
                    "A second claim compiles a private copy that drifts from "
                    f"the first: depend on ':{_OWNER}' instead of copying the "
                    "file into srcs.",
                )


if __name__ == "__main__":
    unittest.main()

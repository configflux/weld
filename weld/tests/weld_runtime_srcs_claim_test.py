"""Every ``weld/*.py`` is claimed by exactly one ``//weld`` target (bd tnz3).

The miss this catches is recorded in bd 73xa: a ``bazel test`` cycle failed with
``ModuleNotFoundError`` because a newly added ``weld/*.py`` module was never
added to ``weld/runtime_srcs.bzl``. Until now the only guard was remembering to
run ``bazel query`` rather than trusting a green ``bazel test`` -- a habit, not
a gate.

The assertion is deliberately *not* "every ``weld/*.py`` is in ``RUNTIME_SRCS``".
Some modules belong to other targets on purpose (``weld/contract.py``,
``weld/_yaml.py`` are separate ``py_library``s), so that form would be wrong in
both directions.

bd tnz3 proposed the stronger "claimed by **exactly** one target". Measured
against the tree, that form is false, and not by accident: seven modules are
claimed twice on purpose. ``//weld:node_ids`` and ``//weld:rel_path`` exist so
``//weld/strategies`` can depend on a narrow slice instead of all of
``//weld:runtime``, and ``//weld:workspace`` does the same for the workspace
modules -- while the same files stay in ``RUNTIME_SRCS`` so ``//weld:runtime``
remains self-contained. Two ``py_library``s naming one source file is legal and
harmless (same path, same content, so a target depending on both stages it
once), which is why nothing was broken by it.

So the invariants pinned here are the ones that are actually true:

* **at least one** claim per module -- the bd 73xa miss, and the only direction
  that breaks a build;
* no target declares a ``.py`` that is not on disk -- a stale manifest entry;
* the set of deliberately double-claimed modules does not grow silently. Not
  because overlap is wrong, but because a *new* one should be a decision
  someone made rather than drift nobody noticed.

ADR 0108 is what makes this cheap: ``_bazel_loads.load_module`` resolves the
manifest from source, so the declared list is readable without a bazel
invocation and this runs in the fast hermetic lane.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld.strategies._bazel_loads import resolve_build_loads
from weld.strategies._bazel_starlark import (
    module_bindings,
    parse_module,
    targets_in,
)

#: The rules that can claim a ``.py`` source in this package.
_CLAIMING_RULES = ("py_library", "py_binary", "py_test")


def _repo_root() -> Path:
    """Return the runfiles root holding this package's staged declarations.

    ``weld/BUILD.bazel`` and ``weld/runtime_srcs.bzl`` arrive through *data*,
    and the on-disk ``weld/*.py`` set arrives through ``//weld:all_python_sources``
    -- a glob filegroup, deliberately, because staging the *declared* sources
    would make this test enumerate the very set it is checking against and pass
    vacuously.
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


def _weld_targets(root: Path) -> list[dict]:
    """Parse ``weld/BUILD.bazel`` into target dicts with ``load()`` resolved.

    Mirrors the order the ``bazel`` strategy uses: loads bind first, then the
    BUILD file's own module-level assignments fold over them, then targets are
    evaluated. Without that order ``srcs = RUNTIME_SRCS`` evaluates to nothing.
    """
    read = _reader(root)
    text = read("weld/BUILD.bazel")
    assert text is not None, "weld/BUILD.bazel is unreadable"
    tree = parse_module(text)
    assert tree is not None, "weld/BUILD.bazel did not parse"
    loaded = resolve_build_loads(tree, "weld", read, {})
    env = module_bindings(tree, loaded.bindings)
    targets = targets_in(tree, _CLAIMING_RULES, env, loaded.origins)
    assert targets is not None, "weld/BUILD.bazel targets did not evaluate"
    return targets


class RuntimeSrcsClaimTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = _repo_root()
        cls.targets = _weld_targets(cls.root)

    def test_the_manifest_actually_resolved(self) -> None:
        """Guard the guard: an unresolved RUNTIME_SRCS would pass vacuously.

        If ``weld/strategies/_bazel_loads.py``'s ``load()`` resolution
        regressed, every ``srcs = RUNTIME_SRCS`` would evaluate to an empty
        list, every module would look unclaimed, and the
        real assertion below would fail loudly -- but a *future* refactor that
        also relaxed that assertion could turn this file into a no-op. Pinning
        a healthy target count keeps that honest.
        """
        self.assertGreater(len(self.targets), 5)
        runtime = [t for t in self.targets if t["name"] == "runtime"]
        self.assertEqual(len(runtime), 1, "//weld:runtime not found")
        self.assertGreater(
            len(runtime[0]["srcs"]), 50,
            "RUNTIME_SRCS resolved to almost nothing -- load() resolution broke",
        )

    def _claims(self) -> dict[str, list[str]]:
        claims: dict[str, list[str]] = {}
        for target in self.targets:
            for src in target["srcs"]:
                if src.endswith(".py") and "/" not in src:
                    claims.setdefault(src, []).append(target["name"])
        return claims

    def _on_disk(self) -> set[str]:
        return {path.name for path in (self.root / "weld").glob("*.py")}

    def test_every_weld_module_is_claimed_by_at_least_one_target(self) -> None:
        """The bd 73xa shape: on disk, imported at runtime, in no target."""
        unclaimed = sorted(self._on_disk() - set(self._claims()))
        self.assertEqual(
            unclaimed, [],
            "weld/*.py present on disk but claimed by no //weld target -- this "
            "is the bd 73xa ModuleNotFoundError shape, which a green "
            "`bazel test` does not catch because the module is simply never "
            "imported. Add it to weld/runtime_srcs.bzl or to the target that "
            "owns it",
        )

    def test_double_claims_stay_the_deliberate_ones(self) -> None:
        """Overlap is legal here; an *unrecorded* overlap is what to catch.

        Each entry below is a narrow ``py_library`` that exists so another
        package can depend on a slice of ``weld/`` without pulling all of
        ``//weld:runtime``, while the file stays in ``RUNTIME_SRCS`` so runtime
        remains self-contained. Adding to this list is fine -- it just has to be
        a choice someone made.
        """
        expected = {
            "_discover_node_merge.py": ["discover_node_merge", "runtime"],
            # bd 5038-q4t3d: the shared placeholder-anchor predicate rides in
            # the micro-library beside the rule that imports it, so
            # weld/strategies/cpp_resolver.py still reaches that rule without a
            # cycle through //weld:runtime.
            "_discover_placeholder_anchor.py": [
                "discover_unresolved_symbol_purge", "runtime",
            ],
            "_discover_unresolved_symbol_purge.py": [
                "discover_unresolved_symbol_purge", "runtime",
            ],
            "_federation_endpoints.py": ["runtime", "workspace"],
            "_gitignore_scan.py": ["runtime", "workspace"],
            "_node_ids.py": ["node_ids", "runtime"],
            "_rel_path.py": ["rel_path", "runtime"],
            "workspace.py": ["runtime", "workspace"],
            "workspace_dump.py": ["runtime", "workspace"],
            "workspace_scan.py": ["runtime", "workspace"],
            "workspace_scan_filter.py": ["runtime", "workspace"],
        }
        actual = {
            src: sorted(names)
            for src, names in self._claims().items() if len(names) > 1
        }
        self.assertEqual(actual, expected)

    def test_no_target_claims_a_module_that_does_not_exist(self) -> None:
        """The other direction: a stale manifest entry for a deleted module."""
        self.assertEqual(
            sorted(set(self._claims()) - self._on_disk()), [],
            "a //weld target declares a weld/*.py that is not on disk",
        )


if __name__ == "__main__":
    unittest.main()

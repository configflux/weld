"""Live toolchain tests for angle-bracket ``<...>`` C++ includes.

Covers the resolver path added for ADR 0042 §C++ follow-up:
``resolve_cpp_include`` now consults ``STDLIB_INCLUDE_ROOTS`` so
directives like ``<vector>``, ``<string>``, ``<unordered_map>``,
``<memory>`` resolve to a real toolchain header without mocking.
Once resolved, ``classify_resolved_include`` must tag them ``stdlib``
so the visualization 'Hide stdlib' toggle is effective for unqualified
C++ callees.

The probe is intentionally best-effort: a runner without a host C++
toolchain skips the live assertions (correct behaviour, exercised by
``test_system_include_unknown_header_returns_none`` in
``weld_cpp_resolver_test`` instead). The bogus-header case lives there
too; this file focuses on positive-path coverage of the live walk.

That skip is an accepted gap, not a defect (bd yvtz): this target sits
in the ``_HERMETIC_GRAMMAR`` lane of ``weld/tests/treesitter_tests.bzl``
on the axis that lane actually checks (grammar-free, sandboxed), and the
lane's docstring records the filesystem-ambient gap separately. The two
live cases skip with :data:`_NO_HOST_STDLIB_ROOT` rather than a bare
environment fact, so the skip line itself says "accepted gap" and points
at the tests that cover the same claims hermetically.

bd yvtz also deferred hermetic coverage of the directory-walk enumeration
itself (``_enumerate_versioned_root``, ``_enumerate_glob_prefix``,
``_system_include_dirs``) as a separate, precise follow-up: bd zczi.
``CppSystemIncludeEnumerationTest`` below closes that gap by synthesizing
each of the four toolchain-layout branches in a tempdir and monkeypatching
``STDLIB_INCLUDE_ROOTS``, so it runs -- and asserts something real -- on
every host regardless of which C++ toolchain, if any, is installed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

#: Attributable skip reason for the two live-probe cases below, mirroring
#: the ADR 0069 "accepted gap" convention
#: (:func:`tier_check_grammar_gate.require_ambient_grammar`, bd
#: srzy): a bare environment fact ("no host root") reads the same whether
#: the gap is intended or wiring regressed, so the reason states which one
#: this is and names what already covers the overlapping claims without a
#: live host. There is no ADR behind this one -- STDLIB_INCLUDE_ROOTS names
#: real host toolchain paths that are deliberately not vendored, a smaller
#: decision than ADR 0069's -- so the record lives here and on this
#: target's entry in ``weld/tests/treesitter_tests.bzl`` instead.
_NO_HOST_STDLIB_ROOT = (
    "no host C++ stdlib root present on this machine; this is an "
    "accepted gap (bd yvtz), not a wiring regression -- "
    "STDLIB_INCLUDE_ROOTS names real host toolchain paths that are "
    "deliberately not vendored, so these two live-probe cases "
    "contribute coverage only on hosts that carry a C++ toolchain. "
    "The classify_resolved_include() -> 'stdlib' tag they exercise is "
    "pinned hermetically by weld_cpp_origin_test."
    "ClassifyResolvedIncludeTest.test_synthetic_stdlib_path; the "
    "unresolvable-header path is pinned by weld_cpp_resolver_test."
    "CppIncludeResolverHeaderResolutionTest."
    "test_system_include_unknown_header_returns_none."
)


def _any_stdlib_root_exists() -> bool:
    from weld.strategies._cpp_origin import STDLIB_INCLUDE_ROOTS

    for prefix in STDLIB_INCLUDE_ROOTS:
        try:
            if Path(prefix).is_dir():
                return True
        except OSError:
            continue
    return False


class CppSystemIncludeLiveTest(unittest.TestCase):
    """Live probe of ``_resolve_cpp_include`` for ``<...>`` headers."""

    CANONICAL_HEADERS: tuple[str, ...] = (
        "<vector>",
        "<string>",
        "<unordered_map>",
        "<memory>",
    )

    def test_canonical_stdlib_header_resolves_live(self) -> None:
        if not _any_stdlib_root_exists():
            self.skipTest(_NO_HOST_STDLIB_ROOT)

        from weld.strategies._cpp_origin import classify_resolved_include
        from weld.strategies.tree_sitter import _resolve_cpp_include

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            file_path = root / "src" / "main.cpp"

            resolved_any = False
            for header in self.CANONICAL_HEADERS:
                resolved = _resolve_cpp_include(
                    root=root,
                    file_path=file_path,
                    include_text=header,
                )
                if resolved is None:
                    continue
                resolved_any = True
                # Resolved path is a real file on disk and classifies
                # as stdlib via the same helper layer-2 already uses.
                self.assertTrue(resolved.is_file(), str(resolved))
                origin = classify_resolved_include(resolved, root)
                self.assertEqual(
                    origin, "stdlib",
                    f"{header} -> {resolved} should classify as stdlib",
                )
            self.assertTrue(
                resolved_any,
                f"at least one of {self.CANONICAL_HEADERS} must resolve "
                "on a host with a populated C++ stdlib root",
            )

    def test_cstdio_resolves_or_returns_none_cleanly(self) -> None:
        """The C-library wrapper ``<cstdio>`` resolves on standard
        toolchains; on hosts that ship only some of these wrappers, a
        clean ``None`` is acceptable too. The point is: no crash."""
        if not _any_stdlib_root_exists():
            self.skipTest(_NO_HOST_STDLIB_ROOT)

        from weld.strategies.tree_sitter import _resolve_cpp_include

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            resolved = _resolve_cpp_include(
                root=root,
                file_path=root / "src" / "main.cpp",
                include_text="<cstdio>",
            )
            if resolved is not None:
                self.assertTrue(resolved.is_file())


class CppSystemIncludeUnitTest(unittest.TestCase):
    """Unit-level coverage of ``resolve_system_include`` itself."""

    def test_empty_text_returns_none(self) -> None:
        from weld.strategies._cpp_system_include import resolve_system_include

        self.assertIsNone(resolve_system_include(""))

    def test_unknown_header_returns_none(self) -> None:
        from weld.strategies._cpp_system_include import resolve_system_include

        self.assertIsNone(
            resolve_system_include("wd_definitely_not_a_real_header_xyzzy"),
        )

    def test_cache_is_populated_after_call(self) -> None:
        """After at least one probe, the directory cache is populated
        for any host root that exists. The cache is keyed by the literal
        prefix string so subsequent probes never re-stat the tree.
        """
        from weld.strategies import _cpp_system_include as mod

        # Probe a known canonical header; whether it resolves or not,
        # the cache must contain entries for the prefixes we walked.
        mod.resolve_system_include("vector")
        # Cache must contain at least one entry (non-empty list value
        # indicates a populated root; empty list indicates a missing
        # root that has been observed). Either way the dict is filled.
        self.assertTrue(
            mod._SYSTEM_INCLUDE_DIR_CACHE,
            "directory cache should hold at least one prefix after probe",
        )


class CppSystemIncludeEnumerationTest(unittest.TestCase):
    """Hermetic coverage for the four toolchain-layout branches walked by
    ``_enumerate_versioned_root``, ``_enumerate_glob_prefix``, and
    ``_system_include_dirs`` (bd zczi, follow-up to bd yvtz). Each case
    synthesizes one layout under a fresh tempdir and monkeypatches
    ``STDLIB_INCLUDE_ROOTS`` so the walk only ever sees the synthetic
    tree, never the real host. This is characterization coverage: it
    pins what the code actually does -- including the lexicographic
    (not numeric) version sort -- not what an idealized implementation
    would do.
    """

    def setUp(self) -> None:
        from weld.strategies import _cpp_system_include as mod

        self._mod = mod
        # Keyed by the literal prefix string -- clear before and after
        # every case so no case observes a prior case's (or a live
        # test's) cached entries, and this class leaves the shared
        # module-level cache empty for whichever class runs next in
        # this process.
        mod._SYSTEM_INCLUDE_DIR_CACHE.clear()
        self.addCleanup(mod._SYSTEM_INCLUDE_DIR_CACHE.clear)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _dirs(self, prefix: str) -> list[Path]:
        self._mod._SYSTEM_INCLUDE_DIR_CACHE.clear()
        return self._mod._system_include_dirs(prefix)

    def _resolve(self, prefix: str, header: str) -> Path | None:
        self._mod._SYSTEM_INCLUDE_DIR_CACHE.clear()
        with mock.patch.object(self._mod, "STDLIB_INCLUDE_ROOTS", (prefix,)):
            return self._mod.resolve_system_include(header)

    def test_libcxx_versioned_root_branch(self) -> None:
        """``<match>/include/c++/<ver>`` -- multiple versions sort
        lexicographically, not numerically: "10" precedes "2". A header
        placed one level too shallow (no version dir) is never found.
        """
        child = self.root / "opt" / "llvm-14"
        (child / "include" / "c++" / "10").mkdir(parents=True)
        (child / "include" / "c++" / "2").mkdir(parents=True)
        (child / "include" / "c++" / "10" / "hdr.h").write_text("v10")
        (child / "include" / "c++" / "2" / "hdr.h").write_text("v2")
        (child / "include" / "c++" / "shallow.h").write_text("miss")

        prefix = str(self.root / "opt" / "llvm-")
        versions = [
            d.name for d in self._dirs(prefix) if d.parent.name == "c++"
        ]
        self.assertEqual(versions, ["10", "2"])

        resolved = self._resolve(prefix, "hdr.h")
        self.assertEqual(resolved, child / "include" / "c++" / "10" / "hdr.h")
        self.assertIsNone(self._resolve(prefix, "shallow.h"))

    def test_gcc_short_form_branch(self) -> None:
        """``<match>/include`` (flat, no version dir) -- root-level
        matches also sort lexicographically ("...-10" precedes
        "...-9"), and a near-miss directory name ("includes") is never
        treated as "include": inner path segments match exactly, only
        the outer toolchain root is prefix-matched.
        """
        ten = self.root / "opt" / "gccflat-10"
        nine = self.root / "opt" / "gccflat-9"
        (ten / "include").mkdir(parents=True)
        (nine / "include").mkdir(parents=True)
        (ten / "include" / "hdr.h").write_text("10")
        (nine / "include" / "hdr.h").write_text("9")
        (ten / "includes").mkdir()
        (ten / "includes" / "miss.h").write_text("x")

        prefix = str(self.root / "opt" / "gccflat-")
        resolved = self._resolve(prefix, "hdr.h")
        self.assertEqual(resolved, ten / "include" / "hdr.h")
        self.assertIsNone(self._resolve(prefix, "miss.h"))

    def test_gcc_multiarch_triple_branch(self) -> None:
        """``<match>/<triple>/include`` -- exactly one triple level below
        the matched root, sorted alphabetically across triples. A real
        Debian/Ubuntu-style extra version level between the triple and
        include (``<triple>/<ver>/include``) is one level too deep and
        is never found by this branch.
        """
        child = self.root / "opt" / "gcctriple-11"
        (child / "aarch64-linux-gnu" / "include").mkdir(parents=True)
        (child / "x86_64-linux-gnu" / "include").mkdir(parents=True)
        (child / "aarch64-linux-gnu" / "include" / "hdr.h").write_text("a")
        (child / "x86_64-linux-gnu" / "include" / "hdr.h").write_text("x")
        deep = child / "x86_64-linux-gnu" / "13" / "include"
        deep.mkdir(parents=True)
        (deep / "deep.h").write_text("deep")

        prefix = str(self.root / "opt" / "gcctriple-")
        resolved = self._resolve(prefix, "hdr.h")
        self.assertEqual(
            resolved, child / "aarch64-linux-gnu" / "include" / "hdr.h",
        )
        self.assertIsNone(self._resolve(prefix, "deep.h"))

    def test_clang_builtins_branch(self) -> None:
        """``lib/clang/<ver>/include`` -- versions sort lexicographically
        ("18" precedes "9", a real span for LLVM's own version numbers),
        and a header missing the version level between "clang" and
        "include" is never found.
        """
        child = self.root / "opt" / "clangbuiltins-18"
        (child / "lib" / "clang" / "18" / "include").mkdir(parents=True)
        (child / "lib" / "clang" / "9" / "include").mkdir(parents=True)
        (child / "lib" / "clang" / "18" / "include" / "hdr.h").write_text(
            "18",
        )
        (child / "lib" / "clang" / "9" / "include" / "hdr.h").write_text("9")
        (child / "lib" / "clang" / "include").mkdir()
        (child / "lib" / "clang" / "include" / "miss.h").write_text("x")

        prefix = str(self.root / "opt" / "clangbuiltins-")
        resolved = self._resolve(prefix, "hdr.h")
        self.assertEqual(
            resolved, child / "lib" / "clang" / "18" / "include" / "hdr.h",
        )
        self.assertIsNone(self._resolve(prefix, "miss.h"))

    def test_gcc_debian_multiarch_literal_dir_branch(self) -> None:
        """Debian/Ubuntu/Fedora's real ``/usr/lib/gcc``: a literal
        existing directory (unlike ``/usr/lib/llvm-``, which has no
        literal-dir sibling and always reaches ``_enumerate_glob_prefix``
        instead), so ``_system_include_dirs`` dispatches it through
        ``_enumerate_versioned_root`` -- which returns only the triple
        dir itself, one level short of any header. Headers live two
        levels deeper, under ``<triple>/<version>/include`` -- GCC's own
        private headers (``stddef.h``, ``immintrin.h``, ...), confirmed
        against the real host tree in bd hg47; NOT the
        ``include/c++/<version>`` shape originally hypothesized there
        (that directory does not exist on Debian -- the real C++ stdlib
        headers live entirely under the separate ``/usr/include/c++/``
        root). Pins the fix: the literal-dir branch now also walks that
        extra level, sorting versions lexicographically like every other
        branch in this file ("13" precedes "9"), while a stray
        non-directory entry where a version dir would go (mirrors real
        ``.o``/``.so`` content sitting directly in the triple dir) is
        never mistaken for one.
        """
        gcc_root = self.root / "usr" / "lib" / "gcc"
        triple_dir = gcc_root / "x86_64-linux-gnu"
        (triple_dir / "13" / "include").mkdir(parents=True)
        (triple_dir / "9" / "include").mkdir(parents=True)
        (triple_dir / "13" / "include" / "stddef.h").write_text("13")
        (triple_dir / "9" / "include" / "stddef.h").write_text("9")
        (triple_dir / "14").write_text("not-a-version-dir")

        prefix = str(gcc_root) + "/"
        dirs = self._dirs(prefix)
        self.assertIn(triple_dir / "13" / "include", dirs)
        self.assertIn(triple_dir / "9" / "include", dirs)
        # The triple dir itself is still returned too --
        # _enumerate_versioned_root's existing contribution is additive,
        # not replaced.
        self.assertIn(triple_dir, dirs)
        # Exactly these three -- the "14" file must not smuggle in a
        # fourth, phantom entry (e.g. a dropped is_dir() guard treating
        # it as a version dir would try triple_dir/"14"/"include", which
        # does not exist, but a looser guard could still miscount).
        self.assertEqual(
            len(dirs), 3, f"unexpected extra/missing entries in {dirs}",
        )

        resolved = self._resolve(prefix, "stddef.h")
        self.assertEqual(
            resolved,
            triple_dir / "13" / "include" / "stddef.h",
            'lexicographic sort must pick "13" before "9", matching '
            "every other versioned branch in this file",
        )

    def test_literal_directory_prefix_bypasses_glob_matching(self) -> None:
        """A prefix whose stripped path already exists as a directory
        (mirrors the real ``/usr/include/c++/``) is treated by
        ``_system_include_dirs`` itself as a direct versioned root --
        it never reaches ``_enumerate_glob_prefix``'s sibling-matching
        at all, even when a sibling would otherwise match the same
        prefix name.
        """
        base = self.root / "usrlike" / "include" / "c++"
        (base / "13").mkdir(parents=True)
        (base / "13" / "hdr.h").write_text("13")
        # Would match via glob-prefix matching on the parent (base_name
        # "c++" is a startswith-prefix of "c++-decoy") if the dispatch
        # ever fell through to it -- it must not be picked up here.
        decoy = self.root / "usrlike" / "include" / "c++-decoy"
        (decoy / "include").mkdir(parents=True)
        (decoy / "include" / "decoy_only.h").write_text("decoy")

        prefix = str(base) + "/"
        resolved = self._resolve(prefix, "hdr.h")
        self.assertEqual(resolved, base / "13" / "hdr.h")
        self.assertIsNone(self._resolve(prefix, "decoy_only.h"))

    def test_multiarch_cxx_include_branch(self) -> None:
        """Debian's arch-specific libstdc++ split -- /usr/include/
        <triple>/c++/<ver>/ (bd d3et). A c++ dir with an empty
        version dir must not contribute."""
        inc = self.root / "usr" / "include"
        populated = inc / "x86_64-linux-gnu" / "c++" / "13"
        (populated / "bits").mkdir(parents=True)
        (populated / "bits" / "c++config.h").write_text("13")
        empty = inc / "x86_64-linux-gnu" / "c++" / "9"
        empty.mkdir(parents=True)
        prefix = str(inc) + "/"
        dirs = self._dirs(prefix)
        self.assertIn(populated, dirs)
        self.assertNotIn(empty, dirs)
        resolved = self._resolve(prefix, "bits/c++config.h")
        self.assertEqual(resolved, populated / "bits" / "c++config.h")
        self.assertIsNone(self._resolve(prefix, "bits/not_real.h"))


if __name__ == "__main__":
    unittest.main()

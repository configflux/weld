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
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


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
            self.skipTest("no host C++ stdlib root present on this machine")

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
            self.skipTest("no host C++ stdlib root present on this machine")

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


if __name__ == "__main__":
    unittest.main()

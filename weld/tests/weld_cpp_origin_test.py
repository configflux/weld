"""Unit tests for ADR 0042 C++ origin classifier helpers.

Covers the pure helpers in ``weld.strategies._cpp_origin``:

  * ``is_stdlib_include_path`` — every known stdlib root (libstdc++,
    libc++, clang builtins, gcc toolchain, Apple, Homebrew) and a few
    deliberate non-stdlib paths.
  * ``is_std_namespace_callee`` — accepts ``std::*`` and ``::std::*``
    qualifications; rejects bare names and other namespaces.
  * ``classify_resolved_include`` — project / stdlib / external paths
    using synthetic absolute paths so the test does not depend on the
    host's real ``/usr/include`` layout.
  * ``classify_layer2_origin`` — combines callee namespace with
    header-path classification; the ``std::`` callee branch must beat
    the path branch.
  * ``upgrade_origin`` — never silently downgrades a definite tag.

End-to-end resolver tests live in
``weld_cpp_origin_integration_test.py`` so each file stays under the
400-line cap.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._cpp_origin import (  # noqa: E402
    STDLIB_INCLUDE_ROOTS,
    classify_layer2_origin,
    classify_resolved_include,
    is_std_namespace_callee,
    is_stdlib_include_path,
    upgrade_origin,
)


class IsStdlibIncludePathTest(unittest.TestCase):
    """``is_stdlib_include_path`` covers each known stdlib root."""

    def test_libstdcpp_under_usr_include(self) -> None:
        self.assertTrue(
            is_stdlib_include_path("/usr/include/c++/13/vector"),
        )

    def test_clang_builtins(self) -> None:
        self.assertTrue(
            is_stdlib_include_path("/usr/include/clang/17/include/stddef.h"),
        )

    def test_llvm_toolchain_root(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/usr/lib/llvm-17/include/c++/v1/string",
            ),
        )

    def test_gcc_toolchain_root(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h",
            ),
        )

    def test_apple_command_line_tools(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/Library/Developer/CommandLineTools/usr/include/c++/v1/vector",
            ),
        )

    def test_local_clang(self) -> None:
        self.assertTrue(
            is_stdlib_include_path("/usr/local/include/c++/13/string"),
        )

    def test_external_boost_is_not_stdlib(self) -> None:
        self.assertFalse(
            is_stdlib_include_path("/usr/include/boost/asio.hpp"),
        )

    def test_project_path_is_not_stdlib(self) -> None:
        self.assertFalse(
            is_stdlib_include_path("/workspace/project/include/foo.h"),
        )

    def test_empty_string(self) -> None:
        self.assertFalse(is_stdlib_include_path(""))

    def test_taxonomy_distinct(self) -> None:
        """Sanity: stdlib root tuple has no duplicates."""
        self.assertEqual(
            len(set(STDLIB_INCLUDE_ROOTS)), len(STDLIB_INCLUDE_ROOTS),
        )

    # --- Exotic toolchain layouts (ADR 0042 follow-up) ---
    #
    # These cover prefix families that contain a variable segment
    # (a Nix store hash, a conda env name, an /opt/<tool>/ tool name,
    # or a Bazel hermetic repo name) so the literal STDLIB_INCLUDE_ROOTS
    # tuple cannot match them; they are recognised by a pattern helper
    # anchored on the ``/include/c++/`` (or ``/lib/clang/``) marker.

    def test_nix_store_libstdcpp(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/nix/store/0abc123def456-gcc-13.2.0/include/c++/13.2.0/vector",
            ),
        )

    def test_nix_store_libcpp(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/nix/store/9zzz-llvm-17.0.6-lib/include/c++/v1/string",
            ),
        )

    def test_nix_store_clang_builtins(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/nix/store/9zzz-clang-17.0.6/lib/clang/17/include/stddef.h",
            ),
        )

    def test_conda_miniconda_env_libstdcpp(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/home/alice/miniconda3/envs/cpp-dev/include/c++/12.3.0/vector",
            ),
        )

    def test_conda_anaconda_env_libstdcpp(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/opt/anaconda3/envs/devel/include/c++/12.3.0/vector",
            ),
        )

    def test_conda_base_env_libstdcpp(self) -> None:
        # Base conda install (no nested /envs/) still ships a c++ root.
        self.assertTrue(
            is_stdlib_include_path(
                "/home/alice/miniconda3/include/c++/12.3.0/vector",
            ),
        )

    def test_opt_custom_toolchain_libstdcpp(self) -> None:
        # Generic /opt/<tool>/ custom toolchain layout.
        self.assertTrue(
            is_stdlib_include_path(
                "/opt/gcc-13/include/c++/13.2.0/vector",
            ),
        )

    def test_opt_custom_toolchain_lib_clang(self) -> None:
        self.assertTrue(
            is_stdlib_include_path(
                "/opt/llvm-17/lib/clang/17/include/stddef.h",
            ),
        )

    def test_opt_homebrew_still_classified(self) -> None:
        # Apple Silicon Homebrew clang -- already covered by literal
        # prefix; pinned here so the new pattern path does not regress
        # the existing one.
        self.assertTrue(
            is_stdlib_include_path(
                "/opt/homebrew/include/c++/v1/string",
            ),
        )

    def test_bazel_hermetic_external_libstdcpp(self) -> None:
        # Hermetic Bazel C++ toolchain stdlib under bazel-bin/external/.
        self.assertTrue(
            is_stdlib_include_path(
                "/workspace/project/bazel-bin/external/llvm_toolchain/"
                "include/c++/v1/vector",
            ),
        )

    def test_bazel_external_libstdcpp(self) -> None:
        # Plain external/ form (e.g. inside a runfiles tree).
        self.assertTrue(
            is_stdlib_include_path(
                "external/llvm_toolchain/include/c++/v1/string",
            ),
        )

    def test_bazel_bzlmod_main_external(self) -> None:
        # bzlmod runfiles-style _main/external/<repo>/ layout.
        self.assertTrue(
            is_stdlib_include_path(
                "/tmp/runfiles/_main/external/llvm_tc/include/c++/v1/vector",
            ),
        )

    def test_opt_non_cpp_path_still_external(self) -> None:
        # /opt/ alone is not enough; the /include/c++/ or /lib/clang/
        # marker must also be present, otherwise a vendored library
        # under /opt/ would be misclassified as stdlib.
        self.assertFalse(
            is_stdlib_include_path("/opt/vendor/include/widget.h"),
        )

    def test_nix_non_cpp_path_still_external(self) -> None:
        # A Nix-built non-stdlib library (e.g. boost) must remain
        # external.
        self.assertFalse(
            is_stdlib_include_path(
                "/nix/store/abcd-boost-1.83.0/include/boost/asio.hpp",
            ),
        )

    def test_external_without_cpp_marker_is_not_stdlib(self) -> None:
        # A bazel external repo that does not actually carry the
        # stdlib must not be misclassified.
        self.assertFalse(
            is_stdlib_include_path(
                "external/com_github_eigen/include/Eigen/Dense",
            ),
        )


class IsStdNamespaceCalleeTest(unittest.TestCase):
    """``is_std_namespace_callee`` accepts qualified ``std::`` callees."""

    def test_simple_std_call(self) -> None:
        self.assertTrue(is_std_namespace_callee("std::max"))

    def test_double_qualified_std_call(self) -> None:
        self.assertTrue(is_std_namespace_callee("std::vector::push_back"))

    def test_root_qualified_std(self) -> None:
        self.assertTrue(is_std_namespace_callee("::std::string"))

    def test_unqualified_name_rejected(self) -> None:
        # A bare ``max`` cannot be distinguished from a project symbol.
        self.assertFalse(is_std_namespace_callee("max"))

    def test_other_namespace_rejected(self) -> None:
        self.assertFalse(is_std_namespace_callee("boost::asio::io_context"))

    def test_empty_callee(self) -> None:
        self.assertFalse(is_std_namespace_callee(""))


class ClassifyResolvedIncludeTest(unittest.TestCase):
    """``classify_resolved_include`` picks project / stdlib / external."""

    def test_project_path_in_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "include").mkdir()
            hdr = root / "include" / "foo.h"
            hdr.write_text("// foo\n")
            self.assertEqual(
                classify_resolved_include(hdr, root), "project",
            )

    def test_synthetic_stdlib_path(self) -> None:
        # The classifier accepts a string-only path; no filesystem
        # lookup is required because we operate on the resolved string.
        synthetic = Path("/usr/include/c++/13/vector")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                classify_resolved_include(synthetic, root), "stdlib",
            )

    def test_synthetic_external_boost(self) -> None:
        synthetic = Path("/usr/include/boost/asio.hpp")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                classify_resolved_include(synthetic, root), "external",
            )

    def test_synthetic_eigen_path(self) -> None:
        synthetic = Path("/usr/include/eigen3/Eigen/Dense")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                classify_resolved_include(synthetic, root), "external",
            )

    def test_arbitrary_outside_root_is_external(self) -> None:
        synthetic = Path("/opt/vendor/include/widget.h")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                classify_resolved_include(synthetic, root), "external",
            )


class ClassifyLayer2OriginTest(unittest.TestCase):
    """``classify_layer2_origin`` combines callee + header-path rules."""

    def test_std_callee_overrides_repo_local_header(self) -> None:
        """A ``std::max`` callee resolved through any header (even a
        repo-local re-export) is classified ``stdlib`` because the
        callee namespace is the authoritative signal in ADR 0042."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "include").mkdir()
            hdr = root / "include" / "compat.h"
            hdr.write_text("// compat shim\n")
            self.assertEqual(
                classify_layer2_origin("std::max", hdr, root), "stdlib",
            )

    def test_project_callee_resolved_in_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "include").mkdir()
            hdr = root / "include" / "foo.h"
            hdr.write_text("// foo\n")
            self.assertEqual(
                classify_layer2_origin("Foo::bar", hdr, root), "project",
            )

    def test_external_via_synthetic_boost(self) -> None:
        synthetic = Path("/usr/include/boost/asio.hpp")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(
                classify_layer2_origin("io_context", synthetic, root),
                "external",
            )

    def test_stdlib_via_synthetic_system_include(self) -> None:
        synthetic = Path("/usr/include/c++/13/vector")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Even a non-``std::`` qualified callee that happens to
            # resolve to a libstdc++ header is classified ``stdlib``
            # via the path branch.
            self.assertEqual(
                classify_layer2_origin("vector", synthetic, root), "stdlib",
            )


class UpgradeOriginTest(unittest.TestCase):
    """``upgrade_origin`` never silently downgrades a definite tag."""

    def test_unresolved_to_project_upgrades(self) -> None:
        self.assertEqual(upgrade_origin("unresolved", "project"), "project")

    def test_unresolved_to_stdlib_upgrades(self) -> None:
        self.assertEqual(upgrade_origin("unresolved", "stdlib"), "stdlib")

    def test_project_to_unresolved_does_not_downgrade(self) -> None:
        self.assertEqual(upgrade_origin("project", "unresolved"), "project")

    def test_stdlib_to_unresolved_does_not_downgrade(self) -> None:
        self.assertEqual(upgrade_origin("stdlib", "unresolved"), "stdlib")

    def test_none_prior_returns_new(self) -> None:
        self.assertEqual(upgrade_origin(None, "project"), "project")

    def test_definite_replaced_by_definite(self) -> None:
        # Among definite values, the new (layer-2) claim wins because
        # layer 2 has strictly more context than layer 1.
        self.assertEqual(upgrade_origin("project", "stdlib"), "stdlib")
        self.assertEqual(upgrade_origin("external", "project"), "project")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the Go-specific origin helpers (ADR 0042 § Go).

Covers the pure helpers in :mod:`weld.strategies._go_origin`:

* The hard-coded Go standard-library package set
  (``GO_STDLIB_PACKAGES`` / :func:`is_go_stdlib`).
* The minimal ``go.mod`` parser that extracts the declared module path
  (:func:`parse_go_mod_module_path`).
* The four-way classifier that maps an import path + module path to
  one of ``project`` / ``stdlib`` / ``external`` / ``unresolved``
  (:func:`classify_go_import`).

Strategy-level integration (the call-graph dispatch wired through
``_language_origin.origin_for_callgraph_sentinel``) is asserted by
:mod:`weld.tests.weld_go_origin_integration_test`. Fixture files for
the integration test live under
``weld/tests/fixtures/go_origin_project/`` and are deliberately
distinct from the existing ``go_project`` blast-radius fixture so the
two surfaces stay independent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._go_origin import (  # noqa: E402
    GO_STDLIB_PACKAGES,
    classify_go_import,
    is_go_stdlib,
    parse_go_mod_module_path,
)


# ---------------------------------------------------------------------------
# Stdlib detection
# ---------------------------------------------------------------------------


class IsGoStdlibTest(unittest.TestCase):
    """``is_go_stdlib`` matches the hard-coded Go standard-library set."""

    def test_top_level_packages(self) -> None:
        for name in (
            "fmt",
            "os",
            "io",
            "strings",
            "strconv",
            "errors",
            "context",
            "sync",
            "time",
            "bytes",
            "log",
        ):
            self.assertTrue(
                is_go_stdlib(name),
                f"expected {name!r} to be classified Go stdlib",
            )

    def test_dotted_packages(self) -> None:
        # The Go stdlib has many sub-packages; the helper must
        # classify them by their full dotted-slash path, not just the
        # first segment, because some first segments are also valid
        # external module path prefixes.
        for name in (
            "net/http",
            "net/url",
            "encoding/json",
            "encoding/base64",
            "encoding/xml",
            "crypto/sha256",
            "path/filepath",
            "go/ast",
            "go/parser",
            "math/rand",
            "database/sql",
            "html/template",
            "text/template",
        ):
            self.assertTrue(
                is_go_stdlib(name),
                f"expected {name!r} to be classified Go stdlib",
            )

    def test_non_stdlib(self) -> None:
        for name in (
            "github.com/example/foo",
            "github.com/gin-gonic/gin",
            "go.uber.org/zap",
            "example.com/internal/handlers",
            "myproject/utils",
            "",
        ):
            self.assertFalse(
                is_go_stdlib(name),
                f"did not expect {name!r} to be Go stdlib",
            )

    def test_quoted_path_rejected(self) -> None:
        # Tree-sitter captures the import path as the literal source
        # token including the surrounding double quotes ("fmt"). The
        # helper deliberately operates on the *unquoted* path; the
        # caller is responsible for stripping quotes.
        self.assertFalse(is_go_stdlib('"fmt"'))


# ---------------------------------------------------------------------------
# go.mod parsing
# ---------------------------------------------------------------------------


class ParseGoModModulePathTest(unittest.TestCase):
    """``parse_go_mod_module_path`` extracts the ``module`` directive."""

    def test_simple(self) -> None:
        text = "module github.com/example/myapi\n\ngo 1.22\n"
        self.assertEqual(
            parse_go_mod_module_path(text),
            "github.com/example/myapi",
        )

    def test_leading_blank_lines_and_comments(self) -> None:
        text = (
            "// auto-generated\n"
            "\n"
            "module example.com/foo/bar\n"
            "\n"
            "go 1.21\n"
            "\n"
            "require (\n"
            "    github.com/example/dep v1.2.3\n"
            ")\n"
        )
        self.assertEqual(
            parse_go_mod_module_path(text),
            "example.com/foo/bar",
        )

    def test_quoted_module_path(self) -> None:
        # Go allows ``module "path"`` with a quoted string literal.
        text = 'module "go.uber.org/zap"\n\ngo 1.22\n'
        self.assertEqual(
            parse_go_mod_module_path(text),
            "go.uber.org/zap",
        )

    def test_indented_module_directive(self) -> None:
        # ``go.mod`` allows leading whitespace on directives.
        text = "  module   github.com/example/spaced  \n\ngo 1.22\n"
        self.assertEqual(
            parse_go_mod_module_path(text),
            "github.com/example/spaced",
        )

    def test_missing_module_directive(self) -> None:
        text = "go 1.22\n\nrequire github.com/example/dep v1.0.0\n"
        self.assertEqual(parse_go_mod_module_path(text), "")

    def test_empty_text(self) -> None:
        self.assertEqual(parse_go_mod_module_path(""), "")

    def test_non_module_line_with_module_keyword(self) -> None:
        # ``modulefoo`` (no whitespace) is not a ``module`` directive
        # and must not match.
        text = "modulefoo bar\nmodule github.com/example/real\n"
        self.assertEqual(
            parse_go_mod_module_path(text),
            "github.com/example/real",
        )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class ClassifyGoImportTest(unittest.TestCase):
    """``classify_go_import`` returns the ADR-0042 four-way origin."""

    MODULE = "github.com/example/myapi"

    def test_stdlib_top_level(self) -> None:
        self.assertEqual(
            classify_go_import("fmt", self.MODULE),
            "stdlib",
        )

    def test_stdlib_dotted(self) -> None:
        self.assertEqual(
            classify_go_import("net/http", self.MODULE),
            "stdlib",
        )
        self.assertEqual(
            classify_go_import("encoding/json", self.MODULE),
            "stdlib",
        )

    def test_project_under_module_path(self) -> None:
        # Anything under the declared module path is project-local.
        self.assertEqual(
            classify_go_import(
                "github.com/example/myapi/internal/handlers", self.MODULE,
            ),
            "project",
        )
        self.assertEqual(
            classify_go_import(
                "github.com/example/myapi/pkg/utils", self.MODULE,
            ),
            "project",
        )

    def test_project_exact_module_path(self) -> None:
        # An import equal to the module path itself (rare but legal
        # for single-package modules) is also project.
        self.assertEqual(
            classify_go_import(self.MODULE, self.MODULE),
            "project",
        )

    def test_external_other_module(self) -> None:
        for path in (
            "github.com/gin-gonic/gin",
            "github.com/jmoiron/sqlx",
            "go.uber.org/zap",
        ):
            self.assertEqual(
                classify_go_import(path, self.MODULE),
                "external",
                f"expected {path!r} to classify external",
            )

    def test_module_prefix_collision_not_project(self) -> None:
        # ``github.com/example/myapi-extras`` shares the literal
        # prefix ``github.com/example/myapi`` but is NOT a path
        # segment under that module — it must classify as external,
        # not project.
        self.assertEqual(
            classify_go_import(
                "github.com/example/myapi-extras", self.MODULE,
            ),
            "external",
        )
        self.assertEqual(
            classify_go_import(
                "github.com/example/myapiother", self.MODULE,
            ),
            "external",
        )

    def test_empty_import_is_unresolved(self) -> None:
        self.assertEqual(
            classify_go_import("", self.MODULE),
            "unresolved",
        )

    def test_no_module_path_falls_through(self) -> None:
        # No ``go.mod`` parsed: stdlib still classifies via the
        # static list; everything else falls through to external.
        self.assertEqual(classify_go_import("fmt", ""), "stdlib")
        self.assertEqual(classify_go_import("github.com/x/y", ""), "external")

    def test_quoted_import_path(self) -> None:
        # Tree-sitter captures the literal token including quotes.
        # The classifier must tolerate both shapes so callers do not
        # have to remember to strip.
        self.assertEqual(
            classify_go_import('"fmt"', self.MODULE),
            "stdlib",
        )
        self.assertEqual(
            classify_go_import(
                '"github.com/example/myapi/internal/handlers"',
                self.MODULE,
            ),
            "project",
        )
        self.assertEqual(
            classify_go_import(
                '"github.com/gin-gonic/gin"', self.MODULE,
            ),
            "external",
        )


# ---------------------------------------------------------------------------
# Module surface stays in sync with what callers import
# ---------------------------------------------------------------------------


class GoStdlibSurfaceTest(unittest.TestCase):
    def test_stdlib_set_is_frozenset(self) -> None:
        self.assertIsInstance(GO_STDLIB_PACKAGES, frozenset)

    def test_stdlib_set_covers_common_packages(self) -> None:
        # Sanity floor: at least the ~50 packages the spec calls
        # out must be present so the classifier does not silently
        # regress to "everything is external" on a future edit.
        self.assertGreaterEqual(len(GO_STDLIB_PACKAGES), 40)

    def test_stdlib_set_contains_no_quotes(self) -> None:
        for path in GO_STDLIB_PACKAGES:
            self.assertNotIn('"', path)
            self.assertEqual(path, path.strip())


# ---------------------------------------------------------------------------
# Fixture-based assertion: the canned go.mod + .go files classify as expected
# ---------------------------------------------------------------------------


class GoOriginFixtureTest(unittest.TestCase):
    """Fixture mirrors a small Go project: stdlib + project + external imports.

    The fixture under ``weld/tests/fixtures/go_origin_project/`` declares
    a module path in ``go.mod`` and contains two ``.go`` files that
    together import a stdlib package, a project sub-package, and an
    external dependency. This test reads the fixture, parses the
    module path, and walks the import lines to assert classification.

    It is the per-issue acceptance gate: "A unit test fixtures a small
    Go project (a couple of `.go` files plus a `go.mod` declaring a
    module path with at least one dependency) and asserts the
    classification for each kind."
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "go_origin_project"
        )

    def test_fixture_exists(self) -> None:
        self.assertTrue((self.fixture / "go.mod").is_file())
        self.assertTrue((self.fixture / "main.go").is_file())
        self.assertTrue((self.fixture / "internal" / "svc.go").is_file())

    def test_module_path_parsed(self) -> None:
        text = (self.fixture / "go.mod").read_text(encoding="utf-8")
        self.assertEqual(
            parse_go_mod_module_path(text),
            "example.com/myapi",
        )

    def _imports_in(self, rel_path: str) -> list[str]:
        out: list[str] = []
        text = (self.fixture / rel_path).read_text(encoding="utf-8")
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith('"') and line.endswith('"'):
                out.append(line[1:-1])
        return out

    def test_main_go_classification(self) -> None:
        module_path = "example.com/myapi"
        classifications = {
            imp: classify_go_import(imp, module_path)
            for imp in self._imports_in("main.go")
        }
        # main.go imports fmt (stdlib), project sub-package, and
        # github.com/example/external (external).
        self.assertEqual(classifications.get("fmt"), "stdlib")
        self.assertEqual(
            classifications.get("example.com/myapi/internal"),
            "project",
        )
        self.assertEqual(
            classifications.get("github.com/example/external"),
            "external",
        )

    def test_internal_svc_go_classification(self) -> None:
        module_path = "example.com/myapi"
        classifications = {
            imp: classify_go_import(imp, module_path)
            for imp in self._imports_in("internal/svc.go")
        }
        # internal/svc.go imports net/http (stdlib) and
        # encoding/json (stdlib).
        self.assertEqual(classifications.get("net/http"), "stdlib")
        self.assertEqual(classifications.get("encoding/json"), "stdlib")


if __name__ == "__main__":
    unittest.main()

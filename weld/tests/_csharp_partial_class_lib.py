"""Shared test fixtures for the C# Wave 3 partial-class merger tests.

Both :mod:`weld.tests.weld_csharp_partial_class_test` (basic merging,
edge contracts) and :mod:`weld.tests.weld_csharp_partial_generics_test`
(generic-parameter preservation, modifier-order tolerance) import the
helpers below. Keeping them in one place ensures the two test files
stay each under the 400-line line-count cap without duplicating the
setup boilerplate.
"""

from __future__ import annotations

import textwrap
from pathlib import Path


def make_partial_tree(
    tmp: str,
    *,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Build a fixture tree with two partial-class declarations.

    The two files declare ``partial class Foo`` in the same namespace.
    Callers add extra files via *extra_files* (relative-path -> body).
    """
    root = Path(tmp)
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (root / "Sample.Api.csproj").write_text(
        textwrap.dedent("""\
            <Project Sdk="Microsoft.NET.Sdk">
              <PropertyGroup>
                <RootNamespace>Sample.Api</RootNamespace>
              </PropertyGroup>
            </Project>
        """),
        encoding="utf-8",
    )
    (src / "Foo.Part1.cs").write_text(
        textwrap.dedent("""\
            namespace Sample.Api;

            public partial class Foo {
                public int GetA() => 1;
            }
        """),
        encoding="utf-8",
    )
    (src / "Foo.Part2.cs").write_text(
        textwrap.dedent("""\
            namespace Sample.Api;

            public partial class Foo {
                public int GetB() => 2;
            }
        """),
        encoding="utf-8",
    )
    for rel, body in (extra_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def make_single_file_tree(
    tmp: str,
    body: str,
    *,
    filename: str = "Foo.cs",
) -> Path:
    """Build a fixture tree with a single .cs file plus minimal csproj.

    Used by the focused "single declaration" tests so each scenario
    starts from a clean tree.
    """
    root = Path(tmp)
    (root / "Sample.Api.csproj").write_text(
        textwrap.dedent("""\
            <Project Sdk="Microsoft.NET.Sdk">
              <PropertyGroup>
                <RootNamespace>Sample.Api</RootNamespace>
              </PropertyGroup>
            </Project>
        """),
        encoding="utf-8",
    )
    (root / filename).write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def stub_symbol_payload(
    *,
    classes: list[str],
    methods: list[str] | None = None,
) -> dict:
    """Return a tree-sitter ``_parse_file_symbols`` payload stub."""
    payload: dict[str, list[str]] = {
        "exports": list(classes) + list(methods or []),
        "classes": list(classes),
        "imports": [],
    }
    if methods:
        payload["methods"] = list(methods)
    return payload


__all__ = [
    "make_partial_tree",
    "make_single_file_tree",
    "stub_symbol_payload",
]

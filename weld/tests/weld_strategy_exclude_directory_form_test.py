"""Directory-form ``exclude:`` coverage for every glob-resolving strategy.

Regression coverage for bd eerc, the fleet-wide half of bd 3abf.

The defect class: a strategy resolves its ``glob:`` with *no* excludes::

    matched, dirs = _resolve_glob(root, pattern)

and leans on a per-file ``should_skip(f, excludes, root=root)`` in its
emit loop. ``should_skip`` delegates to ``matches_exclude``, which tests
the *file* path with no ancestor-directory check -- so a directory-form
pattern (``pkg/tests``, ``fixtures``) never matches ``pkg/tests/foo.py``
and the whole subtree is read and emitted anyway. Only
:func:`weld.glob_match.walk_glob` -- which prunes matching directories
*during descent* -- gives the directory form its meaning.

``exclude:`` is user-authored config in every consumer's
``.weld/discover.yaml``, so a silently-ignored exclude is a correctness
trap, not a cosmetic one: on this repo the python_callgraph instance of
the bug minted ~10.4k spurious symbol nodes.

Each case below is a real ``extract()`` call against a real fixture tree,
so this file genuinely exercises every strategy it lists rather than
asserting on a shared helper. ``csharp_project`` and ``csharp_solution``
were the worst of the set -- they matched excludes by *basename only*, so
even the ``pkg/tests/**`` subtree form leaked before this fix.

The six assertions themselves live in
:mod:`weld.tests._exclude_form_harness` (bd 9gdq), shared with the
wave-3 batteries; this file owns the eerc fixture bodies and case table.
"""

from __future__ import annotations

import unittest

from weld.strategies import (
    axum,
    boundary_entrypoint,
    csharp_aspnet_routes,
    csharp_efcore,
    csharp_project,
    csharp_solution,
    csharp_test_framework,
    express,
    gin,
    tree_sitter,
    typescript_exports,
)
from weld.tests._exclude_form_harness import EXCLUDED_DIR as _EXCLUDED_DIR
from weld.tests._exclude_form_harness import Case, ExcludeFormBatteryMixin


def _py_app(tag: str) -> str:
    return f"""
    from fastapi import FastAPI

    app = FastAPI(title="{tag}")
    """


def _py_fn(tag: str) -> str:
    return f"""
    def {tag}_fn():
        return 1
    """


def _ts_export(tag: str) -> str:
    return f"export function {tag}Fn() {{ return 1; }}\n"


def _cs_controller(tag: str) -> str:
    return f"""
    using Microsoft.AspNetCore.Mvc;

    namespace App {{
      [ApiController]
      [Route("api/{tag}")]
      public class {tag.capitalize()}Controller : ControllerBase {{
        [HttpGet]
        public IActionResult Get() {{ return Ok(); }}
      }}
    }}
    """


def _cs_dbcontext(tag: str) -> str:
    return f"""
    using Microsoft.EntityFrameworkCore;

    namespace App {{
      public class {tag.capitalize()}Context : DbContext {{
        public DbSet<Item> Items {{ get; set; }}
      }}
    }}
    """


def _cs_tests(tag: str) -> str:
    return f"""
    using Xunit;

    namespace App {{
      public class {tag.capitalize()}Tests {{
        [Fact]
        public void ItWorks() {{ }}
      }}
    }}
    """


def _sln(tag: str) -> str:
    return f"""
    Microsoft Visual Studio Solution File, Format Version 12.00
    Project("{{FAE04EC0}}") = "{tag}", "{tag}\\{tag}.csproj", "{{111}}"
    EndProject
    """


def _csproj() -> str:
    """csproj identity comes from the filename stem, not the body."""
    return """
    <Project Sdk="Microsoft.NET.Sdk">
      <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>
    </Project>
    """


def _rust_axum(tag: str) -> str:
    return f"""
    use axum::Router;
    use axum::routing::get;

    async fn handler() {{}}

    fn app() -> Router {{ Router::new().route("/{tag}", get(handler)) }}
    """


def _js_express(tag: str) -> str:
    return f"""
    const express = require('express');
    const app = express();
    app.get('/{tag}', (req, res) => res.send('x'));
    """


def _go_gin(tag: str) -> str:
    return f"""
    package main

    import "github.com/gin-gonic/gin"

    func main() {{ r := gin.Default(); r.GET("/{tag}", nil) }}
    """


CASES: tuple[Case, ...] = (
    Case(
        "boundary_entrypoint", boundary_entrypoint, "pkg/**/*.py",
        "pkg/zzkeep.py", _py_app("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.py", _py_app("zzdrop"),
        keep_marker="zzkeep",
    ),
    # This case asserts on ``discovered_from`` -- populated from
    # ``_resolve_glob`` before any parse -- so it pins the exclude fix rather
    # than the parse. It still needs the grammar: without it the strategy
    # returns empty at its availability guard, before stamping anything. The
    # sandbox does not withhold one (it reads the interpreter's user site),
    # so the target declares the wheels from requirements_lock.txt. bd c42b.
    Case(
        "tree_sitter", tree_sitter, "pkg/**/*.py",
        "pkg/zzkeep.py", _py_fn("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.py", _py_fn("zzdrop"),
        keep_marker="pkg/",
        drop_markers=(_EXCLUDED_DIR,),
        extra_source={"language": "python"},
    ),
    Case(
        "typescript_exports", typescript_exports, "pkg/**/*.ts",
        "pkg/zzkeep.ts", _ts_export("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.ts", _ts_export("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "csharp_aspnet_routes", csharp_aspnet_routes, "pkg/**/*.cs",
        "pkg/Zzkeep.cs", _cs_controller("zzkeep"),
        f"{_EXCLUDED_DIR}/Zzdrop.cs", _cs_controller("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "csharp_efcore", csharp_efcore, "pkg/**/*.cs",
        "pkg/Zzkeep.cs", _cs_dbcontext("zzkeep"),
        f"{_EXCLUDED_DIR}/Zzdrop.cs", _cs_dbcontext("zzdrop"),
        keep_marker="Zzkeep",
    ),
    Case(
        "csharp_test_framework", csharp_test_framework, "pkg/**/*.cs",
        "pkg/Zzkeep.cs", _cs_tests("zzkeep"),
        f"{_EXCLUDED_DIR}/Zzdrop.cs", _cs_tests("zzdrop"),
        keep_marker="Zzkeep",
    ),
    Case(
        "csharp_solution", csharp_solution, "pkg/**/*.sln",
        "pkg/zzkeep.sln", _sln("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.sln", _sln("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "csharp_project", csharp_project, "pkg/**/*.csproj",
        "pkg/zzkeep.csproj", _csproj(),
        f"{_EXCLUDED_DIR}/zzdrop.csproj", _csproj(),
        keep_marker="zzkeep",
    ),
    Case(
        "axum", axum, "pkg/**/*.rs",
        "pkg/zzkeep.rs", _rust_axum("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.rs", _rust_axum("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "express", express, "pkg/**/*.js",
        "pkg/zzkeep.js", _js_express("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.js", _js_express("zzdrop"),
        keep_marker="zzkeep",
    ),
    Case(
        "gin", gin, "pkg/**/*.go",
        "pkg/zzkeep.go", _go_gin("zzkeep"),
        f"{_EXCLUDED_DIR}/zzdrop.go", _go_gin("zzdrop"),
        keep_marker="zzkeep",
    ),
)


class StrategyExcludeDirectoryFormTest(
    ExcludeFormBatteryMixin, unittest.TestCase,
):
    """``exclude:`` must prune subtrees, not only exact file paths."""

    CASES = CASES


if __name__ == "__main__":
    unittest.main()

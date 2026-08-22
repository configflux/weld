"""Every bundled strategy records per-file ``discovered_from``.

Two sub-families derived ``discovered_from`` from a *directory* rather than
from the files they read, and both degenerate to the same value at the repo
root:

* from the glob *pattern* -- ``(root / pattern).parent``, appended
  unconditionally before any file matched (eight strategies, bd 8ia5);
* from each matched file's *parent* -- ``f.parent.relative_to(root)``
  (fourteen more, bd jv5d for ``test_peer`` and bd od2a for the rest).

For a root-anchored glob (``README.md``, ``*.sh``, ``*_test.py``, ``main.go``,
``index.js``) that directory IS the repo root, so the recorded value was
``"./"`` -- and ``"./"`` is the root marker that makes
:func:`weld._git._path_is_tracked` report *every* path in the repository as
tracked source. ``source_stale`` was then computed over the whole tree instead
of over the files discovery actually read, which is the failure mode
``WELD_BOOKKEEPING_PATHS`` has had to be extended after five separate
incidents to contain.

This test pins the repaired contract for the whole family at once, in the
three shapes that matter: a root-anchored glob (the live trigger -- this
repo's ``glob: README.md`` + ``strategy: markdown`` entry put a real ``"./"``
in ``.weld/graph.json``), a nested glob (the uniform per-file form), and the
one carve-out where a directory entry is load-bearing because the node *is*
the directory (``python_package``). ADR 0017 names the field a *source-file*
model -- "any file in ``meta.discovered_from``" -- so per-file provenance is
the model being restored, not a new one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies.axum import extract as axum_extract
from weld.strategies.boundary_entrypoint import extract as boundary_extract
from weld.strategies.csharp_aspnet_routes import extract as cs_routes_extract
from weld.strategies.csharp_efcore import extract as cs_efcore_extract
from weld.strategies.csharp_package import extract as cs_package_extract
from weld.strategies.csharp_test_framework import extract as cs_test_extract
from weld.strategies.express import extract as express_extract
from weld.strategies.fastapi import extract as fastapi_extract
from weld.strategies.firstline_md import extract as firstline_md_extract
from weld.strategies.flask import extract as flask_extract
from weld.strategies.frontmatter_md import extract as frontmatter_md_extract
from weld.strategies.gin import extract as gin_extract
from weld.strategies.markdown import extract as markdown_extract
from weld.strategies.pydantic import extract as pydantic_extract
from weld.strategies.python_callgraph import extract as callgraph_extract
from weld.strategies.python_module import extract as python_module_extract
from weld.strategies.python_package import extract as python_package_extract
from weld.strategies.sqlalchemy import extract as sqlalchemy_extract
from weld.strategies.test_peer import extract as test_peer_extract
from weld.strategies.typescript_exports import extract as ts_exports_extract
from weld.strategies.validator_targets import extract as validator_extract
from weld.strategies.worker_stage import extract as worker_stage_extract
from weld.strategies.yaml_meta import extract as yaml_meta_extract

# The repo-root markers ``_path_is_tracked`` reads as "every path in this
# repository is tracked source". No strategy may emit either one.
_ROOT_MARKERS = ("./", ".")

_MODEL_BODY = """\
from pydantic import BaseModel


class Thing(BaseModel):
    name: str
"""

_ENTITY_BODY = """\
class Thing(Base):
    __tablename__ = "thing"
"""

_ROUTER_BODY = """\
from fastapi import APIRouter

router = APIRouter(prefix="/things")


@router.get("/list")
def list_things():
    return []
"""

_AGENT_BODY = """\
---
name: helper
description: Does a thing.
---

Body text.
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_markdown(root: Path, prefix: str) -> None:
    _write(root / prefix / "guide.md", "# Guide\n\nText.\n")


def _build_firstline(root: Path, prefix: str) -> None:
    _write(root / prefix / "push.md", "Run the gate.\n")


def _build_frontmatter(root: Path, prefix: str) -> None:
    _write(root / prefix / "helper.md", _AGENT_BODY)


def _build_yaml(root: Path, prefix: str) -> None:
    _write(root / prefix / "ci.yml", "name: CI\non:\n  push:\n")


def _build_pydantic(root: Path, prefix: str) -> None:
    _write(root / prefix / "models.py", _MODEL_BODY)


def _build_sqlalchemy(root: Path, prefix: str) -> None:
    _write(root / prefix / "entities.py", _ENTITY_BODY)


def _build_fastapi(root: Path, prefix: str) -> None:
    _write(root / prefix / "things.py", _ROUTER_BODY)


def _build_worker_stage(root: Path, prefix: str) -> None:
    # worker_stage takes ``(root / glob).parent`` as the directory whose
    # immediate subdirectories are stages, so the file it actually reads is
    # one level below: ``<parent>/<stage>/__init__.py``.
    _write(root / prefix / "plan" / "__init__.py", "__all__ = ['run']\n")


def _one_file(name: str, body: str):
    """Builder for the common case: one file of *body* named *name*."""
    def build(root: Path, prefix: str) -> None:
        _write(root / prefix / name, body)
    return build


# Root-level filenames chosen to be the ones that really do sit at a repo
# root in each ecosystem -- ``main.go``, ``index.js``, ``setup.py``,
# ``*_test.py`` -- because that is the placement that used to produce
# ``"./"``.
_FLASK = "from flask import Flask\n\napp = Flask(__name__)\n\n\n@app.route('/x')\ndef x():\n    return ''\n"
_EXPRESS = "const express = require('express');\nconst app = express();\napp.get('/x', (q, r) => r.send());\n"
_GIN = 'package main\n\nimport "github.com/gin-gonic/gin"\n\nfunc main() {\n\tr := gin.Default()\n\tr.GET("/x", nil)\n}\n'
_AXUM = 'use axum::Router;\n\nfn app() -> Router {\n    Router::new().route("/x", get(h))\n}\n'
_CS_ROUTES = 'namespace N;\n\n[ApiController]\n[Route("api")]\npublic class CController : ControllerBase {\n  [HttpGet]\n  public void G() {}\n}\n'
_CS_EFCORE = "namespace N;\n\npublic class AppDb : DbContext {\n  public DbSet<Thing> Things { get; set; }\n}\n"
_CS_TEST = "namespace N;\n\npublic class TTests {\n  [Fact]\n  public void A() {}\n}\n"
_CS_PACKAGE = "namespace Acme.Lib;\n\npublic class P {}\n"

# (label, extract, fixture builder, glob template, expected file template).
# ``{p}`` is the directory prefix: empty for the root-anchored case, a real
# directory plus separator for the nested case.
_FAMILY = (
    ("markdown", markdown_extract, _build_markdown,
     "{p}*.md", "{p}guide.md"),
    ("firstline_md", firstline_md_extract, _build_firstline,
     "{p}*.md", "{p}push.md"),
    ("frontmatter_md", frontmatter_md_extract, _build_frontmatter,
     "{p}*.md", "{p}helper.md"),
    ("yaml_meta", yaml_meta_extract, _build_yaml,
     "{p}*.yml", "{p}ci.yml"),
    ("pydantic", pydantic_extract, _build_pydantic,
     "{p}*.py", "{p}models.py"),
    ("sqlalchemy", sqlalchemy_extract, _build_sqlalchemy,
     "{p}*.py", "{p}entities.py"),
    ("fastapi", fastapi_extract, _build_fastapi,
     "{p}*.py", "{p}things.py"),
    ("worker_stage", worker_stage_extract, _build_worker_stage,
     "{p}*", "{p}plan/__init__.py"),
    # --- the file-derived-directory sub-family (bd jv5d, bd od2a) ---
    ("test_peer", test_peer_extract,
     _one_file("thing_test.py", "def test_x():\n    pass\n"),
     "{p}*_test.py", "{p}thing_test.py"),
    ("python_module", python_module_extract,
     _one_file("mod.py", "def f():\n    return 1\n"),
     "{p}*.py", "{p}mod.py"),
    ("python_callgraph", callgraph_extract,
     _one_file("mod.py", "def f():\n    return 1\n"),
     "{p}*.py", "{p}mod.py"),
    ("boundary_entrypoint", boundary_extract,
     _one_file("main.py", "if __name__ == '__main__':\n    print(1)\n"),
     "{p}*.py", "{p}main.py"),
    ("validator_targets", validator_extract,
     _one_file("check.py", "import sys\n\nTARGET = 'weld/graph.py'\n"),
     "{p}*.py", "{p}check.py"),
    ("typescript_exports", ts_exports_extract,
     _one_file("index.ts", "export function f() { return 1; }\n"),
     "{p}*.ts", "{p}index.ts"),
    ("flask", flask_extract, _one_file("app.py", _FLASK),
     "{p}*.py", "{p}app.py"),
    ("express", express_extract, _one_file("index.js", _EXPRESS),
     "{p}*.js", "{p}index.js"),
    ("gin", gin_extract, _one_file("main.go", _GIN),
     "{p}*.go", "{p}main.go"),
    ("axum", axum_extract, _one_file("main.rs", _AXUM),
     "{p}*.rs", "{p}main.rs"),
    ("csharp_aspnet_routes", cs_routes_extract,
     _one_file("C.cs", _CS_ROUTES), "{p}*.cs", "{p}C.cs"),
    ("csharp_efcore", cs_efcore_extract,
     _one_file("Db.cs", _CS_EFCORE), "{p}*.cs", "{p}Db.cs"),
    ("csharp_test_framework", cs_test_extract,
     _one_file("T.cs", _CS_TEST), "{p}*.cs", "{p}T.cs"),
    ("csharp_package", cs_package_extract,
     _one_file("P.cs", _CS_PACKAGE), "{p}*.cs", "{p}P.cs"),
)


def _source(pattern: str) -> dict:
    # ``include_readme`` is inert for every strategy but ``markdown``, which
    # needs it only when the fixture is literally named README.md; passing it
    # uniformly keeps the table single-shaped.
    return {"glob": pattern, "include_readme": True}


class TestRootAnchoredGlobProvenance(unittest.TestCase):
    """A root-anchored glob must never record the repo-root marker."""

    def test_no_strategy_emits_the_root_marker(self) -> None:
        for label, extract, build, glob_tmpl, file_tmpl in _FAMILY:
            with self.subTest(strategy=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    build(root, "")
                    result = extract(root, _source(glob_tmpl.format(p="")), {})
                    for marker in _ROOT_MARKERS:
                        self.assertNotIn(
                            marker, result.discovered_from,
                            f"{label} recorded the repo-root marker "
                            f"{marker!r}, which makes every path in the "
                            f"repository count as tracked source",
                        )

    def test_root_anchored_glob_records_the_matched_file(self) -> None:
        for label, extract, build, glob_tmpl, file_tmpl in _FAMILY:
            with self.subTest(strategy=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    build(root, "")
                    result = extract(root, _source(glob_tmpl.format(p="")), {})
                    self.assertEqual(
                        result.discovered_from, [file_tmpl.format(p="")],
                        f"{label} must record the file it read",
                    )


class TestNestedGlobProvenance(unittest.TestCase):
    """A nested glob records the file path, not its parent directory."""

    def test_records_file_not_directory(self) -> None:
        for label, extract, build, glob_tmpl, file_tmpl in _FAMILY:
            with self.subTest(strategy=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    build(root, "src")
                    result = extract(
                        root, _source(glob_tmpl.format(p="src/")), {}
                    )
                    self.assertEqual(
                        result.discovered_from, [file_tmpl.format(p="src/")],
                        f"{label} must record the file, not its directory",
                    )
                    self.assertNotIn("src/", result.discovered_from)


class TestProvenanceCoversUnproductiveFiles(unittest.TestCase):
    """A file that is read but emits no node is still provenance.

    ``pydantic``, ``fastapi`` and ``sqlalchemy`` can parse a file and emit
    nothing from it. Recording only the files that produced nodes would leave
    a staleness hole: adding a ``BaseModel`` to a previously-empty file would
    not mark the graph stale, so discovery would never re-read it. The
    file-oriented model strategies (``bazel``, ``runbook``) record provenance
    *before* the read for exactly this reason.
    """

    def test_parsed_but_empty_file_is_recorded(self) -> None:
        cases = (
            ("pydantic", pydantic_extract, "models.py"),
            ("fastapi", fastapi_extract, "routers.py"),
            ("sqlalchemy", sqlalchemy_extract, "entities.py"),
        )
        for label, extract, name in cases:
            with self.subTest(strategy=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    # Valid Python, no contract/route/entity of any kind.
                    _write(root / "src" / name, "VALUE = 1\n")
                    result = extract(root, _source("src/*.py"), {})
                    self.assertEqual(result.nodes, {})
                    self.assertEqual(
                        result.discovered_from, [f"src/{name}"],
                        f"{label} must record a file it read even when the "
                        f"file produced no node",
                    )


class TestEmptyDirectoryProvenance(unittest.TestCase):
    """No matched file means no provenance -- not a bare directory."""

    def test_empty_match_records_nothing(self) -> None:
        for label, extract, _build, glob_tmpl, _file_tmpl in _FAMILY:
            with self.subTest(strategy=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / "src").mkdir()
                    result = extract(
                        root, _source(glob_tmpl.format(p="src/")), {}
                    )
                    self.assertEqual(
                        result.discovered_from, [],
                        f"{label} recorded provenance for a directory it "
                        f"read no file from",
                    )


class TestPackageNodeProvenance(unittest.TestCase):
    """The one carve-out: a node that *is* a directory (bd od2a).

    ``python_package`` records its directory because a package's membership
    changes when a file appears beside its siblings -- the directory is the
    discovered thing, not a stand-in for the files under it. That licence
    does not extend to the repo root, where the entry would be the ``"./"``
    marker, so the members are recorded there instead.

    ``csharp_package`` is deliberately *not* here. Its node is a namespace
    whose members can sit in any directory and which carries no ``dir``
    prop, so its parent directories were only ever a lossy stand-in -- it
    sits in ``_FAMILY`` with the per-file strategies.
    """

    def test_subdirectory_package_records_its_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "pkg" / "__init__.py", "X = 1\n")
            _write(root / "pkg" / "mod.py", "Y = 2\n")
            result = python_package_extract(root, {"glob": "pkg/*.py"}, {})
            self.assertEqual(result.discovered_from, ["pkg/"])

    def test_root_package_records_members_not_the_root_marker(self) -> None:
        # A root-level package is only minted when the source config names
        # it explicitly; without ``package`` the strategy declines to claim
        # a package for the whole repo.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "setup.py", "X = 1\n")
            _write(root / "main.py", "Y = 2\n")
            result = python_package_extract(
                root, {"glob": "*.py", "package": "app"}, {},
            )
            for marker in _ROOT_MARKERS:
                self.assertNotIn(marker, result.discovered_from)
            self.assertEqual(result.discovered_from, ["main.py", "setup.py"])

    def test_root_package_provenance_is_not_simply_dropped(self) -> None:
        """Dropping the entry is what the old guard did, and it is not safe.

        A package with no provenance at all is a package no source change
        can ever mark stale, which is the same silent-staleness harm the
        ``"./"`` marker causes from the opposite direction.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "main.py", "Y = 2\n")
            result = python_package_extract(
                root, {"glob": "*.py", "package": "app"}, {},
            )
            self.assertTrue(result.nodes)
            self.assertNotEqual(result.discovered_from, [])


if __name__ == "__main__":
    unittest.main()

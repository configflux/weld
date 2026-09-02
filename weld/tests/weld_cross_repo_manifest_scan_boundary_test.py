"""Which files the ``package_graph`` manifest scan is allowed to read.

Field-eval v0.24.0 finding N2: ``scan_child_manifests`` walked a child repo
with its own ``os.walk`` and a hand-maintained skip list, so a service that
vendored a ``.venv`` was credited with *producing* every distribution inside
it -- ``pandas`` from a ``dist-info/pyproject.toml``, ``google.protobuf`` from
a bundled ``grpc_tools`` ``.proto`` -- and any sibling declaring those as
dependencies got a fabricated cross-repo edge to it.

The fix (ADR 0137 s6) is not another skip-list entry. The scan asks the repo
boundary which files the child *claims*, so ``.gitignore`` is honoured
natively, with the excluded-dir set as the fallback for a child that is not a
git repository. These tests pin both halves plus the one thing git-visibility
alone does not settle: a vendored directory the repo happens to **track** is
still not that repo's own package declaration, so its manifests stay out on
both paths.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.cross_repo._package_manifest_scan import scan_child_manifests
from weld.repo_boundary import get_repo_boundary

#: The two manifests the field-eval fixture hides inside notify-service's
#: gitignored ``.venv`` -- reproduced here at the same depth, because the
#: depth is what a shallow skip list gets wrong.
_VENV = ".venv/lib/python3.12/site-packages"
_VENDORED: dict[str, str] = {
    f"{_VENV}/pandas-3.0.2.dist-info/pyproject.toml": (
        '[project]\nname = "pandas"\nversion = "3.0.2"\n'
    ),
    f"{_VENV}/grpc_tools/_proto/google/protobuf/any.proto": (
        'syntax = "proto3";\n\npackage google.protobuf;\n'
    ),
}

#: The child's own declaration: what it publishes and what it depends on.
_OWN_PYPROJECT = (
    '[project]\nname = "notify-service"\ndependencies = ["order-schema>=1.0"]\n'
)

#: An MSBuild project file, which since ADR 0141 D2 declares a *producer* --
#: ``<PackageId>`` or, absent one, the filename. Both spellings appear inside
#: excluded trees below: the boundary has to hold for the producer half or M4's
#: fix reopens N2 under a .NET filename.
_VENDORED_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
    "    <PackageId>Vendored.Third.Party</PackageId>\n"
    "  </PropertyGroup>\n</Project>\n"
)

#: An npm child's own declaration (ADR 0142 D5).
_OWN_PACKAGE_JSON = (
    '{"name": "@acme/storefront", "dependencies": {"@acme/ui-kit": "^1.2.0"}}\n'
)

#: What ``npm install`` leaves behind: a complete, self-naming manifest for
#: every transitive dependency. The vendored copy of ``@acme/ui-kit`` is the
#: sharp one -- read as a declaration it makes the *consumer* a producer of
#: its supplier's package, and every other sibling depending on that name gets
#: an edge to the wrong repo.
_VENDORED_NODE_MODULES: dict[str, str] = {
    "node_modules/react/package.json": (
        '{"name": "react", "dependencies": {"loose-envify": "^1.1.0"}}\n'
    ),
    "node_modules/@acme/ui-kit/package.json": '{"name": "@acme/ui-kit"}\n',
}


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_all(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        _write(root, rel, body)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


class GitVisibleManifestScanTest(unittest.TestCase):
    """A git-backed child: the scan reads what git says the repo claims."""

    def test_gitignored_vendored_venv_contributes_no_package_names(self) -> None:
        # The fixture's shape: the notifier is committed first, then the
        # .venv and the .gitignore that hides it arrive untracked.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "notify-service"
            _init_repo(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _commit_all(root, "notify service")
            _write(root, ".gitignore", ".venv/\n")
            _write_all(root, _VENDORED)

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})
        self.assertEqual(consumed, {"order-schema"})

    def test_an_ignored_path_is_dropped_by_git_not_by_a_directory_name(
        self,
    ) -> None:
        """``.gitignore`` decides, for a directory no name list knows about.

        ``.venv`` is on the fallback exclusion list too, so a probe using only
        that directory cannot tell which mechanism did the work. ``deps-cache``
        is on no list anywhere: if its manifest stays out, git-visibility is
        what kept it out.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            _init_repo(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _write(root, ".gitignore", "deps-cache/\n")
            _commit_all(root, "svc")
            _write(
                root,
                "deps-cache/pandas/pyproject.toml",
                '[project]\nname = "pandas"\n',
            )

            produced, _consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})

    def test_untracked_but_unignored_manifest_still_contributes(self) -> None:
        """Git-visible is not the same as committed.

        The fixture's first-party sources land after the commit and are not
        ignored; a scan that only read ``--cached`` would go blind to every
        manifest a developer has written but not yet committed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            _init_repo(root)
            _write(root, "README.md", "# svc\n")
            _commit_all(root, "svc")
            _write(root, "libs/schema/pyproject.toml", '[project]\nname = "wip"\n')

            produced, _consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"wip"})

    def test_a_tracked_vendored_directory_is_still_not_a_declaration(self) -> None:
        """Committing a vendored tree does not make the repo its producer.

        Git-visibility answers "does the repo claim this file", which is the
        whole of finding N2. It does not answer "is this the repo's own
        package declaration": a committed ``vendor/`` or ``packages/`` tree is
        third-party code the repo carries, and crediting it as a producer is
        the same fabricated edge under a different directory. That exclusion
        predates this fix and is kept on both paths.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            _init_repo(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _write(
                root,
                "vendor/pandas/pyproject.toml",
                '[project]\nname = "pandas"\n',
            )
            _write(
                root,
                "packages/protobuf/any.proto",
                'syntax = "proto3";\n\npackage google.protobuf;\n',
            )
            _commit_all(root, "svc with vendored deps")

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})
        self.assertEqual(consumed, {"order-schema"})


class NonGitFallbackManifestScanTest(unittest.TestCase):
    """A child that is not a git repository falls back to directory names."""

    def _assert_not_git_backed(self, root: Path) -> None:
        if get_repo_boundary(root.resolve()).uses_git:  # pragma: no cover
            self.fail(
                f"{root} resolves to a git repository, so this test would "
                "exercise the git path instead of the fallback it exists for"
            )

    def test_fallback_excludes_the_vendored_and_build_output_directories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            root.mkdir(parents=True)
            self._assert_not_git_backed(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _write_all(root, _VENDORED)
            for rel in (
                "venv/lib/site-packages/x/pyproject.toml",
                ".tox/py312/lib/x/pyproject.toml",
                "node_modules/x/pyproject.toml",
                "dist/x/pyproject.toml",
                "build/x/pyproject.toml",
                "target/x/pyproject.toml",
                "bin/x/pyproject.toml",
                "obj/x/pyproject.toml",
                "vendor/x/pyproject.toml",
                "packages/x/pyproject.toml",
                "__pycache__/x/pyproject.toml",
            ):
                _write(root, rel, '[project]\nname = "excluded"\n')
            # The .NET build-output pair, with a real project file in each:
            # a copy under obj/ carries a PackageId, one under bin/ has only
            # its filename, and neither is this repo's own declaration.
            _write(root, "obj/Debug/net8.0/Restored.csproj", _VENDORED_CSPROJ)
            _write(root, "bin/Release/Acme.Copied.csproj", "<Project />\n")

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})
        self.assertEqual(consumed, {"order-schema"})

    def test_a_symlinked_directory_is_not_descended(self) -> None:
        """Same symlink posture the private walk had (``followlinks=False``).

        A link out of the child tree must not pull an unrelated manifest into
        this repo's produced set -- and must not hang on a cycle.
        """
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            _write(outside, "pyproject.toml", '[project]\nname = "outside"\n')
            root = Path(tmp) / "svc"
            root.mkdir(parents=True)
            self._assert_not_git_backed(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            (root / "linked").symlink_to(outside, target_is_directory=True)

            produced, _consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})


class MsbuildProducerBoundaryTest(unittest.TestCase):
    """The N2 boundary, on the producer half MSBuild gained (ADR 0141 D2).

    A ``.csproj`` used to contribute consumed names only, so every question
    this file asks about *producers* was asked of pyproject and proto alone.
    Now that a project file declares what its repo publishes -- and does so by
    filename when it says nothing else -- a vendored one is a fabricated
    producer waiting to happen, under a filename no ``.venv`` probe covers.
    Both routes are re-asked here rather than assumed from the shared dispatch
    (bd lcq0c.4).
    """

    def test_a_gitignored_project_file_declares_no_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            _init_repo(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _write(root, ".gitignore", "third-party/\n")
            _commit_all(root, "svc")
            _write(root, "third-party/Vendored.csproj", _VENDORED_CSPROJ)
            _write(root, "third-party/Unnamed.csproj", "<Project />\n")

            produced, _consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})

    def test_a_tracked_vendored_project_file_declares_no_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "svc"
            _init_repo(root)
            _write(root, "pyproject.toml", _OWN_PYPROJECT)
            _write(root, "vendor/Vendored.csproj", _VENDORED_CSPROJ)
            _write(root, "packages/Acme.Copied.csproj", "<Project />\n")
            _commit_all(root, "svc with a vendored project file")

            produced, _consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"notify-service"})


class NpmProducerBoundaryTest(unittest.TestCase):
    """The N2 boundary on the ecosystem that vendors by default (ADR 0142 D5).

    ``node_modules`` is npm's ``.venv``, and installing is what fills it: one
    complete manifest per transitive dependency, each naming itself. A scan
    that read them would credit an app repo with producing ``react``. The
    directory sits outside the shared repo boundary already, so what these ask
    is that neither route buys it back -- committing the tree, which some
    repos do, least of all.
    """

    def test_a_gitignored_node_modules_declares_no_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "storefront"
            _init_repo(root)
            _write(root, "package.json", _OWN_PACKAGE_JSON)
            _write(root, ".gitignore", "node_modules/\n")
            _commit_all(root, "storefront")
            _write_all(root, _VENDORED_NODE_MODULES)

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"@acme/storefront"})
        self.assertEqual(consumed, {"@acme/ui-kit"})

    def test_a_committed_node_modules_declares_no_producer(self) -> None:
        """Tracked is not the same as declared -- the tracked-``vendor/`` case.

        Git-visibility says the repo claims these files, and it does; it does
        not say the repo publishes what they name.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "storefront"
            _init_repo(root)
            _write(root, "package.json", _OWN_PACKAGE_JSON)
            _write_all(root, _VENDORED_NODE_MODULES)
            _commit_all(root, "storefront with node_modules committed")

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"@acme/storefront"})
        self.assertEqual(consumed, {"@acme/ui-kit"})

    def test_the_fallback_route_excludes_node_modules_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "storefront"
            root.mkdir(parents=True)
            # Same guard as ``NonGitFallbackManifestScanTest``: a temp dir that
            # resolved inside a git repository would take the other route and
            # this case would pass without asking its question.
            self.assertFalse(get_repo_boundary(root.resolve()).uses_git)
            _write(root, "package.json", _OWN_PACKAGE_JSON)
            _write_all(root, _VENDORED_NODE_MODULES)

            produced, consumed = scan_child_manifests(str(root))

        self.assertEqual(produced, {"@acme/storefront"})
        self.assertEqual(consumed, {"@acme/ui-kit"})


class MissingChildTest(unittest.TestCase):
    def test_a_directory_that_does_not_exist_scans_to_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            produced, consumed = scan_child_manifests(str(Path(tmp) / "absent"))
        self.assertEqual((produced, consumed), (set(), set()))

    def test_an_empty_child_path_scans_nothing_not_the_current_directory(
        self,
    ) -> None:
        # A ``Path("")`` is the process's own working directory, so an empty
        # child path must be rejected rather than resolved -- otherwise a
        # misconfigured workspace credits a child with whatever weld was run in.
        self.assertEqual(scan_child_manifests(""), (set(), set()))


if __name__ == "__main__":
    unittest.main()

"""Disk-side manifest scan for the ``package_graph`` cross-repo resolver.

The resolver joins *manifest-declared* package dependencies to the child
repository that produces the package. Unlike ``package_import_resolver``
(which reads ``imports_from`` import evidence out of a child *graph*), the
facts this needs live in build manifests, and discovery does not emit them as
graph nodes: a ``pyproject.toml`` ``[project].name`` is dropped by the
``config_file`` strategy, and ``<PackageReference>`` is parsed only to
classify ``using`` imports, never surfaced as a node or prop. So this module
reads the manifests directly from disk -- the same pattern
``compose_topology`` uses to read ``docker-compose.yml`` off
``ResolverContext.workspace_root``.

*What* each manifest declares is the sibling question, answered per ecosystem
by :mod:`weld.cross_repo._manifest_readers` and its
:data:`~weld.cross_repo._manifest_readers.MANIFEST_READERS` registry. This
module answers the other one: **which files that registry may be handed at
all**, and it is the more consequential of the two.

Matching is by :func:`normalize_package_name` -- a casefold -- so a C#
``PackageReference Include="Acme.Platform.Order.Schema"`` matches a
proto-derived ``acme.platform.order.schema`` and a Python ``order-schema``
matches a pyproject ``name = "order-schema"``. No separator munging: that
would over-merge ``order-schema`` and ``order.schema``, which are distinct
package identities.

**Which files it may read (ADR 0137 s6).** Only the ones the child repository
claims as its own, via :func:`weld.repo_boundary.iter_repo_files`: git-visible
files, so ``.gitignore`` is honoured natively, with the excluded-directory set
as the fallback for a child that is not a git repository. A private
``os.walk`` with a hand-maintained skip list read whatever was on disk, and a
service that vendored a ``.venv`` was credited with *producing* every
distribution inside it -- ``pandas`` out of a ``dist-info/pyproject.toml``, a
bundled ``grpc_tools`` ``.proto`` as ``google.protobuf`` -- so every sibling
declaring those as dependencies got a fabricated edge to it (field-eval
v0.24.0, N2).

Git-visibility answers "does this repo claim this file", which is the whole of
that finding. It does not answer "is this the repo's own package
declaration", so the vendored / build-output directory names are applied on
*both* routes -- see :data:`_NOT_OWN_DECLARATION_DIRS`.
"""

from __future__ import annotations

from pathlib import Path

from weld.cross_repo._manifest_readers import MANIFEST_READERS, ManifestReader
from weld.repo_boundary import EXCLUDED_DIR_NAMES, iter_repo_files

#: Directory names holding build output or third-party copies. A manifest
#: under one of these is never *this* repo's own declaration, whatever git
#: thinks of it: a committed ``vendor/`` or ``node_modules/`` tree is code the
#: repo carries, not code it publishes, and crediting it as a producer is the
#: same fabricated edge N2 reported under a different directory name. So this
#: set is applied on both routes -- git-backed and not -- and git-visibility
#: narrows it further rather than replacing it.
#:
#: ``node_modules`` is also in the shared :data:`EXCLUDED_DIR_NAMES`, and is
#: restated here for the same reason ``_FALLBACK_EXCLUDED_DIRS`` restates that
#: set rather than relying on it: the shared one states what *discovery* keeps
#: out of the graph, this one states what the scan refuses to read as a
#: declaration. They agree today; a change to one must not silently move the
#: other, and an npm repo is the case where that would cost the most (one
#: ``npm install`` leaves a complete self-naming manifest per transitive
#: dependency on disk).
#:
#: These are names, and a name can mean opposite things in two ecosystems:
#: ``packages/`` is where NuGet restores somebody else's code and where npm
#: workspaces keep this repo's own. An entry that publishes out of such a
#: directory says so through ``ManifestReader.first_party_dirs``, and the
#: exemption reaches that ecosystem's manifests only.
_NOT_OWN_DECLARATION_DIRS: frozenset[str] = frozenset({
    ".venv",
    "venv",
    "site-packages",
    ".tox",
    "dist",
    "build",
    "target",
    "bin",
    "obj",
    "vendor",
    "packages",
    "node_modules",
})

#: Directory names some ecosystem publishes its own packages out of, declared
#: by the registry entry that knows (``ManifestReader.first_party_dirs``). A
#: name here is not third-party *for that ecosystem*; the per-file check below
#: still applies the full set to every other one.
_FIRST_PARTY_DIRS: frozenset[str] = frozenset().union(
    *(reader.first_party_dirs for reader in MANIFEST_READERS)
)

#: What a child that is *not* a git repository is judged by instead: the
#: shared repo-boundary set (``.git``, ``node_modules``, ``__pycache__``,
#: ``.weld``, ``bazel-*``, ``.worktrees``, ...) plus the names above. Spelled
#: as the union rather than relying on ``iter_repo_files`` to re-apply the
#: shared half, so this constant reads as the whole fallback policy.
#:
#: First-party names are held back from the *walk* -- it decides whether to
#: descend before any reader has claimed the file, so it cannot know which
#: ecosystem's rule applies -- and re-applied per file below. Only this
#: module's own list gives ground: the shared set is unioned in whole, so a
#: first-party declaration can never walk an ecosystem back into
#: ``node_modules``.
_FALLBACK_EXCLUDED_DIRS: frozenset[str] = EXCLUDED_DIR_NAMES | (
    _NOT_OWN_DECLARATION_DIRS - _FIRST_PARTY_DIRS
)


def normalize_package_name(name: str) -> str:
    """Return the case-folded comparison key for a package name.

    Casefold only: two names join iff they are equal ignoring case. This is
    deliberately conservative -- ``Acme.Platform.Order.Schema`` and
    ``acme.platform.order.schema`` are the same package under NuGet / proto
    casing rules, but ``order-schema`` and ``order.schema`` are two different
    identities and must not be collapsed.
    """
    return name.strip().casefold()


def scan_child_manifests(child_dir: str) -> tuple[set[str], set[str]]:
    """Return ``(produced, consumed)`` package-name sets for one child repo.

    Reads the repo-visible files under *child_dir* once and dispatches each
    manifest among them to the ``MANIFEST_READERS`` entry that claims it.
    Returns raw, un-normalized names; the caller normalizes at match time so
    both the produced and consumed side go through the identical key function.

    A directory the child does not claim contributes nothing -- see the module
    docstring for which files that admits and why.
    """
    produced: set[str] = set()
    consumed: set[str] = set()
    root = Path(child_dir)
    # ``Path("")`` is the current directory where ``os.path.isdir("")`` is
    # False, so the empty string is rejected explicitly: an empty child path
    # means "no such child", never "scan wherever this process happens to be".
    if not child_dir or not root.is_dir():
        return produced, consumed

    # ``iter_repo_files`` resolves the root it returns paths under, so
    # relativize against the same resolved root rather than the argument.
    root = root.resolve()
    for path in iter_repo_files(
        root, fallback_excluded_dir_names=_FALLBACK_EXCLUDED_DIRS
    ):
        reader = _claiming_reader(path.name)
        if reader is None:
            continue
        # Claim first, then judge the location: which directory names mean
        # "not this repo's own declaration" is the claiming ecosystem's
        # question, not the file's.
        excluded = _NOT_OWN_DECLARATION_DIRS - reader.first_party_dirs
        if excluded.intersection(path.relative_to(root).parts[:-1]):
            continue
        manifest_produced, manifest_consumed = reader.read(str(path))
        produced |= manifest_produced
        consumed |= manifest_consumed
    return produced, consumed


def _claiming_reader(filename: str) -> ManifestReader | None:
    """The first registry entry claiming *filename*, or ``None`` for neither.

    First claim wins, as the registry's dispatch order documents.
    """
    for reader in MANIFEST_READERS:
        if reader.claims(filename):
            return reader
    return None

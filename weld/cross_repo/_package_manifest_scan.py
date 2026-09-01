"""Disk-side manifest scan for the ``package_graph`` cross-repo resolver.

The resolver joins *manifest-declared* package dependencies to the child
repository that produces the package. Unlike ``package_import_resolver``
(which reads ``imports_from`` import evidence out of a child *graph*),
the facts this needs live in build manifests, and discovery does not emit
them as graph nodes: a ``pyproject.toml`` ``[project].name`` is dropped by
the ``config_file`` strategy, and ``<PackageReference>`` is parsed only to
classify ``using`` imports, never surfaced as a node or prop. So this
module reads the manifests directly from disk -- the same pattern
``compose_topology`` uses to read ``docker-compose.yml`` off
``ResolverContext.workspace_root``.

Three manifest families are parsed, each contributing to two sets per
child:

* **produced** -- names this repo publishes as a consumable package:
  ``pyproject.toml`` ``[project].name``; ``go.mod`` ``module`` path; and
  the language-neutral package name derived from a ``.proto`` ``package``
  declaration (dropping a trailing ``.vN`` segment), because a schema
  library is consumed by generated-code package names that mirror the
  proto package, not the repo's own pyproject name.
* **consumed** -- names this repo declares a dependency on:
  ``pyproject.toml`` ``[project].dependencies`` (version specifier
  stripped); ``.csproj`` ``<PackageReference Include>``; ``go.mod``
  ``require`` entries.

Matching is by :func:`normalize_package_name` -- a casefold -- so a C#
``PackageReference Include="Acme.Platform.Order.Schema"`` matches a
proto-derived ``acme.platform.order.schema`` and a Python
``order-schema`` matches a pyproject ``name = "order-schema"``. No
separator munging: that would over-merge ``order-schema`` and
``order.schema``, which are distinct package identities.

The scan tolerates every malformed input (bad TOML, unreadable XML,
missing files) by contributing nothing for that manifest rather than
raising -- one bad file in one child must not sink the whole resolver.

**Which files it may read (ADR 0137 s6).** Only the ones the child
repository claims as its own, via
:func:`weld.repo_boundary.iter_repo_files`: git-visible files, so
``.gitignore`` is honoured natively, with the excluded-directory set as
the fallback for a child that is not a git repository. A private
``os.walk`` with a hand-maintained skip list read whatever was on disk,
and a service that vendored a ``.venv`` was credited with *producing*
every distribution inside it -- ``pandas`` out of a
``dist-info/pyproject.toml``, ``google.protobuf`` out of a bundled
``grpc_tools`` ``.proto`` -- so every sibling declaring those as
dependencies got a fabricated edge to it (field-eval v0.24.0, N2).

Git-visibility answers "does this repo claim this file", which is the
whole of that finding. It does not answer "is this the repo's own package
declaration", so the vendored / build-output directory names are applied
on *both* routes -- see :data:`_NOT_OWN_DECLARATION_DIRS`.
"""

from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from weld.repo_boundary import EXCLUDED_DIR_NAMES, iter_repo_files

#: Manifest filenames probed inside each child repo. Kept small and
#: explicit; a new manifest family is added here plus a parser below.
_PYPROJECT = "pyproject.toml"
_GO_MOD = "go.mod"
_CSPROJ_SUFFIX = ".csproj"
_PROTO_SUFFIX = ".proto"

#: Directory names holding build output or third-party copies. A manifest
#: under one of these is never *this* repo's own declaration, whatever git
#: thinks of it: a committed ``vendor/`` tree is code the repo carries, not
#: code it publishes, and crediting it as a producer is the same fabricated
#: edge N2 reported under a different directory name. So this set is applied
#: on both routes -- git-backed and not -- and git-visibility narrows it
#: further rather than replacing it.
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
})

#: What a child that is *not* a git repository is judged by instead: the
#: shared repo-boundary set (``.git``, ``node_modules``, ``__pycache__``,
#: ``.weld``, ``bazel-*``, ``.worktrees``, ...) plus the names above. Spelled
#: as the union rather than relying on ``iter_repo_files`` to re-apply the
#: shared half, so this constant reads as the whole fallback policy.
_FALLBACK_EXCLUDED_DIRS: frozenset[str] = (
    EXCLUDED_DIR_NAMES | _NOT_OWN_DECLARATION_DIRS
)

_PROTO_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", re.MULTILINE)
_GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
#: A single-line ``require <path> <version>`` outside a block. The
#: negative lookahead rejects ``require (`` (a block opener), whose ``(``
#: would otherwise be captured as a bogus module path.
_GO_REQUIRE_LINE_RE = re.compile(
    r"^\s*require\s+(?!\()(\S+)\s+\S+", re.MULTILINE
)


def normalize_package_name(name: str) -> str:
    """Return the case-folded comparison key for a package name.

    Casefold only: two names join iff they are equal ignoring case. This
    is deliberately conservative -- ``Acme.Platform.Order.Schema`` and
    ``acme.platform.order.schema`` are the same package under NuGet /
    proto casing rules, but ``order-schema`` and ``order.schema`` are
    two different identities and must not be collapsed.
    """
    return name.strip().casefold()


def _strip_pep508_version(spec: str) -> str:
    """Return the bare distribution name from a PEP 508 dependency string.

    ``order-schema>=1.0.0`` -> ``order-schema``; ``requests[socks]`` ->
    ``requests``; ``foo ; python_version<'3.9'`` -> ``foo``. Only the
    leading name token is kept; extras, version specifiers, and
    environment markers are dropped.
    """
    token = re.split(r"[<>=!~;\[\s(]", spec.strip(), maxsplit=1)[0]
    return token.strip()


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _scan_pyproject(path: str, produced: set[str], consumed: set[str]) -> None:
    text = _read_text(path)
    if text is None:
        return
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return
    project = data.get("project")
    if not isinstance(project, dict):
        return
    name = project.get("name")
    if isinstance(name, str) and name.strip():
        produced.add(name.strip())
    deps = project.get("dependencies")
    if isinstance(deps, list):
        for dep in deps:
            if not isinstance(dep, str):
                continue
            bare = _strip_pep508_version(dep)
            if bare:
                consumed.add(bare)


def _scan_csproj(path: str, consumed: set[str]) -> None:
    text = _read_text(path)
    if text is None:
        return
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag != "PackageReference":
            continue
        include = elem.attrib.get("Include", "").strip()
        if include:
            consumed.add(include)


def _scan_proto(path: str, produced: set[str]) -> None:
    text = _read_text(path)
    if text is None:
        return
    match = _PROTO_PACKAGE_RE.search(text)
    if not match:
        return
    pkg = match.group(1)
    # Drop a trailing ``.vN`` version segment: generated-code package
    # names (C# ``Acme.Platform.Order.Schema``, Python
    # ``acme.platform.order.schema``) mirror the proto package without
    # the API-version tail.
    parts = pkg.split(".")
    if len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts = parts[:-1]
    if parts:
        produced.add(".".join(parts))


def _scan_go_mod(path: str, produced: set[str], consumed: set[str]) -> None:
    text = _read_text(path)
    if text is None:
        return
    module = _GO_MODULE_RE.search(text)
    if module:
        produced.add(module.group(1).strip())
    for line in _GO_REQUIRE_LINE_RE.findall(text):
        consumed.add(line.strip())
    _scan_go_require_blocks(text, consumed)


def _scan_go_require_blocks(text: str, consumed: set[str]) -> None:
    """Collect module paths from multi-line ``require ( ... )`` blocks."""
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not in_block:
            if line.startswith("require") and line.endswith("("):
                in_block = True
            continue
        if line == ")":
            in_block = False
            continue
        if not line or line.startswith("//"):
            continue
        parts = line.split()
        if parts:
            consumed.add(parts[0].strip())


def scan_child_manifests(child_dir: str) -> tuple[set[str], set[str]]:
    """Return ``(produced, consumed)`` package-name sets for one child repo.

    Reads the repo-visible files under *child_dir* once and dispatches
    each manifest among them to its parser. Returns raw, un-normalized
    names; the caller normalizes at match time so both the produced and
    consumed side go through the identical key function.

    A directory the child does not claim contributes nothing -- see the
    module docstring for which files that admits and why.
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
        if _NOT_OWN_DECLARATION_DIRS.intersection(path.relative_to(root).parts[:-1]):
            continue
        name = path.name
        full = str(path)
        if name == _PYPROJECT:
            _scan_pyproject(full, produced, consumed)
        elif name == _GO_MOD:
            _scan_go_mod(full, produced, consumed)
        elif name.endswith(_CSPROJ_SUFFIX):
            _scan_csproj(full, consumed)
        elif name.endswith(_PROTO_SUFFIX):
            _scan_proto(full, produced)
    return produced, consumed

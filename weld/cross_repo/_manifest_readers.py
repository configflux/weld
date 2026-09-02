"""What each manifest family declares: one reader per ecosystem.

The ``package_graph`` resolver joins a dependency one child repo *declares* to
the sibling repo that *produces* that package. Neither name is graph data --
discovery drops a ``pyproject.toml`` ``[project].name`` in the ``config_file``
strategy and parses ``<PackageReference>`` only to classify ``using`` imports
-- so both are read straight off disk, and this module is where each family's
reading lives. *Which* files a reader may be handed is the separate question,
answered by the boundary policy in
:mod:`weld.cross_repo._package_manifest_scan`.

Every registry entry contributes to two sets:

* **produced** -- names this repo publishes as a consumable package:
  ``pyproject.toml`` ``[project].name``; ``go.mod`` ``module`` path;
  ``package.json`` ``name``; MSBuild ``<PackageId>`` or, absent one, the
  project filename; and the language-neutral package name derived from a
  ``.proto`` ``package`` declaration (dropping a trailing ``.vN`` segment),
  because a schema library is consumed by generated-code package names that
  mirror the proto package, not the repo's own pyproject name.
* **consumed** -- names this repo declares a dependency on:
  ``pyproject.toml`` ``[project].dependencies`` (version specifier stripped);
  ``package.json`` ``dependencies``; ``.csproj``
  ``<PackageReference Include>``; ``go.mod`` ``require`` entries.

Both halves, always: an ecosystem weld can read no *producer* from can be
joined from but never to, which is field-eval finding M4 (ADR 0141 D2) -- how
most of a .NET workspace's dependency graph came to be silently absent while
the resolver read as correct. :data:`MANIFEST_READERS` is the registry, and
adding an ecosystem is a reader here plus its entry there.

**Malformed input contributes nothing.** A child repo is somebody else's
tree. One manifest weld cannot parse must not sink the resolver -- which is
what raising would do, since the framework isolates per resolver, not per
file -- so every reader answers input it cannot make sense of with empty
sets. That includes the two parsers which answer deep nesting with
``RecursionError`` rather than a decode error.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

#: ``(produced, consumed)`` -- what one manifest declares, un-normalized.
ManifestNames = tuple[set[str], set[str]]

#: Manifest filenames probed inside each child repo. Kept small and explicit;
#: a new manifest family is a name here, a reader below, and the
#: :data:`MANIFEST_READERS` entry that ties the two together.
PYPROJECT = "pyproject.toml"
GO_MOD = "go.mod"
PACKAGE_JSON = "package.json"
CSPROJ_SUFFIX = ".csproj"
PROTO_SUFFIX = ".proto"

_PROTO_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", re.MULTILINE)
_GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
#: A single-line ``require <path> <version>`` outside a block. The negative
#: lookahead rejects ``require (`` (a block opener), whose ``(`` would
#: otherwise be captured as a bogus module path.
_GO_REQUIRE_LINE_RE = re.compile(
    r"^\s*require\s+(?!\()(\S+)\s+\S+", re.MULTILINE
)

#: Every way a manifest parser here reports input it cannot handle.
#: ``tomllib.TOMLDecodeError`` and ``json.JSONDecodeError`` are both
#: ``ValueError``s; ``RecursionError`` is not, and is what *both* parsers
#: answer deep nesting with -- see the module docstring on why neither may
#: escape. Spelled once so a reader cannot be hardened against only half of it.
_UNPARSEABLE: tuple[type[BaseException], ...] = (ValueError, RecursionError)


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _strip_pep508_version(spec: str) -> str:
    """Return the bare distribution name from a PEP 508 dependency string.

    ``order-schema>=1.0.0`` -> ``order-schema``; ``requests[socks]`` ->
    ``requests``; ``foo ; python_version<'3.9'`` -> ``foo``. Only the leading
    name token is kept; extras, version specifiers, and environment markers
    are dropped.
    """
    token = re.split(r"[<>=!~;\[\s(]", spec.strip(), maxsplit=1)[0]
    return token.strip()


def read_pyproject(path: str) -> ManifestNames:
    produced: set[str] = set()
    consumed: set[str] = set()
    text = _read_text(path)
    if text is None:
        return produced, consumed
    try:
        data = tomllib.loads(text)
    except _UNPARSEABLE:
        return produced, consumed
    project = data.get("project")
    if not isinstance(project, dict):
        return produced, consumed
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
    return produced, consumed


def read_package_json(path: str) -> ManifestNames:
    """npm: the package this repo publishes, and its runtime dependencies.

    ``name`` is the published identity and ``dependencies`` the requirements
    an install resolves -- the two halves of what npm itself joins on. Three
    things are deliberately not read (ADR 0142 D5):

    * ``devDependencies``, ``peerDependencies`` and ``optionalDependencies``.
      A build-time tool, a contract with whoever installs this package, and a
      may-be-absent extra are none of them a run-time dependency on a sibling
      repo, and an edge would assert one.
    * ``workspaces``. A member of the same repo is not a cross-repo fact: the
      members' own manifests are read where they sit, and a dependency that
      closes inside one child is dropped by the resolver's no-self-edge rule.
    * a package that declares itself ``private`` -- npm's own "this is never
      published". Every workspace root and most applications carry it, and no
      sibling can depend on a name npm refuses to publish, so crediting one
      as a producer fabricates the edge that finding N2 was about. Read as
      npm reads it, any truthy value, and the direct analogue of the
      ``<IsPackable>false</IsPackable>`` guard in :func:`read_csproj`.
    """
    produced: set[str] = set()
    consumed: set[str] = set()
    text = _read_text(path)
    if text is None:
        return produced, consumed
    try:
        data = json.loads(text)
    except _UNPARSEABLE:
        return produced, consumed
    # Valid JSON is not a manifest: a top-level array or string parses fine
    # and declares nothing.
    if not isinstance(data, dict):
        return produced, consumed
    name = data.get("name")
    if isinstance(name, str) and name.strip() and not data.get("private"):
        produced.add(name.strip())
    deps = data.get("dependencies")
    if isinstance(deps, dict):
        for dep in deps:
            if isinstance(dep, str) and dep.strip():
                consumed.add(dep.strip())
    return produced, consumed


def read_csproj(path: str) -> ManifestNames:
    """MSBuild: what this project publishes, and what it references.

    The published name is ``<PackageId>`` when the project states one and the
    project *filename* otherwise -- NuGet's own default for ``dotnet pack``
    (via ``AssemblyName``), and for a library that ships no other manifest the
    only place the name exists at all. Two guards on that default, both about
    not fabricating a producer:

    * ``<IsPackable>false</IsPackable>`` is the one property that says this
      project publishes nothing; an application or test project is not the
      producer of a package named after its project file.
    * A ``<PackageId>`` still holding an unexpanded ``$(...)`` property
      reference is not a package name. MSBuild properties are not evaluated
      here, so the reference is dropped in favour of the filename default --
      which is what ``$(AssemblyName)``, the common case, resolves to.

    Conditions on ``<PropertyGroup>`` are likewise not evaluated; the last
    literal assignment wins, as it would in an unconditional build.
    """
    produced: set[str] = set()
    consumed: set[str] = set()
    text = _read_text(path)
    if text is None:
        return produced, consumed
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return produced, consumed
    package_id = ""
    packable = True
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "PackageReference":
            include = elem.attrib.get("Include", "").strip()
            if include:
                consumed.add(include)
        elif tag == "PackageId":
            value = (elem.text or "").strip()
            if value and "$(" not in value:
                package_id = value
        elif tag == "IsPackable":
            packable = (elem.text or "").strip().casefold() != "false"
    # ``removesuffix`` rather than ``Path.stem``: a file named exactly
    # ``.csproj`` is a dotfile whose stem is the whole name, and it has no
    # project name to contribute.
    name = package_id or Path(path).name.removesuffix(CSPROJ_SUFFIX).strip()
    if packable and name:
        produced.add(name)
    return produced, consumed


def read_proto(path: str) -> ManifestNames:
    produced: set[str] = set()
    text = _read_text(path)
    if text is None:
        return produced, set()
    match = _PROTO_PACKAGE_RE.search(text)
    if not match:
        return produced, set()
    pkg = match.group(1)
    # Drop a trailing ``.vN`` version segment: generated-code package names
    # (C# ``Acme.Platform.Order.Schema``, Python
    # ``acme.platform.order.schema``) mirror the proto package without the
    # API-version tail.
    parts = pkg.split(".")
    if len(parts) > 1 and re.fullmatch(r"v\d+", parts[-1]):
        parts = parts[:-1]
    if parts:
        produced.add(".".join(parts))
    return produced, set()


def read_go_mod(path: str) -> ManifestNames:
    produced: set[str] = set()
    consumed: set[str] = set()
    text = _read_text(path)
    if text is None:
        return produced, consumed
    module = _GO_MODULE_RE.search(text)
    if module:
        produced.add(module.group(1).strip())
    for line in _GO_REQUIRE_LINE_RE.findall(text):
        consumed.add(line.strip())
    _scan_go_require_blocks(text, consumed)
    return produced, consumed


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


class ManifestReader(NamedTuple):
    """One ecosystem's manifest reader, and the filenames it claims.

    *claims* is asked of a bare filename, so a family identified by an exact
    name (``pyproject.toml``) and one identified by a suffix (``.csproj``)
    join the registry the same way.

    *first_party_dirs* names directories this ecosystem publishes its **own**
    packages out of, where the scan's vendored-directory list says otherwise
    (:data:`weld.cross_repo._package_manifest_scan._NOT_OWN_DECLARATION_DIRS`).
    That list is a heuristic over directory *names*, and one name means
    opposite things in two ecosystems; an entry states its own layout here
    rather than the scan carrying a table of exceptions to itself. It exempts
    a name from this module's own list only -- never from the shared repo
    boundary, so no ecosystem can read itself back into ``node_modules``.
    """

    ecosystem: str
    claims: Callable[[str], bool]
    read: Callable[[str], ManifestNames]
    first_party_dirs: frozenset[str] = frozenset()


def _named(filename: str) -> Callable[[str], bool]:
    return lambda name: name == filename


def _suffixed(suffix: str) -> Callable[[str], bool]:
    return lambda name: name.endswith(suffix)


#: The ecosystems ``package_graph`` can read a dependency fact out of, in
#: dispatch order (first claim wins). Every entry contributes produced names
#: as well as consumed ones -- see the module docstring on why that is the
#: registry's admission rule rather than a convention. ``Cargo.toml`` and
#: ``pom.xml`` are recorded as future entries; demand is on record, code is
#: not.
MANIFEST_READERS: tuple[ManifestReader, ...] = (
    ManifestReader("python", _named(PYPROJECT), read_pyproject),
    ManifestReader("go", _named(GO_MOD), read_go_mod),
    # npm workspaces publish their members out of ``packages/`` -- the
    # directory name NuGet restores *third-party* packages into, and which
    # the scan therefore refuses to read a declaration from. Left applied to
    # ``package.json`` it would drop the producers of the commonest npm
    # monorepo layout, which is the recall loss G8 exists to close. npm's own
    # vendored tree is ``node_modules``, excluded for every ecosystem.
    ManifestReader(
        "npm",
        _named(PACKAGE_JSON),
        read_package_json,
        first_party_dirs=frozenset({"packages"}),
    ),
    ManifestReader("msbuild", _suffixed(CSPROJ_SUFFIX), read_csproj),
    ManifestReader("protobuf", _suffixed(PROTO_SUFFIX), read_proto),
)

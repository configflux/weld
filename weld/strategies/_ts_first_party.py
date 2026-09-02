"""Bind a first-party TypeScript import spelling to its file (ADR 0142 D3).

Two maps answer that question and they are built and consulted here, once per
discovery run: the npm workspace members this repository declares
(:mod:`weld.strategies._ts_workspace_members`) and the ``tsconfig`` path
aliases in scope for the importing file
(:mod:`weld.strategies._ts_tsconfig_paths`). Everything above this module sees
one call -- "what file, if any, does this specifier name?" -- and everything
below it stays a single-purpose reader.

**Aliases are consulted before workspace members**, which is TypeScript's own
order: ``paths`` are applied to a non-relative specifier before the resolver
ever looks in ``node_modules``, and a workspace member is reached *through*
``node_modules`` (npm symlinks it there). A repo that aliases a name it also
publishes as a member therefore gets the reading its own compiler would give.

An unanswered specifier is answered ``""`` and nothing else happens to it: the
existing origin classification (ADR 0042) still runs, the package node it
would have minted is still minted, and the graph is exactly what it was. That
is the whole containment story of this fix -- it can add a binding, it cannot
take one away.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._ts_module_files import resolve_module_file
from weld.strategies._ts_tsconfig_paths import AliasMap, nearest_alias_map, resolve_alias
from weld.strategies._ts_workspace_members import (
    WorkspaceMember,
    load_workspace_members,
)
from weld.strategies._typescript_origin import (
    is_relative_import,
    package_root_from_specifier,
)


class FirstPartyImports:
    """The first-party import spellings of one repository root.

    Built once per discovery run and consulted per import. Three caches make
    that affordable on a large workspace: the member map is read once, the
    per-directory ``tsconfig`` lookup is memoised across every file under an
    app, and each ``(specifier, importing directory)`` pair is answered from a
    memo the second time it is asked -- which on a real monorepo is most of
    the time, since the same handful of shared names is imported everywhere.
    """

    __slots__ = ("_root", "_members", "_alias_cache", "_resolved")

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._members: dict[str, WorkspaceMember] = load_workspace_members(self._root)
        self._alias_cache: dict[str, AliasMap | None] = {}
        self._resolved: dict[tuple[str, str], str] = {}

    def resolve(self, specifier: str, importer: str) -> str:
        """The repo-relative file *specifier* names, or ``""``.

        *importer* is the repo-relative path of the file the import was read
        from; it selects the ``tsconfig`` whose aliases are in scope, so the
        same spelling can legitimately answer differently in two apps.

        Relative specifiers are refused outright rather than resolved: the
        closure already binds those from the path index (and does it without
        touching the filesystem), so answering here would be a second, slower
        source of truth for the one case that already worked.
        """
        if not specifier or is_relative_import(specifier):
            return ""
        directory = importer.rpartition("/")[0]
        key = (specifier, directory)
        if key not in self._resolved:
            self._resolved[key] = self._resolve_uncached(specifier, importer)
        return self._resolved[key]

    def _resolve_uncached(self, specifier: str, importer: str) -> str:
        aliases = nearest_alias_map(self._root, importer, self._alias_cache)
        if aliases is not None:
            bound = resolve_alias(self._root, aliases, specifier)
            if bound:
                return bound
        return self._resolve_member(specifier)

    def _resolve_member(self, specifier: str) -> str:
        """The file a workspace member name (or a sub-path of one) names."""
        name = package_root_from_specifier(specifier)
        member = self._members.get(name)
        if member is None:
            return ""
        subpath = specifier[len(name):].lstrip("/")
        if subpath:
            # Sub-path imports (``@acme/shared/money``) are read against the
            # member's own directory. That is what they mean in a source
            # checkout; the published-package ``exports`` sub-path map, which
            # could redirect them, is not resolved here (see
            # ``_ts_workspace_members._export_entries``).
            return resolve_module_file(self._root, f"{member.directory}/{subpath}")
        return member.entry


def build_first_party_imports(root: Path) -> FirstPartyImports:
    """Construct the per-root index. One call site, named for readability."""
    return FirstPartyImports(root)


__all__ = ["FirstPartyImports", "build_first_party_imports"]

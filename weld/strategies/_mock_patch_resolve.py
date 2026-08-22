"""Resolve a mock-patch dotted string to a Python symbol node id.

The scanning half -- finding ``patch("...")`` calls in a test module and
turning resolved targets into edges -- lives in
:mod:`weld.strategies._mock_patch_python`, which is this module's only
caller. Split from it because the two halves answer different questions and
together outgrew the source line cap: that module asks "what did this test
try to patch", this one asks "does that name a symbol weld actually knows".

The bar for resolution is deliberately high. A patch target becomes an edge
only when its module prefix backs a real file under the project root *and*
the remaining attribute chain is a qualname the call-graph visitor reports
for that module. Everything else resolves to None and is dropped at the call
site, rather than emitted for
:func:`weld._discover_postprocess._clean_and_dedup_edges` to prune: an absent
target costs nothing when dropped, while one that resolves to the wrong real
symbol is a lie the graph then repeats to every consumer that reads it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._python_callgraph_visitor import _CallGraphVisitor

#: Cache of ``module dotted path -> (qualnames it defines, its import
#: table)``. Built once per ``extract()`` call and passed in by the caller so
#: a module patched by fifty test files is parsed once. A ``(frozenset(),
#: {})`` pair is a real cache entry meaning "resolved to nothing", so an
#: unresolvable module is not re-probed on every reference.
ModuleCache = dict


def new_cache() -> ModuleCache:
    """Return an empty per-run module cache for :func:`resolve_patch_target`."""
    return {}


def _callgraph_api() -> tuple:
    """Return ``(_symbol_id, _build_import_table)`` from the call-graph strategy.

    Imported lazily and from that module on purpose: the symbol-id shape and
    the import-table shape are each defined in exactly one place, and reusing
    them is what guarantees a resolved target names a node
    ``python_callgraph`` actually mints. The visitor next door defers its own
    import of these for the same reason.
    """
    from weld.strategies.python_callgraph import _build_import_table, _symbol_id

    return _symbol_id, _build_import_table


def is_dotted_python_path(dotted: str) -> bool:
    """Return whether *dotted* is a multi-segment dotted identifier path.

    The guard that keeps an unrelated ``client.patch("/api/v1/thing")`` or
    ``requests.patch("https://host/path")`` out of the resolver: those are
    patch calls by name but their arguments are not dotted Python paths.
    Requiring every segment to be an identifier also means a segment can
    never be ``..`` or contain a separator, so the module-path join below
    cannot escape the project root.
    """
    parts = dotted.split(".")
    if len(parts) < 2:
        return False
    return all(part.isidentifier() for part in parts)


def _module_file(root: Path, module: str) -> Path | None:
    """Return the file backing dotted *module* under *root*, or None.

    Probes the module form (``weld/_mcp_sdk.py``) then the package form
    (``weld/foo/__init__.py``), matching the two shapes
    ``python_callgraph._module_dotted_path`` inverts.

    Every segment is re-checked as an identifier before it becomes a path
    component. Targets arrive already validated, but a module name reached by
    following an import table has not been through that check, and a segment
    that is not an identifier is the only way this join could climb out of
    *root*. Cheaper to re-assert than to reason about every producer.
    """
    parts = module.split(".")
    if not all(part.isidentifier() for part in parts):
        return None
    candidates = (
        root.joinpath(*parts[:-1], parts[-1] + ".py"),
        root.joinpath(*parts, "__init__.py"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _absolute_imports_only(
    tree: ast.Module, table: dict[str, tuple[str, str]]
) -> dict[str, tuple[str, str]]:
    """Drop every table entry a *relative* import bound.

    ``ast`` does not resolve relative imports: ``from .core import thing``
    parses with ``module="core"`` and ``level=1``, so the shared import table
    records the defining module as ``core`` -- wrong in general, and in a
    project with a top-level ``core.py`` wrong *while still resolving on
    disk*. That is the failure mode worth engineering against: an absent
    target is dropped and costs nothing, but one that resolves to the wrong
    real symbol is a lie the graph repeats. Resolving these needs the
    importing file's package position, which this module does not model, so
    it declines rather than guesses.
    """
    relative: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
            relative.update(alias.asname or alias.name for alias in node.names)
    return {k: v for k, v in table.items() if k not in relative}


def _module_facts(
    root: Path, module: str, cache: ModuleCache
) -> tuple[frozenset[str], dict[str, tuple[str, str]]]:
    """Return *module*'s ``(qualnames, import table)``, memoized in *cache*.

    Both halves are delegated rather than re-walked here, so they are by
    construction what ``python_callgraph`` computes for the same file: the
    qualnames it mints nodes for (including nested ``ClassName.method``
    shapes) and the import table it resolves calls through. Re-implementing
    either rule would let them drift, and a drifted rule emits edges to
    symbol ids no strategy ever mints.

    A module that cannot be read or parsed (vanished mid-run, bad encoding,
    syntax error) resolves to empty: nothing matches, so no edge is emitted.
    Discovery never fails on a file it merely could not use (bd pt38).
    """
    cached = cache.get(module)
    if cached is not None:
        return cached
    path = _module_file(root, module)
    facts: tuple[frozenset[str], dict[str, tuple[str, str]]] = (frozenset(), {})
    if path is not None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            tree = None
        if tree is not None:
            _symbol_id, build_import_table = _callgraph_api()
            visitor = _CallGraphVisitor(module, {})
            visitor.visit(tree)
            facts = (
                frozenset(visitor.symbols),
                _absolute_imports_only(tree, build_import_table(tree)),
            )
    cache[module] = facts
    return facts


def _follow_import(
    module: str, qualname: str, imports: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    """Rewrite ``(module, qualname)`` through *module*'s import table.

    Returns the ``(defining_module, qualname)`` the leading segment was bound
    from, or None when the segment is not an imported name. ``from weld._git
    import is_git_repo`` in ``weld.doctor`` turns
    ``("weld.doctor", "is_git_repo")`` into ``("weld._git", "is_git_repo")``,
    and the ``as`` form resolves to the original name rather than the alias.

    A module alias (``import os`` -> ``("os", "")``) contributes no symbol of
    its own, so it only rewrites when there is a further attribute to carry:
    ``weld.doctor.sys`` yields None, while ``weld.x.os.replace`` becomes
    ``("os", "replace")`` -- which then fails the on-disk module check,
    because stdlib is not project source.
    """
    head, _, rest = qualname.partition(".")
    binding = imports.get(head)
    if binding is None:
        return None
    bound_module, bound_attr = binding
    if bound_module == module:
        return None
    if not bound_attr:
        return (bound_module, rest) if rest else None
    return bound_module, ".".join(filter(None, (bound_attr, rest)))


def resolve_patch_target(
    root: Path, dotted: str, cache: ModuleCache
) -> str | None:
    """Resolve a patch-target string to a symbol node id, or None.

    Splits *dotted* into ``(module, qualname)`` at every position, longest
    module prefix first so the most specific module wins, and accepts the
    first split whose module exists on disk and whose remaining attribute
    chain is a qualname that module defines.

    When the module is real but the name is one it *imported*, resolution
    follows the binding to the defining module. That hop is the whole point
    rather than a nicety: ``patch("weld.doctor.is_git_repo")`` patches a name
    ``weld.doctor`` bound from ``weld._git`` at import, and a name mocked at a
    re-binding site rather than at its definition is exactly the shape that
    produced bd kj4z. The emitted edge names the defining symbol while
    ``props.raw`` keeps the literal lookup path, so the two disagreeing is the
    readable signal that a rebinding sits between test and definition. The
    walk carries a ``seen`` set, so a re-export cycle terminates.

    Returns None -- emit nothing -- for every target that clears neither bar,
    which is the common case and deliberately so:

    * ``sys.stdout``, ``shutil.which``, ``pathlib.Path.glob`` -- stdlib, no
      project file backs the module prefix.
    * ``weld.doctor.sys`` -- the name is an imported *module*, which defines
      no symbol of its own.
    * ``weld.workspace_state.os.replace`` -- follows to ``os.replace``, which
      is stdlib and therefore not project source.
    * ``weld_query`` -- single segment, no module to resolve against.
    """
    if not is_dotted_python_path(dotted):
        return None
    symbol_id, _build_import_table = _callgraph_api()
    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        module = ".".join(parts[:split])
        qualname = ".".join(parts[split:])
        seen: set[tuple[str, str]] = set()
        while (module, qualname) not in seen:
            seen.add((module, qualname))
            qualnames, imports = _module_facts(root, module, cache)
            if qualname in qualnames:
                return symbol_id(module, qualname)
            followed = _follow_import(module, qualname, imports)
            if followed is None:
                break
            module, qualname = followed
    return None


__all__ = [
    "ModuleCache",
    "is_dotted_python_path",
    "new_cache",
    "resolve_patch_target",
]

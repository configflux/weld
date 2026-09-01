"""Read a module's import table the way ``sys.path[0]`` does.

``_build_import_table`` records the module name exactly as the source spells it.
That is the whole answer for a file inside a package, where Python 3's absolute
imports send every bare name to ``sys.path`` proper. It is the wrong answer for
a file in a directory that is *not* a package: running ``python tools/lint.py``
(or a Bazel ``py_binary``/``py_test`` whose main sits there) puts that directory
on ``sys.path`` ahead of everything else, so a bare module name binds to the file
next to it.

``tools/lint_pinned_citations.py`` is the measured case (bd sigz2). It opens
``from lint_test_hygiene import
changed_test_lines`` and calls it bare. No top-level ``lint_test_hygiene`` module
exists anywhere; the file it means is ``tools/lint_test_hygiene.py``, which weld
names ``tools.lint_test_hygiene``. Reading the spelling literally resolved the
call to ``symbol:py:lint_test_hygiene:changed_test_lines`` -- a *confident* edge
naming a module that exists under no spelling, minted ``origin=external`` -- so
the real definition collected no caller edge and ``wd callers`` on the primitive
behind six diff-scoped lints answered one caller of three. That is the ADR 0042
first-party-as-external failure and bd 1m1g9's fabricated-id failure at once: a
miss is recoverable, a confident wrong answer is what a reader acts on.

Rewriting the table rather than the resolver is deliberate. Three separate
branches of ``_ExprResolutionMixin._resolve_expr_target`` read the module slot --
the bare-name import lookup, the module-alias attribute call, and the deferral
that hands ``props.import_attr`` to :mod:`weld._graph_closure_import_attr` -- and
they are all reading the same fact. Correcting it once, where the table is built,
is what keeps them from drifting; in particular the closure never has to know
this rule existed, because the hint it reads back already names the real module.

What the rule demands
---------------------
Both conditions have to hold, and each is refused by a shape live in this repo.

1. **The importing file's own directory has no ``__init__.py``.** Inside a real
   package a bare name is an absolute import and cannot mean a sibling.
   ``weld/providers/anthropic.py`` opens ``from anthropic import Anthropic`` --
   the third-party SDK -- and ``weld/strategies/tree_sitter.py`` opens ``import
   tree_sitter``. Both name their own file; without this gate the rule resolves
   each module to *itself*, which is worse than the bug it fixes. The check is a
   filesystem probe rather than a glob-membership test because an ``__init__.py``
   need not be inside the configured glob, and "is this directory a package" must
   not vary with how the glob was written.

2. **The candidate sibling is a module this glob owns.** ``glob_modules`` is
   derived from the whole resolved glob on both discover paths, so it asks the
   same question of a full and an incremental run -- the property ADR 0074
   equivalence turns on, and the reason the submodule reading one branch above
   keys on it rather than on ``project_modules`` (bd yhz70). Without this check a
   script directory's ``from yaml import safe_load`` would resolve under a
   ``<dir>.yaml`` that is not there, trading one fabricated population for a
   larger one. It also bounds the rule to what the graph can actually hold: a
   sibling in another glob, or in none, keeps the literal spelling rather than
   pointing at a module no node represents.

When both hold, the sibling wins over any same-named module elsewhere, because
that is what ``sys.path[0]`` does -- ahead of the stdlib included. Measured on
this repo: 194 import statements take the rewrite, all of them under ``tools/``,
and every one names a file sitting beside its importer.

Scope: a bare (``level == 0``) import only. An explicit relative import
(``from .helper import x``) is the same family read from the other end, and it
is answered before this rule ever sees the table -- by
:mod:`weld.strategies._python_relative_import`, inside ``_build_import_table``
where ``node.level`` is still in hand (bd ``zr486``). That split is not
cosmetic: ``level`` is written in the source, so that half is arithmetic with
nothing to infer and no ``glob_modules`` bound to earn, while this half is a
reading of ``sys.path`` that has to justify itself against the two refusals
above. By the time a table reaches this function, any relative-bound entry
already names the module Python would import; the ``glob_modules`` membership
test below is what keeps this rule from re-prefixing one, since an
already-absolute name is not a sibling of anything.
"""

from __future__ import annotations

from pathlib import Path

#: The marker whose presence makes a directory a package -- and so makes a bare
#: import inside it an absolute one. A namespace package (PEP 420) has no such
#: file, which is exactly the case this module reads as script-relative.
_PACKAGE_MARKER = "__init__.py"


def normalize_sibling_imports(
    import_table: dict[str, tuple[str, str]],
    *,
    module_path: str,
    source_dir: Path,
    glob_modules: frozenset[str],
) -> dict[str, tuple[str, str]]:
    """Return *import_table* with script-relative module names made dotted.

    ``module_path`` is the importing file's own dotted path, whose leading
    segments are the package prefix a bare name resolves under. ``source_dir``
    is the directory that file sits in -- probed for ``__init__.py``, never
    walked. ``glob_modules`` is the dotted-module set of the whole resolved
    glob, the same value on a full and an incremental discover.

    Returns the table itself, unallocated, whenever no entry qualifies -- which
    is every file inside a package and every file whose imports all name
    something other than a sibling.
    """
    if not _is_script_directory(module_path, source_dir):
        return import_table
    package = module_path.rsplit(".", 1)[0]
    rewritten = {
        local: (f"{package}.{module}", attr)
        for local, (module, attr) in import_table.items()
        if f"{package}.{module}" in glob_modules
    }
    if not rewritten:
        return import_table
    return {**import_table, **rewritten}


def _is_script_directory(module_path: str, source_dir: Path) -> bool:
    """True when a bare import in this file binds against its own directory.

    Two refusals, in the order they cost. A module at the repository root has
    no package prefix, and needs none: its siblings' dotted paths are already
    their bare file stems, so the spelling the source uses is the answer and
    there is nothing to correct. And a directory carrying ``__init__.py`` is a
    package, where a bare name is an absolute import that cannot reach a
    sibling -- see the module docstring for the two live modules in this repo
    that would otherwise resolve to themselves.
    """
    if "." not in module_path:
        return False
    return not (source_dir / _PACKAGE_MARKER).exists()


__all__ = ["normalize_sibling_imports"]

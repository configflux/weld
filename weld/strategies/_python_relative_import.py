"""Resolve an explicit relative import the way the interpreter resolves it.

``_build_import_table`` used to read ``node.module`` off every
``ast.ImportFrom`` and drop ``node.level`` on the floor. So ``from .helper
import work`` inside ``pkg/caller.py`` recorded ``("helper", "work")`` and a
bare ``work()`` resolved to ``symbol:py:helper:work`` -- a *confident* edge
naming a module that exists under no spelling, minted ``origin=external``,
while the real ``pkg/helper.py`` definition collected no caller edge at all
(bd ``zr486``).

That is the same failure as the bare-name half next door in
:mod:`weld.strategies._python_source_root_import` (bd ``sigz2``) and as bd
``1m1g9``'s fabricated ``symbol:py:<module>:<method>``: a miss is recoverable,
a confident wrong answer is what a reader acts on. It is also the half that
matters most *outside* this repo -- the walked globs here hold no relative
import at all, while a relative import is the dominant intra-package spelling
in most Python libraries, so every such codebase weld discovers was getting
fabricated ids for its own internal calls.

Where the two halves differ is in what they are allowed to assume. The sibling
rule *infers*: a bare name might mean the file next door or a third-party
package, and it must earn the rewrite against two conditions and decline
otherwise. Here ``level`` is written in the source, so there is nothing to
infer and nothing to calibrate -- the answer is arithmetic on the importing
file's own package, and this module is a transcription of CPython's
``importlib._bootstrap._resolve_name`` plus the ``_sanity_check`` that guards
it. Both refusals below are that sanity check, not a policy this repo chose.

Correcting the table rather than the resolver is the same call
:mod:`_python_source_root_import` documents and for the same reason: three
separate branches of ``_ExprResolutionMixin._resolve_expr_target`` read the
module slot,
and one of them hands ``props.import_attr`` on to
:mod:`weld._graph_closure_import_attr`. Fixing the fact once, where the table
is built, is what keeps them from drifting -- and the closure never has to
learn this rule existed, because the hint it reads back already names the real
module. Unlike the sibling rule, this one cannot be a rewrite *of* the finished
table: the table's module slot is the only thing a later pass sees, and
``level`` is gone by then. So it runs inside ``_build_import_table``, which is
why that function now takes the importing file's package.

Deliberately *not* bounded by ``glob_modules``. The sibling rule needs that
bound because it is guessing and a wrong guess mints a new fabricated
population; this one names the module Python itself would import, whether or
not the configured glob happens to own it. Whether that module is first-party
is a separate question, answered where it already is -- ``project_modules``,
the run-level union, and the closure pass.

``from . import x`` (``node.module is None``) was skipped outright before, so a
bare call of ``x`` fell to the ``symbol:unresolved:`` sentinel. It resolves to
``(package, "x")`` here, which is byte-for-byte the shape ``from pkg import x``
already produces -- so the submodule-versus-value reading of that entry stays
exactly where it lives today, in the visitor and the closure, and this module
adds no new ambiguity for them to settle.
"""

from __future__ import annotations

from pathlib import Path

#: The filename whose presence makes a directory a package. A file *named* this
#: is the package itself, so its ``__package__`` is its own dotted path rather
#: than its parent's -- the one asymmetry ``package_of`` exists to carry.
_PACKAGE_INIT = "__init__.py"


def package_of(module_path: str, source_path: Path) -> str:
    """Return the dotted package *source_path* belongs to -- its ``__package__``.

    ``module_path`` is the file's own dotted path as
    ``python_callgraph._module_dotted_path`` derives it, which collapses
    ``foo/__init__.py`` to ``foo`` and so cannot be inverted on its own:
    ``foo`` is either the package (whose ``__package__`` is ``foo``) or a
    top-level module ``foo.py`` (whose ``__package__`` is empty). *source_path*
    settles it, by name only -- the file is never read and the directory is
    never probed.

    Returns ``""`` for a module sitting at the root under no package. A
    relative import there is an error in real Python, and
    :func:`absolute_module` refuses it for exactly that reason.
    """
    if source_path.name == _PACKAGE_INIT:
        return module_path
    parent, _, _ = module_path.rpartition(".")
    return parent


def absolute_module(module: str | None, level: int, *, package: str) -> str | None:
    """Return the module an explicit relative import names, or None to refuse.

    *module* and *level* are ``ast.ImportFrom``'s own fields and *package* is
    :func:`package_of` for the importing file. ``level == 0`` is not this
    function's business -- the caller keeps the absolute spelling -- so it
    refuses that too rather than inventing an answer for it.

    The arithmetic is CPython's::

        bits = package.rsplit(".", level - 1)
        if len(bits) < level: raise ImportError(...)
        base = bits[0]

    so ``from .helper import work`` in ``pkg.caller`` (package ``pkg``) yields
    ``pkg.helper``, ``from ..shared import top`` in ``pkg.sub.deep`` (package
    ``pkg.sub``) yields ``pkg.shared``, and ``from . import x`` in ``pkg.caller``
    yields ``pkg`` -- the package itself, with ``x`` left in the table's attr
    slot exactly as ``from pkg import x`` leaves it.

    Two refusals, both of them CPython's, and both returning None so the caller
    records nothing and the call falls to the ``symbol:unresolved:`` sentinel:

    * **No package at all.** ``_sanity_check`` raises "attempted relative import
      with no known parent package" for a top-level module, and there is no
      dotted name a rewrite could produce anyway.
    * **A level that walks past the top-level package.** ``from .. import x`` in
      ``pkg.caller`` is "attempted relative import beyond top-level package".
      Answering it would mean minting a name above the root -- a fresh instance
      of the fabrication this module removes, so refusing is not a fallback
      here, it is the correct answer.
    """
    if level <= 0 or not package:
        return None
    bits = package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    return f"{base}.{module}" if module else base


__all__ = ["absolute_module", "package_of"]

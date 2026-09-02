"""Read a module's import table the way ``sys.path[0]`` does.

``_build_import_table`` records the module name exactly as the source spells
it. That is the right answer only when the directory Python searches first
happens to be the repository root. It is the wrong answer everywhere else,
because a dotted id derived from a repo-relative path and a name written
against a *source root* are two spellings of one module, and the graph keys
nodes on the first while call sites are written in the second.

Two shapes, one predicate. ``tools/lint_pinned_citations.py`` opens ``from
lint_test_hygiene import changed_test_lines`` and calls it bare (bd ``sigz2``);
no top-level ``lint_test_hygiene`` module exists anywhere, and the file it
means is ``tools/lint_test_hygiene.py``, which weld names
``tools.lint_test_hygiene``. ``src/acme_notify/runner.py`` opens ``from
acme_notify.config import load_config`` (bd ``z98p7``, field-eval finding M2);
no top-level ``acme_notify`` package exists in that tree either, and the file
it means is ``src/acme_notify/config.py``, which weld names
``src.acme_notify.config``. Reading either spelling literally resolved the call
to a *confident* edge naming a module that exists under no spelling, minted
``origin=external``, so the real definition collected no caller edge and ``wd
callers`` on it answered "no callers" while a node with no file held them. That
is the ADR 0042 first-party-as-external failure and bd ``1m1g9``'s
fabricated-id failure at once: a miss is recoverable, a confident wrong answer
is what a reader acts on.

The two are the same question -- *which directory does Python put on
``sys.path``* -- asked from inside a script directory and from inside a package
tree, and ADR 0143 answers both with one rule rather than two. Walk up from the
importing file's own directory while ``__init__.py`` is there; the first
ancestor without one is the source root, and its repo-relative dotted path is
the prefix a written name resolves under. The script-directory case is that
walk terminating immediately, so the shipped behaviour is this rule's
degenerate case and not a second rule beside it. An empty prefix -- the
repository root is itself the source root -- makes the whole thing a no-op,
which is why most of this checkout never sees it.

Rewriting the table rather than the resolver is deliberate. Three separate
branches of ``_ExprResolutionMixin._resolve_expr_target`` read the module slot --
the bare-name import lookup, the module-alias attribute call, and the deferral
that hands ``props.import_attr`` to :mod:`weld._graph_closure_import_attr` -- and
they are all reading the same fact. Correcting it once, where the table is built,
is what keeps them from drifting; in particular the closure never has to know
this rule existed, because the hint it reads back already names the real module.
ADR 0143 D6 turns on exactly that: the re-export facade walk and the
``import_attr`` classmethod case both get their answer from a normalized table,
with no edit of their own.

What the rule demands
---------------------
Three refusals, in the order they cost, and each is a shape live in this repo.

1. **A literal spelling this glob already owns wins.** An ancestor-relative
   reading may only answer a name that resolves to nothing otherwise. Without
   it the rule would re-prefix a name that is already absolute and already
   correct -- including every entry :mod:`_python_relative_import` has just
   resolved -- and there is no evidence in the source to prefer the
   ancestor-relative reading over one the graph can already satisfy.

2. **The candidate must be a module this glob owns.** ``glob_modules`` is
   derived from the whole resolved glob on both discover paths, so it asks the
   same question of a full and an incremental run -- the property ADR 0074
   equivalence turns on, and the reason the submodule reading one branch above
   keys on it rather than on ``project_modules`` (bd ``yhz70``). Without this
   check a script directory's ``from yaml import safe_load`` would resolve
   under a ``<dir>.yaml`` that is not there, trading one fabricated population
   for a larger one. It also bounds the rule to what the graph can actually
   hold: a module in another glob, or in none, keeps the literal spelling
   rather than pointing at an id no node represents. It is what makes the
   walk's over-strip harmless -- a prefix taken one level too high names a
   module that does not exist, and the rule declines.

3. **A candidate equal to the importing file's own module is refused.** No
   import statement names the file it is written in, so a candidate that does
   is arithmetic, not evidence. The live shape is a package ``__init__`` that
   names its own package -- ``import pkg`` inside ``src/pkg/__init__.py``,
   where the candidate ``src.pkg`` is this file's own module and *is* in the
   glob, so the two refusals above both pass it. Resolving it would mint a
   symbol inside the importing file's own module for a name that file never
   defines: a definite-looking id fabricated out of the rule's own arithmetic,
   which is worse than the external stub the literal reading gives.

When all three pass, the source root wins over any same-named module elsewhere,
because that is what ``sys.path[0]`` does -- ahead of the standard library and
of a third-party distribution alike, and there is deliberately no "leave
stdlib names alone" shortcut: ``scripts/json.py`` beside its importer already
wins today (bd ``sigz2``, pinned by
``weld_python_callgraph_sibling_import_test``), and a rule with one predicate
cannot answer that differently one level up. ADR 0143 D3 lists a stdlib
refusal among the guards it carries over; that bullet restates the guard the
generalization *removes* (a bare name inside a package being an absolute
import, which is precisely why the walk has to look above the package), and
adopting it as a fourth refusal would reverse a shipped, deliberately-pinned
contract while the same decision claims the shipped rewrites are made
identically. Scope: this rule reads an *absolute* import only. An
explicit relative import (``from .helper import x``) is the same family read
from the other end, and it is answered before this rule ever sees the table --
by :mod:`weld.strategies._python_relative_import`, inside
``_build_import_table`` where ``node.level`` is still in hand (bd ``zr486``).
That split is not cosmetic: ``level`` is written in the source, so that half is
arithmetic with nothing to infer and no ``glob_modules`` bound to earn, while
this half is a reading of ``sys.path`` that has to justify itself against the
three refusals above. Refusal 1 is what keeps this rule from re-prefixing a
name that half already resolved.

One thing this rule is *not*: an identity. Nothing here renames a node. ADR
0143 D1 keeps ``symbol:`` ids path-derived and moves only the reference, and
D2 records that the source root is never stored -- it is an admission rule
evaluated per file, so a wrong walk can only fail to resolve a call, never
change what a node is or collide two definitions onto one id.

Incremental equivalence (ADR 0143 D5): ``__init__.py`` presence is part of the
resolution basis above, so creating or deleting one moves the source root of
every file beneath it. Only the marker itself is dirty on such a round, so the
orchestrator widens the dirty scope with that directory's subtree before any
strategy runs -- see :mod:`weld._discover_incremental_merge`. The bound this
rule inherits from that: a marker no source glob resolves is invisible to the
file-hash delta, exactly as it is invisible to every other incremental basis.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

#: The marker whose presence makes a directory a package -- and so makes it
#: something Python imports *through* rather than *from*. A namespace package
#: (PEP 420) has no such file, which is exactly the case this module reads as
#: a source root.
_PACKAGE_MARKER = "__init__.py"


def normalize_source_root_imports(
    import_table: dict[str, tuple[str, str]],
    *,
    module_path: str,
    rel_path: str,
    source_dir: Path,
    glob_modules: frozenset[str],
) -> dict[str, tuple[str, str]]:
    """Return *import_table* with written module names made path-derived.

    ``module_path`` is the importing file's own dotted path, used only by the
    self-refusal. ``rel_path`` is that file's repo-relative path, whose parent
    segments are walked in lockstep with ``source_dir`` -- the directory the
    file sits in, probed for ``__init__.py`` on the way up. Deriving the
    segments from the path rather than from ``module_path`` is what keeps the
    two walks aligned for a file whose stem contains a dot, where the dotted
    path carries a segment the directory chain does not.  ``glob_modules`` is
    the dotted-module set of the whole resolved glob, the same value on a full
    and an incremental discover.

    Returns the table itself, unallocated, whenever no entry qualifies -- which
    is every file whose source root is the repository root, and every file
    whose imports all name something the three refusals decline.
    """
    prefix = _source_root_prefix(rel_path, source_dir)
    if not prefix:
        return import_table
    rewritten: dict[str, tuple[str, str]] = {}
    for local, (module, attr) in import_table.items():
        candidate = _source_root_module(module, prefix, module_path, glob_modules)
        if candidate is not None:
            rewritten[local] = (candidate, attr)
    if not rewritten:
        return import_table
    return {**import_table, **rewritten}


def _source_root_prefix(rel_path: str, source_dir: Path) -> str:
    """Return the dotted path of the first ancestor directory that is a source root.

    Walks the file's own directory upward while ``__init__.py`` is present,
    dropping one repo-relative segment per step so the dotted answer and the
    probed directory never disagree. The walk is bounded by the repository
    root: when the segments run out there is nothing left to prefix with, and
    probing above the root would read a directory that is not part of this
    tree. An empty answer means the source root is the root itself, where a
    written module name and a path-derived one already coincide.
    """
    segments = list(PurePosixPath(rel_path).parent.parts)
    directory = source_dir
    while segments and (directory / _PACKAGE_MARKER).exists():
        segments.pop()
        directory = directory.parent
    return ".".join(segments)


def _source_root_module(
    module: str,
    prefix: str,
    module_path: str,
    glob_modules: frozenset[str],
) -> str | None:
    """Return the path-derived module *module* names under *prefix*, or None.

    The three refusals of the module docstring, in the order they cost: a
    literal spelling the glob already owns, a candidate the glob does not own,
    and a candidate that is the importing file itself. None means "leave this
    entry exactly as the source spelled it".
    """
    if not module:
        return None
    if module in glob_modules:
        return None
    candidate = f"{prefix}.{module}"
    if candidate not in glob_modules:
        return None
    if candidate == module_path:
        return None
    return candidate


__all__ = ["normalize_source_root_imports"]

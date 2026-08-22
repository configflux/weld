"""Single source of truth for "does this ``.py`` file become a file anchor?".

``python_module`` owns the rule: every matched Python source file becomes
a ``file:`` node *except* an ``__init__.py`` with no exported surface
(and any file that fails to parse). Those exceptions are deliberate --
an empty or purely-re-exporting-nothing ``__init__.py`` has no queryable
surface, and ADR 0008 records such files in ``files_with_no_nodes``
rather than inventing an anchor for them.

The rule stopped being private to one strategy once ``python_package``
needed the same answer: a package node exists to parent file anchors, so
a directory that contributes none should not produce one (issue
``ddsy`` -- ``package:python:weld.demos`` was emitted for a directory
whose only module is a docstring-only ``__init__.py``, leaving a node
with no edges in either direction).

Restating the rule inside ``python_package`` would recreate exactly the
failure ADR 0041 § Layer 3 was written to stop: a skip rule that lived in
one strategy, drifted from its counterpart, and produced structurally
orphaned nodes. Both callers therefore import from here, and a change to
the anchor rule is a change to this module.

Alongside the rule sit the module-level reads it is built on -- what a parsed
Python module *declares about itself*: its exports, and (bd ph1g) the opening
paragraph of its docstring. Both are read once from a tree the caller already
holds, so no file is parsed twice. bd p6ke adds the same docstring reduction
one level down, for a single symbol's own ``FunctionDef`` / ``AsyncFunctionDef``
/ ``ClassDef`` rather than the ``Module`` -- :func:`symbol_summary`, called
by :mod:`weld.strategies.python_callgraph` on a tree it too already holds.

The paragraph/collapse/bound reduction itself (:data:`MAX_SUMMARY_LEN`,
formerly the private ``_summary_from_docstring``) has moved to
:mod:`weld.strategies._doc_summary` as :func:`~weld.strategies._doc_summary.collapse_summary`
(bd 5038-009x): it never touched ``ast``, only the already-extracted docstring
text, so it is the shared home for every ``props.summary`` writer -- this
module's two Python-specific readers below, and
:mod:`weld.strategies._ts_doc_comments`'s Go/Rust doc-comment readers.
:data:`MAX_SUMMARY_LEN` is re-exported here unchanged so existing callers of
``weld.strategies._python_anchor.MAX_SUMMARY_LEN`` keep resolving.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Sequence

from weld.strategies._doc_summary import (
    MAX_SUMMARY_LEN,  # noqa: F401 -- re-exported for callers
    collapse_summary,
)


def module_summary(tree: ast.Module) -> str:
    """Return the opening paragraph of *tree*'s module docstring (bd ph1g).

    The one sentence the module's author wrote to say what the module is,
    whitespace-collapsed onto a single line and bounded by
    :data:`MAX_SUMMARY_LEN`. Empty when the module has no docstring.

    Only the opening paragraph, because only the opening paragraph is a
    summary. The rest is prose, and prose in the query index is an essay
    describing a module that already said what it is in line one -- the
    tradeoff :mod:`weld.query_index` states when it sends prose to
    ``description`` and keeps ``keywords`` short.

    This is deliberately *not* ``props.description``: that field is enrichment
    output (``weld.enrich`` writes it, and
    ``enrichment_persistence.FINGERPRINT_EXCLUDED_PROPS`` excludes it from the
    node fingerprint for exactly that reason). A summary is structural input --
    read from the source on every discover, like ``exports`` beside it.

    See :func:`symbol_summary` for the same contract one level down, on a
    single symbol's own docstring rather than the module's.
    """
    return collapse_summary(ast.get_docstring(tree) or "")


def symbol_summary(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    """Return the opening paragraph of *node*'s own docstring (bd p6ke).

    Identical paragraph/collapse/bound contract to :func:`module_summary`,
    read one level down: ``ast.get_docstring`` accepts a ``FunctionDef`` /
    ``AsyncFunctionDef`` / ``ClassDef`` exactly as it does a ``Module``, so a
    function or class summarises itself the same way a module does. bd
    ph1g gave ``file:`` nodes a summary from the module docstring and left
    ``symbol:`` nodes with none, so a name stated only in a function's own
    docstring -- nowhere in its signature -- stayed unreachable; this is the
    read half of closing that gap (the write half is
    :mod:`weld.strategies.python_callgraph`, which calls this once per
    defined symbol on the tree it already parsed, no second parse).
    """
    return collapse_summary(ast.get_docstring(node) or "")


def module_exports(tree: ast.Module) -> list[str]:
    """Return the top-level exported names of a parsed module.

    Exports are top-level classes plus top-level functions (sync or
    async) whose name does not start with an underscore. Classes are
    kept regardless of a leading underscore, matching the long-standing
    ``python_module`` behaviour. Order follows source order, which is
    what ``python_module`` records on ``props.exports``.
    """
    exports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            exports.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                exports.append(node.name)
    return exports


def yields_file_anchor(filename: str, exports: Sequence[str]) -> bool:
    """Return True when a file with *exports* becomes a ``file:`` anchor.

    *filename* is the basename (``__init__.py``, ``service.py``). The
    single exception is an ``__init__.py`` with no exports; every other
    module anchors even when it exports nothing (a constants-only module
    is still a real file with a real surface).

    Callers that already hold a parsed tree use this directly so no file
    is parsed twice; callers holding only a path use
    :func:`path_yields_file_anchor`.
    """
    return bool(exports) or filename != "__init__.py"


def path_yields_file_anchor(path: Path) -> bool:
    """Parse *path* and report whether ``python_module`` would anchor it.

    A file weld cannot read or parse yields no anchor: ``python_module``
    swallows exactly the same errors and emits nothing for it, so
    promising an anchor here would leave the caller expecting a node that
    never arrives.

    That includes read errors (bd pt38). The two run over one glob in one
    run -- ``python_package`` asks this predicate about the very files
    ``python_module`` is anchoring -- so letting a read error propagate
    here only moved the crash one strategy over: the file that vanished
    between the walk and the read took the run down through
    ``python_package`` instead. Answering ``False`` is also simply the
    true answer, because a file that is gone anchors nothing.
    """
    tree = _parse(path)
    if tree is None:
        return False
    return yields_file_anchor(path.name, module_exports(tree))


def _parse(path: Path) -> ast.Module | None:
    """Return the parsed module for *path*, or ``None`` when unreadable.

    Catches what ``python_module`` catches, deliberately: this predicate
    exists to answer for that strategy, and an answer that raises where
    the strategy would have skipped is not the same answer.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None

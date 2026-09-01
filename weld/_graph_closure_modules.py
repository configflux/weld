"""Module-name candidates for the closure's import resolution.

:func:`weld.graph_closure._link_imports` turns every ``imports_from`` entry
into a ``depends_on`` edge. It asks this module for the names that entry
could be known by, looks each up in the closure's module index, and mints an
external ``package:<lang>:<name>`` node only when every candidate misses. So
what is offered here decides which imports are understood to be first-party
-- and an import that should have resolved does not merely lose an edge, it
gains a second, ``external=True`` representation of a module the graph
already holds.

That is the shape of field-eval finding N4. The index keys a Python file node
by its full repo-relative path (``src.acme_notify.config``), but the source
says ``from acme_notify.config import load_config``, because ``src`` is the
source root and not part of the module path. The literal name missed, and
roughly 894 spurious external nodes appeared on one real workspace (1454 in
this one).

:func:`python_source_root_candidates` closes that gap by walking the
importer's own **ancestor directories**, deepest first, prefixing each onto
the imported name. That one rule covers both shapes the evaluation hit -- an
importer whose own directory is the source root (``src/main.py`` importing
``broker``) and one nested in a package below it (``src/acme_notify/runner.py``
importing ``acme_notify.config``) -- without having to *detect* where the
source root is. Detecting it would mean looking for ``__init__.py``, and that
marker file is routinely empty, so it has no file node in the graph to find.

Three constraints keep the inference from paying for itself with worse edges:

* **Stdlib-rooted names are never inferred against.** Prefixing an ancestor
  directory is the Python-2 implicit-relative rule, and under Python 3 a
  sibling module does not shadow the standard library. This repo has
  ``weld/trace.py`` and ``weld/warnings.py``, so an unguarded walk really
  would rewrite ``import warnings`` in every ``weld/*.py`` onto the wrong node.
* **An inferred name must land on a ``file:`` node.** The index also holds
  speculative ``symbol:`` stubs keyed by their declared module, and a guess
  that lands on one of those has invented a relationship rather than found
  one. Measured on this repo, dropping this rule pointed ``import
  collections.abc`` at ``symbol:py:collections:Counter`` -- an arbitrary
  sibling of the right module.
* **The whole name is resolved before its parent is.** A referenced-symbol
  capture spells ``from a.b import C`` as ``a.b.C``, so the module has to be
  recoverable by dropping the last segment -- but ``import weld.graph_closure``
  must not fall back to ``weld`` while a node for the full name exists. Hence
  two ordered groups rather than one list.

Nothing here changes the *name* an unresolved import is minted under: that
stays :func:`external_package_name`, so the cross-repo package nodes a sibling
repo's manifest joins against keep the ids they already had.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from weld.strategies._python_origin import is_stdlib_module


def module_key(name: str, language: str) -> str:
    """Normalise an import name into this language's module spelling."""
    value = name
    if language == "python":
        return value.replace("/", ".")
    if language in {"typescript", "go", "rust"}:
        return value.strip("/")
    if language == "java" and value.endswith(".*"):
        return value[:-2]
    return value


def python_dotted_module(rel_path: str) -> str:
    """The dotted module a Python file's symbol ids are minted under.

    ``pkg/mod.py`` -> ``pkg.mod``; ``pkg/__init__.py`` -> ``pkg``; anything
    that is not a ``.py`` path -> ``""``. The empty answer is what makes this
    safe to run over a whole path index: only Python files have a Python module
    name, and a same-named ``.ts`` file next door is not one.

    Lives here, beside the closure's other module-name rules, because what it
    answers has to agree exactly with the ids ``python_callgraph`` mints --
    ``weld._graph_closure_reexport`` looks a definition up by this name, and a
    spelling that disagreed would silently find nothing. The strategy keeps its
    own copy over ``pathlib.Path``, since it derives from a live filesystem
    path where this one reads ``props.file`` (POSIX-spelled by the graph);
    ``weld_graph_closure_reexport_guards_test`` pins the two against each other
    rather than leaving the agreement to inspection.

    ``graph_closure._index_python_module`` deliberately keeps its own inline
    derivation: it also keys a slash spelling built from the path's own parts,
    which is not the dotted name with its dots swapped once a filename contains
    a dot.
    """
    path = PurePosixPath(rel_path)
    if path.suffix != ".py":
        return ""
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def python_module_index(path_index: dict[str, str]) -> dict[str, str]:
    """Map every Python file node in *path_index* to its dotted module name.

    The closure's answer to "is this dotted path a first-party Python module",
    and the only one two separate rules are allowed to have: both
    :mod:`weld._graph_closure_reexport` (which follows a facade's imports) and
    :mod:`weld._graph_closure_import_attr` (which decides whether an imported
    name was a submodule) ask it, and a second derivation that disagreed would
    silently find nothing.

    Deliberately narrower than ``graph_closure._module_index``, which is built
    later and also keys modules to speculative ``symbol:`` stubs and to
    same-named files of other languages -- neither of which is a Python module
    whose imports could be followed or whose members could be named.

    ``setdefault`` over a sorted walk, so two paths that read as one module
    resolve the same way whatever order the nodes arrived in.
    """
    index: dict[str, str] = {}
    for rel_path, node_id in sorted(path_index.items()):
        module = python_dotted_module(rel_path)
        if module:
            index.setdefault(module, node_id)
    return index


def module_candidates(name: str, language: str, source_file: str) -> list[str]:
    """Names *name* is directly indexed under, best candidate first."""
    value = module_key(name, language)
    candidates = [value, value.replace("/", ".")]
    if language == "rust":
        candidates.extend(_rust_candidates(value, source_file))
    return _dedupe(candidates)


def python_source_root_candidates(
    name: str, language: str, source_file: str,
) -> tuple[list[str], list[str]]:
    """Source-root-relative readings of *name*, as two ordered groups.

    Returns ``(before, after)``: names to try *before* the direct
    :func:`module_candidates` lookup, and names to try *after* it. Both are
    guesses and the caller must accept a hit only on a ``file:`` node.

    The split is the ordering rule in one place. ``before`` reads the whole
    imported name -- first literally, then against each ancestor directory --
    and outranks the direct lookup because the direct lookup will settle for
    a speculative stub. Taking the literal spelling first inside that group
    is what keeps Python's absolute-import semantics: an ancestor-relative
    reading may only answer a name that resolves to no real file otherwise.
    ``after`` re-reads the name as *module plus member* by dropping its last
    segment; that is strictly less specific, so it must lose to anything the
    whole name found.
    """
    if language != "python":
        return [], []
    dotted = module_key(name, language).strip(".")
    # A name rooted in the standard library is resolved by Python against
    # sys.path, never against the importer's directory.
    if not dotted or is_stdlib_module(dotted):
        return [], []
    prefixes = _ancestor_prefixes(source_file)
    before = [dotted, *(f"{prefix}.{dotted}" for prefix in prefixes)]
    parent, _, member = dotted.rpartition(".")
    if not (parent and member):
        return _dedupe(before), []
    after = [f"{prefix}.{parent}" for prefix in prefixes]
    after.append(parent)
    return _dedupe(before), _dedupe(after)


def first_file_node(
    candidates: list[str], module_index: dict[str, str],
) -> str | None:
    """First of *candidates* the index maps to a ``file:`` node, else None.

    The acceptance rule for an inferred candidate. The index also holds
    speculative ``symbol:`` stubs keyed by their declared module, and a guess
    that lands on one of those has invented a relationship rather than found
    one -- so inference is only ever allowed to hit a real file.
    """
    for module_name in candidates:
        target = module_index.get(module_name)
        if target and target.startswith("file:"):
            return target
    return None


def external_package_name(name: str, language: str) -> str:
    """The package name to mint when no candidate resolved."""
    if language == "java":
        value = name[:-2] if name.endswith(".*") else name
        package, dot, _class = value.rpartition(".")
        return package if dot else value
    return name


def _ancestor_prefixes(source_file: str) -> list[str]:
    """Dotted ancestor directories of *source_file*, deepest first.

    Deepest first because the nearest enclosing directory is the likeliest
    source root: two vendored trees can each hold a ``config.py``, and the
    importer means its own.
    """
    parts = PurePosixPath(source_file).parent.parts if source_file else ()
    return [".".join(parts[:depth]) for depth in range(len(parts), 0, -1)]


def _rust_candidates(value: str, source_file: str) -> list[str]:
    rust = value.replace("::", ".").strip(".")
    for prefix in ("crate.", "self.", "super."):
        if rust.startswith(prefix):
            rust = rust[len(prefix):]
    candidates = [rust, rust.replace(".", "/")]
    parts = PurePosixPath(source_file).parts
    if parts and rust:
        candidates.extend([f"{parts[0]}.{rust}", f"{parts[0]}/{rust}"])
    return candidates


def _dedupe(candidates: list[str]) -> list[str]:
    return [c for c in dict.fromkeys(candidates) if c]


__all__ = [
    "external_package_name",
    "first_file_node",
    "module_candidates",
    "module_key",
    "python_dotted_module",
    "python_module_index",
    "python_source_root_candidates",
]

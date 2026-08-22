"""Resolving ``load()`` for the ``bazel`` strategy (ADR 0044 amendment).

A BUILD file's ``load("//weld:runtime_srcs.bzl", "RUNTIME_SRCS")`` is the one
Starlark construct ADR 0044 and ADR 0105 both left unhandled, and it is what
made three separate lookups fail (bd 73xa, bd akwh, bd rh3l). This module owns
the half that needs the filesystem -- turning a label into a path, reading it,
and building the namespace it exports -- so :mod:`weld.strategies._bazel_starlark`
stays a pure AST evaluator with no I/O. The two macro-shaped AST helpers
(:func:`macro_defs`, :func:`zero_arg_calls`) live here rather than there for the
same reason: they are about *macros*, which are this module's subject.

Two kinds of symbol come back, and the caller wants them apart:

* a **constant** (``RUNTIME_SRCS``) binds into the loading file's namespace, so
  ``srcs = RUNTIME_SRCS`` evaluates to the ~900 paths it really names;
* a **macro** (``cross_repo_tests``) is a zero-parameter ``def`` whose body
  declares targets **in the package that calls it**, never in the package the
  ``.bzl`` lives in. Attributing by ``.bzl`` location is exactly how a
  plausible-but-wrong target ID gets minted, which is the property bd akwh
  requires be preserved; :class:`Macro` therefore carries the defining module's
  own bindings and nothing about its location.

Bounded on purpose. Foreign ``@repo//`` labels resolve to nothing (weld does
not have the external repo on disk, and inventing its contents is the failure
mode above). Loads recurse, because a ``.bzl`` may load another
(``tools/local_gate_targets.bzl`` loads ``:srcs.bzl``), but with a visited set
and a depth cap so a cyclic or deep chain terminates.
"""

from __future__ import annotations

import ast
from typing import Callable, Container, NamedTuple

from weld.strategies._bazel_macro_args import param_macro_defs
from weld.strategies._bazel_starlark import (
    module_bindings,
    parse_module,
)

#: How many ``.bzl`` files deep a single BUILD file's loads are followed.
#: This repo's deepest chain is 2 (BUILD -> *_targets.bzl -> srcs.bzl). The cap
#: is a termination guarantee, not a tuning knob: a visited set already stops
#: cycles, and this stops a pathological chain from turning one BUILD file into
#: an unbounded read.
_MAX_LOAD_DEPTH = 8


class Macro(NamedTuple):
    """A zero-parameter macro plus the namespace its own module bound.

    ``bindings`` are the defining ``.bzl``'s module-level names, not the
    caller's: the body's ``for _n in _TARGETS`` means the tuple that ``.bzl``
    declared. ``path`` is where the body lives -- it is provenance for the
    targets the body declares, and is never used to *place* them.
    """

    node: object
    bindings: dict
    origins: dict[str, str]
    path: str


class LoadedModule(NamedTuple):
    """What a ``.bzl`` exports, plus every path read to find it out.

    ``origins`` maps each exported binding name to the ``.bzl`` that actually
    defined it, which is not always the file that exported it: ``srcs.bzl``
    defines ``RELEASE_CLAIMS_SRCS`` and ``local_gate_targets.bzl`` re-exports it
    by loading it. Attributing the edge to the re-exporter would point a reader
    at a file that only forwards the value.

    ``macros`` and ``param_macros`` partition every macro def this module
    exports by parameter shape (ADR 0123): a def lands in exactly one, never
    both, so a caller looking up a name in one dict never also finds it in
    the other. See :func:`weld.strategies._bazel_macro_args.param_macro_defs`
    for exactly which non-zero-parameter shapes are recognized.
    """

    bindings: dict
    macros: dict[str, Macro]
    read_paths: list[str]
    origins: dict[str, str]
    param_macros: dict[str, Macro]


def macro_defs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Return module-level zero-parameter ``def``s by name.

    Only zero-parameter definitions are returned. A macro that takes arguments
    needs parameter binding to evaluate, and this evaluator has none -- so it
    is not a macro this module can expand, and reporting it as one would invite
    a caller to expand it with an empty namespace and emit whatever literal
    targets happened to survive.
    """
    out: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args
        if (
            args.args
            or args.posonlyargs
            or args.kwonlyargs
            or args.vararg
            or args.kwarg
        ):
            continue
        out[node.name] = node
    return out


def zero_arg_calls(tree: ast.Module, names: Container[str]) -> list[str]:
    """Return the names in *names* called at module level with no arguments.

    Order-preserving and de-duplicated. Expanding the same macro twice would
    emit its targets twice: the node dict collapses that, but the edge list
    does not, so the caller would get duplicate edges for targets bazel would
    have rejected the second declaration of anyway.
    """
    out: list[str] = []
    seen: set[str] = set()
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        name = call.func.id
        if name in names and name not in seen and not call.args and not call.keywords:
            seen.add(name)
            out.append(name)
    return out


def resolve_bzl_label(label: str, pkg_dir: str) -> str | None:
    """Resolve a ``load()`` label to a repo-relative ``.bzl`` path.

    Accepted::

        ":srcs.bzl"                  -> "<pkg_dir>/srcs.bzl"
        "//weld:runtime_srcs.bzl"    -> "weld/runtime_srcs.bzl"
        "//:top.bzl"                 -> "top.bzl"

    Everything else returns ``None`` and is dropped silently, matching how
    :mod:`weld.strategies._bazel_labels` treats a label it cannot place:

    * ``"@rules_python//python:defs.bzl"`` -- an external workspace, not on
      disk here;
    * any label not ending in ``.bzl`` -- bazel does not accept one either;
    * any label whose resolved path escapes the repo (``..``) or is absolute.
      A BUILD file is repository input, and this resolver's output is fed
      straight to a filesystem read, so containment is checked here rather
      than trusted from the caller.
    """
    if not label or label.startswith("@") or not label.endswith(".bzl"):
        return None

    if label.startswith("//"):
        rest = label[2:]
        pkg_part, sep, name = rest.partition(":")
        if not sep or not name:
            return None
        rel = f"{pkg_part}/{name}" if pkg_part else name
    elif label.startswith(":"):
        name = label[1:]
        if not name:
            return None
        rel = f"{pkg_dir}/{name}" if pkg_dir else name
    else:
        return None

    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or rel.startswith("/"):
        return None
    return rel


def load_module(
    rel_path: str,
    read: Callable[[str], str | None],
    _seen: frozenset[str] = frozenset(),
    cache: dict[str, LoadedModule] | None = None,
) -> LoadedModule:
    """Read the ``.bzl`` at *rel_path* and return the namespace it exports.

    *read* maps a repo-relative path to its text, or ``None`` when the file is
    absent or unreadable. Every path actually read is reported in
    ``read_paths`` so the caller can record it as provenance -- without that,
    editing a macro would not mark the graph stale and the targets it declares
    would silently drift from the tree (bd akwh).

    A file that will not parse, is missing, or sits past the depth cap exports
    an empty namespace. That costs the entries it would have bound and nothing
    else: the loading BUILD file still parses, and its literal targets are
    still emitted.

    *cache* memoises by path and is what keeps this linear. The visited set
    below stops a *cycle*, but not a diamond: without memoisation a file that
    fans out to F loads at each of D levels is read F**D times, and weld runs
    discovery over repositories it did not write (ADR 0025). A ``.bzl``'s
    exports depend only on its own loads, never on who loaded it, so reusing
    the result is exact. The one exception is a load *cycle*, where the cached
    entry may have elided the back edge -- bazel rejects cyclic loads outright,
    so that shape has no correct answer to lose.
    """
    if cache is not None and rel_path in cache:
        return cache[rel_path]
    if rel_path in _seen or len(_seen) >= _MAX_LOAD_DEPTH:
        return LoadedModule({}, {}, [], {}, {})
    text = read(rel_path)
    if text is None:
        return LoadedModule({}, {}, [], {}, {})
    tree = parse_module(text)
    if tree is None:
        return LoadedModule({}, {}, [rel_path], {}, {})

    pkg_dir = rel_path.rpartition("/")[0]
    env, macros, read_paths, origins, param_macros = _resolve_loads(
        tree, pkg_dir, read, _seen | {rel_path}, cache
    )
    read_paths.insert(0, rel_path)

    bindings = module_bindings(tree, env)
    # A name this module bound itself originates here; one it merely forwarded
    # keeps the origin the load gave it.
    origins.update({name: rel_path for name in bindings if name not in env})
    for name, node in macro_defs(tree).items():
        macros[name] = Macro(node, bindings, origins, rel_path)
    for name, node in param_macro_defs(tree).items():
        param_macros[name] = Macro(node, bindings, origins, rel_path)
    result = LoadedModule(bindings, macros, read_paths, origins, param_macros)
    if cache is not None:
        cache[rel_path] = result
    return result


def resolve_build_loads(
    tree,
    pkg_dir: str,
    read: Callable[[str], str | None],
    cache: dict[str, LoadedModule] | None = None,
) -> LoadedModule:
    """Return the namespace a BUILD file's ``load()`` statements bring in.

    The BUILD file's own module-level assignments are **not** folded in here --
    that is the caller's job, because they must be evaluated *after* the loaded
    names are bound (``srcs = RELEASE_CLAIMS_SRCS + [...]`` needs the load
    first) and they belong to the BUILD file rather than to any ``.bzl``.

    *cache* should be one dict per discovery run: BUILD files in a repo share
    their ``.bzl`` files heavily, and re-deriving each one per package is the
    difference between reading 26 files and reading them once each.
    """
    env, macros, read_paths, origins, param_macros = _resolve_loads(
        tree, pkg_dir, read, frozenset(), cache
    )
    return LoadedModule(env, macros, read_paths, origins, param_macros)


def _resolve_loads(
    tree,
    pkg_dir: str,
    read: Callable[[str], str | None],
    seen: frozenset[str],
    cache: dict[str, LoadedModule] | None = None,
) -> tuple[dict, dict[str, Macro], list[str], dict[str, str], dict[str, Macro]]:
    """Fold every resolvable ``load()`` in *tree* into one namespace."""
    env: dict = {}
    macros: dict[str, Macro] = {}
    read_paths: list[str] = []
    origins: dict[str, str] = {}
    param_macros: dict[str, Macro] = {}

    for label, symbols in parse_load_statements(tree):
        dep_path = resolve_bzl_label(label, pkg_dir)
        if dep_path is None:
            continue
        loaded = load_module(dep_path, read, seen, cache)
        read_paths.extend(loaded.read_paths)
        # Only the symbols this load names enter scope. A ``.bzl`` exports its
        # whole module namespace to bazel, but binding names the loading file
        # never asked for would let one file's private helper shadow another's
        # real value.
        for symbol in symbols:
            if symbol in loaded.macros:
                macros[symbol] = loaded.macros[symbol]
            elif symbol in loaded.param_macros:
                param_macros[symbol] = loaded.param_macros[symbol]
            elif symbol in loaded.bindings:
                env[symbol] = loaded.bindings[symbol]
                origins[symbol] = loaded.origins.get(symbol, dep_path)
    return env, macros, read_paths, origins, param_macros


def parse_load_statements(tree) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``(label, symbols)`` for each module-level ``load()`` call.

    Only string literals are collected. ``load()`` in real Starlark also takes
    ``alias = "name"`` keywords; those are ignored rather than guessed at,
    which drops the alias binding and nothing else.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name) or call.func.id != "load":
            continue
        args = [a.value for a in call.args if isinstance(a, ast.Constant)]
        args = [a for a in args if isinstance(a, str)]
        if len(args) < 2:
            continue
        out.append((args[0], tuple(args[1:])))
    return out

"""Strategy: Top-level classes and functions from Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld._rel_path import rel_to_root
from weld.file_index import _module_constant_names
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies._helpers import StrategyResult
from weld.strategies._python_anchor import (
    module_exports,
    module_summary,
    yields_file_anchor,
)
from weld.strategies._python_module_incremental import dirty_scoped_matched
from weld.strategies._strategy_failure import note_strategy_failure

def _looks_like_sibling_module(name: str) -> bool:
    """Heuristic: does *name* look like a private sibling module?

    Targets the ``_ros2_py`` / ``_ros2_cpp`` shape -- a single
    leading underscore, all-lowercase, no uppercase letters. The
    convention in this repo (and broadly in Python) is that
    ``_lower_snake_case`` names imported via ``from pkg import _name``
    refer to a sibling module the package author wants to keep
    package-private. Classes (``PascalCase``) and ordinary functions
    (``snake_case`` *without* a leading underscore) are excluded so
    the resolver does not create spurious ``package:python:x.SomeFunc``
    or ``package:python:x.SomeClass`` nodes for the common
    ``from x import some_helper`` / ``from x import SomeClass`` shape.

    This filter gates only the *qualified* ``module.name`` emission
    for ``ImportFrom`` -- the parent ``pkg.mod`` form is always
    emitted regardless. A false negative simply falls back to the
    pre-existing parent-package edge behaviour, which is what shipped
    before this change.
    """
    if not name or name == "*":
        return False
    if not name.startswith("_"):
        return False
    candidate = name.lstrip("_")
    if not candidate:
        return False
    # Lowercase + digits + underscores only; reject any uppercase
    # (classes are PascalCase even when prefixed with ``_``).
    return all(ch.islower() or ch.isdigit() or ch == "_" for ch in candidate)


def _top_level_import_node_ids(tree: ast.Module) -> set[int]:
    """``id()`` of every Import/ImportFrom node reachable without entering a
    function, async function, or class body.

    uuxaz.6-repair: a function-scoped import is this repo's own sanctioned
    cycle-breaking idiom (ADR 0130; see ``doctor.py``/``_doctor_staleness.py``,
    ``ros2_topology.py``/``_ros2_py.py``). ``ast.walk`` does not distinguish
    that from a top-level import, so both produced identical ``depends_on``
    edge evidence -- the idiom that breaks a *runtime* cycle was exactly
    what made the *graph* see one. Recurses through control-flow nodes
    (``if TYPE_CHECKING:`` must still count as top-level) but stops at any
    node introducing a new callable/class scope.
    """
    top_level: set[int] = set()

    def _walk_top_level(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                top_level.add(id(child))
                continue
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                continue  # new scope -- imports inside are lazy, not counted
            _walk_top_level(child)

    _walk_top_level(tree)
    return top_level


def _referenced_names(tree: ast.Module) -> set[str]:
    """Local names that are *loaded* (referenced) somewhere in the module.

    uuxaz.6 (Finding 04 secondary): the event-handler pattern imports a
    contract and passes it *by value* -- ``subscriber.subscribe(OrderPlacedEvent)``,
    ``handler: SomeEvent = ...`` -- never calling it. ``python_callgraph``
    deliberately does not record cross-module references (ADR 0127), so such
    a name produced no dependency evidence at all. We treat "the imported
    name is loaded in the body" as the noise gate: an import that is actually
    referenced is signal worth a qualified ``module.Name`` edge; an import
    that is never used stays a parent-package-only entry.

    A ``Name`` is counted only in ``Load`` context (a use, not a binding
    target), and the root of an attribute chain (``Event.field`` counts
    ``Event``). Import aliases themselves are excluded by the caller, which
    keys on the *bound* local name.
    """
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            referenced.add(node.id)
    return referenced


def _extract_imports(tree: ast.Module) -> tuple[list[str], list[str]]:
    """Extract full-path package references from imports.

    Returns deduplicated dotted module strings like
    ``myapp.worker.acquisition.models`` from
    ``from myapp.worker.acquisition.models import Foo`` -- the *full*
    dotted path, not a coarse prefix.

    Walks the *entire* AST (not just ``tree.body``) so function-local
    and method-local lazy imports surface alongside top-level ones.
    The lazy-import shape -- e.g. ``ros2_topology.extract`` does
    ``from weld.strategies import _ros2_py as _py`` to break a cycle
    -- previously left the imported module with zero inbound
    ``depends_on`` edges (the j5rj symptom that motivated ADR 0041's
    file-anchor-symmetry rule). Walking ``ast.walk`` captures these;
    the emitted dotted path is what ``graph_closure._link_imports``
    consumes off ``props.imports_from`` to mint ``package:python:*``
    nodes and ``depends_on`` edges.

    Finding 04 (uuxaz.5): the module path is kept at *full* depth. The
    prior ``parts[:3]`` truncation collapsed reverse-DNS namespaces --
    ``acme.platform.order.schema.v1`` and ``.v2`` (distinct protobuf
    contract versions) both became ``acme.platform.order``, merging two
    dependencies into one node. The C# path already keeps the full
    namespace, so the same dependency was separated in C# and collapsed
    in Python. Keeping the full path restores parity; any coarse display
    grouping can be derived from the full id at read time.

    For ``from pkg.mod import name`` statements, the parent package
    (``pkg.mod``, full depth) is always emitted, and the qualified
    ``pkg.mod.name`` form is *also* emitted when either:

    - ``name`` matches the private-sibling-module convention (leading
      ``_``, all-lowercase body) -- the ``_ros2_py`` / ``_ros2_cpp``
      shape. The qualified form lets ``_link_imports`` land an edge
      directly on the sibling module's file node, which is what j5rj
      needs to satisfy the file-anchor-symmetry rule; or

    - uuxaz.6 (Finding 04 secondary): the imported name is actually
      *referenced* in the module body. The event-handler pattern imports
      a contract and passes it by value -- ``subscriber.subscribe(
      OrderPlacedEvent)``, ``handler: SomeEvent = ...`` -- never calling
      it. ``python_callgraph`` deliberately excludes cross-module
      references (ADR 0127), so such a name otherwise produced no
      symbol/dependency evidence: "which services depend on this
      contract" under-reported the Python consumer while C# reported it.
      Emitting the qualified ``pkg.mod.OrderPlacedEvent`` form lets
      ``_link_imports`` mint a ``package:python:...OrderPlacedEvent``
      node and a ``depends_on`` edge.

    Noise control keys on the *bound* local name: a name that is imported
    but never referenced (a dead import, or ``from x import SomeClass``
    where ``SomeClass`` is unused) keeps the pre-change behaviour and only
    emits the parent package, so the graph does not gain a spurious
    ``package:python:x.SomeClass`` node for every function/class import.
    ``import *`` binds nothing and is skipped.

    Returns ``(packages, deferred_only)``: *packages* is the full sorted,
    deduplicated list described above; *deferred_only* (uuxaz.6-repair) is
    the subset of *packages* whose every contributing site is inside a
    function/method/class body (see :func:`_top_level_import_node_ids`) --
    ``graph_closure``/``arch_lint_cycles`` use it to exclude the sanctioned
    lazy-import cycle-breaking idiom from structural-dependency evidence.
    """
    packages: set[str] = set()
    lazy_sites: dict[str, int] = {}
    total_sites: dict[str, int] = {}
    referenced = _referenced_names(tree)
    top_level_ids = _top_level_import_node_ids(tree)

    def _record(name: str, node_id: int) -> None:
        total_sites[name] = total_sites.get(name, 0) + 1
        if node_id not in top_level_ids:
            lazy_sites[name] = lazy_sites.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Full dotted path -- no truncation (Finding 04).
                packages.add(alias.name)
                _record(alias.name, id(node))
        elif isinstance(node, ast.ImportFrom) and node.module:
            packages.add(node.module)
            _record(node.module, id(node))
            # Emit the qualified ``module.name`` form when either:
            #   (a) ``name`` looks like a private sibling module
            #       (``from weld.strategies import _ros2_py``) -- this lands
            #       an edge on the sibling file node directly, satisfying
            #       ADR 0041's file-anchor-symmetry rule (j5rj), OR
            #   (b) uuxaz.6: the imported name is actually *referenced* in
            #       the module body -- the event-handler pattern
            #       (``subscriber.subscribe(OrderPlacedEvent)``) imports a
            #       contract and passes it by value, never calling it, so it
            #       otherwise left no symbol/dependency evidence. Keying on
            #       the *bound* local name (``alias.asname or alias.name``)
            #       is the noise gate: an imported-but-unreferenced name
            #       stays a parent-package-only entry, so the graph does not
            #       gain a ``package:python:x.Foo`` node for every unused
            #       ``from x import Foo``. ``import *`` (``name == "*"``)
            #       binds nothing and is skipped.
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                if _looks_like_sibling_module(alias.name) or bound in referenced:
                    qualified = f"{node.module}.{alias.name}"
                    packages.add(qualified)
                    _record(qualified, id(node))
    deferred_only = {
        name for name, count in lazy_sites.items() if count == total_sites.get(name, 0)
    }
    return sorted(packages), sorted(deferred_only)

def _make_node_id(rel_path: str, id_prefix: str) -> str:
    """Build the canonical file-anchor ID for a Python module.

    Per ADR 0041 § Layer 1, file IDs use the full repo-relative POSIX
    path without extension (``file:weld/strategies/python_module``)
    rather than the legacy bare-stem form (``file:python_module``).
    The full-path form is order-independent and unambiguous: two files
    with the same stem in different directories no longer collide.

    The ``id_prefix`` parameter is preserved for backward compatibility
    (other strategies still use it), but for ``python_module`` the
    canonical form already encodes the full path so the prefix is
    folded in only when it would *narrow* the path scope further
    (i.e. when the prefix is not already a path segment).
    """
    if id_prefix:
        # Anchor the rel-path to the named scope when present so the
        # ID still reflects the in-config namespace boundary.
        parts = Path(rel_path).parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1 :])
            if sub_parts:
                sub_parts[-1] = Path(sub_parts[-1]).stem
            else:
                sub_parts = [Path(rel_path).stem]
            sub_path = "/".join(sub_parts) if sub_parts else Path(rel_path).stem
            return _canonical_file_id(f"{id_prefix}/{sub_path}")
        return _canonical_file_id(f"{id_prefix}/{Path(rel_path).stem}")
    return _canonical_file_id(rel_path)


def _legacy_stem_file_id(rel_path: str) -> str:
    """Return the pre-ADR-0041 ``file:<stem>`` form for *rel_path*.

    The legacy ID was the bare module stem -- e.g.
    ``file:python_module`` for ``weld/strategies/python_module.py``.
    Recorded under ``aliases`` for one minor version per ADR 0041 so
    external transcripts that pasted the old form still resolve via
    the alias-aware lookup.
    """
    return f"file:{Path(rel_path).stem}"

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract top-level classes and functions from Python modules."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    package_id = source.get("package", "")
    id_prefix = source.get("id_prefix", "")

    matched, dirs = resolve_glob_with_provenance(root, pattern, excludes)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # ADR 0084: incremental dirty-scoping. When the orchestrator hands a
    # dirty-file hint (``context[INCREMENTAL_HINT_KEY]``), parse only the
    # dirty subset of this glob instead of re-parsing every sibling to keep
    # one. ``python_module`` has zero cross-file state, so the narrowed node
    # set is byte-identical to a full parse's -- the orchestrator discards
    # the non-dirty siblings' nodes anyway (``discover.py`` merge). ``hint is
    # None`` (full discover + every non-incremental caller) returns ``matched``
    # unchanged, preserving whole-glob behaviour byte-for-byte.
    parse_files = dirty_scoped_matched(matched, root, context)

    for py in parse_files:
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd hch4: a file we could not parse is a *failure*, not this
            # strategy deciding the file anchors nothing -- ``yields_file_anchor``
            # below is that decision. Recording a parse failure as a decision
            # exempted it from the ADR 0008 per-file repair for good, so the
            # day weld's parser grows to accept the file, nothing would ever
            # re-read it.
            #
            # bd pt38: the same holds for a file we could not *read*, which is
            # why this catches what ``python_callgraph`` catches rather than
            # ``SyntaxError`` alone. ``read_text`` raises ``OSError``, never
            # ``SyntaxError``, so a file removed between the walk and the read
            # used to take the whole run down with it. That window is never
            # zero -- the run walks once and reads later (``build_file_hashes``
            # at run start, strategies after), and the per-run glob memo
            # (bd cjij) widens it to the length of the run -- so a concurrent
            # editor, worktree switch, or CI checkout is enough to hit it.
            # A vanished file is the repairable kind of failure by definition:
            # if it comes back the next pass anchors it, and if it does not it
            # leaves ``state.files`` and stops being asked about.
            note_strategy_failure(context, [rel_to_root(py, root)])
            continue
        rel_path = rel_to_root(py, root)
        # The export collection and the "does this file anchor at all?"
        # rule both live in ``_python_anchor`` so ``python_package`` can
        # ask the same question without restating it (issue ``ddsy``).
        # Restating it is the drift ADR 0041 § Layer 3 exists to prevent.
        exports = module_exports(tree)
        if not yields_file_anchor(py.name, exports):
            continue

        nid = _make_node_id(rel_path, id_prefix)
        newlines = source_text.count("\n")
        line_count = newlines + (1 if source_text and not source_text.endswith("\n") else 0)
        imports_from, deferred_imports = _extract_imports(tree)
        # Module-level constants (UPPER_CASE / _UPPER_CASE). These are
        # the residual "what does this module own" surface that
        # ``exports`` (classes + public functions) does not cover. They
        # feed ``wd query`` via ``query_index.node_tokens`` -- bounded
        # and ReDoS-safe; see ``weld.file_index`` for the cap rationale.
        # Sorted + deduplicated so the graph artifact is canonical
        # (ADR 0012 §3).
        constants = sorted(set(_module_constant_names(tree)))
        # bd ph1g: the opening paragraph of the module docstring -- the one
        # sentence the author wrote to say what this module is. Read here for
        # the same reason ``constants`` is: it feeds ``wd query`` through
        # ``query_index.node_tokens``. Before this, discovery kept a module's
        # exports and threw its summary away, so the graph's only prose came
        # from an enrichment pass covering 1.96% of nodes -- which is why a
        # query for a filename stated plainly in ``weld/serializer.py``'s first
        # line matched nothing at all. Always emitted, empty when there is no
        # docstring, so the node shape does not vary with the source.
        summary = module_summary(tree)

        # ADR 0041 § Migration: record the pre-rename ``file:<stem>``
        # form on ``aliases`` when the canonical full-path ID differs
        # from the bare-stem legacy ID. ``[]`` for the (rare) case
        # where the legacy and canonical forms collapse to the same
        # string (a single-segment module at the repo root).
        legacy_nid = _legacy_stem_file_id(rel_path)
        aliases = [legacy_nid] if legacy_nid != nid else []
        props = {
            "file": rel_path,
            "exports": exports,
            "constants": constants,
            "summary": summary,
            "imports_from": imports_from,
            "line_count": line_count,
            "source_strategy": "python_module",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "aliases": aliases,
            # ADR 0042: project files are always project-origin -- the
            # strategy only walks files inside the discovered glob, which
            # by construction is the project tree.
            "origin": "project",
        }
        if deferred_imports:
            # uuxaz.6-repair: sparse marker (only set when non-empty) so
            # every pre-existing golden fixture stays byte-identical.
            # Subset of imports_from that is lazy-only (ADR 0130 idiom);
            # graph_closure marks the resulting depends_on edge
            # ``deferred`` so arch_lint_cycles excludes it as evidence.
            props["deferred_imports"] = deferred_imports
        nodes[nid] = {"type": "file", "label": py.stem, "props": props}
        if package_id:
            edges.append(
                {
                    "from": package_id,
                    "to": nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "python_module",
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, discovered_from)

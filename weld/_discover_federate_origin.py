"""Federation-aware origin re-tagging for child graphs (ADR 0042 §Federation).

When ``wd discover`` runs inside a child repo, each language strategy
only knows the modules its own glob matched. A symbol defined in child
A and imported from child B is therefore speculatively minted in B's
graph with ``origin="external"`` because A's modules are not in B's
local project module set.

ADR 0042 §Federation says the opposite: in a polyrepo workspace,
``project`` means *any* federated child of the active root. Closing
that gap inside a strategy would require children to be aware of
their siblings at discovery time, which breaks the single-repo
isolation strategies rely on. Instead, the federation pipeline runs
a small post-discovery pass that:

1. Builds the *federated* project module set per language by unioning
   every present child's project-tagged ``symbol`` / ``file`` /
   ``module`` nodes' ``module`` props.
2. Walks each child graph and re-tags any ``symbol`` node whose
   ``origin == "external"`` to ``"project"`` when its ``module`` is
   in the federated set for the same language.
3. Writes back the corrected child graph atomically so the on-disk
   file (the source of truth for :class:`weld.federation.FederatedGraph`)
   reflects the federation contract.

The pass is pure with respect to a child whose tags are already
correct: nothing is rewritten, so byte-identical reruns stay
byte-identical. Children with status other than ``present`` are
skipped, as are corrupt graphs (the ``federate`` step already prints
a notice for those).

The federation rule is language-agnostic, but each language emits its
own match key. To keep cross-language pollution impossible, the pass
dispatches by ``props.language``: a Python project symbol never feeds
the C++ federated set and vice versa, even when ``module`` strings
collide. Adding a new language is a matter of adding it to
:data:`SUPPORTED_LANGUAGES`; the dispatch is otherwise generic.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._graph_meta_sidecar import write_graph_with_meta
from weld._workspace_inspect import resolve_child_root
from weld.workspace import WorkspaceConfig
from weld.workspace_state import WorkspaceState
from weld._notice import emit

#: Languages whose strategies ship per-ADR-0042 origin tagging and
#: therefore participate in federated cross-child re-tagging. Each
#: language must already stamp ``props.origin`` on its emitted symbols
#: and use ``props.module`` as the match key. New languages are added
#: by appending here once their strategy meets that contract.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("python", "cpp")

__all__ = [
    "SUPPORTED_LANGUAGES",
    "federated_cpp_project_modules",
    "federated_project_modules_for_language",
    "federated_python_project_modules",
    "retag_external_cpp_origins",
    "retag_external_origins_for_language",
    "retag_external_python_origins",
    "retag_federated_origins_on_disk",
]


def federated_project_modules_for_language(
    children: dict[str, dict],
    language: str,
) -> frozenset[str]:
    """Return the union of every child's project module paths for *language*.

    Scans every child graph in *children* (a mapping of child name to
    parsed graph dict) for ``symbol`` / ``file`` / ``module`` nodes
    whose ``props.language == language`` and ``props.origin ==
    "project"``. Returns the frozenset of every non-empty
    ``props.module`` string encountered.

    Per ADR 0042 §Federation, this set is the federation-wide notion
    of "project modules" for that language: any node whose dotted
    ``module`` falls here is considered project code regardless of
    which child it came from.
    """
    out: set[str] = set()
    for graph in children.values():
        nodes = graph.get("nodes")
        if not isinstance(nodes, dict):
            continue
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            props = node.get("props")
            if not isinstance(props, dict):
                continue
            if props.get("language") != language:
                continue
            if props.get("origin") != "project":
                continue
            module = props.get("module")
            if isinstance(module, str) and module:
                out.add(module)
    return frozenset(out)


def retag_external_origins_for_language(
    graph: dict,
    federated_project_modules: frozenset[str],
    language: str,
) -> int:
    """Re-tag external *language* symbol nodes whose module is federated.

    Walks ``graph["nodes"]`` and, for every ``symbol`` node whose
    ``props.language == language`` and ``props.origin == "external"``,
    promotes its ``origin`` to ``"project"`` when its ``props.module``
    is a member of *federated_project_modules*.

    Mutates *graph* in place and returns the number of nodes whose
    ``origin`` was changed. Returns ``0`` immediately when
    *federated_project_modules* is empty (no possible match) or the
    graph has no ``nodes`` mapping.
    """
    if not federated_project_modules:
        return 0
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict):
        return 0

    changed = 0
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "symbol":
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("language") != language:
            continue
        if props.get("origin") != "external":
            continue
        module = props.get("module")
        if not isinstance(module, str) or not module:
            continue
        if module in federated_project_modules:
            props["origin"] = "project"
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Per-language convenience wrappers
# ---------------------------------------------------------------------------


def federated_python_project_modules(
    children: dict[str, dict],
) -> frozenset[str]:
    """Return the union of every child's Python project module paths.

    Thin wrapper over
    :func:`federated_project_modules_for_language` pinned to
    ``language="python"``. Kept as a stable public name because it
    pre-dates the language-agnostic refactor and is imported by name
    from :mod:`weld._discover_federate` and by the federation tests.
    """
    return federated_project_modules_for_language(children, "python")


def retag_external_python_origins(
    graph: dict,
    federated_project_modules: frozenset[str],
) -> int:
    """Re-tag external Python symbol nodes whose module is federated.

    Thin wrapper over
    :func:`retag_external_origins_for_language` pinned to
    ``language="python"``.
    """
    return retag_external_origins_for_language(
        graph, federated_project_modules, "python"
    )


def federated_cpp_project_modules(
    children: dict[str, dict],
) -> frozenset[str]:
    """Return the union of every child's C++ project module paths.

    Thin wrapper over
    :func:`federated_project_modules_for_language` pinned to
    ``language="cpp"``. The C++ tree-sitter strategy stamps a stable
    dotted ``module`` on every emitted symbol (via
    ``_ts_call_graph.ts_module_from_path``) and on layer-2 resolved
    includes (via ``cpp_resolver``); both feed this set when tagged
    ``origin='project'``.
    """
    return federated_project_modules_for_language(children, "cpp")


def retag_external_cpp_origins(
    graph: dict,
    federated_project_modules: frozenset[str],
) -> int:
    """Re-tag external C++ symbol nodes whose module is federated.

    Thin wrapper over
    :func:`retag_external_origins_for_language` pinned to
    ``language="cpp"``. Layer-2 ``unresolved`` sentinels are
    untouched: the federation pass only promotes definite
    ``external`` tags. Sentinel upgrades remain the responsibility of
    :func:`weld.strategies._cpp_origin.upgrade_origin`.
    """
    return retag_external_origins_for_language(
        graph, federated_project_modules, "cpp"
    )


def _load_child_graph_dict(
    graph_path: Path,
) -> tuple[dict, bytes] | None:
    """Return ``(graph_dict, raw_bytes)`` for *graph_path*, or ``None`` on failure.

    Mirrors ``_discover_federate._load_present_child_graph`` but parses
    the graph as a plain dict (not :class:`weld.graph.Graph`). The
    re-tag pass needs to mutate node ``props`` and re-serialize, which
    is most natural on the dict form. Raw bytes are returned alongside
    so the caller can detect a no-op (current bytes already canonical).

    Failures (missing file, OS error, JSON parse error) print a notice
    to stderr and return ``None`` — the cross-repo edge step already
    handles the same children, so duplicate noise is acceptable.
    """
    if not graph_path.is_file():
        return None
    try:
        raw = graph_path.read_bytes()
    except OSError as exc:
        emit(
            f"[weld] federate: failed to read {graph_path}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None
    try:
        graph = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        emit(
            f"[weld] federate: failed to parse {graph_path}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None
    if not isinstance(graph, dict):
        emit(
            f"[weld] federate: graph at {graph_path} is not a JSON object; "
            "skipping origin pass"
        )
        return None
    return graph, raw


def retag_federated_origins_on_disk(
    root: Path,
    config: WorkspaceConfig,
    state: WorkspaceState,
) -> dict[str, int]:
    """Re-tag external -> project on every present child whose modules collide.

    Loads every child whose ledger status is ``present``, then for
    each language in :data:`SUPPORTED_LANGUAGES` computes the
    federated project module set across all of them and applies
    :func:`retag_external_origins_for_language` per child. When a
    child's graph changed across any language pass, it is rewritten
    atomically once at the end so the on-disk file is the source of
    truth federation consumers read.

    Returns a mapping of child name -> total number of nodes re-tagged
    (summed across language passes) for every child that had at least
    one change. Children that were not rewritten do not appear in the
    result.

    Skips ``missing`` / ``uninitialized`` / ``corrupt`` children
    silently — they have no graph to re-tag. A child whose graph fails
    to parse here gets the same skip-with-notice treatment used by the
    cross-repo edge step.
    """
    paths_by_name = {c.name: c.path for c in config.children}

    loaded: dict[str, tuple[Path, dict]] = {}
    for child in config.children:
        entry = state.children.get(child.name)
        if entry is None or entry.status != "present":
            continue
        child_path = paths_by_name.get(child.name)
        if child_path is None:
            continue
        graph_path = (
            resolve_child_root(root, child_path) / ".weld" / "graph.json"
        )
        result = _load_child_graph_dict(graph_path)
        if result is None:
            continue
        graph_dict, _raw = result
        loaded[child.name] = (graph_path, graph_dict)

    if not loaded:
        return {}

    children_view = {name: graph for name, (_p, graph) in loaded.items()}
    federated_by_lang: dict[str, frozenset[str]] = {}
    for language in SUPPORTED_LANGUAGES:
        federated_by_lang[language] = federated_project_modules_for_language(
            children_view, language
        )

    if not any(federated_by_lang.values()):
        return {}

    changes: dict[str, int] = {}
    for name, (graph_path, graph_dict) in loaded.items():
        total = 0
        for language in SUPPORTED_LANGUAGES:
            federated = federated_by_lang[language]
            if not federated:
                continue
            total += retag_external_origins_for_language(
                graph_dict, federated, language
            )
        if total == 0:
            continue
        changes[name] = total
        # Re-serialize through the ADR 0065 paired writer so the
        # on-wire bytes match the rest of the discover path
        # (deterministic key/edge ordering, single trailing newline)
        # AND the child stays content-addressable: volatile meta
        # (updated_at/git_sha) is stripped to the graph-meta.json
        # sidecar instead of being re-stamped into graph.json. A
        # legacy child loaded here still carries those keys in-graph;
        # routing the re-tag write through the paired writer migrates
        # it to the sidecar in place rather than re-persisting them.
        write_graph_with_meta(graph_path, graph_dict)
    return changes

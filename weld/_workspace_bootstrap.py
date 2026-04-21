"""One-shot polyrepo bootstrap orchestrator (ADR 0018).

``wd workspace bootstrap`` runs the complete federation-onboarding sequence
at a polyrepo root, in order:

1. Root init (``wd init`` on the root) when the root still lacks
   ``.weld/discover.yaml`` or ``.weld/workspaces.yaml``.
2. Scan the tree for nested git repositories (honouring ``--max-depth``),
   matching the scanner used by ``wd init``.
3. Per-child init: for every discovered child that has ``.git/`` but no
   ``.weld/discover.yaml``, run ``wd init`` inside the child.
4. In-process recurse-discover via :func:`weld._discover_recurse.recurse_children`
   so every child with status ``present`` or ``uninitialized`` gets a
   freshly built ``.weld/graph.json``.
5. Rebuild the ledger via :func:`weld.workspace_state.build_workspace_state`
   and re-emit the root meta-graph so
   :func:`weld.federation_root._present_children` observes the new state.

The orchestrator is intentionally a thin composition of existing
building blocks (``init``, ``init_workspace``, ``recurse_children``,
``build_workspace_state``, ``build_root_meta_graph``). It does not
introduce new lifecycle state and it never removes data it did not
create: a pre-existing child graph is refreshed by the in-process
recurse, never blown away. A fully-initialized workspace is a no-op
modulo ``meta.generated_at`` / ``meta.updated_at`` stamps.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from weld.init import init as _root_init
from weld.init_workspace import discover_children, init_workspace
from weld.workspace import DEFAULT_MAX_DEPTH
from weld.workspace_state import (
    atomic_write_text,
    build_workspace_state,
    load_workspace_config,
    save_workspace_state,
)

__all__ = ["BootstrapResult", "bootstrap_workspace"]


@dataclass
class BootstrapResult:
    """Structured outcome of a bootstrap run, useful for tests and CLI output.

    Field contract
    --------------
    ``root_init_ran``
        ``True`` when step 1 wrote a new ``.weld/discover.yaml`` at the
        root. ``False`` when the file was already present (no-op re-run).
    ``workspace_yaml_written``
        ``True`` when step 2 wrote a new ``.weld/workspaces.yaml``.
        ``False`` when that file was already present.
    ``children_discovered``
        Sorted names of every nested git repo found by the step 2 scan,
        regardless of their current ledger status. This is the
        denominator for "how many children does this polyrepo have".
    ``children_initialized``
        Subset of ``children_discovered`` for which step 3 actually ran
        ``wd init`` this run (they had ``.git/`` but no discover.yaml).
        Already-initialized children are silently skipped and do NOT
        appear here.
    ``children_recursed``
        Names of children that step 4 (:func:`recurse_children`)
        successfully visited AND wrote a fresh ``.weld/graph.json`` for,
        during THIS run. Recurse considers only children whose ledger
        status at the start of step 4 is ``present`` or ``uninitialized``;
        children with status ``missing`` or ``corrupt`` are skipped and
        never appear here. A child whose ``_discover_single_repo`` raises
        is also omitted from this list -- the failure is logged to stderr
        AND mirrored into :attr:`errors` as
        ``"recurse <name>: <ExcType>: <msg>"`` so programmatic callers
        inspecting the result can see per-child recurse failures.
        Recurse is unconditional for eligible children: an
        already-``present`` child is re-discovered and its graph rewritten
        with equivalent content, so on a healthy idempotent re-run this
        list contains every eligible child.
    ``children_present``
        Names of children whose ledger status is ``present`` at the END
        of the run, computed by re-running :func:`build_workspace_state`
        AFTER step 4. This is the set that :func:`_present_children` and
        the root meta-graph observe going forward.
    ``errors``
        Free-form human-readable error strings accumulated from step 3
        (per-child ``wd init`` failures), from step 4 per-child recurse
        failures (``"recurse <name>: <ExcType>: <msg>"``), and from the
        step 4 guard when ``workspaces.yaml`` is missing.

    Divergence between ``children_recursed`` and ``children_present``
    ----------------------------------------------------------------
    The two lists are related but not identical; operators reading both
    should understand the following cases:

    * Common case (healthy run): every eligible child appears in both
      lists and the sets are equal modulo children whose status at the
      start of step 4 was ``missing`` or ``corrupt`` (those never enter
      ``children_recursed`` and, without external intervention, they
      also do not reach ``children_present``).
    * In ``children_recursed`` but NOT in ``children_present``: step 4
      wrote the child graph successfully, but the post-step-4 inspection
      in :func:`_graph_status` classified the on-disk graph as
      ``corrupt`` or ``uninitialized``. In practice this means the
      graph file was removed or truncated between the atomic write and
      the ledger rebuild (filesystem anomaly). Extremely rare.
    * In ``children_present`` but NOT in ``children_recursed``: the
      child was ``present`` at the start of step 4 (a prior run left a
      valid ``.weld/graph.json`` on disk) and this run's
      ``_discover_single_repo`` call raised. The pre-existing graph is
      untouched, so inspection still classifies the child as
      ``present``, but the current run did not refresh it. The failure
      surfaces on stderr AND as a ``"recurse <name>: ..."`` entry in
      :attr:`errors`.

    For operators: :attr:`children_present` is the ground-truth set
    that downstream federation tools will use;
    :attr:`children_recursed` answers "what did this run actually do".
    """

    root_init_ran: bool = False
    workspace_yaml_written: bool = False
    children_discovered: list[str] = field(default_factory=list)
    children_initialized: list[str] = field(default_factory=list)
    children_recursed: list[str] = field(default_factory=list)
    children_present: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Human-readable bullet summary of a bootstrap run."""
        lines = [
            f"Bootstrapped workspace at: {len(self.children_discovered)} "
            f"child repo(s) discovered",
        ]
        if self.root_init_ran:
            lines.append("  * root init: wrote .weld/discover.yaml")
        else:
            lines.append("  * root init: already initialized (no-op)")
        if self.workspace_yaml_written:
            lines.append("  * workspaces.yaml: written")
        else:
            lines.append("  * workspaces.yaml: already present (no-op)")
        if self.children_initialized:
            lines.append(
                "  * per-child init: "
                + ", ".join(sorted(self.children_initialized)),
            )
        else:
            lines.append("  * per-child init: all children already initialized")
        if self.children_recursed:
            lines.append(
                "  * discover: "
                + ", ".join(sorted(self.children_recursed)),
            )
        lines.append(
            f"  * present after bootstrap: {len(self.children_present)} "
            f"of {len(self.children_discovered)}",
        )
        for err in self.errors:
            lines.append(f"  ! {err}")
        return lines


def _run_root_init(root: Path) -> bool:
    """Run ``wd init`` at the root when the discover config is missing.

    Returns ``True`` when the root init wrote ``.weld/discover.yaml``.
    A pre-existing ``discover.yaml`` is a no-op because ``init()`` refuses
    to overwrite without ``--force``; this mirrors the idempotency rule
    from ADR 0018.
    """
    output = root / ".weld" / "discover.yaml"
    if output.exists():
        return False
    return _root_init(root, output, force=False)


def _child_has_discover_yaml(child_root: Path) -> bool:
    return (child_root / ".weld" / "discover.yaml").is_file()


def _init_child(child_root: Path, result: BootstrapResult, name: str) -> None:
    """Run ``wd init`` inside a child that has ``.git/`` but no discover.yaml.

    Per-child errors are recorded on *result* and do not abort the run;
    the recurse step will still inspect the child by its ledger status.
    """
    if _child_has_discover_yaml(child_root):
        return
    output = child_root / ".weld" / "discover.yaml"
    try:
        wrote = _root_init(child_root, output, force=False)
    except Exception as exc:  # noqa: BLE001 -- per-child isolation
        result.errors.append(f"init {name}: {type(exc).__name__}: {exc}")
        return
    if wrote:
        result.children_initialized.append(name)


def bootstrap_workspace(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> BootstrapResult:
    """Run the 5-step polyrepo bootstrap sequence on *root* and return a summary.

    Parameters
    ----------
    root:
        Workspace root directory. Must exist.
    max_depth:
        Maximum directory depth for the nested-repo scan, mirroring the
        ``--max-depth`` flag on ``wd init``.

    Returns
    -------
    BootstrapResult
        Structured record of what ran and the resulting workspace shape.
        Callers render ``result.summary_lines()`` for human output or
        inspect the individual list fields for programmatic use.
    """
    # Local imports avoid a circular dependency: ``discover`` imports from
    # ``federation_root`` and ``workspace_state``; this module imports
    # ``discover`` for recurse. Importing at call time keeps module-load
    # order clean whichever one is imported first.
    from weld._discover_recurse import recurse_children
    from weld.federation_root import build_root_meta_graph
    from weld.serializer import dumps_graph
    from weld.workspace_state import WorkspaceLock

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"bootstrap root is not a directory: {root_path}")

    result = BootstrapResult()

    # Step 1: root init if needed.
    result.root_init_ran = _run_root_init(root_path)

    # Step 2: scan nested git repos and write workspaces.yaml when needed.
    workspaces_yaml = root_path / ".weld" / "workspaces.yaml"
    if not workspaces_yaml.is_file():
        result.workspace_yaml_written = init_workspace(
            root_path, workspaces_yaml, force=False, max_depth=max_depth,
        )

    # Re-scan to learn children regardless of whether workspaces.yaml existed
    # already; the yaml may be stale or we may be bootstrapping a root that
    # was partially initialized on a previous run.
    children = discover_children(root_path, max_depth=max_depth)
    result.children_discovered = sorted(c.name for c in children)

    if not children:
        # Single-repo root: nothing further to do. Root init has already
        # run above if it was needed. Callers treat an empty
        # children_discovered list as "non-polyrepo".
        return result

    # Step 2.5 (bd-72n): if the filesystem scan found children that the
    # persisted workspaces.yaml does not list (e.g. a new nested repo was
    # added after the first bootstrap), refresh workspaces.yaml with
    # force=True so step 4's ``load_workspace_config`` sees the full set.
    # Without this, step 3 would init the new child's discover.yaml but
    # step 4 (which iterates ``config.children`` loaded from disk) would
    # skip it, leaving the new child as ``uninitialized`` in the ledger
    # until a second bootstrap run. Idempotency still holds: when the FS
    # scan matches the persisted set, the yaml is left alone.
    if workspaces_yaml.is_file():
        persisted = load_workspace_config(root_path)
        persisted_names: set[str] = (
            {entry.name for entry in persisted.children}
            if persisted is not None
            else set()
        )
        scanned_names = {c.name for c in children}
        if scanned_names != persisted_names:
            init_workspace(
                root_path,
                workspaces_yaml,
                force=True,
                max_depth=max_depth,
            )
            result.workspace_yaml_written = True

    # Step 3: per-child init for children lacking discover.yaml.
    for child in children:
        _init_child(root_path / child.path, result, child.name)

    # Step 4 + 5: recurse-discover then rebuild root meta-graph + ledger
    # inside the workspace lock. This matches the ordering used by
    # ``wd discover --recurse`` so crash-safety invariants from ADR 0011
    # section 8 carry over.
    config = load_workspace_config(root_path)
    if config is None:
        # init_workspace() returned False without writing (or the file was
        # removed mid-run). Record an error and bail without attempting to
        # discover/write a meta-graph for a non-existent registry.
        result.errors.append(
            "workspaces.yaml missing after scan; bootstrap cannot recurse",
        )
        return result

    with WorkspaceLock(root_path):
        state = build_workspace_state(root_path, config)
        recurse_outcome = recurse_children(
            root_path, config, state, incremental=False,
        )
        result.children_recursed = sorted(recurse_outcome.discovered)
        # Mirror per-child recurse failures into result.errors so
        # programmatic callers see them (the docstring contract on
        # children_recursed requires this). Sorted for deterministic
        # output.
        for name in sorted(recurse_outcome.errors):
            result.errors.append(f"recurse {name}: {recurse_outcome.errors[name]}")
        # Rebuild ledger AFTER recurse so federation_root sees fresh state.
        state = build_workspace_state(root_path, config)
        graph = build_root_meta_graph(root_path, config, state)
        atomic_write_text(
            root_path / ".weld" / "graph.json", dumps_graph(graph),
        )
        save_workspace_state(root_path, state)

    result.children_present = sorted(
        name for name, entry in state.children.items()
        if entry.status == "present"
    )

    # Emit a user-visible hint when the bootstrap could not reach its
    # nominal goal so operators do not have to parse the JSON summary.
    missing = [
        name for name in result.children_discovered
        if name not in result.children_present
    ]
    if missing:
        print(
            "[weld] bootstrap: children not present after run: "
            + ", ".join(missing),
            file=sys.stderr,
        )

    return result

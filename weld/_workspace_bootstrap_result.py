"""Structured result type for ``wd workspace bootstrap`` (ADR 0018).

Split from :mod:`weld._workspace_bootstrap` so the orchestrator module
stays focused on the 5-step sequence; the result type is a stable data
contract that both tests and CLI output bind to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["BootstrapResult"]


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
    ``yaml_listed_but_missing`` (bd-...-9slg)
        Names declared in workspaces.yaml whose path does not currently
        resolve on disk. Bootstrap still attempts per-child init in case
        the path materialises during recovery, but operators see the
        missing paths in the result and stderr summary.
    ``excluded_by_gitignore`` (bd-...-9slg)
        Names declared in workspaces.yaml that the root ``.gitignore``
        would otherwise mask. The yaml overrides the mask (operator
        opted in explicitly), but the diagnostic explains why an
        FS-only scan would have missed them.
    ``excluded_by_invalid_name``
        Workspace-relative paths of nested repos the FS scan found
        whose auto-derived child name failed the
        ``^[A-Za-z0-9_-]+$`` validator (typically directories with
        leading dots or interior dots in their path segments).
        These are skipped from the registry rather than aborting
        bootstrap; operators see the path so they can either rename
        the directory or add it to ``scan.exclude_paths``.

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
    yaml_listed_but_missing: list[str] = field(default_factory=list)
    excluded_by_gitignore: list[str] = field(default_factory=list)
    excluded_by_invalid_name: list[str] = field(default_factory=list)

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
        if self.yaml_listed_but_missing:
            lines.append(
                "  * yaml-listed but missing on disk: "
                + ", ".join(sorted(self.yaml_listed_but_missing)),
            )
        if self.excluded_by_gitignore:
            lines.append(
                "  * yaml-listed but masked by root .gitignore (yaml wins): "
                + ", ".join(sorted(self.excluded_by_gitignore)),
            )
        if self.excluded_by_invalid_name:
            lines.append(
                "  * scan-skipped (invalid auto-derived child name): "
                + ", ".join(sorted(self.excluded_by_invalid_name)),
            )
        for err in self.errors:
            lines.append(f"  ! {err}")
        return lines

"""Ledger-versus-disk reconciliation for the child-roster surfaces (ADR 0138).

Used by ``wd workspace status`` (the detail surface, which prints the drift
rows) and by ``wd stats`` (the summary surface, which prints only their
count and points at the other command). Both take the same probe and the
same overlay, which is what keeps their presence counts equal by
construction rather than by two renderers agreeing to count alike.

``wd stale`` rebuilds the child ledger live on every read
(:func:`weld._stale_payload.stale_payload`) while ``wd workspace status`` used
to report the stored ``workspace-state.json`` verbatim. Two commands, one
roster, two sources -- so a child deleted after the last ``wd discover`` was
still counted ``present`` on one surface and ``missing`` on the other, and the
ADR 0066 oracle, invited to probe it as a present child, found no git
repository at the path and reported the ADR 0017 non-git result: ``fresh``.

ADR 0138 settles which source wins: **the ledger is a claim recorded at write
time; the disk is the fact at read time.** This module re-probes the
registered children through the same :func:`build_workspace_state` call
``wd stale`` uses -- no second lifecycle classifier, so the ADR 0028
linked-worktree fallback stays identical on both surfaces -- overlays the
observed status onto the stored entry, and hands back the differences for the
caller to print.

Two constraints shape :func:`reconcile` and are easy to undo by accident:

* A **contradicted** entry is replaced whole; an **agreeing** one is kept
  whole. Overlaying only ``status`` onto the stored row looked tidier and
  printed ``docs-site: missing dirty (refs/heads/main a1b2c3d4e5f6)`` -- a
  branch, a SHA and a dirty working tree for a directory that is not there.
  Keeping the agreeing row is what preserves ``graph_sha256`` where it is
  load-bearing: ADR 0066's tier-2 check is "recorded digest differs from
  current bytes" (:func:`weld._federation_staleness._present_child_info`) and
  only ever runs over ``present`` children, so a child that was ``present``
  and still is keeps the baseline that makes ``graph_drift`` reachable.
* Nothing here writes. Repairing the ledger from a read command would race a
  concurrent ``wd discover`` for the same file with no lock held (ADR 0094),
  and would make the command an operator runs *to find out* whether a refresh
  is needed quietly perform half of one.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["observed_children", "reconcile", "drift_lines", "UNPROBED_NOTICE"]

#: Emitted (to stderr, never stdout) when :func:`observed_children` declines.
#: The fallback itself is correct -- the stored ledger is the only answer left
#: -- but reporting its numbers *silently* is the same failure this module
#: exists to remove, one case over: a confident count sourced from a claim.
UNPROBED_NOTICE = (
    "notice: could not re-read the workspace registry, so the child status "
    "below is the stored ledger's last claim rather than a fresh read of "
    "disk. Check .weld/workspaces.yaml, then run: wd discover"
)

#: The renderer's word for a ledger entry that is not an object at all
#: (:func:`weld.workspace_state.format_workspace_status` prints
#: ``<name>: invalid``). Reused rather than re-spelled so a drift row and a
#: child line describe the same broken entry the same way.
_INVALID = "invalid"


def observed_children(root: Path | str) -> dict[str, dict] | None:
    """Re-probe every registered child's lifecycle on disk. Read-only.

    Returns ``{name: ledger-entry-dict}`` from a live
    :func:`weld.workspace_state.build_workspace_state`, or ``None`` when the
    workspace registry is absent or unreadable, or the probe raises.

    ``None`` is the "report the stored ledger unchanged" signal, and it is
    deliberately distinct from ``{}`` (a registry that lists no children,
    where every stored entry really has been de-registered). Failure
    isolation matches :func:`weld._stale_payload.stale_payload`: a root whose
    ``workspaces.yaml`` was moved away, or one child that cannot be stat'd,
    degrades this command to its previous behaviour rather than crashing a
    status read.
    """
    try:
        from weld.workspace_state import build_workspace_state, load_workspace_config

        config = load_workspace_config(root)
        if config is None:
            return None
        children = build_workspace_state(root, config).to_dict().get("children")
    except Exception:  # noqa: BLE001 -- failure isolation is the contract
        return None
    return children if isinstance(children, dict) else None


def _status_of(entry: object) -> str:
    """Return the lifecycle status a ledger entry claims."""
    if not isinstance(entry, dict):
        return _INVALID
    return str(entry.get("status", "unknown"))


def reconcile(
    state: dict,
    observed: dict[str, dict] | None,
) -> tuple[dict, list[dict]]:
    """Return ``(state reported from disk, drift rows)``.

    The returned state is a copy: *state* is never mutated, so a caller that
    still needs the ledger as loaded keeps it. Its ``children`` map is keyed
    by the **registered** set -- the children :func:`observed_children`
    probed. An entry whose stored lifecycle matches what the probe saw is
    reported unchanged, digest and timestamps included. An entry the probe
    contradicts is replaced by the probed one entirely, because a row
    describing a child in a lifecycle state it is not in describes the wrong
    child: its ``head_ref`` / ``head_sha`` / ``is_dirty`` were recorded of a
    repository that no longer matches them.

    Drift rows are ``{"name", "stored", "observed"}``, sorted by name, with
    ``None`` on whichever side has nothing to say: a child registered since
    the last discover has no ``stored`` status, and one dropped from
    ``workspaces.yaml`` has no ``observed`` one. The latter also leaves
    ``children`` -- it is not a registered child any more, and keeping it
    would restate this same bug about registration instead of about a
    directory. The drift row is what keeps that removal reportable rather
    than silent.

    ``observed is None`` (no registry, or an unreadable one) returns *state*
    and no drift: with nothing to compare against, the stored ledger is the
    only answer available and claiming drift would be inventing it.
    """
    if observed is None:
        return state, []
    stored = state.get("children")
    stored = stored if isinstance(stored, dict) else {}

    children: dict[str, object] = {}
    rows: list[dict] = []
    for name, probed in observed.items():
        entry = stored.get(name)
        seen = _status_of(probed)
        claimed: str | None = _status_of(entry) if name in stored else None
        if isinstance(entry, dict) and claimed == seen:
            children[name] = dict(entry)
        else:
            children[name] = dict(probed)
        if claimed != seen:
            rows.append({"name": name, "stored": claimed, "observed": seen})

    for name in stored:
        if name not in observed:
            rows.append(
                {"name": name, "stored": _status_of(stored[name]), "observed": None},
            )

    result = dict(state)
    result["children"] = children
    return result, sorted(rows, key=lambda row: str(row["name"]))


def _phrase(row: dict) -> str:
    """Render one drift row as the clause that follows ``<name>: ``."""
    claimed, seen = row.get("stored"), row.get("observed")
    if claimed is None:
        return f"ledger has no entry, disk says {seen}"
    if seen is None:
        return f"ledger says {claimed}, no longer registered"
    return f"ledger says {claimed}, disk says {seen}"


def drift_lines(drift: list[dict]) -> list[str]:
    """Render the human drift block, or ``[]`` when the ledger agrees.

    Silence on agreement is the point: the block exists to explain a number
    that moved, so printing it when nothing moved would train the reader to
    skip it. The header says which source the counts above came from and the
    footer names the remedy, because a status command that reports drift
    without naming ``wd discover`` leaves the reader knowing only that
    something is wrong.
    """
    if not drift:
        return []
    lines = [
        f"Ledger drift ({len(drift)}) -- counts above are from disk, "
        "not from the stored ledger:",
    ]
    lines.extend(f"  {row.get('name')}: {_phrase(row)}" for row in drift)
    lines.append("  run: wd discover")
    return lines

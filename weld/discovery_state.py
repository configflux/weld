"""Incremental discovery state tracking.

Manages ``.weld/discovery-state.json`` -- a content-hash index that
records which files were processed during the last discovery run and their
SHA-256 hashes.  Used by the discovery orchestrator to skip unchanged files
on subsequent runs.

Design reference: ADR 0008 (docs/adrs/0008-incremental-discovery.md).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._incremental_purge import purge_edges_by_provenance
from weld._notice import emit
from weld._rel_path import canonical_rel_path, canonical_rel_paths

#: State file schema version.  Bump on a format or resolution change (2: ADR 0143; 3, 4: ADR 0142 -- D2/D3 resolution, D4/D5 TSX dispatch and re-exports).
STATE_VERSION: int = 4

#: Filename for the discovery state, adjacent to graph.json.
STATE_FILENAME: str = "discovery-state.json"


@dataclass(frozen=True)
class StateDiff:
    """Result of diffing previous state against the current file set."""

    added: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)

    @property
    def dirty(self) -> set[str]:
        """Files that need re-extraction (added + modified)."""
        return self.added | self.modified

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


@dataclass
class DiscoveryState:
    """In-memory representation of the discovery state file."""

    version: int = STATE_VERSION
    # No ``created_at`` (bd lrfu; ADR 0110 amended): re-stamped every save, so
    # tracking this file made it a diff line per no-change discover, and ADR
    # 0065 already keeps volatile per-run values in the gitignored
    # ``graph-meta.json``, which carries that wall clock as ``updated_at``.
    files: dict[str, str] = field(default_factory=dict)
    # ADR 0008 §file-level cross-check: files a strategy processed and left
    # without a graph node on purpose (e.g. empty ``__init__.py`` skipped by
    # ``python_module``). Recording the declined set lets the per-file
    # graph<->state audit distinguish "stale graph predates this file"
    # (re-run) from "strategy legitimately produces nothing for this file"
    # (skip). Only a *decision* belongs here -- see the field below.
    files_with_no_nodes: set[str] = field(default_factory=set)
    # ADR 0008 §file-level cross-check, amended (bd hch4): files this run
    # could not speak for at all -- a strategy that would not load or was
    # refused by ``--safe``, an ``external_json`` adapter that bailed, a file
    # a strategy could not parse. Folding these into the set above recorded a
    # failure as a decision, and since the exemption keys on the path alone, a
    # failure caused by anything other than the file's own content never
    # re-armed. Read through :mod:`weld._graph_anchors`: exempt from the
    # vouching audit, never from the per-file repair.
    files_with_failed_strategy: set[str] = field(default_factory=set)
    # ADR 0008 §file-level cross-check, amended again (bd um00): a source
    # entry with no ``glob``/``path``/``files`` key at all -- a command-only
    # ``external_json`` adapter is the shipped example -- resolves to an
    # empty file list, so the field above is a structural no-op for it: there
    # is no path to record a failure under. Keyed by
    # :func:`weld._discover_basis.entry_fingerprint` (content, not list
    # position, so a reorder elsewhere in ``sources:`` cannot orphan a
    # record) rather than a path. Value: ``{"kind": <short code>, "reason":
    # <bounded str>}``. Read/written through
    # :mod:`weld.strategies._strategy_failure`; consumed by
    # :func:`weld._discover_basis.sources_needing_retry` to force a retry on
    # the next incremental pass, the way the field above rides
    # ``files_missing_from_graph`` to the same effect for path-shaped
    # failures.
    sources_with_failed_strategy: dict[str, dict] = field(default_factory=dict)
    # ADR 0101 (bd esww/hfm6/nwyq/wq9i): identity of the ``graph.json`` this
    # run published -- ``{sha256, size, mtime_ns}``, or ``None`` if it
    # published none. The boolean this replaced said only *that* a graph was
    # published, never that the body on disk is still that one. Read it only
    # through :func:`weld._discover_state_check.state_vouches_for_graph`.
    published_graph: dict | None = None
    # ADR 0008 §7 (fifth fallback, bd 4fpj): fingerprint of the parsed
    # ``discover.yaml`` this run ran, or ``None`` if it recorded none.
    # ``files`` says which files were seen; it cannot say which *strategies*
    # were pointed at them, so a config edit that changes that mapping leaves
    # a delta computed under one config applied under another. Read it only
    # through :func:`weld._discover_basis.state_vouches_for_config`.
    config_fingerprint: str | None = None
    # ADR 0008 §7 (sixth fallback, bd jzxl): the field above says which
    # strategies were pointed at a file, this says what they *do* -- which
    # content hashing cannot see. Stamped by :func:`save_state`;
    # ``weld._discover_basis.strategy_fingerprint`` owns the rationale.
    strategy_fingerprint: str | None = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "files": dict(self.files),
            "files_with_no_nodes": sorted(self.files_with_no_nodes),
            "files_with_failed_strategy": sorted(self.files_with_failed_strategy),
            "sources_with_failed_strategy": {
                k: dict(v)
                for k, v in sorted(self.sources_with_failed_strategy.items())
            },
            "published_graph": self.published_graph,
            "config_fingerprint": self.config_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            # Compatibility mirror: written, never read here. An older weld
            # gates its incremental basis on this boolean; dropping it costs
            # that reader a full re-discovery per alternating run.
            "graph_published": self.published_graph is not None,
        }


def _opt_str(value: object) -> str | None:
    """A recorded fingerprint, or ``None`` for anything that is not one."""
    return value if isinstance(value, str) else None


def compute_hash(path: Path) -> str:
    """Compute SHA-256 content hash for a single file.

    Returns ``"sha256:<hex>"`` string.  Reads in 64 KiB chunks to handle
    large files without excessive memory use.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def load_state(root: Path) -> DiscoveryState | None:
    """Load discovery state from disk.

    Returns ``None`` if the state file is missing, corrupt, or has an
    incompatible schema version.  Callers should fall back to full
    discovery in all three cases.
    """
    state_path = root / ".weld" / STATE_FILENAME
    if not state_path.is_file():
        return None

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        emit(
            f"[weld] warning: corrupt discovery state file, "
            f"falling back to full discovery: {exc}"
        )
        return None

    if not isinstance(raw, dict):
        emit(
            "[weld] warning: discovery state file is not a JSON object, "
            "falling back to full discovery"
        )
        return None

    version = raw.get("version")
    if version != STATE_VERSION:
        emit(
            f"[weld] warning: discovery state version mismatch "
            f"(got {version}, expected {STATE_VERSION}), "
            f"falling back to full discovery"
        )
        return None

    files = raw.get("files", {})
    if not isinstance(files, dict):
        emit(
            "[weld] warning: discovery state 'files' is not a dict, "
            "falling back to full discovery"
        )
        return None

    raw_no_nodes = raw.get("files_with_no_nodes", [])
    files_with_no_nodes: set[str] = (
        set(raw_no_nodes) if isinstance(raw_no_nodes, list) else set()
    )
    # Absent on a state an older weld wrote, which recorded failures as
    # declines. Empty is the safe reading of that: nothing is exempted from
    # the vouching audit that was not already exempted before, and the first
    # run under this weld re-derives the split.
    raw_failed = raw.get("files_with_failed_strategy", [])
    # Coerced to str, not merely collected: ``to_dict`` sorts this set, and
    # ``mark_state_published`` round-trips a loaded state straight back
    # through it, so a hand-edited list of mixed types would raise TypeError
    # out of a best-effort path that only guards OSError.
    files_with_failed_strategy: set[str] = (
        {str(f) for f in raw_failed} if isinstance(raw_failed, list) else set()
    )
    # Absent on a state an older weld wrote (or one predating bd um00), which
    # names no footprint-less-entry failures. Empty is the safe reading, same
    # as its file-keyed sibling above: nothing is exempted that was not
    # already exempt, and the next run re-derives it. Each value is
    # re-validated as a dict, not merely collected, for the same
    # round-trip-through-``to_dict`` reason the sibling coerces to ``str``.
    raw_sources_failed = raw.get("sources_with_failed_strategy", {})
    sources_with_failed_strategy: dict[str, dict] = (
        {
            str(k): dict(v) for k, v in raw_sources_failed.items()
            if isinstance(v, dict)
        }
        if isinstance(raw_sources_failed, dict) else {}
    )
    raw_published = raw.get("published_graph")
    return DiscoveryState(
        version=version,
        files=files,
        files_with_no_nodes=files_with_no_nodes,
        files_with_failed_strategy=files_with_failed_strategy,
        sources_with_failed_strategy=sources_with_failed_strategy,
        # Absent both on a run that published no graph and on a state
        # predating the field; neither names one, so neither vouches. Not a
        # STATE_VERSION bump: that discards the inventory and forces a *full*
        # pass, where an unnamed graph costs one refresh that re-stamps it.
        published_graph=raw_published if isinstance(raw_published, dict) else None,
        # Same reading as ``published_graph`` above: absent on a state
        # predating the field, so it names no config and vouches for none.
        config_fingerprint=_opt_str(raw.get("config_fingerprint")),
        # Same reading again (bd jzxl), and not a STATE_VERSION bump for the
        # same reason: an unnamed set costs one full run that stamps it.
        strategy_fingerprint=_opt_str(raw.get("strategy_fingerprint")),
    )


def save_state(root: Path, state: DiscoveryState) -> None:
    """Write discovery state to disk atomically.

    Stamps an *unset* ``strategy_fingerprint`` (bd jzxl) -- a state
    round-tripped from disk already names the code its own run used, and
    overwriting that would vouch for code that never built the graph. Nothing
    else is stamped, so two runs over an unchanged tree write equal bytes (bd
    lrfu).

    bd 70he: the write itself is delegated to
    :func:`weld.workspace_state.atomic_write_text` rather than hand-rolling a
    temp-file-then-rename here. This function used to compute that temp file
    as a FIXED name (``state_path.with_suffix(".tmp")``), which two
    concurrent callers targeting the same root -- e.g. two live-repo
    discover() calls from sibling Bazel test actions -- could collide on: the
    second rename would target a temp file the first had already consumed,
    raising an uncaught ``FileNotFoundError``. ``atomic_write_text`` already
    solves this correctly elsewhere via ``tempfile.mkstemp``, whose name is
    unique per call by construction.
    """
    from weld.workspace_state import atomic_write_text

    state_dir = root / ".weld"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / STATE_FILENAME

    if state.strategy_fingerprint is None:
        from weld._discover_basis import strategy_fingerprint
        state.strategy_fingerprint = strategy_fingerprint(root)

    atomic_write_text(
        state_path,
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
    )


def diff_state(
    old_state: DiscoveryState | None,
    current_files: dict[str, str],
) -> StateDiff:
    """Diff previous state against current file hashes.

    Returns StateDiff with ``added``, ``modified``, and ``deleted`` sets.
    """
    if old_state is None:
        return StateDiff(added=set(current_files.keys()))

    old_files = old_state.files
    current_keys = set(current_files.keys())
    old_keys = set(old_files.keys())

    added = current_keys - old_keys
    deleted = old_keys - current_keys
    modified = {
        p for p in current_keys & old_keys
        if current_files[p] != old_files[p]
    }
    return StateDiff(added=added, modified=modified, deleted=deleted)


def build_file_hashes(root: Path, files: list[str]) -> dict[str, str]:
    """Compute content hashes for a list of repo-relative file paths.

    Skips files that cannot be read (e.g. broken symlinks).
    """
    result: dict[str, str] = {}
    for rel_path in files:
        try:
            result[rel_path] = compute_hash(root / rel_path)
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Graph helpers for incremental merge (ADR 0008 sections 4-5)
# ---------------------------------------------------------------------------

def purge_stale_nodes(
    nodes: dict[str, dict],
    edges: list[dict],
    stale_files: set[str],
) -> tuple[dict[str, dict], list[dict]]:
    """Remove nodes whose ``props.file`` matches any file in *stale_files*.

    Edges are purged by ADR 0074's provenance rule
    (:func:`weld._incremental_purge.purge_edges_by_provenance`): an edge
    carrying a usable ``props.provenance.file`` is dropped iff *that* file is
    stale -- not because an endpoint node was purged -- so a ``calls`` edge
    from a clean sibling into a dirty file's symbol survives and the dirty
    re-parse re-mints the same-id endpoint. Edges without usable provenance
    keep the conservative endpoint-membership purge. This preserves the
    incremental == full byte-identity invariant under parse-only-dirty
    re-extraction (the cjij.2 defect the amendment fixes).

    Used before merging incremental results so modified/deleted files get a
    clean slate.

    *stale_files* is in the index spelling and ``props.file`` in whichever
    spelling its strategy chose, so the membership test goes through the
    canonical form (:mod:`weld._rel_path`; identity on POSIX). Off POSIX the
    two disagree and a genuinely dirty file's node is never purged (bd pbi8).
    The canonical set is passed on to the edge purge rather than the raw one,
    so both tiers judge staleness by the same yardstick.

    bd g7rs / bd pkz2s / bd oao53 / bd ukt95 / bd n4nvt / bd 5ouuf / bd
    5038-q4t3d: the *emptied* id set is computed by
    :func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`,
    which unions five independently-scoped placeholder-node rules (see that
    function's docstring for all five), each purged only once every
    file-purge and provenance-edge-purge pass above has already run.
    The widened id set is then folded through one more such pass so any edge
    now pointing at a freshly-removed node is judged by the identical rule
    the first pass already applied -- a provenance-carrying survivor is left
    dangling for :mod:`weld._discover_orphan_edges` to widen-and-retry
    downstream, never silently kept here. That retry is a repair, not a
    licence: q4t3d's two rules removed live nodes here on every round that
    purged anything, and only its doubled merge pass showed it.
    """
    if not stale_files:
        return nodes, edges

    stale = canonical_rel_paths(stale_files)
    removed_ids: set[str] = set()
    surviving_nodes: dict[str, dict] = {}
    for nid, node in nodes.items():
        if canonical_rel_path(node.get("props", {}).get("file")) in stale:
            removed_ids.add(nid)
        else:
            surviving_nodes[nid] = node

    if not removed_ids:
        return nodes, edges

    surviving_edges = purge_edges_by_provenance(edges, stale, removed_ids)

    emptied = emptied_placeholder_node_ids(surviving_nodes, surviving_edges)
    if emptied:
        surviving_nodes = {
            nid: n for nid, n in surviving_nodes.items() if nid not in emptied
        }
        removed_ids |= emptied
        surviving_edges = purge_edges_by_provenance(
            surviving_edges, stale, removed_ids
        )

    return surviving_nodes, surviving_edges


# The source-level audit over ``existing_graph`` / ``source_file_map`` moved
# to :func:`weld._graph_anchors.files_missing_strategy_outputs` (bd um00),
# alongside its entry-keyed sibling for footprint-less sources -- both are
# graph<->state predicates, not content-hash bookkeeping, and the move made
# room for ``sources_with_failed_strategy`` below.

# Source-entry glob resolution lives in :mod:`weld._source_resolve` -- it is
# not part of the content-hash index this module owns. Import it from there.

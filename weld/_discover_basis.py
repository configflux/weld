"""Is the recorded discovery state still a valid incremental basis?

ADR 0008 section 7 enumerates the reasons a run must fall back to full
discovery. They accumulated in three places -- four inline in
``_discover_single_repo``, the graph-identity one in
:mod:`weld._discover_state_check`, and (before bd 4fpj) the config one
nowhere at all. This module is the single place that asks the question, so
a new reason has an obvious home and the answer is auditable in one read.

The reasons share one rule: ``discovery-state.json``'s ``files`` inventory
is a delta *basis*, and a basis is only valid against the world it was
recorded in. It was recorded beside a particular graph body (ADR 0101), and
it was recorded under a particular ``discover.yaml``. Change either and the
delta is computed from one world and applied to another.

The config half is bd 4fpj: ``files`` records which files were seen, never
which strategies were pointed at them. Adding a source entry over files that
did not themselves change produces an empty dirty set, so the entry never
runs -- and the source-level audit in
:func:`weld.discovery_state.files_missing_strategy_outputs` cannot catch it
either, because it asks whether a source's files have *any* node and files
covered by a pre-existing entry already do. The config change is then
silently a no-op, for as long as the tree stays clean, while the graph
reports itself fresh.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from weld._discover_state_check import state_vouches_for_graph
from weld._notice import emit
from weld.discovery_state import DiscoveryState


def config_fingerprint(config: dict) -> str:
    """Fingerprint the *parsed* ``discover.yaml`` mapping.

    Canonicalized by parse rather than by bytes: hashing the file body would
    fire on a comment, a blank line, or a reordered key, each of which costs a
    full re-discovery and teaches the user that the signal is noise. Hashing
    the parsed mapping with ``sort_keys`` fires on semantic change only.

    The *whole* config is covered, not just ``sources``. ``topology`` feeds
    ``_apply_topology_overlay`` in post-processing, which the no-change fast
    path skips outright -- so a topology-only edit is the same silent no-op
    that motivated this, in a different key. Covering the whole mapping also
    means a config key added later is protected on the day it lands, rather
    than on the day someone remembers to extend this function.

    ``default=str`` keeps a value the YAML parser produced but ``json`` will
    not serialize from raising here: a fingerprint that is merely coarse
    costs an unnecessary full run, while an exception costs the discover.
    """
    canonical = json.dumps(
        config, sort_keys=True, ensure_ascii=False, default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def entry_fingerprint(source: dict) -> str:
    """Fingerprint one ``discover.yaml`` source entry's own config dict.

    Sibling to :func:`config_fingerprint` above, scoped to a single entry
    rather than the whole mapping (bd um00): a command-only ``external_json``
    source resolves no files at all, so nothing hashes its *content* the way
    ADR 0008 hashes a file's bytes -- this is the identity a per-entry
    failure record keys on instead (see
    :mod:`weld.strategies._strategy_failure`).

    Content-hashed rather than keyed by list position, so a reorder
    elsewhere in ``sources:`` cannot orphan a record for an entry that did
    not itself change -- the entry-identity problem this ADR's
    config-fingerprint amendment already documents ("config entries carry no
    stable identity across a reorder"). Editing the entry itself changes
    both its own fingerprint and the whole-config one, which forces a full
    discovery (section 7) that re-attempts every entry under its new
    identity -- so "changes when the entry changes" falls out for free
    rather than needing its own tracking.

    Same canonicalization as :func:`config_fingerprint`: parsed, not raw
    bytes, sorted keys, deterministic.
    """
    return config_fingerprint(source)


def sources_needing_retry(
    sources: list[dict], old_state: DiscoveryState | None,
) -> frozenset[str]:
    """Entry ids an incremental pass must force-run despite no file dirt.

    A footprint-less entry can never appear in the file-content dirty set
    computed elsewhere in the incremental path, because it resolves no files
    at all -- so a recorded failure for one would otherwise never re-arm (bd
    um00, verified empirically: a command-only ``external_json`` source runs
    exactly once, on the first full discovery, and never again while the
    incremental basis holds, success or failure alike).

    Intersected with the *current* entries' own fingerprints so a removed or
    edited entry's stale record is never force-run: editing or removing an
    entry already changes the whole-config fingerprint and forces a full run
    (section 7), which does not consult this function at all.
    """
    if old_state is None or not old_state.sources_with_failed_strategy:
        return frozenset()
    current_ids = {entry_fingerprint(s) for s in sources}
    return frozenset(current_ids & set(old_state.sources_with_failed_strategy))


def strategy_fingerprint(root: Path) -> str:
    """Fingerprint the strategy *code* available at *root* (bd jzxl).

    The config half above covers which strategies ``discover.yaml`` points at
    a file. This covers what those strategies *do*. ``files`` records source
    content, so a strategy rewrite over an unchanged tree produces an empty
    dirty set: only nodes whose own source file also changed get re-emitted,
    and the rest keep props the previous strategy version wrote -- at
    ``confidence: definite``, with ``wd stale`` reading clean. Confidently
    wrong rather than absent, and nothing downstream can find it: the ADR
    0008 per-file repair looks for files with *zero* nodes, and these have
    nodes, just the wrong generation's.

    Both strategy directories are covered: the bundled
    :mod:`weld.strategies` (which changes on a weld upgrade, and on every
    edit when weld is being developed against itself -- which is where this
    was found) and the project-local ``.weld/strategies`` overrides of ADR
    0024. Content-hashed rather than stat-hashed: mtimes change on every
    clone, checkout and reinstall without the code changing, and each of
    those would buy a full re-discovery for nothing.

    Deliberately coarse -- the whole of both directories, not the transitive
    imports of the entries ``discover.yaml`` wires. Resolving that set
    exactly means tracking each strategy's private helper modules
    (``test_peer`` alone reaches two), and getting it wrong silently
    under-reports, which is the failure this exists to prevent. The cost of
    coarseness is a full run when an unrelated strategy changed; for an
    installed weld that is never, and for someone editing a strategy it is
    the run they wanted anyway. Measured at 7.6 ms over 133 files, against a
    discover this guards that takes seconds.

    Unreadable files are folded in by name alone rather than raising: a
    fingerprint that is merely coarse costs an unnecessary full run, while an
    exception costs the discover.
    """
    from weld import strategies as _bundled

    digest = hashlib.sha256()
    for directory in (Path(_bundled.__file__).parent, root / ".weld" / "strategies"):
        if not directory.is_dir():
            continue
        # Name the directory, so a file moving between bundled and local
        # changes the fingerprint even if its bytes do not.
        digest.update(f"\0dir:{directory.name}\0".encode("utf-8"))
        for path in sorted(directory.glob("*.py")):
            digest.update(f"\0{path.name}\0".encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
    return f"sha256:{digest.hexdigest()}"


def state_vouches_for_strategies(
    state: DiscoveryState | None, fingerprint: str,
) -> bool:
    """True when *state* was recorded under the strategy code *fingerprint* names.

    Same fail-closed reading as :func:`state_vouches_for_config`: a state
    predating the field names no strategy code and vouches for none.
    """
    if state is None or state.strategy_fingerprint is None:
        return False
    return state.strategy_fingerprint == fingerprint


def state_vouches_for_config(
    state: DiscoveryState | None, fingerprint: str,
) -> bool:
    """True when *state* was recorded under the config *fingerprint* names.

    A state predating the field records no fingerprint, so it names no config
    and vouches for none -- the same reading ``published_graph`` gets. Fail
    closed in both directions: over-reporting doubt costs one full run that
    re-stamps the state, under-reporting it runs a stale strategy set for as
    long as nobody edits a file.
    """
    if state is None or state.config_fingerprint is None:
        return False
    return state.config_fingerprint == fingerprint


def incremental_basis_valid(
    old_state: DiscoveryState | None,
    graph_path: Path,
    existing_graph: dict | None,
    fingerprint: str,
    strategy_fp: str | None = None,
) -> bool:
    """True when an incremental run may proceed from *old_state*.

    Emits the notice naming which reason failed, so a user paying for an
    unexpected full run can see why. Ordered cheapest-first: the config
    comparison is a string equality on an already-parsed mapping, the
    strategy comparison likewise (its digest is computed once per run by the
    caller), while :func:`state_vouches_for_graph` may fall through to
    digesting a multi-megabyte graph body.

    *strategy_fp* defaults to ``None`` for callers that do not compute it,
    which skips the check rather than failing it -- an omitted argument is
    not evidence that strategy code changed, and treating it as such would
    cost every such caller a full run forever.
    """
    if old_state is None:
        emit("[weld] notice: no discovery state file, running full discovery")
        return False
    if not graph_path.is_file():
        emit("[weld] notice: no graph.json found, running full discovery")
        return False
    if existing_graph is None:
        emit("[weld] warning: corrupt graph.json, falling back to full discovery")
        return False
    if not state_vouches_for_config(old_state, fingerprint):
        # Split by which half is missing so the message is true: an upgrade
        # from a weld that never wrote the field must not be reported as a
        # config edit the user did not make.
        if old_state.config_fingerprint is None:
            emit(
                "[weld] notice: discovery state records no config fingerprint, "
                "running full discovery"
            )
        else:
            emit(
                "[weld] notice: .weld/discover.yaml changed since the graph was "
                "built, running full discovery"
            )
        return False
    if strategy_fp is not None and not state_vouches_for_strategies(
        old_state, strategy_fp,
    ):
        # Split for the same reason the config branch splits: an upgrade from
        # a weld that never wrote the field is not a strategy edit the user
        # made, and reporting it as one sends them looking for a change that
        # is not there.
        if old_state.strategy_fingerprint is None:
            emit(
                "[weld] notice: discovery state records no strategy "
                "fingerprint, running full discovery"
            )
        else:
            emit(
                "[weld] notice: strategy code changed since the graph was "
                "built, running full discovery"
            )
        return False
    if not state_vouches_for_graph(old_state, graph_path):
        # ``files`` is a valid delta basis only for the graph its own run
        # published *and* still on disk; against any other body the delta is
        # computed from one generation's content and applied to another's.
        # Nothing downstream can recover from that -- the ADR 0008 per-file
        # repair finds files with *zero* nodes, while a file whose content
        # drifted still has nodes, just the wrong ones. ADR 0101, second and
        # third amendments (bd nwyq, bd wq9i). "Cannot vouch for", not "does
        # not describe": what is known is that this inventory makes no claim
        # on the body now being read.
        emit(
            "[weld] notice: discovery state cannot vouch for graph.json, "
            "running full discovery"
        )
        return False
    return True

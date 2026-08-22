"""Which graph a discovery state was written beside, and how it is stamped.

ADR 0008 keys incremental re-runs on file content hashes. That is
not enough on its own: a graph rolled back (or committed) at an
older revision can lack nodes for files the current
``discovery-state.json`` already records at their on-disk SHAs. The
source-level audit in
:func:`weld._graph_anchors.files_missing_strategy_outputs` only
catches the case where every file in a source is missing -- a
single freshly-added file slips through whenever any sibling in the
same source still has nodes.

Nor is the per-file audit enough on its own. It compares a state
against *a* graph; it cannot tell whether that graph is the one the
state was written beside. So this module also owns the binding between
the two -- :func:`published_graph_token` records which graph a run
published, :func:`state_vouches_for_graph` asks whether the body now on
disk is still that one -- which is what makes both the incremental
basis decision and the ADR 0101 coverage probe answerable at all.

This module hosts that binding and the matching state-save helpers. The
pure per-file audit predicates it consumes live beside it in
:mod:`weld._graph_anchors`. Carved out of :mod:`weld.discovery_state` to keep
that file under the 400-line cap (CLAUDE.md "Line-Count Policy").
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._discover_vanished import vanished_since_inventory
from weld._graph_anchors import (
    files_missing_from_graph,
    files_with_no_nodes_and_failed,
    graph_files_with_nodes,
)
from weld._notice import emit
from weld.discovery_state import (
    DiscoveryState,
    compute_hash,
    load_state,
    save_state,
)

#: Basename of the graph an inventory may vouch for. Only the canonical
#: artifact counts: a ``--output`` elsewhere leaves readers on the older
#: body, which is exactly the divergence being detected.
_CANONICAL_GRAPH = "graph.json"


def published_graph_token(graph_path: Path) -> dict | None:
    """Identity of the graph at *graph_path*: what it is, and where it sat.

    ``sha256`` is what the graph *is*, and it is the field that survives a
    copy: gate 5 lands a sibling's graph bytes verbatim, so the seeded
    inventory still names the body beside it even though the file is a new
    inode. ``size`` and ``mtime_ns`` record where those bytes sat when the
    digest was taken, which is what lets the common case answer without
    re-reading a multi-megabyte file (see :func:`state_vouches_for_graph`).

    The stat is taken on both sides of the read and must agree. A graph
    rewritten mid-digest would otherwise yield a token whose cheap half
    vouches for a body its digest never described -- a smaller copy of the
    bug this whole mechanism exists to close. ``None`` when the graph cannot
    be read or would not hold still: no token is recorded, which reads as
    "vouches for nothing" and costs one refresh.
    """
    try:
        before = graph_path.stat()
        digest = compute_hash(graph_path)
        after = graph_path.stat()
    except OSError:
        return None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None
    return {
        "sha256": digest,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
    }


def _well_formed_token(token: object) -> bool:
    """True when *token* is a complete token as this module writes them.

    Checked before anything is believed, including the cheap ``stat``
    shortcut. A token carrying only ``(size, mtime_ns)`` would otherwise
    vouch on placement alone -- the weak encoding this whole mechanism
    exists to replace -- and :func:`_token_for_published_graph` would carry
    it forward untouched for as long as the graph sat still. Nothing this
    module writes is ever partial, so a partial one means the state was
    truncated or edited, and doubt is the only honest answer.
    """
    return (
        isinstance(token, dict)
        and isinstance(token.get("sha256"), str)
        and isinstance(token.get("size"), int)
        and isinstance(token.get("mtime_ns"), int)
    )


def _token_pins_file(token: dict, graph_path: Path) -> bool:
    """True when *token*'s ``(size, mtime_ns)`` still match *graph_path*.

    The cheap half of the check. weld writes ``graph.json`` by atomic
    rename, so any rewrite lands a fresh inode and therefore a fresh
    ``mtime_ns`` -- the same identity pair
    :func:`weld._graph_meta_sidecar.read_staleness_meta` uses to prove its
    mirror still belongs to the graph beside it.
    """
    try:
        info = graph_path.stat()
    except OSError:
        return False
    return (
        token.get("size") == info.st_size
        and token.get("mtime_ns") == info.st_mtime_ns
    )


def state_vouches_for_graph(
    state: DiscoveryState | None, graph_path: Path,
) -> bool:
    """True when *state*'s inventory describes the graph now at *graph_path*.

    The full form of the rule bd nwyq established: ``files`` is a valid
    delta basis only for the graph its own run published. Recording *that*
    a graph was published (the boolean this replaced) proves the writing
    run's half and nothing about the body a reader will now load, so a body
    replaced under a state that already vouched for it was accepted --
    reachable through ADR 0096 gate 5, whose copy deliberately keeps a state
    file already present at the destination while landing a *foreign* graph
    beside it (bd wq9i).

    Three answers, cheapest first:

    * no token -- the run published no graph, or the state predates the
      field -- so there is nothing to compare: ``False``.
    * the token still pins the file: ``True``, at the cost of one ``stat``.
      This is the steady state, and the read path must stay this cheap
      (bd aqqa moved the freshness precheck off the graph body for the same
      reason).
    * otherwise the bytes decide. A size mismatch settles it without
      hashing; equal sizes fall through to the digest, which is what keeps
      a legitimate seed incremental: the landed graph is byte-for-byte the
      source's, so it still answers ``True`` at a new inode.

    Anything malformed answers ``False``. Over-reporting doubt costs one
    refresh that re-stamps the state; under-reporting it serves a confident
    wrong answer for as long as the tree stays clean.
    """
    token = state.published_graph if state is not None else None
    if not _well_formed_token(token):
        return False
    if _token_pins_file(token, graph_path):
        return True
    try:
        if graph_path.stat().st_size != token["size"]:
            return False
        return compute_hash(graph_path) == token["sha256"]
    except OSError:
        return False


def inventory_describes_graph(
    state: DiscoveryState | None, graph_path: Path,
) -> bool:
    """True when *state*'s file coverage is actually true of the body at *graph_path*.

    :func:`state_vouches_for_graph` asks *which* graph an inventory named;
    this asks whether what it says about that graph holds. They are
    independent, and only the pair is sound: an inventory can name the body on
    disk exactly and still claim node-bearing files that body does not anchor.

    That shape is invisible to every other freshness signal, which is why it
    has to be refused at the moment the claim is made rather than detected
    later. ``files_missing_from_inventory`` reports in-scope files the
    inventory *never recorded*; a file recorded in ``files``, absent from
    ``files_with_no_nodes``, and absent from the graph is covered as far as it
    can see, so ``coverage_stale`` reads clean, so auto-refresh never runs, so
    the ADR 0008 per-file repair below -- which closes the hole in a single
    pass -- is never scheduled. Three tracked modules answered "no such
    symbol" that way while freshness reported no staleness at all
    (bd qmbp; 104 files were absent on the checkout that reported it).

    The audit is :func:`files_missing_from_graph` over the whole inventory,
    which is precisely the repair discovery would schedule. Reading the body
    costs one parse (~280 ms on a 17 MB graph); this runs only where a graph
    was just published, never on the read path, which ADR 0101 section 4 /
    bd aqqa deliberately keep off the graph body.

    A hole the inventory already *names* as one is not a misdescription (bd
    hch4): ``files_with_failed_strategy`` records the files this run could not
    speak for, which is a true statement about the body beside it. Failing the
    audit on those would cost every degraded environment -- no ``tree_sitter``,
    a habitual ``--safe``, an ``external_json`` command that is not installed
    -- its incremental basis on every single run, forever. The hole is carried
    by the repair queue instead: ``files_missing_from_graph`` keeps returning
    those files, so the next pass re-runs their sources. Vouching answers
    "is this inventory about this body"; the repair queue answers "does this
    body have known holes to retry". They are different questions.

    Anything unreadable or malformed answers ``False``. Fail closed: the cost
    of doubt is one refresh, and the cost of misplaced confidence is a hole
    that never heals.
    """
    if state is None:
        return False
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(graph, dict):
        return False
    unexplained = files_missing_from_graph(
        state, set(state.files), graph_files_with_nodes(graph),
    )
    return not (unexplained - state.files_with_failed_strategy)


def _token_for_published_graph(root: Path) -> dict | None:
    """Token to record for *root*'s canonical graph, re-hashing only if needed.

    A no-change refresh leaves ``graph.json`` untouched -- ``bd 85tb.2``
    skips the body rewrite outright -- so the token already on disk still
    pins the file exactly, and the digest it carries is still that file's.
    Reusing it keeps the hot path free of a multi-megabyte hash; every other
    path pays one, which is the honest price of naming what was published.
    """
    graph_path = root / ".weld" / _CANONICAL_GRAPH
    prior = load_state(root)
    token = prior.published_graph if prior is not None else None
    if _well_formed_token(token) and _token_pins_file(token, graph_path):
        return token
    return published_graph_token(graph_path)


def save_state_for_graph(
    root: Path,
    current_hashes: dict[str, str],
    graph: dict,
    *,
    graph_published: bool,
    config_fingerprint: str | None = None,
    strategy_failed: set[str] | None = None,
    source_failed: dict[str, dict] | None = None,
) -> None:
    """Persist :class:`DiscoveryState` aligned with the just-built graph.

    Captures both the file content hashes and the set of currently
    discovered files that produced no graph nodes, so the next
    incremental run's per-file audit can distinguish intentional
    empty output from a graph<->state mismatch.

    *strategy_failed* names the files this run could not speak for -- a
    strategy that would not load, an ``external_json`` adapter that refused, a
    file a strategy was handed and could not parse. They are split out of
    ``files_with_no_nodes`` rather than folded into it, because that set is
    read as "the strategy decided nothing belongs here" and a strategy that
    never ran decided nothing at all (bd hch4). Files that vanished between
    the inventory walk and the strategy's own listing join them for the same
    reason, and can only be spotted from here
    (:func:`weld._discover_vanished.vanished_since_inventory`, bd rt65).

    Bounded twice before it is recorded: to the inventory, since a path
    outside ``files`` is claimed by nothing and carrying it would only let
    the set grow without limit; and to
    the files the graph does not anchor, since an incremental pass reports a
    whole source when its strategy will not load, and a clean sibling whose
    nodes survived the purge is not a hole.

    The set is re-derived every pass rather than carried forward, and needs no
    merge with the prior state's: a recorded failure is exempt from nothing
    the per-file repair consults, so it is dirty on the next pass, so its
    source re-runs and re-reports it for as long as the failure lasts. The
    same fact is why the no-change fast path -- which passes no
    *strategy_failed* and would therefore clear the set -- cannot run while a
    failure is outstanding.

    *graph_published* records whether this run also wrote *graph* to
    ``.weld/graph.json``. Only then is the inventory evidence about the
    graph a reader will load; a run that keeps its graph to itself (the
    ``--output`` elsewhere / library-caller shape, or any interruption
    before the graph lands) writes an inventory that describes a graph
    nobody can see. What gets recorded is the *identity* of that graph
    (:func:`published_graph_token`), so a later reader can ask whether the
    body still on disk is the one this inventory was built beside --
    ``files`` is never trusted unconditionally. See
    :func:`state_vouches_for_graph` and
    :func:`weld._staleness_coverage.coverage_stale`.

    *config_fingerprint* names the ``discover.yaml`` this run ran, so a later
    run can refuse this inventory once the config changes (bd 4fpj; see
    :mod:`weld._discover_basis`). ``None`` records no claim.

    *source_failed* (bd um00) is the entry-keyed sibling of *strategy_failed*,
    for source entries with no file footprint at all -- see
    :mod:`weld.strategies._strategy_failure`. Carried straight through with
    no bounding: unlike *strategy_failed* it needs none, since a
    footprint-less entry has no file to test graph-anchoring against, and it
    is already scoped to entries this run actually attempted (drained from
    the run's own strategy context, never read back from a prior state).

    Callers must have written the graph before calling this, which
    ``finalize_single_repo`` guarantees by ordering: the state is the
    inventory *of* ``graph.json``, so it is published after it.
    """
    current = set(current_hashes.keys())
    no_nodes, failed = files_with_no_nodes_and_failed(current, graph, strategy_failed)
    vanished = vanished_since_inventory(root, no_nodes)
    no_nodes -= vanished
    failed |= vanished
    save_state(
        root,
        DiscoveryState(
            files=dict(current_hashes),
            files_with_no_nodes=no_nodes,
            files_with_failed_strategy=failed,
            sources_with_failed_strategy=dict(source_failed or {}),
            published_graph=(
                _token_for_published_graph(root) if graph_published else None
            ),
            config_fingerprint=config_fingerprint,
        ),
    )


def mark_state_published(root: Path, written: Path) -> None:
    """Vouch *root*'s discovery state for *written*, if that is *root*'s graph.

    The counterpart to :func:`save_state_for_graph` for the callers that build
    the graph with ``write_graph=False`` and land the canonical copy
    themselves -- the ``wd discover`` CLI tail and ``wd warm``. Discovery
    already wrote an inventory that could not yet claim to describe a readable
    graph; once ``.weld/graph.json`` lands from that same run the claim holds,
    so the flag is set. Without this a bare ``wd discover`` would leave the
    root reading stale and buy a redundant refresh on the next read.

    A path aimed anywhere else (``--output /tmp/x.json``) is deliberately NOT
    vouched for: readers still load the older graph, which is exactly the
    divergence ADR 0101's amended coverage probe exists to catch. Best-effort
    -- a state that cannot be re-read or re-written costs a refresh, never a
    failed discover.

    The early return is "already vouches for *this* body", not "has vouched
    for something": a state naming some earlier graph must be re-stamped
    once this run's body lands, or it would keep vouching for a graph that
    is no longer there (bd wq9i).

    This is the only vouching path that binds an inventory to a graph body it
    did not itself build -- :func:`save_state_for_graph` derives both
    ``files_with_no_nodes`` and ``files_with_failed_strategy`` from the run in
    hand, so its half of the invariant holds by construction: every file it
    leaves unanchored, it has already named. Here the state comes off disk
    and the body comes off disk, and nothing guarantees they came from the
    same run: an inventory written beside a graph that never landed (the
    ``write_graph=False`` shape) would otherwise be stamped onto whatever
    body happens to be sitting there. So the coverage claim is checked before
    it is believed (:func:`inventory_describes_graph`, bd qmbp).

    Refusing to stamp converges rather than loops: no token means the state
    vouches for nothing, so ``coverage_stale`` reports the hole, the refresh
    that follows runs full discovery (a non-vouching state is not an
    incremental basis), and that run publishes graph and inventory together --
    at which point the audit passes and the stamp lands.
    """
    try:
        if written.resolve() != (root / ".weld" / _CANONICAL_GRAPH).resolve():
            return
        state = load_state(root)
        if state is None or state_vouches_for_graph(state, written):
            return
        # Digest first, then audit, then re-check the digest still pins the
        # file: the token must name the very bytes the audit read. A body
        # rewritten between the two would otherwise be vouched for on another
        # body's coverage -- the same mid-flight hazard
        # ``published_graph_token`` guards with its own paired stat, and the
        # same bug in miniature.
        token = published_graph_token(written)
        vouchable = (
            token is not None
            and inventory_describes_graph(state, written)
            and _token_pins_file(token, written)
        )
        if not vouchable:
            emit(
                "[weld] notice: discovery state does not describe the graph "
                "just written; leaving it unvouched so the next read "
                "re-discovers"
            )
            return
        state.published_graph = token
        save_state(root, state)
    except OSError:
        pass

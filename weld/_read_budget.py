"""Shared byte-budget primitive for the bounded read envelope (ADR 0082).

ADR 0082 makes a bounded, self-reporting read envelope a product contract, and
:mod:`weld.read` implemented it for ``query`` / ``context`` / ``brief``. The
same overflow shape exists on every other agent-facing read that emits node
lists plus an edge list -- ``impact`` / ``callers`` / ``references`` /
``trace`` -- so the pruning loop lives here instead of being written a fourth
time beside each surface.

Every bounded read is the same three moves:

1. Rank the droppable items in a **total order**.
2. Keep the largest prefix whose serialized envelope fits the budget, dropping
   the now-dangling edges along with the items they belonged to.
3. Report the drop. Never silent-truncate.

:func:`fit_buckets` is that loop, parameterised by which envelope keys hold
droppable items and how the surface reports the drop. Determinism (ADR 0012):
the budget is a pure function of content (one canonical serialization), the
prune order is a total order, and the reported counts are folded *into* the
measured bytes -- so the same graph and arguments always yield a byte-identical
envelope that genuinely fits.

``DEFAULT_READ_BUDGET_BYTES`` bounds the bytes the client receives, not an
intermediate value (ADR 0082 amendment, bd hwwo). ``weld._mcp_dispatch``
stamps an additive ``freshness`` object onto every dispatched MCP read, and
``weld.mcp_server`` attaches a ``children_status`` map at a federated root --
both *after* a handler has already shaped its own answer to fit a budget. A
handler that shaped all the way to ``DEFAULT_READ_BUDGET_BYTES`` would let
those stamps push the dispatched payload over it, which is exactly the gap bd
hwwo measured (a uniform +115 B from ``freshness`` on this repo, and an
unbounded per-child +100-200 B from ``children_status`` at a federated root).
:data:`EFFECTIVE_READ_BUDGET_BYTES` is what a handler actually prunes to by
default -- ``DEFAULT_READ_BUDGET_BYTES`` minus a fixed
:data:`TRANSPORT_RESERVE_BYTES` headroom -- so the stamps have room to land
inside the number the client was promised. ``children_status`` additionally
cannot be bounded by a *fixed* reserve on its own (it is one entry per child,
so it is unbounded in child count): :func:`bound_dict_to_budget` gives it the
same fit-and-report treatment as any other droppable bucket, sized against
its own slice of the reserve (:data:`CHILDREN_STATUS_RESERVE_BYTES`), so the
combined promise holds regardless of workspace size.
"""

from __future__ import annotations

import json
from typing import Callable

#: Default byte budget for the agent-facing read envelope: the contract
#: number every parity/dispatched-bytes test compares against, because it is
#: what the *client* receives -- not an intermediate handler value. Defined
#: here, beside the primitive that enforces it, so every bounded surface
#: shares one number; :mod:`weld.read` re-exports it under its established
#: name. 64 KiB is a conservative fraction of the agent tool-result cap,
#: measured against the canonical ``indent=2`` serialization below (the
#: CLI's larger emit shape), so a payload that fits this budget fits under
#: either surface's actual serialization.
DEFAULT_READ_BUDGET_BYTES: int = 65_536

#: Headroom reserved for the ``freshness`` object every dispatched MCP read
#: in ``weld._mcp_read.FRESHNESS_TOOLS`` carries (ADR 0083 transport
#: metadata). Measured at ~85-130 B for realistic branch names (a live git
#: branch name, not request-controlled input -- see
#: ``weld._mcp_read.freshness_for``); 256 B covers a branch name up to
#: roughly 170 characters, far past any real one, with room to spare for a
#: multi-digit ``commits_behind``. Like every other floor in this module
#: (:data:`BUDGET_EXCEEDED_KEY`'s target header, impact's risk verdict), this
#: is a generous best-effort bound, not a mathematical guarantee against an
#: adversarial branch name -- the same tradeoff ADR 0082 already accepts
#: elsewhere for non-prunable summary fields.
FRESHNESS_STAMP_RESERVE_BYTES: int = 256

#: Headroom reserved for the ``children_status`` map ``weld.mcp_server``
#: attaches at a federated root. Unlike the freshness reserve this is not a
#: best-effort guess: :func:`bound_dict_to_budget` prunes ``children_status``
#: itself to fit inside this many bytes, reporting the omitted count, so the
#: bound is exact regardless of how many children a workspace registers.
CHILDREN_STATUS_RESERVE_BYTES: int = 3_840

#: Total transport headroom carved out of ``DEFAULT_READ_BUDGET_BYTES``
#: (bd hwwo). 4 KiB, split 256 B / 3,840 B between the two stamps above.
TRANSPORT_RESERVE_BYTES: int = (
    FRESHNESS_STAMP_RESERVE_BYTES + CHILDREN_STATUS_RESERVE_BYTES
)

#: The budget a handler actually shapes its own answer to, by default (used
#: as the ``budget`` default in :mod:`weld.read`, :mod:`weld.read_traversal`
#: and :mod:`weld._read_impact`). Exactly 60 KiB: ``DEFAULT_READ_BUDGET_BYTES``
#: minus :data:`TRANSPORT_RESERVE_BYTES`, so a handler's own shaped bytes plus
#: every additive MCP transport stamp still sum to at most
#: ``DEFAULT_READ_BUDGET_BYTES`` -- the bytes the client actually receives.
EFFECTIVE_READ_BUDGET_BYTES: int = DEFAULT_READ_BUDGET_BYTES - TRANSPORT_RESERVE_BYTES

#: Reported-drop key for the surfaces whose envelope has no
#: ``omitted_neighbors`` object of its own (``impact`` / ``callers`` /
#: ``references`` / ``trace``). ``query`` / ``context`` keep reporting through
#: ``omitted_neighbors.size_capped``; the reason *name* is deliberately the
#: same word on every surface.
SIZE_CAPPED_KEY: str = "size_capped"

#: Key stamped inside a surface's drop report when the budget could **not** be
#: met -- i.e. everything droppable was dropped and the payload is still over.
#: The budget is a best-effort prune, not a guarantee: what remains after the
#: last droppable item is gone (a target header, a risk verdict, per-bucket
#: counts) has no lower bound this loop controls. Before bd gfpl that floor was
#: silent, so an over-cap envelope was indistinguishable from a comfortable one
#: and ``size_capped`` read like a success report while the answer was twice the
#: budget. A consumer that cannot afford the bytes needs to know that asking
#: again will not help; it needs a narrower question.
BUDGET_EXCEEDED_KEY: str = "budget_exceeded"


#: Human-readable drop notice, shared by every bounded surface so the wording a
#: reader sees does not depend on which read they ran.
READ_BUDGET_MESSAGE = (
    "read-budget: dropped {dropped} lowest-priority {noun} to fit the bounded "
    "read envelope (size_capped); pass --full-size / full_size=True for the "
    "unbounded payload."
)

#: Companion notice for the floor :data:`BUDGET_EXCEEDED_KEY` reports.
OVER_BUDGET_MESSAGE = (
    "read-budget: everything droppable was dropped and this payload is STILL "
    "over the budget (budget_exceeded); what remains is the target header, the "
    "risk verdict and the surface counts, which the budget may not prune. Ask "
    "a narrower question (lower --depth, or a more specific target)."
)


def exceeds_budget(envelope: object, budget: int) -> bool:
    """Return whether *envelope* is still over *budget* after shaping.

    Kept beside :func:`envelope_bytes` so every bounded surface asks the
    question the same way and against the same canonical serialization.
    """
    return envelope_bytes(envelope) > budget


def is_shapeable(envelope: object, buckets: tuple[str, ...]) -> bool:
    """Return whether *envelope* is a payload worth bounding.

    An error payload (``callers`` on an unknown symbol, ``impact`` on a bad
    target) and anything that is not a dict are passed through untouched, for
    the same reason :func:`weld._mcp_read.stamp_freshness` leaves them alone:
    a structured error must stay clean, and there is nothing there to bound.
    """
    return (
        isinstance(envelope, dict)
        and "error" not in envelope
        and any(key in envelope for key in buckets)
    )


def envelope_bytes(obj: object) -> int:
    """Return the canonical serialized byte length of *obj* (ADR 0012).

    Uses ``indent=2`` / ``ensure_ascii=False`` -- the CLI's ``_out`` emit shape,
    which is larger than the MCP server's compact ``json.dumps`` -- so a payload
    that fits this budget fits under either surface's actual serialization. A
    pure function of content: no wall-clock, no randomness, stable key order.
    """
    return len(json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def dedangle(edges: list[dict], keep_ids: set) -> list[dict]:
    """Keep only edges whose *both* endpoints are in *keep_ids* (no dangles).

    An edge naming a node the envelope no longer carries is worse than useless
    to an agent: it asserts a relationship to something it cannot resolve. So
    edges are never a droppable bucket of their own -- they leave with the
    items they connected, which is why pruning nodes reclaims far more bytes
    than the node dicts alone.
    """
    return [
        edge for edge in edges
        if edge.get("from") in keep_ids and edge.get("to") in keep_ids
    ]


def largest_fitting_prefix(total: int, fits: Callable[[int], bool]) -> int:
    """Return the largest ``k <= total`` for which ``fits(k)`` holds.

    Binary search over keep-counts, used by every size cap in the read path.
    Envelope size is monotonic non-decreasing in the number of items kept, so
    this finds the largest fitting prefix in ``O(log n)`` serializations rather
    than ``O(n)``.

    The one imperfection in that monotonicity is the reported drop count, which
    *shrinks* as more items are kept and so can cross a decimal-digit boundary.
    That cannot produce an over-budget answer: ``fits`` is only believed when it
    returns true for the exact candidate the caller then materialises, so a
    missed optimum costs one extra dropped item, never a payload that breaches
    the budget. Returns ``0`` when nothing fits.
    """
    lo, hi, best = 0, total, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def bound_dict_to_budget(
    items: dict[str, object], budget: int, *, key: str,
) -> tuple[dict[str, object], int]:
    """Keep the largest budget-fitting prefix of *items*, in caller order.

    The mapping sibling of :func:`fit_buckets`: some additive stamps (MCP's
    per-child ``children_status``, ADR 0082 amendment bd hwwo) are a *map*
    with no bucket/rank structure of their own, one entry per child, and so
    unbounded in count -- a fixed reserve cannot bound them, only a fit loop
    can. *items* must already be in the caller's deterministic order (ADR
    0012); this function drops from the end rather than imposing its own
    order.

    *key* is the name *items* will be nested under in the envelope it gets
    spliced into (e.g. ``"children_status"``). The fitness check measures
    ``{key: {...}}`` rather than *items* bare: JSON's ``indent=2`` pads a
    dict *value* by its structural depth alone, not by its sibling count, so
    wrapping under *key* here reproduces the exact per-entry byte cost the
    real envelope will pay, without needing the rest of that envelope in
    hand.

    Returns ``(kept, omitted_count)``. Never silent-truncates -- the omitted
    count is the caller's contract to report, not something this function can
    surface on the caller's behalf, because it does not know the envelope key
    the count itself belongs under.
    """
    names = list(items)
    total = len(names)
    if total == 0:
        return dict(items), 0

    def fits(k: int) -> bool:
        candidate = {key: {name: items[name] for name in names[:k]}}
        return envelope_bytes(candidate) <= budget

    best = largest_fitting_prefix(total, fits)
    return {name: items[name] for name in names[:best]}, total - best


def _bucket_node_ids(items: list[dict]) -> set:
    return {i["id"] for i in items if isinstance(i, dict) and i.get("id") is not None}


def fit_buckets(
    envelope: dict,
    *,
    buckets: tuple[str, ...],
    budget: int,
    rank_key: Callable[[str, dict], tuple],
    protected_ids: set | frozenset | None = None,
    edges_key: str | None = "edges",
    annotate: Callable[[dict, dict[str, int], int], dict] | None = None,
) -> tuple[dict, dict[str, int], int]:
    """Prune *envelope*'s bucket items until it fits *budget*.

    *buckets* names the envelope keys holding droppable lists. *rank_key* is
    called as ``rank_key(bucket_name, item)`` and must yield a **total order**
    (tie-break on something unique, normally the node id) -- lower ranks are
    kept. Passing the bucket *name* rather than its position lets a surface
    express its own retention priority (impact keeps nearer hops; brief ranks
    purely on node quality across its buckets) without this function encoding
    either policy, and keeps that policy correct when a caller omits a bucket
    the envelope does not carry.

    Selection is tracked by *position*, not by id, so a bucket of id-less items
    (``references``' file hits) prunes correctly and two buckets can never
    collide on a shared id. Survivors are re-emitted in each bucket's original
    order, preserving the caller's sort contract.

    *protected_ids* are ids that are never dropped and always count as valid
    edge endpoints. Left ``None`` (the normal case) they are derived: every
    edge endpoint that is not a droppable bucket node is protected, which is
    exactly "only drop what you were asked to drop" -- it keeps an impact
    target's seeds and a query's matches as live endpoints without the surface
    having to enumerate them.

    *annotate* stamps the drop report onto a candidate envelope; it is applied
    *inside* the fit check so the reported counts are part of the measured
    bytes and the answer cannot breach the budget by the length of its own
    report. Returns ``(shaped_envelope, dropped_per_bucket, dropped_edges)``.
    """
    originals = {name: list(envelope.get(name) or []) for name in buckets}
    edges = list(envelope.get(edges_key) or []) if edges_key else []
    droppable_ids: set = set()
    for name in buckets:
        droppable_ids |= _bucket_node_ids(originals[name])
    if protected_ids is None:
        endpoints = {e.get("from") for e in edges} | {e.get("to") for e in edges}
        protected: set = endpoints - droppable_ids
    else:
        protected = set(protected_ids)

    ranked = sorted(
        (
            (rank_key(name, item), index, position)
            for index, name in enumerate(buckets)
            for position, item in enumerate(originals[name])
        ),
        key=lambda entry: entry[0],
    )
    total = len(ranked)

    def candidate(keep_count: int) -> tuple[dict, dict[str, int], int]:
        keep: set[tuple[int, int]] = {
            (index, position) for _, index, position in ranked[:keep_count]
        }
        shaped = dict(envelope)
        dropped: dict[str, int] = {}
        kept_ids: set = set()
        for index, name in enumerate(buckets):
            survivors = [
                item for position, item in enumerate(originals[name])
                if (index, position) in keep
            ]
            shaped[name] = survivors
            dropped[name] = len(originals[name]) - len(survivors)
            kept_ids |= _bucket_node_ids(survivors)
        dropped_edges = 0
        if edges_key:
            kept_edges = dedangle(edges, protected | kept_ids)
            shaped[edges_key] = kept_edges
            dropped_edges = len(edges) - len(kept_edges)
        if annotate is not None:
            shaped = annotate(shaped, dropped, dropped_edges)
        return shaped, dropped, dropped_edges

    if total == 0:
        return candidate(0)
    best = largest_fitting_prefix(
        total, lambda k: envelope_bytes(candidate(k)[0]) <= budget,
    )
    return candidate(best)

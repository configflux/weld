"""Cannot-answer detection for blast-radius analysis (ADR 0134, ADR 0137 ss5).

Split out of :mod:`weld.impact_core` so that module stays under the 400-line
cap and the one genuinely-new distinction the cannot-answer contract adds has a
single audit point.

The distinction: ``impact`` on a ``repo:`` node can only see cross-repo edges,
so when none point at the seed the reverse-BFS returns 0 -- and that 0 means
one of two entirely different things. It is a **measurement** when a resolver
pass ran and found nothing, and it is **no answer at all** when no pass ever
looked. A confident ``Risk: LOW, 0 dependents`` in the second case is a
fabricated verdict, so it is surfaced as ``Risk: UNKNOWN`` with the
``result_unknown`` code (ADR 0134 authorises exactly this one code).

What tells the two apart is ``meta.cross_repo``, the record ``wd discover``
stamps whenever cross-repo resolvers ran (ADR 0137 ss4). Before that stamp
existed this module inferred the answer from edge *absence* and stated it as a
fact -- "cross_repo_strategies is empty" -- which the v0.24.0 field evaluation
printed against a workspace whose ``cross_repo_strategies`` listed a resolver
in the very file the sentence named. The absence of an edge is evidence of
nothing on its own.

The boundary matters as much as the trigger: this must fire *only* when the
answer is genuinely uncomputable and must never demote a measured empty result
(a non-repo node that genuinely has no dependents stays ``LOW``, exit 0). See
ADR 0134 section 3 -- turning legitimate empty results into failures would
train agents to ignore the signal.
"""

from __future__ import annotations

from weld._errors import ERROR_HINTS, RESULT_UNKNOWN
from weld.graph import Graph

#: Key on ``meta`` where discovery records a cross-repo resolver pass.
CROSS_REPO_STAMP_KEY = "cross_repo"


def _repo_seed(graph: Graph, seed_ids: list[str]) -> dict | None:
    """Return the seed node when it is a lone ``repo:`` node, else ``None``.

    The precondition is deliberately narrow: cross-repo provenance describes a
    question asked about *one whole repository*, and nothing here should touch
    a multi-seed or file-seeded result.
    """
    if len(seed_ids) != 1:
        return None
    node = graph.get_node(seed_ids[0])
    if node is None or node.get("type") != "repo":
        return None
    return node


def _cross_repo_stamp(graph: Graph) -> dict | None:
    """Return ``meta.cross_repo``, or ``None`` when no resolver pass is recorded."""
    meta = graph.dump().get("meta")
    if not isinstance(meta, dict):
        return None
    stamp = meta.get(CROSS_REPO_STAMP_KEY)
    return stamp if isinstance(stamp, dict) else None


def _configured_strategies(graph: Graph) -> list[str]:
    """Return ``cross_repo_strategies`` from the config beside *graph*.

    Empty for a single repo, for an unreadable config, and for a workspace that
    wires no resolver -- the three cases whose reason is the same sentence.
    Reading it is the point: the message this feeds used to assert the list was
    empty without ever opening the file it named.
    """
    path = getattr(graph, "_path", None)
    if path is None:
        return []
    try:
        from weld.workspace_state import load_workspace_config

        config = load_workspace_config(path.parent.parent)
    except Exception:  # noqa: BLE001 -- a bad config must not sink `wd impact`
        return []
    if config is None:
        return []
    return [str(name) for name in getattr(config, "cross_repo_strategies", ()) or ()]


def uncomputable_repo_reason(graph: Graph, seed_ids: list[str]) -> str | None:
    """Return the cannot-answer reason for an unanswerable repo seed.

    Returns ``None`` -- meaning "run the normal measured path" -- when the seed
    is not a lone repo node, when something already points at it, or when the
    graph carries a resolver-pass stamp. Only the last of those is new, and it
    is the one that turns a zero into a measurement: a stamped graph with no
    inbound edge is a repo that resolvers read and found no dependents for,
    which is an answer.
    """
    node = _repo_seed(graph, seed_ids)
    if node is None:
        return None
    seed_id = seed_ids[0]
    for edge in graph.dump().get("edges", []):
        if edge.get("to") == seed_id:
            return None  # something depends on it -- dependents are measurable
    if _cross_repo_stamp(graph) is not None:
        return None  # a pass ran and found none: a measured 0, not a gap
    label = node.get("label") or seed_id
    strategies = _configured_strategies(graph)
    if strategies:
        return (
            f"cross-repo dependents of {label} cannot be computed: "
            f"cross_repo_strategies names {', '.join(strategies)} in "
            ".weld/workspaces.yaml, but this graph records no cross-repo "
            "resolver pass -- no child repo was present, every child graph "
            "failed to load, or the graph predates the run. Re-run "
            "`wd discover` at the workspace root, and `wd workspace status` "
            "to see which children are readable."
        )
    return (
        f"cross-repo dependents of {label} cannot be computed: no cross-repo "
        "resolver is wired (cross_repo_strategies is empty), so the root graph "
        "holds no cross-repo edges and a measured 0 is impossible. See "
        "cross_repo_strategies in .weld/workspaces.yaml."
    )


def cross_repo_measured_by(graph: Graph, seed_ids: list[str]) -> list[str] | None:
    """Return the strategies whose recorded pass measured this repo's dependents.

    ``None`` when the question is not "what depends on this repository", or when
    no pass is recorded -- so the key stays absent from every single-repo
    envelope rather than present and empty. A repo result that carries it is a
    measurement with its provenance attached; the count may still be zero, and
    that zero now means something.
    """
    if _repo_seed(graph, seed_ids) is None:
        return None
    stamp = _cross_repo_stamp(graph)
    if stamp is None:
        return None
    strategies = stamp.get("strategies")
    if not isinstance(strategies, list):
        return []
    return sorted(str(name) for name in strategies)


def cannot_answer_marker(reason: str) -> dict:
    """Build the ``cannot_answer`` envelope record for *reason*.

    A ``weld._errors`` code (:data:`RESULT_UNKNOWN`), its reason, and its stable
    hint -- the same vocabulary the CLI renders via ``format_error_line`` and MCP
    returns via ``structured_payload``, so both surfaces stay byte-identical.
    """
    return {
        "error_code": RESULT_UNKNOWN,
        "reason": reason,
        "hint": ERROR_HINTS[RESULT_UNKNOWN],
    }


__all__ = [
    "cannot_answer_marker",
    "cross_repo_measured_by",
    "uncomputable_repo_reason",
]

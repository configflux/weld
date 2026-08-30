"""Cannot-answer detection for blast-radius analysis (ADR 0134, Finding 06).

Split out of :mod:`weld.impact_core` so that module stays under the 400-line
cap and the one genuinely-new distinction the cannot-answer contract adds has a
single audit point.

The distinction: ``impact`` on a ``repo:`` node in a federation-root graph with
no cross-repo edges cannot compute dependents at all. ``cross_repo_strategies:
[]`` (the documented default) emits zero cross-repo edges, so nothing points at
the repo node and a reverse-BFS returns 0 not as a measurement but because the
input to the measurement is absent. A confident ``Risk: LOW, 0 dependents``
there is a fabricated verdict, so it is surfaced instead as ``Risk: UNKNOWN``
with the ``result_unknown`` code (ADR 0134 authorises exactly this one code).

The boundary matters as much as the trigger: this must fire *only* when the
answer is genuinely uncomputable and must never demote a measured empty result
(a non-repo node that genuinely has no dependents stays ``LOW``, exit 0). See
ADR 0134 section 3 -- turning legitimate empty results into failures would train
agents to ignore the signal.
"""

from __future__ import annotations

from weld._errors import ERROR_HINTS, RESULT_UNKNOWN
from weld.graph import Graph


def uncomputable_repo_reason(graph: Graph, seed_ids: list[str]) -> str | None:
    """Return the cannot-answer reason when *seed_ids* is an uncomputable repo.

    The precondition is precise so it never demotes a measured empty result:

    * the seed set is exactly one node whose type is ``repo``, and
    * that repo node has no inbound edge in the graph.

    When a resolver *is* wired, the repo (or its members) gain inbound cross-repo
    edges and this returns ``None`` -- the normal measured path runs. Returns the
    human reason string to surface, or ``None`` when the result is computable.
    """
    if len(seed_ids) != 1:
        return None
    seed_id = seed_ids[0]
    node = graph.get_node(seed_id)
    if node is None or node.get("type") != "repo":
        return None
    data = graph.dump()
    for edge in data.get("edges", []):
        if edge.get("to") == seed_id:
            return None  # something depends on it -- dependents are measurable
    label = node.get("label") or seed_id
    return (
        f"cross-repo dependents of {label} cannot be computed: no cross-repo "
        "resolver is wired (cross_repo_strategies is empty), so the root graph "
        "holds no cross-repo edges and a measured 0 is impossible. See "
        "cross_repo_strategies in .weld/workspaces.yaml."
    )


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


__all__ = ["cannot_answer_marker", "uncomputable_repo_reason"]

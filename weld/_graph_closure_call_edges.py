"""The closure's call-edge decoration pass, and the sentinel it reads.

Moved out of :mod:`weld.graph_closure` unchanged (bd lrnx1.4) so that file
stays inside the 400-line cap while the first-party import rule lands beside
its siblings. It joins the ``_graph_closure_*`` family for the same reason
they exist: one pass, one file, so the reviewer question "what does the
closure do to a ``calls`` edge?" has one place to read.

The pass itself is ADR 0134's contract at the edge level. Every ``calls``
edge is stamped with whether its callee resolved, the raw name it was written
under, and where the call was made -- so a *cannot answer* (the callee is
still ``symbol:unresolved:<name>``) is visibly different from an *empty
answer*, rather than the two being told apart by squinting at a node id.
``setdefault`` throughout: a strategy that already knew any of these owns it,
and re-running the closure over a graph it has already decorated must not
change a byte.
"""

from __future__ import annotations

#: The id prefix a call-graph strategy mints for a callee it could not bind.
#: Lives here because this is the module that reads it as a *verdict*; the
#: closure imports it for the one other place that skips such a node.
UNRESOLVED_PREFIX = "symbol:unresolved:"


def decorate_call_edges(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Stamp resolution, raw callee name and provenance on every ``calls`` edge."""
    for edge in edges:
        if edge.get("type") != "calls":
            continue
        props = edge.setdefault("props", {})
        target_id = str(edge.get("to") or "")
        resolved = not target_id.startswith(UNRESOLVED_PREFIX)
        props.setdefault("resolved", resolved)
        props.setdefault("resolution", "resolved" if resolved else "unresolved")
        props.setdefault("raw", raw_callee(target_id, nodes.get(target_id)))
        provenance = props.setdefault("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
            props["provenance"] = provenance
        source = nodes.get(str(edge.get("from") or ""))
        source_props = source.get("props") if isinstance(source, dict) else {}
        if isinstance(source_props, dict):
            if source_props.get("file"):
                provenance.setdefault("file", source_props["file"])
            if isinstance(source_props.get("line"), int):
                provenance.setdefault("line", source_props["line"])


def raw_callee(target_id: str, target_node: dict | None) -> str:
    """The name a call was written under, whatever the edge finally points at."""
    if target_id.startswith(UNRESOLVED_PREFIX):
        return target_id[len(UNRESOLVED_PREFIX):]
    props = target_node.get("props") if isinstance(target_node, dict) else {}
    if isinstance(props, dict) and isinstance(props.get("qualname"), str):
        return str(props["qualname"]).rsplit(".", 1)[-1]
    return target_id.rsplit(":", 1)[-1]


__all__ = ["UNRESOLVED_PREFIX", "decorate_call_edges", "raw_callee"]

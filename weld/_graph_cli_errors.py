"""CLI-side structured-error guards for graph-load and node-lookup failures.

Split out of :mod:`weld._graph_cli` to keep that dispatcher under the
line-count cap. These two helpers turn the failure modes a graph-backed read
command can hit into the shared one-line ``error[<code>]: <summary> | hint:
<hint>`` contract (vocabulary in :mod:`weld._errors`, identical to the MCP
surface) with a nonzero exit, instead of a raw traceback or a silent exit-0:

* :func:`load_graph_or_exit` -- a corrupt/truncated ``graph.json``
  (``json.JSONDecodeError``), a non-file left at the graph path such as a
  directory (``IsADirectoryError``), or one written by a newer Weld
  (``SchemaVersionError``).
* :func:`emit_node_lookup` -- a ``Graph.context`` / ``Graph.callers`` result
  that reports an unknown node id.
* :func:`reject_invalid_enrichment` -- a write whose ``props.enrichment`` is
  structurally incomplete and would be silently dropped by discovery.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable


def load_graph_or_exit(graph):  # type: ignore[no-untyped-def]
    """Load *graph*, converting load failures to a structured stderr line.

    A corrupt/truncated ``graph.json`` (``json.JSONDecodeError``) or a graph
    written by a newer Weld (``SchemaVersionError``) used to escape
    ``Graph.load`` as an unhandled traceback. This wraps the load so those
    cases emit the shared one-line ``error[<code>]: ... | hint: ...`` contract
    and exit nonzero. Any other exception is re-raised unchanged -- we never
    mislabel an unrelated bug as a graph problem. Returns *graph* for chaining.
    """
    from weld._errors import classify_graph_load_error, format_error_line

    try:
        graph.load()
    except Exception as exc:  # noqa: BLE001 - classify then re-raise non-graph
        code, detail = classify_graph_load_error(exc, getattr(graph, "_path", Path()))
        if code is None:
            raise
        sys.stderr.write(format_error_line(code, detail) + "\n")
        sys.exit(1)
    return graph


def emit_node_lookup(
    args, data: dict, renderer, emit: Callable[..., None],
) -> None:
    """Emit a node-lookup result, exiting nonzero on a ``node not found``.

    ``Graph.context`` / ``Graph.callers`` return ``{"error": "node not
    found: ..."}`` for an unknown id but the CLI historically rendered that
    as a success (exit 0). The structured-error contract requires a bad node
    id to be a real failure: stamp the stable ``node_not_found``
    ``error_code`` onto the payload and exit nonzero after emitting via
    *emit* (the caller's renderer-aware writer). A resolved result is emitted
    unchanged.
    """
    from weld._errors import NODE_NOT_FOUND

    if isinstance(data, dict) and isinstance(data.get("error"), str) \
            and "not found" in data["error"]:
        emit(args, {**data, "error_code": NODE_NOT_FOUND}, renderer)
        sys.exit(1)
    emit(args, data, renderer)


#: Sentinel for "this write carries no enrichment record at all". A distinct
#: object, not ``None``, because ``{"enrichment": null}`` is itself an invalid
#: record that must be judged rather than mistaken for the absent case.
_NO_RECORD = object()


def _record_under_judgement(incoming: object, final: object) -> object:
    """The enrichment record a write must be judged on, or :data:`_NO_RECORD`.

    An enrichment record is a single attestation -- "provider P, model M, at
    time T, says D". It cannot be *partially* amended: merging a new
    ``description`` over someone else's record would keep their ``model`` and
    ``timestamp`` against your text. So when the caller supplies an
    ``enrichment`` key we judge **their** record and require it whole.

    When the caller supplies none, the write still carries whatever the node
    already held, so we judge that instead -- which catches a legacy invalid
    record riding along on an unrelated ``--merge``.
    """
    if isinstance(incoming, dict) and "enrichment" in incoming:
        return incoming["enrichment"]
    if isinstance(final, dict) and "enrichment" in final:
        return final["enrichment"]
    return _NO_RECORD


def reject_invalid_enrichment(node_id: str, incoming: object, final: object) -> None:
    """Exit nonzero when the enrichment record a write would land is incomplete.

    ADR 0097: a record missing ``provider``/``model``/``timestamp``/
    ``description`` used to be written happily and then *silently* stripped by
    the next ``wd discover``. Refusing the write instead keeps the failure
    adjacent to the mistake, while the author can still fix it.

    *incoming* is the caller's ``--props`` payload and *final* the post-merge
    props; see :func:`_record_under_judgement` for which one is judged. Only the
    missing *field names* and *node_id* reach stderr -- never the record's
    values, which are arbitrary author text (ADR 0035 no-leak).
    """
    from weld._errors import INVALID_ENRICHMENT, format_error_line
    from weld.enrichment_persistence import missing_enrichment_fields

    record = _record_under_judgement(incoming, final)
    if record is _NO_RECORD:
        return
    missing = missing_enrichment_fields(record)
    if not missing:
        return
    detail = (
        f"{node_id}: props.enrichment is missing required field(s): "
        f"{', '.join(missing)}. An enrichment record must be written whole; "
        "discovery would drop this one, so the write was refused."
    )
    sys.stderr.write(format_error_line(INVALID_ENRICHMENT, detail) + "\n")
    sys.exit(1)

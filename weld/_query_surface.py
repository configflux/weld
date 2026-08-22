"""CLI surface projections for the ``wd query`` / ``wd context`` envelopes.

Extracted from :mod:`weld._graph_cli` so that module stays under the 400-line
cap. Two boundary projections layer on top of the raw ``Graph.query`` /
``Graph.context`` envelope:

* :func:`apply_query_envelope` -- unwraps CLI flags and delegates to the one
  query read command :func:`weld.read.read_query` (speculative-match filter,
  gated on ``--include-speculative``, then the neighbor diet, gated on
  ``--full-neighborhood``);
* :func:`apply_context_envelope` -- the neighbor diet on a context envelope
  (context has no ``matches`` to filter);
* :func:`apply_callers_envelope` / :func:`apply_references_envelope` -- the
  ADR 0082 byte budget on the two traversal reads the CLI serves, delegating
  to :mod:`weld.read_traversal`.

Both keep the diet a *surface* concern: the core ``Graph`` methods, and direct
API callers such as ``brief`` / ``trace`` / ``impact``, still receive the full
envelope. The MCP server shares the same :mod:`weld.read` command, so the two
surfaces cannot drift (ADR 0083).
"""

from __future__ import annotations

from weld.read import read_query, shape_read_envelope


def apply_query_envelope(args, envelope: dict) -> dict:
    """Project a ``wd query`` *envelope* for the CLI default view.

    A thin arg-unwrapper over :func:`weld.read.read_query` -- the one query read
    command the CLI *and* the MCP ``weld_query`` handler both call, so
    ``wd query --json`` and ``weld_query`` shape identically (ADR 0083).
    ``read_query`` composes the speculative-match filter (drop
    ``origin=unresolved`` sentinels from ``matches`` unless
    ``--include-speculative``) with the ADR 0078 neighbor diet + ADR 0082 byte
    budget. ``--full-neighborhood`` returns the raw neighborhood;
    ``--full-size`` keeps the diet but skips the byte budget; full raw parity
    with ``Graph.query`` needs both ``--include-speculative`` and
    ``--full-neighborhood``.
    """
    return read_query(
        envelope,
        include_speculative=getattr(args, "include_speculative", False),
        full=getattr(args, "full_neighborhood", False),
        full_size=getattr(args, "full_size", False),
    )


def apply_context_envelope(args, envelope: dict) -> dict:
    """Apply the bounded read shaping to a ``wd context`` *envelope*.

    Context envelopes carry a focal ``node`` plus its 1-hop ``neighbors`` and
    ``edges`` (no ``matches``), so only the shaping applies. A node-not-found
    miss (``{"error": ...}``) has no ``neighbors`` key and is returned unchanged
    by :func:`weld.read.shape_read_envelope`. ``--full-neighborhood`` restores
    the full neighborhood; ``--full-size`` skips only the byte budget.
    """
    return shape_read_envelope(
        envelope,
        full=getattr(args, "full_neighborhood", False),
        full_size=getattr(args, "full_size", False),
    )


def _bounded_json(args, envelope: dict, shaper) -> dict:
    """Apply *shaper* only when the CLI is emitting JSON (ADR 0082).

    The byte budget exists to fit the *agent tool-result cap*, and on these
    traversal reads only ``--json`` crosses it -- the MCP tools apply the same
    shaper unconditionally, so ``wd <cmd> --json`` and the tool still agree
    (ADR 0083). The human renderers are left with the full result on purpose:
    they list or count what was found, so bounding them would silently
    under-report the answer to a reader who has no tool cap to respect.
    ``--full-size`` skips the budget on the JSON path too.
    """
    if not getattr(args, "as_json", False):
        return envelope
    return shaper(envelope, full_size=getattr(args, "full_size", False))


def apply_callers_envelope(args, envelope: dict) -> dict:
    """Bound a ``wd callers`` envelope for the JSON surface (ADR 0082)."""
    from weld.read_traversal import shape_callers

    return _bounded_json(args, envelope, shape_callers)


def apply_references_envelope(args, envelope: dict) -> dict:
    """Bound a ``wd references`` envelope for the JSON surface (ADR 0082).

    Call this *after* ``files`` has been attached: the file-index hits are the
    largest and lowest-priority part of the payload, so a budget that ran
    before them would bound the wrong thing.
    """
    from weld.read_traversal import shape_references

    return _bounded_json(args, envelope, shape_references)

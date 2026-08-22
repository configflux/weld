"""``wd callers`` seed-plurality rendering.

Split out of :mod:`weld._cli_render`, which sits at the 400-line cap
(AGENTS.md / CLAUDE.md line-count policy) -- same reason and same pattern as
the existing :mod:`weld._cli_render_trust` / :mod:`weld._cli_render_prose` /
:mod:`weld._cli_render_freshness` splits: return raw ``list[str]`` line
fragments that :func:`weld._cli_render.render_callers` merges into its own
``lines`` before the single ``_header``/``_join`` pass, rather than a second
rendered block competing with the first.

``Graph.callers`` resolves a bare name to one or more seed ids (bd jz65r,
the ``callers()`` half of the honesty gap bd nyoks closed for
``references()``'s ``matches``): ``callers()`` never had a ``matches`` field
to attribute a caller to, so the additive fix is a top-level ``seeds`` list
of resolved ids rather than full node dicts. This module renders that list
-- but only when it says something a reader does not already know from the
``symbol:`` header.
"""

from __future__ import annotations

from typing import Any, Mapping


def callers_seeds_lines(payload: Mapping[str, Any]) -> list[str]:
    """Render the ``seeds`` line for ``wd callers``, when it is informative.

    Silent (``[]``) whenever ``seeds`` is absent, empty, or holds exactly one
    id -- an error payload, a full-id lookup, and a bare name that happens to
    resolve uniquely all take this path, matching this renderer's output
    before ``seeds`` existed (older payloads without the key render the same
    way, via ``.get`` defaulting to ``[]``). Printed only when a bare name
    resolved to more than one seed: the plurality itself is the fact worth
    surfacing, the same call bd nyoks made for ``references()``'s ``matches``
    count. Per-caller ``targets`` attribution (depth 1 only) needs no
    counterpart here -- :func:`weld._cli_render._match_block` already renders
    a ``targets`` key generically, from bd nyoks's original addition.
    """
    seeds = list(payload.get("seeds") or [])
    if len(seeds) <= 1:
        return []
    return [f"  seeds ({len(seeds)}): {', '.join(str(s) for s in seeds)}"]


__all__ = ["callers_seeds_lines"]

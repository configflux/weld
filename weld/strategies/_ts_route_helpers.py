"""Payload helpers shared by every TypeScript / JavaScript route strategy.

Two strategies in this tree read TS/JS source and emit ``route:`` nodes --
:mod:`weld.strategies.express` (handler-registration callsites) and
:mod:`weld.strategies.next` (app-router file conventions) -- and the parts of
that job which are *not* about the framework's grammar are identical: the
canonical route id, the boundary ``file:`` placeholder that keeps the
diagnostic ``exposes`` edge from dangling, the edge itself, and the
line-comment strip that stops a commented-out declaration from minting a
route.

They live here rather than in either strategy because the placeholder in
particular carried a defect for both of them (bd iurvv): it stated no
``confidence``, so :func:`weld._discover_node_merge.claim_supersedes` could not
rank it and the orchestrator fell back to last-writer-wins -- a route entry
ordered *after* the tree-sitter entry overwrote the definite file node with the
stub and its ``props.exports`` vanished. One placeholder was one fix; a copy
per framework would have been two. Adding the second TS route strategy is
exactly the moment that second copy would have been minted.

Layering: stdlib and :mod:`weld._node_ids` (a pure id helper) only. The
``authority`` / ``confidence`` literals every route strategy stamps are plain
strings rather than enums imported from :mod:`weld.runtime` -- the
``weld/strategies`` -> ``weld/runtime`` import is a gate-pinned layering
violation.
"""

from __future__ import annotations

from weld._node_ids import file_id as _file_id


def route_id(verb: str, path: str) -> str:
    """Return the canonical ``route:<VERB>:<path>`` id.

    One id namespace across every route-emitting strategy in the repo
    (express, next, axum, gin, fastapi, flask, csharp_aspnet_routes) so a
    polyglot graph does not answer "what routes exist" per framework. The
    verb is upper-cased; the path is taken verbatim, capture syntax included
    (``:id`` for express, ``[id]`` for the app router).
    """
    return f"route:{verb.upper()}:{path}"


def strip_line_comments(text: str) -> str:
    """Drop the ``// ...`` tail of each line of *text*.

    A conservative line-level strip so a commented-out route registration or
    handler export does not mint a route. It can over-trim a ``//`` inside a
    string literal on the same line, which costs nothing here: the strings
    these strategies read are server-relative route paths (``/users``) and
    never contain ``//``. Block comments (``/* ... */``) are left alone -- a
    declaration inside one is rare, and both strategies carry a stronger
    discriminator anyway (an express import gate, an app-router file name).
    """
    out: list[str] = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def boundary_file_id(rel_path: str) -> str:
    """Return the canonical ``file:`` node id for *rel_path*.

    A route callsite names a receiver and a path, and an app-router file
    names nothing at all, so neither strategy has a statically resolvable
    *router symbol* to hang the diagnostic ``exposes`` edge on; both hang it
    off the boundary *file* node instead, as axum / gin / fastapi do.

    Built via :func:`weld._node_ids.file_id` so it is the same id the TS
    ``tree_sitter`` strategy mints for that source file (``src/app.ts`` ->
    ``file:src/app``); otherwise the edge would dangle and the dangling-edge
    post-pass would drop it.
    """
    return _file_id(rel_path)


def boundary_file_node(
    rel_path: str, *, source_strategy: str, language: str = "typescript",
) -> dict:
    """Build the minimal ``file:`` placeholder for a route's boundary file.

    Emitted so the ``file: -> exposes -> route:`` edge survives the
    dangling-edge post-pass when the route strategy runs *without* the TS
    ``tree_sitter`` strategy paired on the same tree (a focused strategy test,
    or a config that wires routes only).

    ``confidence: inferred`` is what keeps it a placeholder rather than a
    claim (bd iurvv). The route scan proved this file exists and registers a
    route; it did not walk the file's definitions, so it knows strictly less
    about the file than the tree-sitter pass does. Stating that rank is what
    lets :func:`weld._discover_node_merge.claim_supersedes` see a comparable
    pair and veto the write -- its veto fires only when *both* sides state a
    confidence, so a stub that stated none fell through to last-writer-wins
    and replaced the definite node whenever the route entry was declared
    later. ``inferred`` rather than ``speculative`` for the same reason
    ``validator_targets`` mints its ``file:`` stub that way: ``speculative``
    is the rank for a claim with no observation behind it, and this one read
    the file.
    """
    return {
        "type": "file",
        "label": rel_path.rsplit("/", 1)[-1],
        "props": {
            "file": rel_path,
            "language": language,
            "source_strategy": source_strategy,
            "confidence": "inferred",
            "roles": ["implementation"],
        },
    }


def exposes_edge(src: str, dst: str, *, source_strategy: str) -> dict:
    """Build a diagnostic ``exposes`` edge from a boundary node to a route."""
    return {
        "from": src,
        "to": dst,
        "type": "exposes",
        "props": {
            "source_strategy": source_strategy,
            "confidence": "definite",
        },
    }


__all__ = [
    "boundary_file_id",
    "boundary_file_node",
    "exposes_edge",
    "route_id",
    "strip_line_comments",
]

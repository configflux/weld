"""Terminal output-boundary marker, derived from the ``calls`` graph (ADR 0129).

``weld._safe_text.sanitize_terminal_text`` / ``sanitize_terminal_line`` are
the ADR 0025 / rn0x mandated write-boundary chokepoint: every human-readable
``wd`` write goes through one of the two first (``tools/lint_terminal_safety.py``
enforces this structurally). So "symbol X calls one of these two functions"
is, by this repo's own enforced convention, "symbol X is a terminal write
boundary" -- not a heuristic.

``python_callgraph`` already resolves every such call to a fully-qualified
``calls`` edge via ordinary import-table resolution. This module adds no new
AST walk: it scans the edges the strategy already built and tags the
*caller's own* node -- a ``symbol:`` node for a function/method/class-body
call site, or the module's ``file:`` anchor for a module-level statement
call, mirroring exactly how ``calls`` edges are already sourced (ADR 0122).

Two props are set, deliberately different in kind:

* ``props.output_sink = "terminal"`` -- the precise, structured fact. Not
  registered in ``weld.contract.NODE_OPTIONAL_PROPS`` and does not bump
  ``SCHEMA_VERSION`` (see ADR 0129 S3): it sits in the same "tolerated,
  round-tripped" tier as ``kind``/``qualname``/``summary``, recorded by one
  trusted, tested, first-party strategy rather than validated against
  adversarial input.
* ``props.keywords`` gains the token ``"terminal-write-boundary"`` -- the
  ADR-0105 channel, already wired into every query match surface
  (``weld._match_surface``, ``weld.query_index``) and the inverted index.
  This is the entire mechanism that makes "enumerate the writers" a single
  ``wd query``/``wd find`` call with no core query-matcher edit.

Deliberately excluded from the sink family: the raw ``sys.stdout.write`` /
``print`` calls (unresolvable to a concrete symbol -- see ADR 0129 S1), and
``dumps_safe_json``/``sanitize_json_text`` (ambiguous CLI-vs-MCP caller
population -- see the ADR's non-goals).
"""

from __future__ import annotations

#: The mandated write-boundary chokepoint (ADR 0025 / rn0x). Calling either
#: of these, by this repo's own lint-enforced convention, IS the terminal
#: write boundary -- see the module docstring and ADR 0129 S1.
_SINK_MODULE = "weld._safe_text"
_SINK_FUNCTIONS = ("sanitize_terminal_text", "sanitize_terminal_line")
_SINK_TARGET_IDS = frozenset(
    f"symbol:py:{_SINK_MODULE}:{name}" for name in _SINK_FUNCTIONS
)

#: The ADR-0105 query-channel token this module is the producer of.
OUTPUT_SINK_KEYWORD = "terminal-write-boundary"

#: The one recognized value today (ADR 0129 S3: tolerated, not
#: schema-enforced -- a second value is a normal follow-up if a real need
#: for cross-value coherence checking ever appears).
OUTPUT_SINK_VALUE = "terminal"


def _mark(node: dict) -> None:
    """Tag *node* as a terminal output-sink boundary, idempotently."""
    props = node.setdefault("props", {})
    props["output_sink"] = OUTPUT_SINK_VALUE
    keywords = props.setdefault("keywords", [])
    if OUTPUT_SINK_KEYWORD not in keywords:
        keywords.append(OUTPUT_SINK_KEYWORD)


def mark_output_sink_callers(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Tag every caller of the terminal-sanitizer chokepoint.

    Runs once, after all of a glob's ``calls`` edges are assembled (function,
    class-body, and module-level sourcing all land in *edges* by then, per
    ADR 0122), so this is a single pass with no re-parsing and no new AST
    traversal. Order-stable: *edges* is already a deterministic list, and
    tagging is idempotent, so re-running discovery yields identical props.
    """
    for edge in edges:
        if edge.get("type") != "calls" or edge.get("to") not in _SINK_TARGET_IDS:
            continue
        # Every ``calls`` edge this strategy emits is sourced at a node it
        # already put in *nodes* (the symbol/class loop above, or
        # ``emit_module_scope_call_edges``'s file-anchor stub) before the
        # edge is appended, so ``edge["from"]`` is always present here.
        _mark(nodes[edge["from"]])


__all__ = ["OUTPUT_SINK_KEYWORD", "OUTPUT_SINK_VALUE", "mark_output_sink_callers"]

"""``wd lint`` command-line surface.

Argument parsing, output selection, and the exit-code contract for the
architectural linter. Split from :mod:`weld.arch_lint` so that module keeps
headroom under the 400-line cap.

The six ``arch_lint_*`` rule modules used to late-import
:class:`~weld.arch_lint.Violation` from :mod:`weld.arch_lint` to break their
own import cycle (the ``file:weld/_graph_closure_invariants`` 8-member SCC);
that shared type now lives in the dependency-free
:mod:`weld._arch_lint_types` leaf instead (bd 5038-mx8sd, ADR 0130
disposition #6), so those six no longer touch this module at all.

This module's own back-edge was a different shape: :mod:`weld.arch_lint`
top-level-imports :func:`main` from here (to re-export it), and
:func:`main` needed the live rule registry and runner
(``available_rule_ids``, ``lint``) back from ``weld.arch_lint`` -- a
registry/runner dependency, not a shared type, so it could not move to a
leaf the way ``Violation`` did. Fixed instead by dependency injection (bd
5038-efr7z, ADR 0130 disposition #7 -- the shape :mod:`weld._mcp_dispatch`
uses for its ``tools_provider``): :func:`main` takes the registry and
runner as required keyword-only parameters, and this module holds no
import of :mod:`weld.arch_lint`, deferred or otherwise. :mod:`weld.arch_lint`
is the composition root -- it wraps :func:`main`, injecting its own
``lint``/``available_rule_ids`` at the one real call site -- so external
callers keep calling ``weld.arch_lint.main`` with the original argv-only
signature. This fully dissolves the ``weld.arch_lint`` <->
``weld.arch_lint_cli`` SCC rather than shrinking it: the one remaining
edge (``arch_lint`` importing this module) runs only one way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from weld._safe_text import dumps_safe_json, sanitize_terminal_text
from weld.arch_lint_format import format_text
from weld.graph import Graph


def main(
    argv: list[str] | None = None,
    *,
    lint_fn: Callable[..., dict],
    available_rule_ids_fn: Callable[[], list[str]],
) -> int:
    """CLI entry point for ``wd lint``.

    Exit code is ``0`` when no *visible* violations were reported and
    ``1`` when any non-suppressed violation fired.  Suppressed orphans
    alone never raise the exit code -- they are reported only in the
    summary line.

    *lint_fn* / *available_rule_ids_fn* are the live rule registry and
    runner (:func:`weld.arch_lint.lint` /
    :func:`weld.arch_lint.available_rule_ids`), dependency-injected by
    :mod:`weld.arch_lint` -- the composition root -- instead of imported
    back from it (ADR 0130 disposition #7). Both are required, not
    defaulted: a def-time default referencing either function would freeze
    a stale reference at first import (the same trap bd 5038-i8n7 hit with
    its own test-seam defaults), and the only real caller always supplies
    its own live functions anyway.
    """
    parser = argparse.ArgumentParser(
        prog="wd lint",
        description=(
            "Lint the graph for architectural violations (dead code, layer "
            "inversion, missing metadata). Loads .weld/lint-rules.yaml when "
            "present. Exits non-zero on visible violations."
        ),
    )
    parser.add_argument(
        "--rule",
        action="append",
        default=None,
        metavar="RULE_ID",
        help=(
            "Run only the named rule (may be repeated). Default: run every "
            f"registered rule. Available: {', '.join(available_rule_ids_fn())}."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the stable JSON envelope instead of human-readable text.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root containing .weld/graph.json (default: cwd).",
    )
    parser.add_argument(
        "--include-noisy",
        action="store_true",
        help=(
            "Disable the orphan-detection default suppression of "
            "doc/config/test nodes; surface every orphan."
        ),
    )
    args = parser.parse_args(argv)

    graph = Graph(args.root)
    graph.load()

    rule_filter = list(args.rule) if args.rule is not None else None
    result = lint_fn(
        graph,
        rule_ids=rule_filter,
        root=args.root,
        include_noisy=args.include_noisy,
    )

    if args.json:
        sys.stdout.write(dumps_safe_json(result, indent=2) + "\n")
    else:
        sys.stdout.write(sanitize_terminal_text(format_text(result)))

    # Exit non-zero only when a non-suppressed violation fired.
    return 1 if result["violation_count"] > 0 else 0

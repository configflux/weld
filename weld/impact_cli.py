"""CLI wrapper for blast-radius analysis.

Owns argparse, git shelling, the staleness gate, and the entry-point
``main`` for ``wd impact``. Delegates the BFS and envelope construction to
:mod:`weld.impact_core`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from weld._impact_git import (
    _git_diff_files,
    _git_status_files,
    _require_git_repo,
)
from weld._root_resolver import ROOT_HELP, resolve_weld_root
from weld._safe_text import dumps_safe_json, sanitize_terminal_text
from weld.graph import Graph
from weld.impact_core import (
    IMPACT_VERSION,
    _resolve_paths_to_seeds,
    _validated_depth,
    format_human,
    impact,
)


# Exposed at the module level so tests can import it without parsing the
# CLI. ``IMPACT_VERSION`` is re-exported for the same reason.
__all__ = [
    "IMPACT_VERSION",
    "format_human",
    "impact",
    "main",
]


_STALE_EXIT_CODE = 2


def _parse_depth(raw: str) -> int:
    try:
        return _validated_depth(int(raw))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd impact",
        description=(
            "Reverse-dependency blast radius. Accepts a positional node id "
            "or file path, or one of --from-diff / --files / --working-tree."
        ),
    )
    # ``target`` stays positional and optional. The locked CLI flag-convention
    # test exercises ``main(["entity:Store"])``; making the positional optional
    # keeps that path while letting the four seed inputs be mutually exclusive
    # at the validation step below.
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Node id or repo-relative file path",
    )
    parser.add_argument(
        "--from-diff",
        dest="from_diff",
        default=None,
        metavar="REF",
        help=(
            "Resolve seeds from `git diff --name-only REF` "
            "(supports REF or REF1..REF2)"
        ),
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        metavar="PATH",
        help="Resolve seeds from an explicit list of file paths",
    )
    parser.add_argument(
        "--working-tree",
        dest="working_tree",
        action="store_true",
        help=(
            "Resolve seeds from `git status --porcelain` "
            "(staged + unstaged changes)"
        ),
    )
    parser.add_argument(
        "--allow-stale", dest="allow_stale", action="store_true",
        help="Proceed even when the graph is stale (warnings.stale_graph is set)",
    )
    parser.add_argument(
        "--depth", type=_parse_depth, default=3,
        help="Maximum reverse traversal depth (default: 3)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the stable JSON envelope instead of human-readable text",
    )
    parser.add_argument(
        "--full-size", dest="full_size", action="store_true", default=False,
        help=(
            "Skip the read byte budget on --json and emit every "
            "dependent, however large the payload"
        ),
    )
    parser.add_argument("--root", type=Path, default=None, help=ROOT_HELP)
    parser.add_argument(
        "--no-refresh", dest="no_refresh", action="store_true", default=False,
        help="Skip auto-refresh on stale graph.",
    )
    return parser


def _validate_seed_inputs(args: argparse.Namespace) -> None:
    """Enforce exactly-one of {target, --from-diff, --files, --working-tree}."""
    selected = [
        ("target", args.target is not None),
        ("--from-diff", args.from_diff is not None),
        ("--files", args.files is not None),
        ("--working-tree", bool(args.working_tree)),
    ]
    chosen = [name for name, present in selected if present]
    if not chosen:
        raise SystemExit(
            "wd impact: one of target, --from-diff, --files, or "
            "--working-tree is required"
        )
    if len(chosen) > 1:
        raise SystemExit(
            "wd impact: target, --from-diff, --files, and --working-tree are "
            f"mutually exclusive (got: {', '.join(chosen)})"
        )


def _resolve_seeds_from_args(
    graph: Graph,
    fg,
    args: argparse.Namespace,
) -> tuple[str, list[str], list[str], str | list[str], list[str] | None]:
    """Resolve CLI args into ``(kind, seeds, unresolved, target_input, low_cap)``.

    ``kind`` is one of ``"node" | "path" | "from-diff" | "files" |
    "working-tree"``. Seed lists and unresolved-input lists are sorted for
    determinism. ``target_input`` is what gets recorded in the JSON envelope
    under ``target.input``. ``low_cap`` is a precomputed
    ``warnings.low_capability_inputs`` list for the federated git-seed fan-out,
    or ``None`` everywhere else -- ``None`` lets ``impact()`` compute the warning
    locally from ``input_paths`` (byte-identical single-repo path).

    *fg* is the :class:`~weld.federation.FederatedGraph` for a polyrepo root,
    or ``None`` for a single repo. When set, the git-seeded modes
    (``--from-diff`` / ``--working-tree``) fan out per present child so seeds
    resolve from the children's git repos (ADR 0089); when ``None`` the
    unchanged single-repo root-git path runs (byte-identical).
    """
    if args.target is not None:
        # Existing single-positional behaviour. Seeds and unresolved inputs
        # are derived inside ``impact()`` itself.
        return "target", [], [], args.target, None
    if args.from_diff is not None:
        if fg is not None:
            seeds, unresolved, paths, low_cap = _federated_git_seeds(
                fg, args.root, diff_ref=args.from_diff,
            )
            return "from-diff", seeds, unresolved, paths, low_cap
        _require_git_repo(args.root, flag="--from-diff")
        paths = _git_diff_files(args.root, args.from_diff)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "from-diff", seeds, unresolved, sorted(paths), None
    if args.files is not None:
        paths = list(args.files)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "files", seeds, unresolved, sorted(paths), None
    if args.working_tree:
        if fg is not None:
            seeds, unresolved, paths, low_cap = _federated_git_seeds(
                fg, args.root, diff_ref=None,
            )
            return "working-tree", seeds, unresolved, paths, low_cap
        _require_git_repo(args.root, flag="--working-tree")
        paths = _git_status_files(args.root)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "working-tree", seeds, unresolved, sorted(paths), None
    # Should be unreachable: _validate_seed_inputs ran before this.
    raise SystemExit("wd impact: no seed input selected")


def _federated_git_seeds(
    fg,
    root: Path,
    *,
    diff_ref: str | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Fan ``--from-diff`` / ``--working-tree`` out across a federation.

    Thin seam over :func:`weld._impact_git_federated.federated_seed_resolution`
    (kept in its own module for the 400-line cap). Returns
    ``(seed_ids, unresolved_inputs, display_paths, low_capability_inputs)`` --
    all sorted. ``low_capability_inputs`` is precomputed per child so
    ``impact()`` records it verbatim (a flattened child's child-relative
    ``props.file`` can never match a child-prefixed display path in the union).
    """
    from weld._impact_git_federated import federated_seed_resolution

    return federated_seed_resolution(fg, root, diff_ref=diff_ref)


def _stale_gate(graph: Graph, *, allow_stale: bool) -> bool | None:
    """Apply the stale-graph gate (ADR 0017).

    Returns the value to record in ``warnings.stale_graph``: ``True`` when
    the graph is stale and ``--allow-stale`` was passed, ``None`` when the
    graph is fresh (or staleness could not be determined). Exits with code
    ``_STALE_EXIT_CODE`` when stale and ``--allow-stale`` was not passed.
    """
    info = graph.stale() or {}
    if not info.get("stale"):
        return None
    if allow_stale:
        return True
    sys.stderr.write(
        "wd impact: graph is stale; run 'wd discover' or pass "
        "--allow-stale (warnings.stale_graph will be set)\n"
    )
    raise SystemExit(_STALE_EXIT_CODE)


def _federation_or_none(root: Path):
    """Return a :class:`~weld.federation.FederatedGraph` for a polyrepo root.

    ``None`` marks a single repo (no ``.weld/workspaces.yaml``): the caller
    then takes the unchanged single-repo graph load and root-git seed paths,
    byte-identical to pre-federation behavior. The one instance is reused for
    both the flattened read graph and, for ``--from-diff`` / ``--working-tree``,
    the per-child git fan-out -- so children are loaded once (cache-warmed).
    """
    from weld.workspace_state import load_workspace_config

    if load_workspace_config(root) is None:
        return None
    from weld.federation import FederatedGraph

    return FederatedGraph(root)


def _load_impact_graph(root: Path, fg) -> Graph:
    """Return the graph ``impact()`` analyzes for *root*.

    Federated root (*fg* set) -> the read-time flattened union of root + every
    present child (ADR 0089), so the reverse-dependency BFS spans child-internal
    and cross-repo edges. Single repo (*fg* is ``None``) -> the loaded
    single-repo ``Graph`` with a corrupt/unsupported graph converted to a
    structured error, not a traceback.
    """
    if fg is not None:
        from weld._federation_flatten import flatten_federation

        return flatten_federation(fg, build_index=False)
    from weld._graph_cli_errors import load_graph_or_exit

    return load_graph_or_exit(Graph(root))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd impact``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.root = resolve_weld_root(args.root)  # ADR 0096
    _validate_seed_inputs(args)

    from weld._auto_refresh import auto_refresh_if_stale
    from weld._graph_cli import _build_retry_hint, ensure_graph_exists

    # Surface the friendly first-run message if the graph has not been
    # built. Mirrors trace/diff/enrich behaviour.
    retry_target = args.target if args.target is not None else "<seed>"
    ensure_graph_exists(
        args.root,
        _build_retry_hint("impact", retry_target),
        no_refresh=args.no_refresh,
    )
    # ADR 0051: auto-refresh stale graphs before the existing
    # --allow-stale gate fires. The banner is suppressed under --json.
    auto_refresh_if_stale(
        args.root, no_refresh=args.no_refresh, json_output=args.json,
    )

    # Federated root -> flattened union so the reverse-BFS reaches child
    # dependents (ADR 0089); single repo (fg is None) -> the loaded graph (a
    # corrupt / unsupported single-repo graph becomes a structured error, not a
    # traceback). The one FederatedGraph is reused for the git seed fan-out.
    fg = _federation_or_none(args.root)
    graph = _load_impact_graph(args.root, fg)

    stale_graph = _stale_gate(graph, allow_stale=args.allow_stale)

    kind, seed_ids, unresolved_inputs, target_input, low_capability = (
        _resolve_seeds_from_args(graph, fg, args)
    )

    if kind == "target":
        # Single positional path -- delegate the resolution to ``impact()``
        # so the existing ``target.kind`` semantics ("node" or "path") are
        # preserved.
        result = impact(
            graph,
            target=args.target,
            depth=args.depth,
            stale_graph=stale_graph,
        )
    else:
        # Multi-seed inputs always have an explicit ``input_paths`` list, so
        # the low-capability detection has something to attribute the result
        # to.
        if isinstance(target_input, list):
            input_paths = list(target_input)
        else:
            input_paths = [target_input]
        result = impact(
            graph,
            seeds=seed_ids,
            unresolved_inputs=unresolved_inputs,
            seed_kind=kind,
            target_input=target_input,
            input_paths=input_paths,
            low_capability_inputs=low_capability,
            depth=args.depth,
            stale_graph=stale_graph,
        )
    if args.json:
        # ADR 0082: bound the *agent-facing* payload only. The byte budget
        # exists to fit the agent tool-result cap, and only --json crosses it;
        # ``weld_impact`` applies the same shaping, so the two surfaces agree
        # (ADR 0083). The human summary below is deliberately left unshaped --
        # it reports blast-radius *counts*, and a bounded count would
        # under-report the very number the reader ran the command for.
        from weld.read_traversal import shape_impact

        sys.stdout.write(
            dumps_safe_json(
                shape_impact(result, full_size=args.full_size),
                indent=2,
                sort_keys=True,
            ) + "\n"
        )
    else:
        sys.stdout.write(sanitize_terminal_text(format_human(result)))
    return _cannot_answer_exit(result)


_CANNOT_ANSWER_EXIT_CODE = 3


def _cannot_answer_exit(result: dict) -> int:
    """Emit the structured cannot-answer line and return the exit code.

    ADR 0134: a cannot-answer outcome exits non-zero and states the reason and
    remediation, so an agent parsing ``error[<code>]: ...`` (already the
    contract for ``graph_missing`` and siblings) sees this cause in the shape it
    already handles. The normal envelope/summary is still written to stdout above
    -- this only adds the stderr diagnostic and the non-zero code. A normal
    (answered) result returns 0 unchanged. ``_STALE_EXIT_CODE`` (2) is taken, so
    this uses 3.
    """
    marker = result.get("cannot_answer")
    if not isinstance(marker, dict):
        return 0
    from weld._errors import format_error_line

    code = marker["error_code"]
    reason = marker.get("reason")
    # ``format_error_line`` is itself the terminal-safety boundary: it returns
    # ``sanitize_terminal_line(...)``, so a control byte smuggled into the repo-
    # derived reason cannot forge a second diagnostic line here.
    sys.stderr.write(format_error_line(code, reason) + "\n")
    return _CANNOT_ANSWER_EXIT_CODE

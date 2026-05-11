"""CLI wrapper for blast-radius analysis.

Owns argparse, git shelling, the staleness gate, and the entry-point
``main`` for ``wd impact``. Delegates the BFS and envelope construction to
:mod:`weld.impact_core`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from weld._git import is_git_repo
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
        "--root", type=Path, default=Path("."),
        help="Project root containing .weld/graph.json",
    )
    parser.add_argument(
        "--no-refresh", dest="no_refresh", action="store_true", default=False,
        help="Skip auto-refresh on stale graph (ADR 0051).",
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


def _require_git_repo(root: Path, *, flag: str) -> None:
    """Fail fast with a dedicated message when *root* is not a git repo.

    The ``--from-diff`` and ``--working-tree`` paths shell out to ``git
    diff`` and ``git status``. When *root* is not inside a git working
    tree, those subprocesses leak git's own ``fatal: not a git
    repository`` (or, worse, the multi-page ``--no-index`` usage banner
    on newer git) verbatim into the user-visible error -- functionally
    correct but useless for diagnosing the actual problem. This guard
    detects the condition once, names the offending flag, and points at
    the resolved root so the user knows exactly which directory to fix
    or which ``--root`` to pass instead.
    """
    if is_git_repo(root):
        return
    raise SystemExit(
        f"wd impact: {flag} requires {root} to be a git repository",
    )


def _git_diff_files(root: Path, ref: str) -> list[str]:
    """Return ``git diff --name-only`` output as a list of paths.

    Accepts ``REF`` (compared against the working tree) or
    ``REF1..REF2``-style ranges -- ``git`` parses both transparently.

    Uses ``-c core.quotePath=false`` and ``-z`` so filenames with non-ASCII
    characters round-trip as UTF-8 instead of git's default C-quoted
    octal-escape form, and so embedded whitespace/newlines in filenames
    cannot collide with the record separator.

    Hardens against argument injection: a ``ref`` starting with ``-``
    (e.g. ``--upload-pack=evil``) would otherwise be parsed by git as
    an option flag and surface git's multi-page ``usage: git diff``
    banner -- functionally not RCE because the user owns their own CLI
    invocation, but a confusing failure mode for callers and
    automation. We reject the leading-dash form up front with a clear
    weld-prefixed error, and additionally pass ``--end-of-options`` so
    even a future code path that bypasses this check forces git to
    treat the value as a revision rather than a flag.
    """
    if ref.startswith("-"):
        raise SystemExit(
            f"wd impact: --from-diff ref cannot start with '-' "
            f"(got: {ref!r}); refs starting with '-' are rejected to "
            f"prevent them from being parsed as git options",
        )
    cmd = [
        "git", "-c", "core.quotePath=false", "-C", str(root),
        "diff", "--name-only", "-z", "--end-of-options", ref,
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("wd impact: 'git' executable not found") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SystemExit(
            f"wd impact: git diff failed for '{ref}': "
            f"{stderr or 'no stderr output'}"
        ) from exc
    # ``-z`` emits NUL-separated records; trailing NUL after the last entry
    # yields an empty tail token that the truthy filter drops.
    return [path for path in proc.stdout.split("\0") if path]


def _git_status_files(root: Path) -> list[str]:
    """Return staged + unstaged file paths from ``git status --porcelain=v2 -z``.

    Untracked files (status ``?``) are intentionally included: if the user
    is asking for the working-tree blast radius, brand-new files are part
    of the answer as long as they resolve to graph nodes.

    Uses ``--porcelain=v2 -z`` (NUL-separated, machine-readable) plus
    ``-c core.quotePath=false`` so unicode/quoted filenames round-trip as
    UTF-8 instead of C-quoted octal escapes, and so a filename that
    happens to contain ``" -> "`` is not misclassified as a rename.
    """
    cmd = [
        "git", "-c", "core.quotePath=false", "-C", str(root),
        "status", "--porcelain=v2", "-z",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("wd impact: 'git' executable not found") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SystemExit(
            f"wd impact: git status failed: {stderr or 'no stderr output'}",
        ) from exc

    # NUL-separated records; trailing NUL after last record yields an empty
    # tail token that we drop with the truthy filter below. Record types
    # (porcelain v2): ``1`` ordinary changed entry, ``2`` rename/copy
    # (followed by an extra NUL-separated original-path token we discard),
    # ``u`` unmerged, ``?`` untracked, ``!`` ignored. Header lines start
    # with ``#`` and are skipped.
    tokens = [tok for tok in proc.stdout.split("\0") if tok]
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if tok[0] == "#":
            continue
        prefix = tok[0]
        if prefix == "1":
            # ``1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>`` -- 9 fields.
            parts = tok.split(" ", 8)
            if len(parts) == 9:
                paths.append(parts[8])
        elif prefix == "2":
            # ``2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>``;
            # original-path follows in the next NUL-separated token, which
            # we consume and discard.
            parts = tok.split(" ", 9)
            if len(parts) == 10:
                paths.append(parts[9])
            i += 1
        elif prefix == "u":
            # ``u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>``.
            parts = tok.split(" ", 10)
            if len(parts) == 11:
                paths.append(parts[10])
        elif prefix in ("?", "!"):
            # ``? <path>`` or ``! <path>``.
            parts = tok.split(" ", 1)
            if len(parts) == 2:
                paths.append(parts[1])
    return paths


def _resolve_seeds_from_args(
    graph: Graph,
    args: argparse.Namespace,
) -> tuple[str, list[str], list[str], str | list[str]]:
    """Resolve CLI args into ``(kind, seed_ids, unresolved_inputs, target_input)``.

    ``kind`` is one of ``"node" | "path" | "from-diff" | "files" |
    "working-tree"``. Seed lists and unresolved-input lists are sorted for
    determinism. ``target_input`` is what gets recorded in the JSON envelope
    under ``target.input``.
    """
    if args.target is not None:
        # Existing single-positional behaviour. Seeds and unresolved inputs
        # are derived inside ``impact()`` itself.
        return "target", [], [], args.target
    if args.from_diff is not None:
        _require_git_repo(args.root, flag="--from-diff")
        paths = _git_diff_files(args.root, args.from_diff)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "from-diff", seeds, unresolved, sorted(paths)
    if args.files is not None:
        paths = list(args.files)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "files", seeds, unresolved, sorted(paths)
    if args.working_tree:
        _require_git_repo(args.root, flag="--working-tree")
        paths = _git_status_files(args.root)
        seeds, unresolved = _resolve_paths_to_seeds(graph, paths)
        return "working-tree", seeds, unresolved, sorted(paths)
    # Should be unreachable: _validate_seed_inputs ran before this.
    raise SystemExit("wd impact: no seed input selected")


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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd impact``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_seed_inputs(args)

    from weld._auto_refresh import auto_refresh_if_stale
    from weld._graph_cli import _build_retry_hint, ensure_graph_exists

    # Surface the friendly first-run message if the graph has not been
    # built. Mirrors trace/diff/enrich behaviour.
    retry_target = args.target if args.target is not None else "<seed>"
    ensure_graph_exists(args.root, _build_retry_hint("impact", retry_target))
    # ADR 0051: auto-refresh stale graphs before the existing
    # --allow-stale gate fires. The banner is suppressed under --json.
    auto_refresh_if_stale(
        args.root, no_refresh=args.no_refresh, json_output=args.json,
    )

    graph = Graph(args.root)
    graph.load()

    stale_graph = _stale_gate(graph, allow_stale=args.allow_stale)

    kind, seed_ids, unresolved_inputs, target_input = _resolve_seeds_from_args(
        graph, args,
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
            depth=args.depth,
            stale_graph=stale_graph,
        )
    if args.json:
        json.dump(
            result,
            sys.stdout,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_human(result))
    return 0

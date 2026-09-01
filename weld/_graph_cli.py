"""CLI entry point for the connected structure graph commands.

Parses ``argv``, settles the project root, and routes each command to the
dispatcher that can serve it: :mod:`weld._graph_cli_federated` at a polyrepo
root, :mod:`weld._graph_cli_single` otherwise. What stays here is what both
routes share -- the command sets, the first-run guidance a graph-backed read
needs when ``.weld/graph.json`` is absent, and the retry-hint builder five
sibling CLIs (``brief`` / ``trace`` / ``impact`` / ``diff`` / ``enrich``)
import from this module.

Originally extracted from ``weld.graph`` to keep the core ``Graph`` class
under the 400-line default; the dispatch chains and the renderer-aware
writers (:mod:`weld._graph_cli_emit`) have since been split out for the
same reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

from weld._errors import ERROR_HINTS, GRAPH_MISSING, default_summary
from weld._graph_cli_emit import _emit, _emit_node_lookup, _out
from weld._graph_cli_parser import build_parser
from weld._graph_cli_single import run_single_repo
from weld._root_resolver import resolve_weld_root
from weld._safe_text import sanitize_terminal_text


# Graph-backed read commands. A missing `.weld/graph.json` here yields an
# actionable first-run message instead of a silently empty payload. Mutating
# commands (add-*/rm-*/import/touch) and diagnostic commands
# (stale/stats/dump/list/validate*) are intentionally excluded. ``find`` is
# also excluded because it reads the file-index, not the graph -- users can
# run ``wd build-index`` + ``wd find`` without ever producing a graph. That
# exempts it from *this* precondition only: it carries its own, over the
# artifact it does read (:mod:`weld._find_precondition`, ADR 0134).
_READ_COMMANDS = frozenset(
    {"query", "context", "path", "callers", "references", "communities"}
)

# Commands that answer from ``.weld/`` state and must therefore get a fresh
# checkout's seed (ADR 0096 §2) before they answer -- but that must keep
# answering when the checkout stays graphless, so they take the seed WITHOUT
# the first-run refusal ``_READ_COMMANDS`` carries.
#
# Splitting the two is the bug fix (bd 6osw). ADR 0096 §2 places seeding at
# "the single funnel all graph-backed read CLIs pass through", but the funnel
# it was wired into is ``ensure_graph_exists``, whose membership is the set
# above -- chosen to answer a different question ("who gets first-run
# guidance?"). A fresh worktree's ``wd stale`` therefore reported ``no graph``
# and ``wd find`` reported ``no matches`` -- the second a false negative for a
# file plainly on disk -- while ``wd query`` one keystroke away seeded and
# answered correctly. That is the exact wrong-branch-class answer the seeding
# exists to prevent, and it hit the surface agents are told to run *first*
# (CLAUDE.md: check ``wd stale`` before code work).
#
# These commands cannot simply join ``_READ_COMMANDS``: a freshness probe that
# exits 1 instead of reporting "no graph" is useless, and ``find`` answers off
# the file-index, which a user may legitimately build without any graph.
# Mutating commands stay out on ADR 0096's own rule -- commands that create
# state stay deliberate.
#
# Seed-only is not precondition-free, and reading it that way was the N9
# defect: ``find`` took the seed and then answered "no matches" from an index
# the seed had not produced either. Its precondition lives with the artifact
# it reads (:mod:`weld._find_precondition`), applied by both dispatchers
# after this seeding step.
_SEED_ONLY_COMMANDS = frozenset({"stale", "find", "stats", "list", "dump"})

# Commands that rewrite .weld/graph.json. Each runs load -> mutate -> save,
# so the whole span must hold the exclusive graph write lock (ADR 0094) --
# unlocked concurrent mutators silently lose each other's writes.
_MUTATING_COMMANDS = frozenset(
    {"add-node", "add-edge", "rm-node", "rm-edge", "import", "migrate", "touch"}
)


def _build_retry_hint(cmd: str, *positional: str, **flags: str) -> str:
    """Format a copy-paste ``wd <cmd> ...`` retry hint.

    Centralizes the quote/flag pattern so call sites (here in
    :func:`_retry_hint` plus the inline hints in ``brief`` / ``trace`` /
    ``impact`` / ``diff`` / ``enrich``) all produce the same shape.

    - Positional args are quoted in order:
      ``_build_retry_hint("path", "a:b", "c:d")`` -> ``wd path "a:b" "c:d"``.
    - Keyword flags become ``--flag "value"`` (underscores in the keyword
      become dashes):
      ``_build_retry_hint("enrich", node="entity:Store")`` ->
      ``wd enrich --node "entity:Store"``.
    - With no extra args: ``_build_retry_hint("diff")`` -> ``wd diff``.
    """
    parts = [f"wd {cmd}"]
    for value in positional:
        parts.append(f'"{value}"')
    for flag, value in flags.items():
        parts.append(f'--{flag.replace("_", "-")} "{value}"')
    return " ".join(parts)


def _retry_hint(cmd: str, args) -> str:
    """Format a copy-paste retry command for the guidance block."""
    if cmd == "path":
        return _build_retry_hint("path", args.from_id, args.to_id)
    if cmd == "context":
        return _build_retry_hint("context", args.node_id)
    if cmd == "callers":
        return _build_retry_hint("callers", args.symbol)
    if cmd == "references":
        return _build_retry_hint("references", args.name)
    # query / brief take a bare term.
    term = getattr(args, "term", None) or "<term>"
    return _build_retry_hint(cmd, term)


def missing_graph_message(retry_cmd: str, cause: str | None = None) -> str:
    """Return the friendly missing-graph guidance block (tracked issue / -uqo).

    Used by graph-backed read commands (``wd brief`` / ``query`` /
    ``context`` / ``path`` / ``callers`` / ``references`` / ``trace`` /
    ``impact`` / ``diff`` / ``enrich``) when ``.weld/graph.json`` has not
    yet been produced. ``wd find`` is intentionally exempt -- it reads the
    file-index, not the graph, and refuses on *that* artifact instead
    (:mod:`weld._find_precondition`).

    The headline and the remediation are **read** from ``weld/_errors.py``
    (:func:`~weld._errors.default_summary` and
    :data:`~weld._errors.ERROR_HINTS` under ``graph_missing``), not spelled
    here. Both were literals until bd ``5038-koqmb``, which made this block a
    second source for a vocabulary those tables own: the same two strings also
    reach an agent through :func:`weld._mcp_guard.missing_graph_payload`, which
    has always derived them, so a reworded table used to move one surface and
    leave this one saying what nothing else said any more. Keep the wording
    stable -- onboarding docs and tests match against its substrings -- but
    change it in ``weld/_errors.py``, which is now the only place it can be
    changed.

    *cause* is an optional explanation of why this particular checkout has
    no graph, from :func:`weld._worktree_seed.seed_blocked_reason`. It sits
    between the headline and the remediation because the two say different
    things: the standing ``wd init`` / ``wd discover`` lines fix *this*
    checkout right now, while a cause names a repository-wide prerequisite
    that has to change for the next checkout to fare any better. Additive by
    construction -- every substring the block already had survives.
    """
    return (
        f"{default_summary(GRAPH_MISSING)}\n"
        + (f"{cause}\n" if cause else "")
        + f"{ERROR_HINTS[GRAPH_MISSING]}\n"
        + f"Then retry: {retry_cmd}."
    )


def ensure_graph_exists(root: Path, retry_cmd: str, *, no_refresh: bool = False) -> None:
    """Exit with an actionable message when ``.weld/graph.json`` is missing.

    This is a no-op when the graph file is present (even if empty). Callers
    should invoke this *before* constructing a :class:`~weld.graph.Graph` so
    first-run users get guidance instead of an empty-payload success.

    Seeding runs first (ADR 0096 §2): this is the single funnel every
    graph-backed read passes through, so it is where a fresh checkout gets
    the ``.weld/`` state its tracked graph arrived without. It must precede
    the existence check below, because the Mode B case *has* a graph and
    would otherwise return before ever being repaired. Imported lazily to
    keep the module import graph here flat.

    *no_refresh* is the caller's ``--no-refresh``, one of the two opt-outs
    ADR 0051 gives from the read path's write side-effect. Seeding is a
    write, so every command offering the flag must hand it on: a dropped
    hand-off makes the flag mean less there than on its neighbours.
    Commands with no such flag (``diff``, ``enrich``) take the default.
    """
    from weld._worktree_seed import ensure_seeded, seed_blocked_reason

    ensure_seeded(root, no_refresh=no_refresh)
    graph_path = Path(root) / ".weld" / "graph.json"
    if graph_path.exists():
        return
    # A decline that the user could not have predicted gets named here rather
    # than left to the generic block (field eval 0.23.1 finding 09). The probe
    # sits on the exit path, so its git shell-out costs a read nothing: this
    # line is only reached when the command is already about to fail.
    # retry_cmd embeds the user's own search term (_build_retry_hint), so this
    # block is not the fixed string it looks like.
    sys.stderr.write(
        sanitize_terminal_text(
            missing_graph_message(retry_cmd, seed_blocked_reason(root)),
        ) + "\n",
    )
    sys.exit(1)


def _run_graph_index(args) -> None:  # type: ignore[no-untyped-def]
    """Handle ``wd graph index --rebuild`` (ADR 0058)."""
    if not getattr(args, "rebuild", False):
        sys.stderr.write(
            "wd graph index: pass --rebuild to force a sqlite sidecar rebuild "
            "from the canonical graph.json (ADR 0058).\n",
        )
        sys.exit(2)
    graph_path = Path(args.root) / ".weld" / "graph.json"
    if not graph_path.is_file():
        sys.stderr.write(
            f"wd graph index: {graph_path} not found; run `wd discover` first.\n",
        )
        sys.exit(1)
    from weld._sqlite_writer import build_sidecar_from_graph_path
    target = build_sidecar_from_graph_path(graph_path)
    _out({"sidecar": str(target), "source_json": str(graph_path), "status": "rebuilt"})


def main(argv: list[str] | None = None, *, prog: str = "wd") -> None:  # noqa: C901
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    # ADR 0096: an omitted --root means "the checkout I am standing in",
    # resolved once here so every branch below sees a settled root.
    args.root = resolve_weld_root(args.root)
    cmd = args.command
    from weld._graph_cli_federated import FEDERATED_CLI_COMMANDS
    if cmd in FEDERATED_CLI_COMMANDS:
        from weld.workspace_state import load_workspace_config

        if load_workspace_config(args.root) is not None:
            # ADR 0066 part 3: auto-recurse stale children before serving a
            # federated read. Refreshes only the stale-or-uninitialized
            # subset and rebuilds the root meta-graph, so the FederatedGraph
            # constructed by the dispatcher loads fresh root + child bytes.
            # Honours WELD_AUTO_REFRESH=0 / --no-refresh (the gate freeze).
            from weld._auto_refresh import auto_refresh_if_stale
            auto_refresh_if_stale(
                args.root,
                no_refresh=getattr(args, "no_refresh", False),
                json_output=getattr(args, "as_json", False),
            )

            # ADR 0134 (Finding 02): a graph-backed federated read at a root
            # with no ``.weld/graph.json`` must surface the same cannot-answer
            # "No Weld graph found" guidance + non-zero exit the single-repo
            # path does, not a well-formed empty result at exit 0 -- the two
            # are indistinguishable to the agent that is weld's primary
            # consumer. This is a routing fix, not new vocabulary: it reaches
            # the *same* precondition ``_READ_COMMANDS`` already hit below, at
            # or before ADR 0089's read-time flatten (``FederatedGraph`` loads
            # the root graph in its constructor). ``find`` stays exempt from
            # *this* check on the same rule the single-repo route uses -- it
            # answers off the file-index, so a graph-less root is not a
            # cannot-answer state for it -- and applies the equivalent check
            # over the file-index inside ``run_federated_cli``. Runs after the
            # auto-refresh above so a refresh that legitimately builds the root
            # graph is honoured first.
            if cmd != "find":
                ensure_graph_exists(
                    args.root, _retry_hint(cmd, args),
                    no_refresh=getattr(args, "no_refresh", False),
                )

            # query/context/path navigate the FederatedGraph; callers/
            # references fan out per child; communities runs over the flattened
            # union; find fans out across child file-indexes (ADR 0089).
            from weld._graph_cli_federated import run_federated_cli
            run_federated_cli(
                cmd, args, emit=_emit, emit_node_lookup=_emit_node_lookup,
            )
            return
    if cmd == "index":
        _run_graph_index(args)
        return
    if cmd in _READ_COMMANDS:
        # Single-repo read path: surface a friendly first-run message when
        # the graph has not been built yet (tracked issue).
        ensure_graph_exists(
            args.root, _retry_hint(cmd, args),
            no_refresh=getattr(args, "no_refresh", False),
        )
    elif cmd in _SEED_ONLY_COMMANDS:
        # Same seed, no refusal (bd 6osw). Imported lazily for the same
        # reason ``ensure_graph_exists`` does it: keep this module's import
        # graph flat.
        from weld._worktree_seed import ensure_seeded

        ensure_seeded(args.root, no_refresh=getattr(args, "no_refresh", False))
    # ADR 0051: auto-refresh stale graphs before serving. ``find`` is
    # included even though it reads the file-index (not the graph)
    # because the same incremental discovery pass that refreshes the
    # graph also rewrites the file-index sidecar.
    if cmd in _READ_COMMANDS or cmd == "find":
        from weld._auto_refresh import auto_refresh_if_stale
        auto_refresh_if_stale(
            args.root,
            no_refresh=getattr(args, "no_refresh", False),
            json_output=getattr(args, "as_json", False),
        )
    if cmd in _MUTATING_COMMANDS:
        from weld._graph_write_lock import graph_write_lock
        with graph_write_lock(args.root):
            run_single_repo(cmd, args)
        return
    run_single_repo(cmd, args)

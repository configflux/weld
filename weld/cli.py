"""Top-level CLI dispatcher for the Weld package."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def _install_sigpipe_handler() -> None:
    """Let downstream consumers (``head``, ``less``) close our stdout quietly.

    By default CPython catches SIGPIPE, translates it to BrokenPipeError, and
    prints a traceback on stderr when a write fails. Restoring the default
    handler lets the OS terminate us as soon as we try to write into a
    closed pipe, with no traceback. Best-effort: Windows has no SIGPIPE, and
    some environments (e.g. embedded interpreters) may disallow signal
    changes.
    """
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, OSError, ValueError):
        pass


def _suppress_broken_pipe_at_exit() -> None:
    """Silence tracebacks when a BrokenPipeError escapes to Python's shutdown.

    Even with SIG_DFL installed, buffered writes during interpreter shutdown
    can surface a BrokenPipeError via ``sys.stderr.flush``. Redirect stdout
    and stderr to ``os.devnull`` so shutdown-time flushes do not emit
    tracebacks. Safe to call more than once.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, sys.stdout.fileno())
        os.dup2(devnull, sys.stderr.fileno())
    except (OSError, ValueError):
        pass
    finally:
        os.close(devnull)


_HELP = """Usage: wd <command> [args]

Core commands:
  init           Bootstrap .weld/discover.yaml for the current repo
  discover       Run discovery and emit graph JSON to stdout
  agents         Agent Graph for static AI customization assets
  graph          Canonical graph namespace (stats, validate, query, context, ...)
  workspace      Inspect child status (status) or one-shot federate a polyrepo (bootstrap)
  build-index    Regenerate .weld/file-index.json
  scaffold       Write bundled templates into the current repo
  prime          Check setup status and suggest next steps
  doctor         Diagnostic checks: config, graph, staleness, strategies, tree-sitter
  security       Trust-posture summary (alias for `wd doctor --security`, JSON via --json)
  bootstrap      Write onboarding assets (wd bootstrap claude|codex|copilot)
  bench          Run Weld benchmarks (token cost, first-context quality, or --compare agent tasks)
  demo           Materialize a Weld demo workspace (monorepo or polyrepo)

Retrieval commands:
  brief          Agent-facing context briefing (stable JSON contract)
  trace          Startup/runtime and interaction slice
  impact         Reverse-dependency blast-radius analysis
  query          Tokenized graph search
  find           File-index keyword search
  context        Node + immediate neighborhood
  path           Shortest path between nodes
  callers        Direct (and optionally transitive) callers of a symbol
  references     Callers + textual file-index references for a symbol name
  enrich         LLM-assisted semantic enrichment

Visualization commands:
  export         Export graph to Mermaid, DOT, or D2 format
  viz            Serve a local read-only browser graph explorer

Live commands:
  watch          Watch source files and auto-rediscover on change

MCP commands:
  mcp            MCP server tooling (e.g. `wd mcp config --client=claude`)
  telemetry      show / export / clear local telemetry

Global flags:
  --no-telemetry Disable local telemetry for this invocation (ADR 0035)

Graph commands:
  graph stats    Graph summary counts (canonical)
  graph validate Validate graph against the contract (canonical)
  graph validate-fragment
  diff           Show what changed between discovery runs
  list           List nodes
  stats          Alias for `wd graph stats`
  stale          Compare graph freshness to git HEAD
  dump           Emit full graph JSON
  validate       Alias for `wd graph validate`
  validate-fragment
                 Alias for `wd graph validate-fragment`
  add-node       Add or update a node
  add-edge       Add an edge
  rm-node        Remove a node and its edges
  rm-edge        Remove edges
  import         Merge graph JSON from a file
  lint           Lint the graph for architectural violations

Run `wd <command> --help` for command-specific help.
"""

def _run_export(argv: list[str]) -> int:
    """Parse export subcommand args and run the export."""
    import argparse

    from weld.export import export

    parser = argparse.ArgumentParser(prog="wd export")
    parser.add_argument(
        "--format",
        "-f",
        default="mermaid",
        choices=("mermaid", "dot", "d2"),
        help="Output format (default: mermaid)",
    )
    parser.add_argument(
        "--node",
        default=None,
        help="Center node ID for subgraph extraction",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="BFS depth for subgraph extraction (default: 1)",
    )
    args = parser.parse_args(argv)
    output = export(args.format, node_id=args.node, depth=args.depth)
    sys.stdout.write(output)
    return 0


def _strip_no_telemetry(args: list[str]) -> tuple[list[str], bool]:
    """Remove every ``--no-telemetry`` token. ADR 0035 § "Opt-out" point 1."""
    out = [t for t in args if t != "--no-telemetry"]
    return out, len(out) != len(args)


def _resolve_command_name(args: list[str]) -> str:
    """Map *args* to a telemetry ``command`` value (post-strip).

    Empty / ``-h`` / ``--help`` -> ``"help"``. ``-V`` / ``--version`` ->
    ``"version"``. First non-flag token coerced through the CLI allowlist.
    """
    if not args:
        return "help"
    if args[0] in {"-h", "--help"}:
        return "help"
    if args[0] in {"-V", "--version"}:
        return "version"
    from weld._telemetry_allowlist import coerce_command

    for tok in args:
        if tok and not tok.startswith("-"):
            return coerce_command("cli", tok)
    return "unknown"


def _collect_flag_names(args: list[str]) -> list[str]:
    """Return long/short flag tokens in *args* (``--k=v`` -> ``--k``)."""
    names: list[str] = []
    for tok in args:
        if not isinstance(tok, str) or not tok or tok == "--":
            continue
        if tok.startswith("--"):
            names.append(tok.split("=", 1)[0])
        elif tok.startswith("-") and len(tok) > 1:
            names.append(tok)
    return names


def _is_telemetry_clear(args: list[str]) -> bool:
    """``wd telemetry clear ...`` must not self-record (ADR 0035 UX)."""
    return len(args) >= 2 and args[0] == "telemetry" and args[1] == "clear"


def main(argv: list[str] | None = None) -> int:
    _install_sigpipe_handler()
    raw = list(sys.argv[1:] if argv is None else argv)
    stripped, flag_seen = _strip_no_telemetry(raw)
    # Skip telemetry entirely for ``wd telemetry clear`` so the file
    # really is gone after the command runs (ADR 0035 § "First-run Notice").
    skip = _is_telemetry_clear(stripped)
    # Lazy-import the Recorder so a Python import error in telemetry
    # cannot break ``wd`` itself (ADR 0035 § "Failure-isolated writer").
    rec_cm = None
    if not skip:
        try:
            from weld._telemetry import Recorder, resolve_path

            try:
                target = resolve_path(Path.cwd())
                root = target.parent.parent if target is not None else None
            except Exception:
                root = None
            rec_cm = Recorder(
                surface="cli",
                command=_resolve_command_name(stripped),
                flags=_collect_flag_names(stripped),
                root=root,
                cli_flag=(False if flag_seen else None),
            )
        except Exception:
            rec_cm = None

    try:
        if rec_cm is None:
            return _dispatch(stripped)
        with rec_cm as rec:
            try:
                rc = _dispatch(stripped)
            except BrokenPipeError:
                rec.set_exit_code(141)
                raise
            rec.set_exit_code(rc if isinstance(rc, int) else 0)
            return rc
    except BrokenPipeError:
        _suppress_broken_pipe_at_exit()
        # 128 + SIGPIPE (13) is the conventional exit code for a pipe close.
        return 141


def _dispatch(argv: list[str] | None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_HELP)
        return 0

    if args[0] in {"--version", "-V"}:
        try:
            from importlib.metadata import version

            print(f"wd {version('configflux-weld')}")
        except Exception:
            from pathlib import Path

            version_file = Path(__file__).resolve().parent.parent / "VERSION"
            if version_file.exists():
                print(f"wd {version_file.read_text().strip()}")
            else:
                print("wd (version unknown)")
        return 0

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "init":
        from weld import init as init_mod

        init_mod.main(rest)
        return 0

    if subcmd == "discover":
        from weld import discover as discover_mod

        return discover_mod.main(rest)

    if subcmd == "agents":
        from weld import agent_graph_cli as agents_mod

        return agents_mod.main(rest)

    if subcmd == "workspace":
        from weld import workspace_state as workspace_state_mod

        return workspace_state_mod.main(rest)

    if subcmd == "build-index":
        from weld import file_index as file_index_mod

        file_index_mod.main(rest)
        return 0

    if subcmd == "brief":
        from weld.brief import main as brief_main

        brief_main(rest)
        return 0

    if subcmd == "trace":
        from weld.trace import main as trace_main

        trace_main(rest)
        return 0

    if subcmd == "scaffold":
        from weld import scaffold as scaffold_mod

        scaffold_mod.main(rest)
        return 0

    if subcmd == "prime":
        from weld import prime as prime_mod

        prime_mod.main(rest)
        return 0

    if subcmd == "doctor":
        from weld import doctor as doctor_mod

        return doctor_mod.main(rest)

    if subcmd == "security":
        from weld import security as security_mod

        return security_mod.main(rest)

    if subcmd == "bootstrap":
        from weld import bootstrap as bootstrap_mod

        bootstrap_mod.main(rest)
        return 0

    if subcmd == "bench":
        from weld.bench.runner import main as bench_main

        return bench_main(rest)

    if subcmd == "demo":
        from weld import demo as demo_mod

        return demo_mod.main(rest)

    if subcmd == "export":
        return _run_export(rest)

    if subcmd == "viz":
        from weld.viz.server import main as viz_main

        return viz_main(rest)

    if subcmd == "impact":
        from weld import impact as impact_mod

        return impact_mod.main(rest)

    if subcmd == "enrich":
        from weld import enrich as enrich_mod

        return enrich_mod.main(rest)

    if subcmd == "diff":
        from weld import diff as diff_mod

        return diff_mod.main(rest)

    if subcmd == "watch":
        from weld import watch as watch_mod

        return watch_mod.main(rest)

    if subcmd == "lint":
        from weld import arch_lint as arch_lint_mod

        return arch_lint_mod.main(rest)

    if subcmd == "mcp":
        from weld import mcp_config as mcp_config_mod

        return mcp_config_mod.main(rest)

    if subcmd == "telemetry":
        from weld import telemetry_cli

        return telemetry_cli.main(rest)

    from weld import graph as graph_mod

    if subcmd == "graph":
        graph_mod.main(rest, prog="wd graph")
        return 0

    graph_mod.main(args)
    return 0

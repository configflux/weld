"""Top-level CLI dispatcher for the Weld package."""

from __future__ import annotations

import sys

_HELP = """Usage: wd <command> [args]

Core commands:
  init           Bootstrap .weld/discover.yaml for the current repo
  discover       Run discovery and emit graph JSON to stdout
  workspace      Inspect workspace child status from the root ledger
  build-index    Regenerate .weld/file-index.json
  scaffold       Write bundled templates into the current repo
  prime          Check setup status and suggest next steps
  doctor         Diagnostic checks: config, graph, staleness, strategies, tree-sitter
  bootstrap      Write onboarding assets (wd bootstrap claude|codex|copilot)
  bench          Run Weld benchmarks (token cost, first-context quality, or --compare agent tasks)

Retrieval commands:
  brief          Agent-facing context briefing (stable JSON contract)
  trace          Protocol-aware cross-boundary capability slice
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

Graph commands:
  diff           Show what changed between discovery runs
  list           List nodes
  stats          Graph summary counts
  stale          Compare graph freshness to git HEAD
  dump           Emit full graph JSON
  validate       Validate graph against the contract
  validate-fragment
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


def main(argv: list[str] | None = None) -> int:
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

    if subcmd == "bootstrap":
        from weld import bootstrap as bootstrap_mod

        bootstrap_mod.main(rest)
        return 0

    if subcmd == "bench":
        from weld.bench.runner import main as bench_main

        return bench_main(rest)

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

    from weld import graph as graph_mod

    graph_mod.main(args)
    return 0

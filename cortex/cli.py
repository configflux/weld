"""Top-level CLI dispatcher for the Cortex package."""

from __future__ import annotations

import sys

_HELP = """Usage: cortex <command> [args]

Core commands:
  init           Bootstrap .cortex/discover.yaml for the current repo
  discover       Run discovery and emit graph JSON to stdout
  build-index    Regenerate .cortex/file-index.json
  scaffold       Write bundled templates into the current repo
  prime          Check setup status and suggest next steps
  bootstrap      Write onboarding assets (cortex bootstrap claude|codex)
  bench          Run the on-demand token-cost benchmark (writes cortex/docs/bench-results.md)
  migrate        Migrate a project from the legacy kg/ layout to cortex/ (ADR 0019)

Retrieval commands:
  brief          Agent-facing context briefing (stable JSON contract)
  trace          Protocol-aware cross-boundary capability slice
  query          Tokenized graph search
  find           File-index keyword search
  context        Node + immediate neighborhood
  path           Shortest path between nodes
  callers        Direct (and optionally transitive) callers of a symbol
  references     Callers + textual file-index references for a symbol name

Graph commands:
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

Run `cortex <command> --help` for command-specific help.
"""

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_HELP)
        return 0

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "init":
        from cortex import init as init_mod

        init_mod.main(rest)
        return 0

    if subcmd == "discover":
        from cortex import discover as discover_mod

        discover_mod.main(rest)
        return 0

    if subcmd == "build-index":
        from cortex import file_index as file_index_mod

        file_index_mod.main(rest)
        return 0

    if subcmd == "brief":
        from cortex.brief import main as brief_main

        brief_main(rest)
        return 0

    if subcmd == "trace":
        from cortex.trace import main as trace_main

        trace_main(rest)
        return 0

    if subcmd == "scaffold":
        from cortex import scaffold as scaffold_mod

        scaffold_mod.main(rest)
        return 0

    if subcmd == "prime":
        from cortex import prime as prime_mod

        prime_mod.main(rest)
        return 0

    if subcmd == "bootstrap":
        from cortex import bootstrap as bootstrap_mod

        bootstrap_mod.main(rest)
        return 0

    if subcmd == "bench":
        from cortex.bench.runner import main as bench_main

        return bench_main(rest)

    if subcmd == "migrate":
        from cortex import migrate as migrate_mod

        return migrate_mod.main(rest)

    from cortex import graph as graph_mod

    graph_mod.main(args)
    return 0

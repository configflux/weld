# Weld

Weld is a local codebase and agent graph for AI coding workflows.

It scans code, docs, CI, build files, runtime configs, repo boundaries, and
AI customization files into deterministic local graphs. Agents can query those
graphs through the `wd` CLI or MCP instead of rediscovering the repository from
scratch every session.

The primary graph lives at `.weld/graph.json`. Agent customization inventory
lives at `.weld/agent-graph.json`.

## Install

Recommended:

```bash
uv tool install configflux-weld
wd --version
```

Supported alternatives:

```bash
pipx install configflux-weld
pip install configflux-weld
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
```

Use the source checkout path only when developing Weld itself:

```bash
pip install -e weld/
```

Optional extras:

```bash
pip install "configflux-weld[tree-sitter]"  # broader language extraction
pip install "configflux-weld[mcp]"          # run python -m weld.mcp_server
pip install "configflux-weld[openai]"       # or [anthropic], [ollama], [llm]
```

MCP config generation (`wd mcp config`) works in the default install. Running
the stdio MCP server requires the `mcp` extra.

## Quickstart

```bash
cd path/to/your/repo
wd init
wd discover --safe --output .weld/graph.json
wd build-index

wd query "authentication"
wd trace "how does this service start"
wd find "login"
wd brief "authentication"
wd context file:src/auth/handler
wd graph stats
wd graph validate
```

The full five-minute tutorial is in the public repository:

https://github.com/configflux/weld/blob/main/docs/tutorial-5-minutes.md

## Agent Graph

Weld also maps the AI customization layer around a repository: agents, skills,
instructions, prompts, commands, hooks, MCP servers, tool permissions, and
platform-specific copies.

```bash
wd agents discover
wd agents list
wd agents audit
wd agents explain planner
wd agents impact .github/agents/planner.agent.md
wd agents plan-change "planner should always include test strategy"
wd agents viz --no-open
```

Agent Graph discovery is static and repo-bound. It reads known customization
files and does not execute project code. After discovery, `wd agents viz`
opens a local read-only browser explorer for `.weld/agent-graph.json`.

Platform coverage is tracked in the support matrix:

https://github.com/configflux/weld/blob/main/docs/platform-support.md

## MCP

Generate client snippets from any install:

```bash
wd mcp config --client=claude
wd mcp config --client=vscode
wd mcp config --client=cursor
```

Run the stdio MCP server from an environment that includes the optional SDK:

```bash
uv tool install "configflux-weld[mcp]"
python -m weld.mcp_server --help
python -m weld.mcp_server
```

MCP documentation:

https://github.com/configflux/weld/blob/main/docs/mcp.md

## Trust Model

- Default discovery reads repository files and writes local graph data. It does
  not execute discovered application code and does not open network connections.
- `wd discover --safe` disables project-local strategies and external adapters.
- `wd enrich --safe` refuses network and LLM providers.
- Project-local strategies and `external_json` adapters are trusted-repository
  features because they can execute code or commands during discovery.

Security policy:

https://github.com/configflux/weld/blob/main/SECURITY.md

## Local Telemetry

Weld records the success or failure of every `wd` CLI invocation and MCP tool
call to a local-only file. Nothing leaves your machine; there is no remote
endpoint and no upload.

Each event is one JSON line with a strict allowlist: subcommand or tool name,
exit code, duration in milliseconds, and the exception class name on failure.
Paths, query strings, error messages, flag values, and usernames are never
recorded. The redaction runs at write time, so the file on disk is already
safe to attach to a bug report.

In a single repo the file is `<repo>/.weld/telemetry.jsonl`. In a polyrepo
workspace every event aggregates into `<workspace_root>/.weld/telemetry.jsonl`.
Invocations outside any project fall back to
`${XDG_STATE_HOME:-~/.local/state}/weld/telemetry.jsonl`. The file is
gitignored and rotates at 1 MiB.

Opt out with any one of: `WELD_TELEMETRY=off`, `--no-telemetry`, or
`wd telemetry disable`. Use `wd telemetry --help` to inspect, export, or
clear the file. The full event schema and design are documented in
[`docs/telemetry.md`](https://github.com/configflux/weld/blob/main/docs/telemetry.md).

## Polyrepo Federation

Weld supports workspace roots that contain multiple child Git repositories.
Each child keeps its own `.weld/graph.json`; the root graph records repo nodes
and cross-repo relationships without duplicating child content.

Start with:

```bash
wd init
wd workspace status
wd discover --safe --output .weld/graph.json
```

Workspace child scans discover gitignored child repos by default for
compatibility. To opt into Git ignore rules for scan-only children, set
`scan.respect_gitignore: true` in `.weld/workspaces.yaml` or run
`wd workspace bootstrap --respect-gitignore`. `scan.exclude_paths` also
accepts bare directory names, relative paths, and `*` / `**` glob patterns.

Example:

https://github.com/configflux/weld/tree/main/examples/05-polyrepo

## More

- Toolkit onboarding: [docs/onboarding.md](docs/onboarding.md)
- Agent workflow guide: [docs/agent-workflow.md](docs/agent-workflow.md)
- Strategy cookbook: [docs/strategy-cookbook.md](docs/strategy-cookbook.md)
- Templates: run `wd scaffold` to write starter strategy files.
- Repository: https://github.com/configflux/weld
- Changelog: https://github.com/configflux/weld/blob/main/CHANGELOG.md
- Community and support: https://github.com/configflux/weld/blob/main/docs/community.md

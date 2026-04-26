# Contributing to Weld

Thank you for your interest in Weld. Weld is currently **maintainer-led**.
Issues, bug reports, demo repos, documentation improvements, and strategy
proposals are welcome. For larger changes, please open an issue first so we
can align on scope before implementation.

## Reporting Issues

Bug reports and feature requests are welcome. Open a GitHub issue with:

- A clear description of the problem or suggestion.
- Steps to reproduce (for bugs).
- Expected vs. actual behavior.
- Output of `wd --version` and your Python version.

Open-ended feedback -- architecture ideas, setup show-and-tell, questions
about MCP-client integration, strategy requests, polyrepo patterns -- is
also welcome as a GitHub issue. Use the closest issue template (the
**Feature request**, **Strategy request**, or **Demo feedback** templates
cover most cases) and maintainers will triage it. See
[docs/community.md](docs/community.md) for how community feedback is
organized today.

## Running Locally

If you just want to try weld on your own codebase, the fastest path is
the installer — no clone required:

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
wd prime
```

Continue reading if you want a local development checkout for debugging
or experimenting with the source.

Weld is source/Git-first for now: `install.sh`, editable checkout installs,
and Git URL installs are the supported public paths. A package-index
publication path is not promised by this release.

### Prerequisites

- **Python** >= 3.10 (3.10–3.13 supported for runtime installs; Bazel
  contributor tests use the Python 3.12 toolchain pinned in `MODULE.bazel`)
- **Git**

### Development setup

```bash
git clone https://github.com/configflux/weld.git
cd weld
pip install -e weld/
```

### Optional extras (source checkout)

Editable source-checkout installs for the optional extras. Use these only
when developing Weld itself; end users should install via
`uv tool install "configflux-weld[<extra>]"` (see the root
[README.md](README.md)).

```bash
pip install -e "weld/[tree-sitter]"   # broader language extraction (Go, Rust, TypeScript, C++)
pip install -e "weld/[mcp]"           # run python -m weld.mcp_server (stdio MCP server)
pip install -e "weld/[openai]"        # OpenAI enrichment provider
pip install -e "weld/[anthropic]"     # Anthropic enrichment provider
pip install -e "weld/[ollama]"        # Ollama enrichment provider
pip install -e "weld/[llm]"           # llm-cli enrichment provider
```

### Verify

```bash
wd --help
wd discover
```

### Agent-driven setup

If an agent is running setup on your behalf, it can use the same
installer and then bootstrap framework-specific onboarding files:

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
wd prime
wd bootstrap claude
wd bootstrap codex     # writes .codex/config.toml + .codex/skills/weld/SKILL.md
wd bootstrap copilot
```

## License

Weld is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for
details.

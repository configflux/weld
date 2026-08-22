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
pip install -e "weld/[mcp]"           # run the stdio MCP server (see note below)
pip install -e "weld/[openai]"        # OpenAI enrichment provider
pip install -e "weld/[anthropic]"     # Anthropic enrichment provider
pip install -e "weld/[ollama]"        # Ollama enrichment provider
pip install -e "weld/[llm]"           # llm-cli enrichment provider
```

Users launch the MCP server as `wd mcp serve`, the console script installed
with the package. Working on Weld itself, run it as `python -m
weld.mcp_server` instead: that form serves the checkout you are in, whereas
`wd` resolves to whatever copy is installed on your `PATH`. This repo's own
`.mcp.json` and `.codex/config.toml` are pinned to the `python -m` form for
that reason, and are deliberately not the shape `wd mcp config` generates.

### Which weld is running

The same split applies to every command, not just the MCP server, and it is
the one that quietly wastes an afternoon: `wd discover` inside your checkout
runs the *installed* build against your tree, so a change you just made is
not exercised and the run still looks entirely successful. Weld tells you
when that is happening — one line on stderr, before the command's own
output:

```text
[weld] running weld 0.21.0 from ~/.local/share/uv/tools/configflux-weld/lib/python3.12/site-packages/weld -- not the checkout you are in (/repos/weld, VERSION 0.22.1), so changes in this tree are not exercised; use `python3 -m weld` from the checkout. Silence: WELD_SOURCE_CHECKOUT_NOTICE=off
```

Two ways to make it stop, and they mean different things. Run `python3 -m
weld …` instead of `wd` — that always executes the checkout you are standing
in, and is the form to use whenever you are verifying a change. Or point the
installed console script at this checkout, after which `wd` and the tree are
the same thing and the notice goes away on its own:

```bash
uv tool install --reinstall --editable ./weld   # or: pip install -e weld/
```

An editable install is pinned to *one* checkout, so it does not help in a
second worktree of the same repository — there, `python3 -m weld` remains the
only form that runs your code, and the notice keeps saying so.
`WELD_SOURCE_CHECKOUT_NOTICE=off` silences the line without changing which
build runs, so reach for it only when you know which one you are getting.

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

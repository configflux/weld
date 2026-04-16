# Contributing to Weld

Thank you for your interest in Weld. This project is
**maintainer-driven** and is not currently accepting external pull requests.

## Reporting Issues

Bug reports and feature requests are welcome. Open a GitHub issue with:

- A clear description of the problem or suggestion.
- Steps to reproduce (for bugs).
- Expected vs. actual behavior.
- Output of `wd --version` and your Python version.

## Running Locally

If you just want to try weld on your own codebase, the fastest path is
the installer — no clone required:

```bash
curl -fsSL https://raw.githubusercontent.com/configflux/weld/main/install.sh | sh
wd prime
```

Continue reading if you want a local development checkout for debugging
or experimenting with the source.

### Prerequisites

- **Python** >= 3.10 (3.10–3.13 supported)
- **Git**

### Development setup

```bash
git clone https://github.com/configflux/weld.git
cd weld
pip install -e weld/
```

For tree-sitter language support (Go, Rust, TypeScript, C++):

```bash
pip install -e "weld/[tree-sitter]"
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

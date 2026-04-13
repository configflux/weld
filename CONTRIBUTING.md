# Contributing to Cortex

Thank you for your interest in Cortex. This project is
**maintainer-driven** and is not currently accepting external pull requests.

## Reporting Issues

Bug reports and feature requests are welcome. Open a GitHub issue with:

- A clear description of the problem or suggestion.
- Steps to reproduce (for bugs).
- Expected vs. actual behavior.
- Output of `cortex --version` and your Python version.

## Running Locally

If you want to try cortex on your own codebase:

### Prerequisites

- **Python** >= 3.10
- **Git**

### Setup

```bash
git clone https://github.com/configflux/cortex.git
cd cortex
pip install -e cortex/
```

For tree-sitter language support (Go, Rust, TypeScript, C++):

```bash
pip install -e "cortex/[tree-sitter]"
```

### Verify

```bash
cortex --help
cortex discover
```

## License

Cortex is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for
details.

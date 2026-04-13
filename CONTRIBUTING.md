# Contributing to configflux-cortex

Thank you for your interest in contributing to configflux-cortex. This document
explains how to set up a development environment, make changes, and submit
them for review.

## License

configflux-cortex is licensed under the Apache License 2.0. By submitting a
contribution you agree that your work will be distributed under the same
license terms described in `LICENSE`.

## Getting Started

### Prerequisites

- **Python** >= 3.10
- **Git**

Optional (for the full test suite):

- **Bazel** (via Bazelisk)

### Setup

Clone and install in development mode:

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

With Bazel:

```bash
bazel build //...
bazel test //...
```

## Development Workflow

1. Fork the repository and create a feature branch.
2. Make your changes. Add or update tests as appropriate.
3. Run the test suite to verify nothing is broken.
4. Submit a pull request against `main`.

### Code Style

- Python code is checked with `ruff`. Run `ruff check cortex/` locally before
  submitting.
- Keep source files under 400 lines where practical.
- No type stubs or unused imports.

### Tests

Every behavioral change should include a test. Tests live in `cortex/tests/`
and run under Bazel:

```bash
bazel test //cortex/tests/...
```

Or with pytest directly:

```bash
python -m pytest cortex/tests/
```

## Reporting Issues

Open a GitHub issue with a clear description of the problem, steps to
reproduce, and the expected vs. actual behavior. Include the output of
`cortex --version` and your Python version.

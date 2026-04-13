"""Backwards-compatibility shims for the `kg` -> `cortex` rename.

This module hosts the `kg` console-script entry point. It prints a one-line
deprecation warning on stderr and then delegates to ``cortex.cli:main`` so
that existing `kg <subcommand>` invocations keep working during the
transition period described in ADR 0019.

The shim is intended to be removed after a transition period of at least
two months per ADR 0019. Until then, it is registered in
``cortex/pyproject.toml`` as the `kg` console script.
"""

from __future__ import annotations

import sys

from cortex.cli import main as cortex_cli_main

_DEPRECATION_MSG = (
    "\u26a0 kg has been renamed to cortex — run `cortex migrate` to update "
    "your project. This `kg` shim will be removed in a future release."
)

def kg_shim_main(argv: list[str] | None = None) -> int:
    """Entry point for the legacy ``kg`` console script.

    Prints a one-line deprecation warning to stderr, then delegates the
    remaining arguments to ``cortex.cli:main``. The return code is whatever
    the cortex CLI returns.
    """
    print(_DEPRECATION_MSG, file=sys.stderr)
    result = cortex_cli_main(argv)
    return result if isinstance(result, int) else 0

if __name__ == "__main__":
    raise SystemExit(kg_shim_main())

"""``wd security`` CLI -- thin alias for ``wd doctor --security`` (ADR 0025).

Both this module and ``wd doctor --security`` share the engine in
``weld._security_posture``. The two surfaces emit identical text and JSON
output for the same root, with identical exit codes (1 when any signal is
``high``, else 0). See ADR 0025 for the contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from weld._security_posture import assess, format_human, has_high, to_json


_DESCRIPTION = (
    "Trust-posture summary: project-local strategies, external_json adapters, "
    "enrichment provider configuration, MCP importability, and risk roll-up."
)


def _build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=_DESCRIPTION)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the trust-posture report as JSON",
    )
    return parser


def run_security(root: Path, *, as_json: bool) -> int:
    """Shared entrypoint used by both ``wd security`` and ``wd doctor --security``.

    Returns 1 when any ``high`` signal is present, else 0.
    """
    report = assess(root.resolve())
    if as_json:
        sys.stdout.write(json.dumps(to_json(report), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_human(report) + "\n")
    return 1 if has_high(report) else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd security``."""
    parser = _build_parser("wd security")
    args = parser.parse_args(argv)
    return run_security(args.root, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
